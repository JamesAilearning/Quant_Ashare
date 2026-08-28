"""日度信号与人工决策 — 工件检视 + 人工决策日志。

Renders PERSISTED artifacts only(dated ``daily_recommendation_*.json`` + the
production model's meta sidecars)and appends operator decisions to the
web-owned journal. It never re-runs inference, never triggers training / GPU /
jobs, and — apart from the journal append — is read-only. Spec:
``openspec/changes/add-daily-decision-page`` (v2-daily-decision-page).

Boundary reminders (machine-enforced by tests/logic):
* Missing model-meta fields render a prominent WARN — never a default value.
* The candidate table passes through generation-side fields only.
* The journal is NEVER an input to official metrics; src/ must not reference it.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pandas as pd
import streamlit as st

from web.operator_ui.artifact_reader import read_json_artifact
from web.operator_ui.components import render_empty_state
from web.operator_ui.daily_signal_navigation import (
    DAILY_DECISION_REQUESTED_DATE_KEY,
    prepare_daily_decision_selection,
)
from web.operator_ui.decision_journal import (
    ACTIONS,
    DecisionJournalError,
    append_decision,
    journal_path,
    make_entry,
    read_journal,
)
from web.operator_ui.page_header import render_page_header
from web.operator_ui.pages._daily_decision_helpers import (
    BASELINE_BLOCK_HISTORY_GAP,
    CERTIFIED_SLIPPAGE_BPS,
    COST_REFERENCE_COLUMN,
    VERDICT_ENSEMBLE_SHA_MISSING,
    VERDICT_ENSEMBLE_UNDER_SINGLE,
    VERDICT_INCUMBENT_UNRESOLVED,
    VERDICT_MATCHES_INCUMBENT,
    VERDICT_OTHER_MANIFEST,
    VERDICT_SHAPE_SINGLE_UNDER_ENSEMBLE,
    VERDICT_SINGLE_SHA_MISMATCH,
    VERDICT_SINGLE_SHA_OK,
    VERDICT_SINGLE_SHA_UNKNOWN,
    VERDICT_V1_UNKNOWN,
    anchored_to_repo,
    artifact_entry_timing_is_valid,
    artifact_kind_of,
    artifact_meta_status,
    artifact_schema_is_supported,
    banner_status,
    baseline_roster,
    find_nominal_baseline,
    hold_state,
    journal_model_id,
    list_recommendation_artifacts,
    load_promotion_meta,
    load_trainer_sidecar_sha,
    picks_table_rows,
    provenance_verdict,
    resolve_incumbent,
    resolve_model_path,
    review_progress_is_available,
)
from web.operator_ui.pages._daily_review_progress_helpers import (
    DailyReviewProgress,
    summarise_daily_review_progress,
    validate_review_candidate_codes,
)

_ACTION_LABELS = {"adopt": "采纳", "reject": "拒绝", "watch": "观望"}


def _render_review_progress(progress: DailyReviewProgress) -> None:
    """Render journal-derived human-review coverage without execution claims."""
    st.subheader("人工审阅进度")
    if not progress.candidate_count:
        st.info("当前有效信号没有候选；各项人工审阅统计均为 0。")
    coverage_cols = st.columns(3)
    coverage_cols[0].metric("候选", progress.candidate_count)
    coverage_cols[1].metric("已审阅", progress.reviewed_count)
    coverage_cols[2].metric("未审阅", progress.unreviewed_count)
    action_cols = st.columns(3)
    action_cols[0].metric("人工采纳", progress.adopt_count)
    action_cols[1].metric("人工拒绝", progress.reject_count)
    action_cols[2].metric("人工观望", progress.watch_count)
    st.caption(
        "上述仅表示当前候选的有效人工审阅记录，不表示买入、卖出、持仓或订单已执行。"
    )
    if progress.latest_reviewed_at:
        st.caption(f"最近一次有效人工审阅：{progress.latest_reviewed_at}")
    else:
        st.caption("当前候选尚无有效人工审阅记录。")

render_page_header(
    "日度信号与人工决策",
    "只读检视日度信号工件 + 记录人工决策(采纳/拒绝/观望)。"
    "本页不重跑推断、不触发任何作业;工件由 scripts/daily_recommend.py 晨间产出。",
)

# ---------------------------------------------------------------------------
# 模型元信息横幅(常驻页顶)— 缺任一字段 → 醒目 WARN,绝不默认值
# ---------------------------------------------------------------------------
_model_path = anchored_to_repo(resolve_model_path())
# Which model is production ACTUALLY serving? Before this, the banner always
# described the single model behind QUANT_MODEL_PATH — after the 2026-08-05
# ensemble cutover that named a RETIRED model on every render.
_incumbent = resolve_incumbent()

if _incumbent.kind == "single" and not _model_path.strip():
    # `QUANT_MODEL_PATH` set but empty. The resolver mirrors the CLI, which
    # does NOT substitute the default for an empty value (r24), so say the
    # cause once instead of leaving the operator to read it out of a
    # "元信息缺失" warning whose data source renders as an empty backtick.
    #
    # ONLY under a single-model incumbent. In ensemble mode the CLI refuses
    # `--model` outright (mutually exclusive with `--ensemble-manifest`) and
    # never reads `_DEFAULT_MODEL`, so an empty override changes nothing —
    # a red banner there would report a failure that cannot happen, on the
    # deployment production actually runs (codex #431 r25).
    st.error(
        "⚠ **`QUANT_MODEL_PATH` 被设为空值**——出单侧 CLI 同样不会用默认值顶替,"
        "它会拿着空路径失败。单模型形态下的晋升 meta 因此无从读起。"
        "请把该环境变量取消设置(用文档默认)或指向真实模型。"
    )

if _incumbent.kind == "unresolvable":
    # The pointer says "production is an ensemble" and we cannot confirm
    # which one. Falling back to the single-model banner here would show a
    # model that may not be serving — the exact failure this page exists to
    # prevent — so refuse to describe an incumbent at all.
    st.error(
        "⚠ 现任 ensemble manifest 无法解析(本页绝不退回单模型形态顶替):"
        f"`{_incumbent.manifest_path}` — {_incumbent.error}。"
        "请核查 QUANT_ENSEMBLE_MANIFEST 指向的生产 manifest;"
        "在此之前,下方候选的出处无法确认。"
    )
elif _incumbent.is_ensemble:
    st.caption("现任生产模型(ensemble)")
    _mf_name = Path(str(_incumbent.manifest_path)).name
    _mf_sha = str(_incumbent.manifest_sha256 or "")
    st.markdown(
        f"**{_mf_name}** — sha256 `{_mf_sha[:12]}…`,"
        f"{len(_incumbent.members)} 名成员"
    )
    _member_cols = st.columns(len(_incumbent.members))
    for _col, _mem in zip(_member_cols, _incumbent.members, strict=True):
        with _col:
            st.caption("成员 fit 窗")
            st.markdown(f"**{_mem['fit_start']} ~ {_mem['fit_end']}**")

_promo_meta = load_promotion_meta(_model_path)
_banner_values, _banner_missing = banner_status(_promo_meta)

# The single-model promotion banner applies only when production IS a single
# model; under an ensemble incumbent it would describe an unrelated artifact.
if _incumbent.kind != "single":
    _banner_missing = ()
    _banner_values = {}

if _banner_missing:
    st.error(
        "⚠ 模型元信息缺失(本页绝不用默认值顶替):**"
        + "、".join(_banner_missing)
        + f"**。数据源:`{_model_path}` 旁的晋升 meta(`<stem>.meta.json`)。"
        "请核查晋升流程产物;字段齐全前,请勿把下方候选当作生产建议。"
    )
# Render PRESENT fields only — a missing field lives EXCLUSIVELY in the WARN
# above. Any placeholder ("—") in the value row would make the absence look
# like a benign blank, which the spec forbids (codex P2 on #330).
_banner_items: list[tuple[str, str]] = []
if "fit_end_for_inference" in _banner_values:
    _banner_items.append(
        ("推断归一窗截止 fit_end", str(_banner_values["fit_end_for_inference"]))
    )
if "train_window" in _banner_values:
    _train_window = _banner_values["train_window"]
    _banner_items.append((
        "训练窗口",
        " ~ ".join(str(x) for x in _train_window)
        if isinstance(_train_window, (list, tuple))
        else str(_train_window),
    ))
if "promoted_at" in _banner_values:
    _banner_items.append(("晋升于 promoted_at", str(_banner_values["promoted_at"])))
if "model_path" in _banner_values or "model_type" in _banner_values:
    _model_name = (
        Path(str(_banner_values["model_path"])).name
        if "model_path" in _banner_values else ""
    )
    _model_suffix = (
        f"({_banner_values['model_type']})"
        if "model_type" in _banner_values else ""
    )
    _banner_items.append(("模型", f"{_model_name}{_model_suffix}"))
if _banner_items:
    _cols = st.columns(len(_banner_items))
    for _col, (_label, _value) in zip(_cols, _banner_items, strict=True):
        with _col:
            st.caption(_label)
            st.markdown(f"**{_value}**")

# ---------------------------------------------------------------------------
# 工件选择(默认最新)+ 生成语境交叉核对
# ---------------------------------------------------------------------------
_artifacts = list_recommendation_artifacts()
if not _artifacts:
    render_empty_state(
        "\U0001f4c4",
        "暂无日度信号工件",
        "output/daily_recommend/ 下没有 daily_recommendation_*.json。"
        "请先运行 scripts/daily_recommend.py(本页只渲染落盘工件,不代跑)。",
    )
    st.stop()

_date_options = [item[0] for item in _artifacts]
_session_state = cast(MutableMapping[str, object], st.session_state)
# The Today Workbench can link to one dated artifact.  Consume the URL hint
# once, just as the Run Center's session-state handoff is consumed below, so a
# later operator selection is not silently overwritten on every rerun.
_requested_as_of = st.query_params.get("as_of")
if isinstance(_requested_as_of, str):
    _session_state[DAILY_DECISION_REQUESTED_DATE_KEY] = _requested_as_of
    del st.query_params["as_of"]
prepare_daily_decision_selection(_session_state, _date_options)
_selected_date = st.selectbox("交易日(as_of)", _date_options, key="dd_date")
_selected_path = dict(_artifacts)[_selected_date]

_read = read_json_artifact(_selected_path, artifact_name="daily_recommendation")
if _read.issue is not None or not isinstance(_read.value, dict):
    st.error(
        f"工件不可读:{_read.issue.error_type if _read.issue else 'BadShape'} — "
        f"{_read.issue.message if _read.issue else '顶层不是 JSON object'}"
        f"({_selected_path})"
    )
    st.stop()
_payload: dict[str, Any] = _read.value

# Filename ↔ payload date consistency: a renamed/copied artifact whose payload
# as_of_date disagrees with the filename date would record the decision under
# the payload date while the page filters by the filename date — the fresh
# decision "disappears" from the selected day's table (codex P2 on #330).
# Treat the mismatch as a corrupt artifact BEFORE any journal write is offered.
_payload_as_of = str(_payload.get("as_of_date", ""))
if _payload_as_of != _selected_date:
    st.error(
        "⚠ 工件形状违约:文件名日期与 payload 的 as_of_date 不一致"
        f"(文件名 {_selected_date} vs payload {_payload_as_of!r})。"
        f"该文件可能被改名/拷贝或已损坏:{_selected_path}"
    )
    st.stop()

_current_sha = load_trainer_sidecar_sha(_model_path)
_meta_status = artifact_meta_status(_payload, _current_sha)
if _meta_status.artifact_is_corrupt_v2:
    # A v2-marked file whose meta block is missing/non-dict is CORRUPT (the
    # producer always writes a dict meta for v2) — same failure class as a
    # picks shape violation, not a benign legacy file (codex P2 on #330).
    st.error(
        "⚠ 损坏的 v2 工件:带 artifact_schema_version 标记但 meta 块缺失/"
        f"非 object。文件可能损坏或非本系统产物:{_selected_path}"
    )
    st.stop()
# ---------------------------------------------------------------------------
# 现任 × 工件 交叉核对。判定与接线都是纯函数(provenance_verdict),这一段
# 只把裁定渲染成话。四轮 codex 复审(#430 r1..r4)每一轮漏的都是同一张矩阵里
# 的另一格——有序 elif 链在结构上保证不了格子被穷举,一张表可以;而接线留在
# 页面里同样保证不了对——源码级钉子分不出 `incumbent.kind` 和一个看着也对的
# `"ensemble" if incumbent.is_ensemble else "single"`,后者会把 unresolvable
# 悄悄并进 single,r4 那格原样复活。所以接线也搬进 helpers,由行为测试驱动。
# ---------------------------------------------------------------------------
_artifact_kind = artifact_kind_of(_meta_status)
_art_sha = str(_meta_status.artifact_ensemble_sha or "")
_verdict = provenance_verdict(_incumbent, _meta_status)

if _verdict == VERDICT_MATCHES_INCUMBENT:
    st.info(
        "ℹ 该工件由 **现任 ensemble manifest** 生成:sha256 "
        f"`{_art_sha[:12]}…` — 与现任一致。"
    )
elif _verdict == VERDICT_OTHER_MANIFEST:
    st.warning(
        "⚠ 该工件出自**另一份 manifest**(非现任):工件 "
        f"`{_art_sha[:12]}…` vs 现任 "
        f"`{str(_incumbent.manifest_sha256 or '')[:12]}…`。"
        "它不是当前生产模型给出的建议,请勿据此下单。"
    )
elif _verdict == VERDICT_ENSEMBLE_UNDER_SINGLE:
    # DEFINITE mismatch, not an unknown (codex #430): the sentinel is an
    # explicit statement that production serves ONE model, so an ensemble
    # artifact provably did not come from it. "无法判定" would hide a
    # governance state we actually know.
    #
    # Reached with or without a bindable manifest sha (codex #430 r5): the
    # meta.ensemble block already DECLARES the shape, and the shape alone
    # settles this. The missing digest is reported as an extra fact, never
    # as a reason to downgrade the refusal.
    _id_txt = (
        f"(sha256 `{_art_sha[:12]}…`)" if _art_sha
        else "(meta.ensemble 缺 manifest_sha256,其身份还无法绑定)"
    )
    st.warning(
        f"⚠ 该工件由 **ensemble(manifest)** 生成{_id_txt},"
        "而**现任是单模型形态**"
        "(QUANT_ENSEMBLE_MANIFEST 显式设为 `none` 的 opt-out)。"
        "它不是当前生产模型给出的建议,请勿据此下单。"
    )
elif _verdict == VERDICT_INCUMBENT_UNRESOLVED:
    # Pointer set but unreadable — genuinely unknown, for EITHER artifact
    # shape. The single-model shape used to fall through to the legacy
    # sidecar comparison below (codex #430 r4); that comparison is against
    # the RETIRED model, so a match there printed NOTHING at all and an
    # unconfirmed artifact read as "checked and fine".
    _shape_txt = "ensemble(manifest)" if _artifact_kind == "ensemble" else "单模型"
    st.warning(
        f"⚠ **现任 manifest 不可解析**(`{_incumbent.manifest_path}`),"
        f"无法核对该工件(形态:{_shape_txt})是否出自当前生产模型。"
        "请勿据此下单,直到现任身份可确认。"
    )
elif _verdict == VERDICT_ENSEMBLE_SHA_MISSING:
    st.warning(
        "⚠ 该工件标记为 ensemble 生成,但 meta.ensemble 块缺 "
        "manifest_sha256——无法绑定其身份,请核对工件来源。"
    )
elif _verdict == VERDICT_V1_UNKNOWN:
    # A v1 artifact carries no meta at all, so its provenance is UNKNOWN —
    # calling it "单模型形态" would assert a fact we cannot establish
    # (codex #430 r2).
    st.warning(
        "⚠ 旧版工件(v1,无 meta 块):无生成语境,无法确认它出自当前生产模型。"
        "重跑 scripts/daily_recommend.py 可产出自描述的 v2 工件。"
    )
elif _verdict == VERDICT_SHAPE_SINGLE_UNDER_ENSEMBLE:
    st.warning(
        "⚠ 该工件是**单模型形态**,而现任生产是 **ensemble**"
        f"(`{Path(str(_incumbent.manifest_path)).name}`)。"
        "无论其 sidecar 是否匹配某个旧模型,它都不是当前生产模型给出的"
        "建议,请勿据此下单。"
    )
elif _verdict == VERDICT_SINGLE_SHA_MISMATCH:
    st.warning(
        "⚠ 该工件由**其他模型**生成:工件 meta.model_pkl_sha256 "
        f"(`{str(_meta_status.artifact_model_sha)[:12]}…`) ≠ 当前模型 sidecar 的 "
        f"pkl_sha256(`{str(_meta_status.current_model_sha)[:12]}…`)。"
        "决策前请确认你看的是想要的模型输出。"
    )
elif _verdict == VERDICT_SINGLE_SHA_UNKNOWN:
    st.warning(
        "⚠ 无法交叉核对工件↔模型(缺 trainer sidecar 的 pkl_sha256 或工件 meta 的 sha)。"
    )
elif _verdict != VERDICT_SINGLE_SHA_OK:
    # classify_provenance is total over the matrix, so an unrendered verdict
    # is a code defect — and SILENCE is exactly how a non-incumbent artifact
    # gets presented as safe. Fail loud rather than say nothing.
    st.error(f"⚠ 内部错误:未渲染的来源裁定 `{_verdict}`,请勿据此下单。")

# ---------------------------------------------------------------------------
# HOLD 日语义(cadence-aware,PR-A of csi800-n5-production-promotion):
# rebalance_day: false 的工件是监控视图而非入场指令 — 醒目横幅 + 决策
# 表单阻断;字段缺失(旧日频工件)/true 时渲染与既有契约一致。
# ---------------------------------------------------------------------------
_hold = hold_state(_payload)
if _hold.malformed is not None:
    st.error(f"⚠ {_hold.malformed}(文件:{_selected_path})")
    st.stop()
if _hold.is_hold:
    st.error(
        "⛔ **HOLD 日(非再平衡日)**:本工件为监控视图,**不构成入场指令**。"
        "N5 生产节奏下调仓只发生在每 ISO 周第一个交易日;下一再平衡日:**"
        + (_hold.next_rebalance_date or "(超出日历尾部,未知)")
        + "**。入场决策表单已阻断。"
    )

# ---------------------------------------------------------------------------
# 名义持仓的基准(只读对照,PR-1)
#
# 生产是 csi800 / N5 / 周频 iso_week —— 绝大多数交易日是 HOLD 日。所以「我此刻
# 名义上跟的是哪一天的那张单」这个问题,答案通常**不是**今天。此前这一页只能
# 一次看一天(日期下拉框只给日期、不标哪天是再平衡日),要回答它得逐个日期点开、
# 逐个看上面那条 HOLD 横幅。
#
# 这里把那次回溯做成一次可复核的搜索:找到了就说是哪一天,找不到就说沿途每一份
# 工件**各自因为什么**被跳过。「基准在 30 天前」和「一份合格的基准都没有」对
# 操作人的下一步完全不同。
#
# 红线(操作人已明令):这一段是**只读对照**。不接收手输持仓、不生成差分单、
# 不设缓冲带、不给任何形如下单指令的东西。工件里本来也只有 rank / 评分 /
# 可交易标志——没有权重、没有股数、没有金额,所以名单只能是代码集合。
# ---------------------------------------------------------------------------
st.markdown("#### 名义持仓基准")


def _read_baseline_payload(path: Path) -> dict[str, Any] | None:
    """读一份候选工件——走与本页其余读盘同一道输出目录守卫。"""
    result = read_json_artifact(path, artifact_name=path.name)
    if result.issue is not None or not isinstance(result.value, dict):
        return None
    loaded: dict[str, Any] = result.value
    return loaded


_baseline = find_nominal_baseline(
    _artifacts, read_payload=_read_baseline_payload, as_of=_selected_date,
)
_baseline_roster: tuple[str, ...] = ()
_baseline_unreadable = ""
if _baseline.found:
    try:
        _baseline_roster = baseline_roster(_baseline.baseline_payload)
    except ValueError as _roster_exc:
        # 损坏的名单**不是**空名单。退成 `()` 会让页面接着说「名义上跟的是
        # X 那次的清单(共 0 只)」——把一份损坏工件渲染成一个合法的空仓位,
        # 而这一页别处对 picks 形状违约的处置正是「要被看见,不要被渲染成
        # 良性空缺」。这里改判整段不可用。
        _baseline_unreadable = str(_roster_exc)

if _baseline.found and not _baseline_unreadable:
    _baseline_meta = _baseline.baseline_payload.get("meta")
    _baseline_meta = _baseline_meta if isinstance(_baseline_meta, dict) else {}
    st.info(
        f"截至 **{_selected_date}**,名义上跟的是 **{_baseline.baseline_date}** "
        f"那次再平衡的清单(共 **{len(_baseline_roster)}** 只 · "
        f"topk={_baseline_meta.get('topk', '—')} · "
        f"universe={_baseline_meta.get('instruments', '—')})。"
    )
    st.caption(
        "「名义」= 按那张单**应当**持有的代码集合。这里不知道你的实际账户持仓,"
        "也不产生任何调仓指令——本页只做只读对照。"
    )
    if _baseline_roster:
        st.dataframe(
            [{"序": _i + 1, "代码": _code}
             for _i, _code in enumerate(_baseline_roster)],
            hide_index=True,
            width="stretch",
        )
elif _baseline_unreadable:
    st.error(
        f"⚠ 找到的基准工件（{_baseline.baseline_date}）形状违约，"
        f"**不能**当作名义持仓基准:{_baseline_unreadable}"
    )
elif _baseline.unknowable:
    # 「不知道」与「确实没有」是两件事。回溯停在一份**回答不了自己是不是
    # 再平衡日**的工件上——它本身可能就是一次更近的再平衡,所以再往回翻出
    # 来的那张单可能**已经被它取代**。报成「找不到」会让操作人以为翻遍了,
    # 报成某张旧单会让他拿过期的清单当此刻该持有的。
    _blocked = _baseline.blocked_by
    assert _blocked is not None
    st.error(
        f"⚠ 截至 **{_selected_date}** 名义持仓基准**不可知**:回溯在 "
        f"**{_blocked.trade_date}** 那一份上停下——{_blocked.detail}"
    )
    # 总说明**不替两种成因下同一个结论**（codex #472 上学到的同一课）:
    # 「这一份回答不了它自己是不是再平衡日」对**缺口**那一种是假话——缺口停
    # 下时那一份恰恰是一个经过校验的 HOLD,问题在它与更早那份之间那几天。
    if _blocked.reason == BASELINE_BLOCK_HISTORY_GAP:
        st.caption(
            "为什么不继续往回翻:经过校验的 HOLD 只证明了**那一天**没换手,"
            "证明不了**那一段**。中间那些没有工件的交易日各自都可能是一次"
            "再平衡,而没有任何记录能排除它们——翻过去报出来的清单可能"
            "**早已被取代**。"
        )
    else:
        st.caption(
            "为什么不继续往回翻:只有**经过校验的 HOLD 日**才能证明「那天没换手、"
            "所以更早那张单仍然有效」。这一份回答不了它自己是不是再平衡日,继续"
            "翻出来的清单可能**已经被它取代**——那就成了拿过期的单当此刻该持有的。"
        )
elif _baseline.limit_reached:
    # 「翻到上限就停了」与「翻完了都没有」是两件事(codex P2 on #475):更早的
    # 工件**还在**,只是没读。说成「翻到底」会让操作人以为这台机器上确实没有
    # 更早的再平衡记录,而下面那条上限说明会与这句直接打架。
    st.warning(
        f"⚠ 截至 **{_selected_date}** 名义持仓基准**尚未查到**:已回溯 "
        f"{_baseline.scanned} 份工件(全部是经过校验的 HOLD 日)后**撞到扫描"
        "上限**停下——更早的工件还在，只是没有读。"
    )
    st.caption(
        "这不是「翻完了都没有」。要看更早的基准，请选一个更早的日期再看一次。"
    )
else:
    st.warning(
        f"⚠ 截至 **{_selected_date}** 回溯到底也没遇到再平衡日"
        f"(已回溯 {_baseline.scanned} 份工件,全部是 HOLD 日)。"
    )
    st.caption(
        "这不等于「没有持仓」——它表示**这台机器上的工件**里，最近一次换手"
        "早于现有工件的覆盖范围。"
    )
if _baseline.skipped:
    with st.expander(
        f"回溯途中经过的 HOLD 日({len(_baseline.skipped)} 份)", expanded=False,
    ):
        st.dataframe(
            [{"日期": _c.trade_date, "原因": _c.detail}
             for _c in _baseline.skipped],
            hide_index=True,
            width="stretch",
        )
if _baseline.limit_reached:
    st.caption(
        f"· 回溯在第 {_baseline.scanned} 份停下(扫描上限)。更早的工件没有读——"
        "无上界地往回翻会把「基准早已过期」说成「找到了」。"
    )

# ---------------------------------------------------------------------------
# 候选表(只读透传 + 30bps 成本参照列)
# ---------------------------------------------------------------------------
try:
    _rows = picks_table_rows(_payload)
except ValueError as _shape_exc:
    # Shape violation ≠ empty list: a corrupt/incompatible artifact must be
    # SEEN, not rendered as a benign "no candidates" state (codex P2 on #330).
    st.error(f"⚠ {_shape_exc}(文件:{_selected_path})")
    st.stop()
st.caption(
    f"as_of {_payload.get('as_of_date', '—')} → entry {_payload.get('entry_date', '—')} · "
    f"n_scored={_payload.get('n_scored', '—')} · n_masked={_payload.get('n_masked', '—')} · "
    f"n_st_excluded={_payload.get('n_st_excluded', '—')}"
)
# runbook 红线(docs/daily-recommend-runbook.md「Which session is the list
# for?」):entry 按契约是 T+1 交易日,而可交易性筛选(停牌/一字板)需要该日
# 真实 bar,所以它必在 bundle 内、必是**已收盘**会话——工具永远出不了面向
# 未开盘会话的单。这里只纠正**时点**上的误读("按明早开盘价买"),不谈
# 这张单该不该执行:那由 rebalance_day 决定,上方 HOLD 横幅已单独承载
# (codex #443 r1:笼统否定会让该执行的清单被误当成不该执行)。
#
# 缺 entry_date 的工件不得走这条路:那样会渲染出「entry — 是已收盘会话」
# ——把一份**违约的**数据当成可信引导来背书(codex #443 r2)。
_entry_date = _payload.get("entry_date")
_entry_date_is_valid = artifact_entry_timing_is_valid(_payload)
_artifact_schema_supported = artifact_schema_is_supported(_payload)
_artifact_contract_valid = _entry_date_is_valid and _artifact_schema_supported
if not _entry_date_is_valid:
    st.error(
        "⚠ 工件 `entry_date`/`as_of_date` 缺失、格式错误或非前向会话——"
        "**工件契约被违反**,"
        "本页拒绝据此给出任何入场时点结论。请核查产出该工件的那次运行"
        "(scripts/daily_recommend.py 正常写出该字段)。"
    )
else:
    st.caption(
        f"⏱ **entry {_entry_date} 是已收盘会话**"
        "(契约上的 T+1;可交易性校验需要该日真实 bar,故必在 bundle 内)。"
        "因此**不要读成「明早开盘按市价买入」**——它是该会话收盘口径的"
        "目标持仓;实际下单如何向它靠拢由操作人的执行约定决定(观察期正在"
        "记录这段偏离)。**是否构成入场指令**看上方再平衡/HOLD 披露。"
    )
if not _artifact_schema_supported:
    st.error(
        "⚠ 工件 `artifact_schema_version` 缺失、格式错误或不受当前页面支持——"
        "**工件契约被违反**，不显示人工审阅完成度或候选审阅标签。"
    )
# 成本参照列的读法。滑点数值与常量同源派生——写死「20 bps」会在认证
# profile 挪动时和列名/所减数字对不上(codex #443 r1)。
st.caption(
    f"「{COST_REFERENCE_COLUMN}」= 生产认证成本口径的一次完整往返"
    f"({CERTIFIED_SLIPPAGE_BPS:.0f} bps 单边滑点 + 佣金 + 印花税)。"
    "评分是**1 日**预测收益而生产周频持有约 5 日,故该列是**保守下界**,"
    "不是逐日门槛。"
)
# Declare the visual positions before the form. The journal is read after a
# possible append below, so the same click renders the up-to-date effective
# record without adding a second JSONL reader or a manual refresh step.
_review_summary_slot = st.container()
_candidate_table_slot = st.empty()
with _candidate_table_slot:
    if _rows:
        st.dataframe(
            pd.DataFrame(_rows), use_container_width=True, hide_index=True,
        )
    else:
        st.info("该工件买入清单为空(topk=0 或全部被掩)。")

# ---------------------------------------------------------------------------
# 决策表单(显式按钮 + 落盘 nonce 幂等;见威胁对表 T1)
# HOLD 日整段不渲染(spec: 入场表单 SHALL 被禁用或等效阻断)——监控
# 视图不受理入场决策;当日 effective 决策表在下方照常展示。
# ---------------------------------------------------------------------------
st.markdown("---")
st.subheader("记录决策")
try:
    # Resolves + validates the journal location ONCE for the whole section:
    # a QUANT_DECISION_JOURNAL_DIR pointing under the disposable output/ tree
    # fails loud here (rendered error, not a raw traceback) before any
    # append/read is attempted.
    _journal_file = journal_path()
except DecisionJournalError as _journal_exc:
    st.error(f"⚠ 决策日志不可用:{_journal_exc}")
    st.stop()
if "dd_nonce" not in st.session_state:
    st.session_state["dd_nonce"] = uuid4().hex

try:
    _codes = validate_review_candidate_codes(
        _selected_date, (str(row.get("代码") or "") for row in _rows),
    )
except ValueError as _candidate_code_exc:
    st.error(f"⚠ 工件候选标识无法核验：{_candidate_code_exc}")
    # An ambiguous candidate set disables the entry form and its projection,
    # but it says nothing about the append-only journal.  Continue to its
    # reader below so valid historical entries and malformed-row warnings stay
    # inspectable instead of disappearing behind a candidate-artifact error.
    _candidate_codes_valid = False
    _codes = ()
else:
    _candidate_codes_valid = True
if not _candidate_codes_valid:
    st.info("当前候选标识无法唯一映射；不显示决策表单或人工审阅进度。")
elif _hold.is_hold:
    # spec(v2-daily-decision-page HOLD reader): 入场表单 SHALL 被禁用或
    # 等效阻断 — HOLD 日不渲染表单控件,监控视图不受理入场决策。
    st.caption(
        "⛔ HOLD 日不受理入场决策(监控视图)。入场决策请于下一再平衡日"
        "重跑 scripts/daily_recommend.py 后进行。"
    )
elif not _codes:
    st.info("无候选可决策。")
else:
    _fc1, _fc2 = st.columns([1, 1])
    with _fc1:
        _sel_code = st.selectbox("候选", _codes, key="dd_code")
        _action = st.radio(
            "决策",
            list(ACTIONS),
            horizontal=True,
            key="dd_action",
            format_func=lambda a: _ACTION_LABELS.get(a, a),
        )
    with _fc2:
        _reason = st.text_input(
            "一句话理由(必填)", key="dd_reason",
            placeholder="例:评分高出成本参照且流动性充足",
        )
    if st.button("✍ 记录决策", key="dd_submit", type="primary"):
        # ``validate_review_candidate_codes`` normalises surrounding
        # whitespace before the selectbox exposes the exact journal key.  Use
        # that same normalisation for the display-row lookup, or an accepted
        # artifact code such as ``" SH600000 "`` would lose its rank/score
        # when the append-only decision entry is created.
        _pick_row = next(
            (r for r in _rows if str(r.get("代码") or "").strip() == _sel_code),
            None,
        )
        try:
            _entry = make_entry(
                trade_date=str(_payload.get("as_of_date", "")),
                code=_sel_code,
                action=str(_action),
                reason=_reason,
                rank=(
                    int(_pick_row["rank"])
                    if _pick_row and _pick_row.get("rank") is not None
                    else None
                ),
                score=(
                    float(_pick_row["评分"])
                    if _pick_row and _pick_row.get("评分") is not None
                    else None
                ),
                model_id=journal_model_id(_payload),
                nonce=str(st.session_state["dd_nonce"]),
            )
            _appended = append_decision(_entry)
        except DecisionJournalError as exc:
            st.error(f"未记录:{exc}")
        else:
            if _appended:
                # Fresh nonce AFTER a successful append: the next submission is
                # a new decision; a rerun replay of THIS one stays refused.
                st.session_state["dd_nonce"] = uuid4().hex
                st.success(
                    f"已记录:{_selected_date} {_sel_code} "
                    f"{_ACTION_LABELS.get(str(_action), str(_action))}"
                )
            else:
                # Rotate on the DUPLICATE branch too: a stale already-persisted
                # nonce (e.g. session state outliving a raced success-rotation)
                # would otherwise suppress every future legitimate correction
                # for this (trade_date, code) as a "replay" (codex P2 on #330).
                st.session_state["dd_nonce"] = uuid4().hex
                st.info("该提交已记录过(幂等拦截:同 nonce 重放不会重复入账)。")

# ---------------------------------------------------------------------------
# 当日 effective 决策(更正后以 decided_at 最新为准;历史行永不删除)
# ---------------------------------------------------------------------------
try:
    _journal = read_journal()
except DecisionJournalError as _read_exc:
    st.error(f"⚠ 决策日志读取失败:{_read_exc}")
    st.stop()
with _review_summary_slot:
    if _journal.malformed_count:
        st.warning(
            f"⚠ 决策日志含 {_journal.malformed_count} 行坏行(已跳过未入账;"
            f"文件:{_journal_file})。以下仅统计有效记录，审阅完整性需要核验。"
        )
    if not review_progress_is_available(
        verdict=_verdict, artifact_contract_valid=_artifact_contract_valid,
    ):
        if not _artifact_contract_valid:
            st.info("当前工件契约未通过；不显示人工审阅完成度或候选审阅标签。")
        else:
            st.info("当前工件的来源尚未核验；不显示人工审阅完成度或候选审阅标签。")
        _review_progress = None
    elif _hold.is_hold:
        st.info("HOLD 日不显示人工审阅完成度；该工件不构成入场决策。")
        _review_progress = None
    elif not _candidate_codes_valid:
        st.info("当前候选标识无法唯一映射；不显示人工审阅完成度或候选审阅标签。")
        _review_progress = None
    else:
        try:
            _review_progress = summarise_daily_review_progress(
                _selected_date, _codes, _journal.effective,
            )
        except ValueError as _review_exc:
            st.error(f"⚠ 无法核验当前候选的人工审阅进度：{_review_exc}")
            st.stop()
        _render_review_progress(_review_progress)

with _candidate_table_slot:
    if _rows:
        if _review_progress is None:
            _candidate_display_rows = _rows
        else:
            _candidate_display_rows = [
                {
                    **row,
                    "人工审阅": (
                        "未审阅"
                        if state.action is None
                        else f"人工审阅·{_ACTION_LABELS.get(state.action, state.action)}"
                    ),
                    "最新理由摘要": state.reason_summary or "—",
                    "最近审阅时间": state.decided_at or "—",
                }
                for row, state in zip(
                    _rows, _review_progress.candidates, strict=True,
                )
            ]
        st.dataframe(
            pd.DataFrame(_candidate_display_rows),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("该工件买入清单为空(topk=0 或全部被掩)。")
_today_entries = [
    {
        "代码": entry.code,
        "决策": _ACTION_LABELS.get(entry.action, entry.action),
        "理由": entry.reason,
        "rank": entry.rank,
        "score": entry.score,
        "decided_at": entry.decided_at,
    }
    for entry in _journal.entries
    if entry.trade_date == _selected_date
]
st.caption("下方为该日期的全部有效 append-only 日志记录；与当前候选不匹配的记录不计入上方审阅进度。")
st.subheader(f"{_selected_date} 的有效日志记录({len(_today_entries)})")
if _today_entries:
    st.dataframe(
        pd.DataFrame(_today_entries), use_container_width=True, hide_index=True,
    )
else:
    st.caption("该交易日尚无决策记录。")
st.caption(
    f"日志:{_journal_file}(append-only;更正=追加新条目,同日同代码以 "
    f"decided_at 最新者生效;共 {len(_journal.entries)} 行有效记录)。"
    "本日志永不作为官方指标输入。"
)
