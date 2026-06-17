"""Jobs list page — unified view of UI-launched and CLI-catalogued runs.

Workflow:
- Filters (type / status / source / date range / search) and sort selection are
  reflected in the URL (``st.query_params``) so reload preserves state and the
  current view is shareable.
- Single-row selection on the dataframe surfaces an action bar; "Open" routes
  the operator to the result or walk-forward detail page via
  ``st.switch_page``, seeding the selected run id in ``st.session_state`` and
  ``st.query_params``.
- An optional auto-refresh keeps the list current while any job is running.
"""

from __future__ import annotations

import html
import time
from datetime import date, timedelta
from typing import Any
from urllib.parse import quote_plus

import pandas as pd
import streamlit as st

from web.operator_ui._param_guard import sanitize as _sanitize_qp
from web.operator_ui.components import (
    render_badge,
    render_empty_state,
    render_error_state,
)
from web.operator_ui.formatting import (
    format_date_absolute,
    format_duration,
    format_relative_time,
)
from web.operator_ui.job_io import (
    SORT_OPTIONS,
    jobs_eligible_for_cleanup,
    list_all_jobs,
)
from web.operator_ui.job_manager import JobManager, JobManagerError
from web.operator_ui.page_header import render_page_header

# ---------------------------------------------------------------------------
# URL <-> session sync helpers
# ---------------------------------------------------------------------------

_DEFAULTS: dict[str, str] = {
    "type": "all",
    "status": "all",
    "source": "all",
    "search": "",
    "date_from": "",
    "date_to": "",
    "sort_by": "created_at",
    "sort_dir": "desc",
    "page": "1",
    "autorefresh": "0",
}


def _qp_read(key: str) -> str:
    # Always route URL params through the per-key validator so a
    # hostile/typo URL can't inject arbitrary strings into session_state
    # or downstream filters. Unknown / malformed values fall back to
    # ``_DEFAULTS[key]`` (the same default a user with no URL would
    # see). See ``web/operator_ui/_param_guard.py``.
    raw = st.query_params.get(key, _DEFAULTS[key])
    return _sanitize_qp(key, raw, default=_DEFAULTS[key])


def _qp_write(key: str, value: str) -> None:
    """Mirror non-default values into the URL; remove the key when default."""
    if value == _DEFAULTS.get(key, "") or value == "":
        try:
            del st.query_params[key]
        except KeyError:
            pass
    else:
        st.query_params[key] = value


def _seed_session_from_url(keys: list[str]) -> None:
    """On first render of this page, copy URL params into widget-bound keys."""
    for k in keys:
        sk = f"jobs_{k}"
        if sk not in st.session_state:
            st.session_state[sk] = _qp_read(k)


_seed_session_from_url(list(_DEFAULTS.keys()))


# ---------------------------------------------------------------------------
# 中文标签映射（保留英文值用于 URL / 后端契约，仅在 UI 展示时换中文）
# ---------------------------------------------------------------------------
_TYPE_LABELS: dict[str, str] = {
    "all": "全部",
    "pipeline": "流水线",
    "walk_forward": "滚动验证",
    "provider": "数据源",
}
_STATUS_LABELS: dict[str, str] = {
    "all": "全部",
    "queued": "排队中",
    "pending": "等待中",
    "running": "运行中",
    "completed": "已完成",
    "success": "已完成",
    "ok": "已完成",
    "partial": "部分完成",
    "failed": "失败",
    "cancelled": "已取消",
    "stopped": "已停止",
    "stop_failed": "停止失败",
    "unknown": "未知",
}
_SOURCE_LABELS: dict[str, str] = {
    "all": "全部",
    "ui": "UI",
    "cli": "CLI",
}
_SORT_BY_LABELS: dict[str, str] = {
    "created_at": "创建时间",
    "duration": "耗时",
    "status": "状态",
    "type": "类型",
    "run_id": "运行 ID",
}
_FILTER_KEY_LABELS: dict[str, str] = {
    "type": "类型",
    "status": "状态",
    "source": "来源",
    "search": "搜索",
    "date_from": "起始",
    "date_to": "结束",
}


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
render_page_header("作业", "所有流水线、滚动验证及数据源的运行记录。")
# FU-8: surface bundle freshness next to the header. Operators can
# now spot a stale / mis-configured ``provider_uri`` before they
# launch a multi-hour walk-forward run.
from web.operator_ui.bundle_health import (  # noqa: E402, PLC0415
    render_bundle_health_banner,
)

