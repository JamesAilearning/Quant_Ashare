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
    artifact_kind_of,
    artifact_meta_status,
    banner_status,
    hold_state,
    journal_model_id,
    list_recommendation_artifacts,
    load_promotion_meta,
    load_trainer_sidecar_sha,
    picks_table_rows,
    provenance_verdict,
    resolve_incumbent,
    resolve_model_path,
)

_ACTION_LABELS = {"adopt": "采纳", "reject": "拒绝", "watch": "观望"}

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
if not isinstance(_entry_date, str) or not _entry_date.strip():
    st.error(
        "⚠ 工件缺少 `entry_date`(或为空/非字符串)——**工件契约被违反**,"
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
# 成本参照列的读法。滑点数值与常量同源派生——写死「20 bps」会在认证
# profile 挪动时和列名/所减数字对不上(codex #443 r1)。
st.caption(
    f"「{COST_REFERENCE_COLUMN}」= 生产认证成本口径的一次完整往返"
    f"({CERTIFIED_SLIPPAGE_BPS:.0f} bps 单边滑点 + 佣金 + 印花税)。"
    "评分是**1 日**预测收益而生产周频持有约 5 日,故该列是**保守下界**,"
    "不是逐日门槛。"
)
if _rows:
    st.dataframe(pd.DataFrame(_rows), use_container_width=True, hide_index=True)
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

_codes = [str(row["代码"]) for row in _rows if row.get("代码")]
if _hold.is_hold:
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
        _pick_row = next((r for r in _rows if str(r["代码"]) == _sel_code), None)
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
if _journal.malformed_count:
    st.warning(
        f"⚠ 决策日志含 {_journal.malformed_count} 行坏行(已跳过未入账;"
        f"文件:{_journal_file})。"
    )
_today_effective = [
    {
        "代码": entry.code,
        "决策": _ACTION_LABELS.get(entry.action, entry.action),
        "理由": entry.reason,
        "rank": entry.rank,
        "score": entry.score,
        "decided_at": entry.decided_at,
    }
    for (t_date, _code), entry in sorted(_journal.effective.items())
    if t_date == _selected_date
]
st.subheader(f"{_selected_date} 的决策({len(_today_effective)})")
if _today_effective:
    st.dataframe(
        pd.DataFrame(_today_effective), use_container_width=True, hide_index=True,
    )
else:
    st.caption("该交易日尚无决策记录。")
st.caption(
    f"日志:{_journal_file}(append-only;更正=追加新条目,同日同代码以 "
    f"decided_at 最新者生效;共 {len(_journal.entries)} 行有效记录)。"
    "本日志永不作为官方指标输入。"
)
