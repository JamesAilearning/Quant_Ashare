"""今日推荐 — 每日决策页(工件检视 + 人工决策日志)。

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

from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd
import streamlit as st

from web.operator_ui.artifact_reader import read_json_artifact
from web.operator_ui.components import render_empty_state
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
    artifact_meta_status,
    banner_status,
    journal_model_id,
    list_recommendation_artifacts,
    load_promotion_meta,
    load_trainer_sidecar_sha,
    picks_table_rows,
    resolve_model_path,
)

_ACTION_LABELS = {"adopt": "采纳", "reject": "拒绝", "watch": "观望"}

render_page_header(
    "今日推荐",
    "只读检视每日荐股工件 + 记录人工决策(采纳/拒绝/观望)。"
    "本页不重跑推断、不触发任何作业;推荐由 scripts/daily_recommend.py 晨间产出。",
)

# ---------------------------------------------------------------------------
# 模型元信息横幅(常驻页顶)— 缺任一字段 → 醒目 WARN,绝不默认值
# ---------------------------------------------------------------------------
_model_path = resolve_model_path()
_promo_meta = load_promotion_meta(_model_path)
_banner_values, _banner_missing = banner_status(_promo_meta)

if _banner_missing:
    st.error(
        "⚠ 模型元信息缺失(本页绝不用默认值顶替):**"
        + "、".join(_banner_missing)
        + f"**。数据源:`{_model_path}` 旁的晋升 meta(`<stem>.meta.json`)。"
        "请核查晋升流程产物;字段齐全前,请勿把下方候选当作生产建议。"
    )
if _banner_values:
    _cols = st.columns(4)
    _train_window = _banner_values.get("train_window")
    _banner_items: tuple[tuple[str, str], ...] = (
        ("推断归一窗截止 fit_end", str(_banner_values.get("fit_end_for_inference", "—"))),
        (
            "训练窗口",
            " ~ ".join(str(x) for x in _train_window)
            if isinstance(_train_window, (list, tuple)) and _train_window
            else str(_train_window if _train_window is not None else "—"),
        ),
        ("晋升于 promoted_at", str(_banner_values.get("promoted_at", "—"))),
        (
            "模型",
            Path(str(_banner_values.get("model_path", "—"))).name
            + (
                f"({_banner_values['model_type']})"
                if _banner_values.get("model_type")
                else ""
            ),
        ),
    )
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
        "暂无每日推荐工件",
        "output/daily_recommend/ 下没有 daily_recommendation_*.json。"
        "请先运行 scripts/daily_recommend.py(本页只渲染落盘工件,不代跑)。",
    )
    st.stop()

_date_options = [item[0] for item in _artifacts]
_selected_date = st.selectbox("交易日(as_of)", _date_options, index=0, key="dd_date")
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

_current_sha = load_trainer_sidecar_sha(_model_path)
_meta_status = artifact_meta_status(_payload, _current_sha)
if _meta_status.artifact_is_v1:
    st.warning(
        "⚠ 旧版工件(v1,无 meta 块):无生成语境,无法确认它出自当前生产模型。"
        "重跑 scripts/daily_recommend.py 可产出自描述的 v2 工件。"
    )
elif _meta_status.sha_mismatch is True:
    st.warning(
        "⚠ 该工件由**其他模型**生成:工件 meta.model_pkl_sha256 "
        f"(`{str(_meta_status.artifact_model_sha)[:12]}…`) ≠ 当前模型 sidecar 的 "
        f"pkl_sha256(`{str(_meta_status.current_model_sha)[:12]}…`)。"
        "决策前请确认你看的是想要的模型输出。"
    )
elif _meta_status.sha_mismatch is None:
    st.warning(
        "⚠ 无法交叉核对工件↔模型(缺 trainer sidecar 的 pkl_sha256 或工件 meta 的 sha)。"
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
if _rows:
    st.dataframe(pd.DataFrame(_rows), use_container_width=True, hide_index=True)
else:
    st.info("该工件买入清单为空(topk=0 或全部被掩)。")

# ---------------------------------------------------------------------------
# 决策表单(显式按钮 + 落盘 nonce 幂等;见威胁对表 T1)
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
if not _codes:
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
                st.info("该提交已记录过(幂等拦截:同 nonce 重放不会重复入账)。")

# ---------------------------------------------------------------------------
# 当日 effective 决策(更正后以 decided_at 最新为准;历史行永不删除)
# ---------------------------------------------------------------------------
_journal = read_journal()
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