render_bundle_health_banner(st=st)

# ---------------------------------------------------------------------------
# Filter row 1: type / status / source / search
# ---------------------------------------------------------------------------
fcol1, fcol2, fcol3, fcol4 = st.columns(4)
with fcol1:
    type_filter = st.selectbox(
        "类型",
        ["all", "pipeline", "walk_forward", "provider"],
        key="jobs_type",
        format_func=lambda v: _TYPE_LABELS.get(v, v),
    )
with fcol2:
    status_filter = st.selectbox(
        "状态",
        ["all", "queued", "running", "completed", "failed", "cancelled"],
        key="jobs_status",
        format_func=lambda v: _STATUS_LABELS.get(v, v),
    )
with fcol3:
    source_filter = st.selectbox(
        "来源",
        ["all", "ui", "cli"],
        key="jobs_source",
        format_func=lambda v: _SOURCE_LABELS.get(v, v),
    )
with fcol4:
    search = st.text_input(
        "搜索", placeholder="运行 ID、模型、错误信息…", key="jobs_search"
    )

# ---------------------------------------------------------------------------
# Filter row 2: date range + sort
# ---------------------------------------------------------------------------
dcol1, dcol2, dcol3, dcol4 = st.columns([2, 2, 2, 2])


def _iso_to_date(value: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


with dcol1:
    df_default = _iso_to_date(st.session_state.get("jobs_date_from", ""))
    df_val = st.date_input("起始日期", value=df_default, key="jobs_date_from_widget")
    date_from_iso = df_val.isoformat() if isinstance(df_val, date) else ""
    st.session_state["jobs_date_from"] = date_from_iso
with dcol2:
    dt_default = _iso_to_date(st.session_state.get("jobs_date_to", ""))
    dt_val = st.date_input("结束日期", value=dt_default, key="jobs_date_to_widget")
    date_to_iso = dt_val.isoformat() if isinstance(dt_val, date) else ""
    st.session_state["jobs_date_to"] = date_to_iso
with dcol3:
    sort_by = st.selectbox(
        "排序方式",
        SORT_OPTIONS,
        key="jobs_sort_by",
        format_func=lambda x: _SORT_BY_LABELS.get(x, x),
    )
with dcol4:
    sort_dir = st.selectbox(
        "排序方向",
        ["desc", "asc"],
        key="jobs_sort_dir",
        format_func=lambda x: "最新优先" if x == "desc" else "最旧优先",
    )

# Quick date presets
qp_col1, qp_col2, qp_col3, qp_col4, qp_col5 = st.columns(5)
_today = date.today()


def _apply_quick_range(start: date | None, end: date | None) -> None:
    # Run as an st.button on_click CALLBACK (not inline): a callback fires BEFORE
    # the script reruns and re-instantiates the date_input widgets, so writing
    # their widget keys (jobs_date_from_widget / jobs_date_to_widget) is legal.
    # Writing them inline (after the widgets were instantiated this run) raised
    # StreamlitAPIException on Streamlit 1.57 (audit G). No st.rerun() needed —
    # Streamlit reruns automatically after a callback.
    st.session_state["jobs_date_from_widget"] = start
    st.session_state["jobs_date_to_widget"] = end
    st.session_state["jobs_date_from"] = start.isoformat() if start else ""
    st.session_state["jobs_date_to"] = end.isoformat() if end else ""
    st.session_state["jobs_page"] = "1"


with qp_col1:
    st.button("今天", key="jobs_qp_today", use_container_width=True,
              on_click=_apply_quick_range, args=(_today, _today))
with qp_col2:
    st.button("最近 7 天", key="jobs_qp_7d", use_container_width=True,
              on_click=_apply_quick_range, args=(_today - timedelta(days=6), _today))
with qp_col3:
    st.button("最近 30 天", key="jobs_qp_30d", use_container_width=True,
              on_click=_apply_quick_range, args=(_today - timedelta(days=29), _today))
with qp_col4:
    st.button("本年至今", key="jobs_qp_year", use_container_width=True,
              on_click=_apply_quick_range, args=(date(_today.year, 1, 1), _today))
with qp_col5:
    st.button("清除日期", key="jobs_qp_clear", use_container_width=True,
              on_click=_apply_quick_range, args=(None, None))

# Reset to page 1 whenever filters change.
_filter_signature = (
    type_filter,
    status_filter,
    source_filter,
    search,
    date_from_iso,
    date_to_iso,
    sort_by,
    sort_dir,
)
if st.session_state.get("_jobs_prev_filter_sig") != _filter_signature:
    st.session_state["jobs_page"] = "1"
st.session_state["_jobs_prev_filter_sig"] = _filter_signature

# Reflect everything into the URL.
_qp_write("type", type_filter)
_qp_write("status", status_filter)
_qp_write("source", source_filter)
_qp_write("search", search)
_qp_write("date_from", date_from_iso)
_qp_write("date_to", date_to_iso)
_qp_write("sort_by", sort_by)
_qp_write("sort_dir", sort_dir)
_qp_write("page", st.session_state.get("jobs_page", "1"))

# ---------------------------------------------------------------------------
# Active filter chips
# ---------------------------------------------------------------------------
_active: list[tuple[str, str]] = []  # (label, key)
_VALUE_LABEL_MAPS: dict[str, dict[str, str]] = {
    "type": _TYPE_LABELS,
    "status": _STATUS_LABELS,
    "source": _SOURCE_LABELS,
}
for k in ("type", "status", "source"):
    v = st.session_state[f"jobs_{k}"]
    if v != "all":
        label_value = _VALUE_LABEL_MAPS[k].get(v, v)
        _active.append((f"{_FILTER_KEY_LABELS[k]}: {label_value}", k))
if search.strip():
    _active.append((f"{_FILTER_KEY_LABELS['search']}: {search.strip()}", "search"))
if date_from_iso:
    _active.append((f"{_FILTER_KEY_LABELS['date_from']}: {date_from_iso}", "date_from"))
if date_to_iso:
    _active.append((f"{_FILTER_KEY_LABELS['date_to']}: {date_to_iso}", "date_to"))

# Filter-reset handlers run as on_click CALLBACKS: they reset selectbox /
# text_input / date_input WIDGET keys (jobs_type/status/source, jobs_search,
# jobs_date_*_widget), which is only legal before the widgets are re-instantiated
# next run. Writing them inline (after instantiation) raised StreamlitAPIException
# on Streamlit 1.57 (audit G). No st.rerun() — callbacks auto-rerun.
def _clear_chip(key: str) -> None:
    if key in ("type", "status", "source"):
        st.session_state[f"jobs_{key}"] = "all"
    elif key == "search":
        st.session_state["jobs_search"] = ""
    elif key == "date_from":
        st.session_state["jobs_date_from"] = ""
        st.session_state["jobs_date_from_widget"] = None
    elif key == "date_to":
        st.session_state["jobs_date_to"] = ""
        st.session_state["jobs_date_to_widget"] = None


def _clear_all_filters() -> None:
    for k in ("type", "status", "source"):
        st.session_state[f"jobs_{k}"] = "all"
    st.session_state["jobs_search"] = ""
    st.session_state["jobs_date_from"] = ""
    st.session_state["jobs_date_to"] = ""
    st.session_state["jobs_date_from_widget"] = None
    st.session_state["jobs_date_to_widget"] = None


if _active:
    chip_cols = st.columns(len(_active) + 1)
    for i, (label, key) in enumerate(_active):
        with chip_cols[i]:
            st.button(
                f"× {label}",
                key=f"jobs_chip_clear_{key}",
                use_container_width=True,
                on_click=_clear_chip,
                args=(key,),
            )
    with chip_cols[-1]:
        st.button(
            "清除全部",
            key="jobs_chips_clear_all",
            use_container_width=True,
            on_click=_clear_all_filters,
        )

# ---------------------------------------------------------------------------
# Data load
# ---------------------------------------------------------------------------
try:
    _page = int(st.session_state.get("jobs_page", "1") or 1)
except (TypeError, ValueError):
    _page = 1
_page_size = 25

def _query_page(page_value: int) -> tuple[list[Any], int, int]:
    return list_all_jobs(
        type_filter=type_filter,
        status_filter=status_filter,
        source_filter=source_filter,
        search=search,
        date_from=date_from_iso,
        date_to=date_to_iso,
        sort_by=sort_by,
        sort_dir=sort_dir,
        page=page_value,
        page_size=_page_size,
    )


try:
    items, total, running_count = _query_page(_page)
    # Clamp + re-query when the stored page lands past the end (filter
    # narrowed mid-session, job pruned, URL points to a now-stale page).
    # Without this, the original load returned an empty offset slice for
    # the stale page and the indicator below would say "第 X / Y 页"
    # while rendering zero rows — Codex P2 on PR #197.
    _total_pages_pre = max(1, (total + _page_size - 1) // _page_size)
    if _page > _total_pages_pre:
        _page = _total_pages_pre
        st.session_state["jobs_page"] = str(_page)
        # Mirror the clamp back into the URL too — the earlier
        # ``_qp_write("page", ...)`` block ran BEFORE this clamp using
        # the stale value, so without this write the address bar would
        # still read ``?page=99`` while the rendered page was N.
        # Sharing / reloading the URL would re-trigger the clamp
        # instead of landing on the right page (Codex P3 on PR #197).
        _qp_write("page", str(_page))
        items, total, running_count = _query_page(_page)
except Exception as exc:
    render_error_state(
        "无法加载作业列表",
        "作业列表服务暂时无响应。",
        error=str(exc),
        on_retry="window.location.reload()",
    )
    st.stop()

# ---------------------------------------------------------------------------
# Summary bar — ``running_count`` is computed by ``list_all_jobs`` over
# the FULL filtered set (not just the current page) so the auto-refresh
# control below stays visible while the operator paginates away from
# the running rows.
# ---------------------------------------------------------------------------
if total > 0:
    by_type: dict[str, int] = {}
    for item in items:
        by_type[item.type] = by_type.get(item.type, 0) + 1
    summary_parts = [
        f"{count} 个{_TYPE_LABELS.get(t, t)}" for t, count in sorted(by_type.items())
    ]
    st.caption(
        f"显示 {len(items)} / {total} 条 · "
        + " · ".join(summary_parts)
        + (f" · {running_count} 个运行中" if running_count else "")
    )

# ---------------------------------------------------------------------------
# Empty states
# ---------------------------------------------------------------------------
if total == 0 and not _active:
    render_empty_state(
        "\U0001f4cb",
        "暂无作业",
        "通过「配置运行」启动你的第一个流水线或滚动验证作业。",
        action_label="配置运行",
        action_on_click="window.location.href='/config_run'",
    )
    st.stop()

if total == 0:
    render_empty_state(
        "\U0001f50d",
        "没有符合筛选条件的作业",
        "请放宽筛选条件或清除搜索关键字。",
    )
    st.stop()

# ---------------------------------------------------------------------------
# Table
# ---------------------------------------------------------------------------
_STATUS_ICONS = {
    "queued": "⏸",
    "running": "🔄",
    "completed": "✅",
    "failed": "❌",
    "cancelled": "⊘",
}

_TYPE_ICONS = {
    "pipeline": "📄",
    "walk_forward": "🔁",
    "provider": "📦",
}


rows: list[dict[str, Any]] = []
for item in items:
    rows.append(
        {
            "状态": f"{_STATUS_ICONS.get(item.status, '')} {_STATUS_LABELS.get(item.status, item.status)}",
            # Show the full run id — failing jobs need the timestamp + hash
            # tail to copy into a bug report; truncation forced operators
            # to click into the detail page first. The medium column width
            # below keeps the layout balanced.
            "运行 ID": item.run_id,
            "类型": f"{_TYPE_ICONS.get(item.type, '')} {_TYPE_LABELS.get(item.type, item.type)}",
            "创建时间": format_relative_time(item.created_at) if item.created_at else "—",
            "耗时": (
                format_duration(item.duration_seconds) if item.duration_seconds else ""
            ),
            "关键指标": (
                f"{item.key_metric_label}: {item.key_metric_value}"
                if item.key_metric_label
                else "—"
            ),
            "配置": " · ".join(item.config_summary.values()) if item.config_summary else "—",
            "来源": item.source.upper(),
        }
    )

df = pd.DataFrame(rows)

# Status is rendered as a plain text column so dataframe sort works on it;
# the canonical visual badge appears in the action bar below when a row is
# selected (and via render_badge there).
event = st.dataframe(
    df,
    column_config={
        "状态": st.column_config.TextColumn("状态", width="small"),
        # Full run id is ~33 chars (e.g. pipeline_YYYYMMDD_HHMMSS_<8hex>);
        # "medium" column gives it room without crowding the rest.
        "运行 ID": st.column_config.TextColumn("运行 ID", width="medium"),
        "类型": st.column_config.TextColumn("类型", width="small"),
        "创建时间": st.column_config.TextColumn("创建时间", width="small"),
        "耗时": st.column_config.TextColumn("耗时", width="small"),
        # Failure reason can be a full Python traceback summary line — give
        # the cell enough room and let Streamlit wrap inside it.
        "关键指标": st.column_config.TextColumn("关键指标", width="large"),
        "配置": st.column_config.TextColumn("配置"),
        "来源": st.column_config.TextColumn("来源", width="small"),
    },
    hide_index=True,
    height=480,
    on_select="rerun",
    selection_mode="single-row",
    key="jobs_table",
)

# ---------------------------------------------------------------------------
# Action bar (visible when a row is selected)
# ---------------------------------------------------------------------------
_selected_row: int | None = None
if event is not None:
    selection = getattr(event, "selection", None)
    sel_rows: list[int] | None = None
    if isinstance(selection, dict):
        sel_rows = selection.get("rows")
    elif selection is not None:
        sel_rows = getattr(selection, "rows", None)
    if sel_rows:
        _selected_row = int(sel_rows[0])

if _selected_row is not None and 0 <= _selected_row < len(items):
    selected = items[_selected_row]
    st.markdown("---")
    sel_col1, sel_col2 = st.columns([6, 6])
    with sel_col1:
        render_badge("info", f"已选: {selected.run_id}")
        st.caption(
            f"{_TYPE_LABELS.get(selected.type, selected.type)} · "
            f"{_STATUS_LABELS.get(selected.status, selected.status)} · 创建于 "
            + (
                format_date_absolute(selected.created_at, style="datetime")
                if selected.created_at
                else "—"
            )
        )
    with sel_col2:
        act_open, act_copy = st.columns(2)
        with act_open:
            if st.button(
                "▶ 查看详情",
                key=f"jobs_open_{selected.run_id}",
                type="primary",
                use_container_width=True,
            ):
                # results.py reads ``st.query_params["run_id"]`` via
                # ``_query_run_id`` and walk_forward.py picks up the same
                # key (added in this PR). Use the canonical key so the
                # detail page lands on the row the operator clicked.
                st.query_params["run_id"] = selected.run_id
                if selected.type == "walk_forward":
                    st.session_state["wf_selected_run"] = selected.run_id
                    st.switch_page("pages/walk_forward.py")
                else:
                    st.session_state["results_selected_run"] = selected.run_id
                    st.switch_page("pages/results.py")
        with act_copy:
            copy_id = f"jobs_copy_field_{quote_plus(selected.run_id)}"
            # ``selected.run_id`` can come from the CLI catalog
            # (``output/runs/_index.jsonl``) where it bypasses the URL
            # ``_param_guard`` whitelist. A crafted entry containing ``">``
            # would break out of the value attribute and execute arbitrary
            # JS in ``window.parent`` (the copy button uses
            # ``unsafe_allow_javascript=True``). Escape on the way into the
            # value attribute so the rendered HTML stays well-formed even
            # if catalog data carries unexpected characters.
            escaped_run_id = html.escape(selected.run_id, quote=True)
            st.html(
                (
                    '<button class="qv2-button qv2-button--secondary qv2-button--full" '
                    'type="button" onclick="(function() {'
                    f'const el = window.parent.document.getElementById({copy_id!r});'
                    "if (el) { el.select(); document.execCommand && document.execCommand('copy'); }"
                    '})()">📋 复制运行 ID</button>'
                    f'<input id={copy_id!r} class="qv2-sr-only" readonly '
                    f'value="{escaped_run_id}" />'
                ),
                width="content",
                unsafe_allow_javascript=True,
            )

# ---------------------------------------------------------------------------
# Pagination — real prev/next nav over offset-sliced pages (UI review
# P1-10). The previous "load more" button returned the first N×size
# items cumulatively, so dataframe formatting cost grew linearly with
# click count and the operator had no "what page am I on" signal.
# ---------------------------------------------------------------------------
_total_pages = max(1, (total + _page_size - 1) // _page_size)
# ``_page`` is already clamped above (clamp-and-re-query path) so we
# can render the indicator + control buttons against it directly —
# the indicator and the dataframe always agree on which page we're on.

if _total_pages > 1 or total > _page_size:
    pg_prev, pg_indicator, pg_next = st.columns([1, 2, 1])
    with pg_prev:
        if st.button(
            "← 上一页",
            key="jobs_pg_prev",
            disabled=_page <= 1,
            use_container_width=True,
        ):
            st.session_state["jobs_page"] = str(_page - 1)
            st.rerun()
    with pg_indicator:
        # Centered "第 N / M 页 · 共 X 条" indicator. Renders inside a
        # ``st.caption`` so it lines up with the buttons without
        # competing for visual weight.
        st.caption(
            f"<div style='text-align:center;padding-top:6px;'>"
            f"第 {_page} / {_total_pages} 页 · 共 {total} 条"
            "</div>",
            unsafe_allow_html=True,
        )
    with pg_next:
        if st.button(
            "下一页 →",
            key="jobs_pg_next",
            disabled=_page >= _total_pages,
            use_container_width=True,
        ):
            st.session_state["jobs_page"] = str(_page + 1)
            st.rerun()

# ---------------------------------------------------------------------------
# Auto-refresh (only when there is at least one running job)
# ---------------------------------------------------------------------------
if running_count > 0:
    autorefresh = st.checkbox(
        f"{running_count} 个作业运行中 · 每 5 秒自动刷新",
        value=st.session_state.get("jobs_autorefresh", "0") == "1",
        key="jobs_autorefresh_widget",
    )
    st.session_state["jobs_autorefresh"] = "1" if autorefresh else "0"
    _qp_write("autorefresh", st.session_state["jobs_autorefresh"])
    if autorefresh:
        time.sleep(5)
        st.rerun()

# ---------------------------------------------------------------------------
# Bulk cleanup — delete old completed UI jobs in one click instead of
# clicking through them one at a time (UI review P2-11). Scoped to
# UI-launched terminal jobs only; running jobs and CLI-catalogued runs
# are never touched. Two-step (preview count → explicit confirm) so a
# stray click can't wipe history.
# ---------------------------------------------------------------------------
def _run_bulk_cleanup(eligible: list[str]) -> None:
    # on_click CALLBACK: do the deletes, then reset the confirm CHECKBOX widget
    # key — legal here (pre-instantiation), whereas the old inline reset crashed
    # AFTER the deletes ran (deleted-but-no-feedback, audit G). The success/error
    # summary is stashed and rendered on the next run (callbacks run before render).
    deleted, failed = 0, []
    for run_id in eligible:
        try:
            JobManager.delete(run_id)
            deleted += 1
        except JobManagerError as exc:
            failed.append(f"{run_id}: {exc}")
    st.session_state["jobs_cleanup_result"] = {"deleted": deleted, "failed": failed}
    st.session_state["jobs_cleanup_confirm"] = False


# Keep the panel open on the run AFTER a cleanup so its success/error summary
# (stashed by the callback, rendered below) is actually visible.
with st.expander(
    "🧹 清理旧作业", expanded="jobs_cleanup_result" in st.session_state
):
    # Eligibility is global (not limited to the current page / filters),
    # so re-query all UI jobs. The large page_size pulls everything;
    # the third tuple element (running count) is unused here.
    _all_ui_jobs, _, _ = list_all_jobs(source_filter="ui", page=1, page_size=100_000)
    cleanup_days = st.number_input(
        "删除多少天前的已完成作业",
        min_value=1,
        max_value=3650,
        value=30,
        key="jobs_cleanup_days",
        help="只删除 UI 启动的、已结束（成功 / 失败 / 已停止）且早于该天数的作业；"
        "运行中的作业和 CLI 目录作业不会被删除。",
    )
    _eligible = jobs_eligible_for_cleanup(
        _all_ui_jobs,
        older_than_days=int(cleanup_days),
        today=date.today(),
    )
    # Render the result of a cleanup that ran in the callback on the PREVIOUS run
    # (shown here regardless of whether anything remains eligible afterwards).
    _cleanup_result = st.session_state.pop("jobs_cleanup_result", None)
    if _cleanup_result is not None:
        if _cleanup_result.get("deleted"):
            st.success(f"已删除 {_cleanup_result['deleted']} 个旧作业。")
        if _cleanup_result.get("failed"):
            st.error("以下作业删除失败：\n- " + "\n- ".join(_cleanup_result["failed"]))
    if not _eligible:
        st.caption(f"没有早于 {int(cleanup_days)} 天的已完成 UI 作业。")
    else:
        st.warning(
            f"将删除 **{len(_eligible)}** 个早于 {int(cleanup_days)} 天的已完成 UI "
            "作业（含其模型 / 产物目录）。此操作不可撤销。"
        )
        confirm = st.checkbox(
            "我已确认要删除这些作业", key="jobs_cleanup_confirm",
        )
        st.button(
            f"删除 {len(_eligible)} 个旧作业",
            key="jobs_cleanup_delete",
            type="primary",
            disabled=not confirm,
            on_click=_run_bulk_cleanup,
            args=(_eligible,),
        )
