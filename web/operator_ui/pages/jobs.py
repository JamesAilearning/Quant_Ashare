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

import time
from datetime import date, timedelta
from typing import Any
from urllib.parse import quote_plus

import pandas as pd
import streamlit as st

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
from web.operator_ui.job_io import SORT_OPTIONS, list_all_jobs
from web.operator_ui.page_header import render_breadcrumbs, render_page_header

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
    return st.query_params.get(key, _DEFAULTS[key])


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
# Header
# ---------------------------------------------------------------------------
render_breadcrumbs([("Run", None)])
render_page_header("Jobs", "All pipeline, walk-forward, and data provider runs.")

# ---------------------------------------------------------------------------
# Filter row 1: type / status / source / search
# ---------------------------------------------------------------------------
fcol1, fcol2, fcol3, fcol4 = st.columns(4)
with fcol1:
    type_filter = st.selectbox(
        "Type",
        ["all", "pipeline", "walk_forward", "provider"],
        key="jobs_type",
    )
with fcol2:
    status_filter = st.selectbox(
        "Status",
        ["all", "queued", "running", "completed", "failed", "cancelled"],
        key="jobs_status",
    )
with fcol3:
    source_filter = st.selectbox(
        "Source",
        ["all", "ui", "cli"],
        key="jobs_source",
    )
with fcol4:
    search = st.text_input(
        "Search", placeholder="Run ID, model, error…", key="jobs_search"
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
    df_val = st.date_input("From", value=df_default, key="jobs_date_from_widget")
    date_from_iso = df_val.isoformat() if isinstance(df_val, date) else ""
    st.session_state["jobs_date_from"] = date_from_iso
with dcol2:
    dt_default = _iso_to_date(st.session_state.get("jobs_date_to", ""))
    dt_val = st.date_input("To", value=dt_default, key="jobs_date_to_widget")
    date_to_iso = dt_val.isoformat() if isinstance(dt_val, date) else ""
    st.session_state["jobs_date_to"] = date_to_iso
with dcol3:
    sort_by = st.selectbox(
        "Sort by",
        SORT_OPTIONS,
        key="jobs_sort_by",
        format_func=lambda x: x.replace("_", " ").title(),
    )
with dcol4:
    sort_dir = st.selectbox(
        "Direction",
        ["desc", "asc"],
        key="jobs_sort_dir",
        format_func=lambda x: "Newest first" if x == "desc" else "Oldest first",
    )

# Quick date presets
qp_col1, qp_col2, qp_col3, qp_col4, qp_col5 = st.columns(5)
_today = date.today()


def _apply_quick_range(start: date | None, end: date | None) -> None:
    st.session_state["jobs_date_from_widget"] = start
    st.session_state["jobs_date_to_widget"] = end
    st.session_state["jobs_date_from"] = start.isoformat() if start else ""
    st.session_state["jobs_date_to"] = end.isoformat() if end else ""
    st.session_state["jobs_page"] = "1"
    st.rerun()


with qp_col1:
    if st.button("Today", key="jobs_qp_today", use_container_width=True):
        _apply_quick_range(_today, _today)
with qp_col2:
    if st.button("Last 7d", key="jobs_qp_7d", use_container_width=True):
        _apply_quick_range(_today - timedelta(days=6), _today)
with qp_col3:
    if st.button("Last 30d", key="jobs_qp_30d", use_container_width=True):
        _apply_quick_range(_today - timedelta(days=29), _today)
with qp_col4:
    if st.button("This year", key="jobs_qp_year", use_container_width=True):
        _apply_quick_range(date(_today.year, 1, 1), _today)
with qp_col5:
    if st.button("Clear dates", key="jobs_qp_clear", use_container_width=True):
        _apply_quick_range(None, None)

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
for k in ("type", "status", "source"):
    v = st.session_state[f"jobs_{k}"]
    if v != "all":
        _active.append((f"{k}: {v}", k))
if search.strip():
    _active.append((f"search: {search.strip()}", "search"))
if date_from_iso:
    _active.append((f"from: {date_from_iso}", "date_from"))
if date_to_iso:
    _active.append((f"to: {date_to_iso}", "date_to"))

if _active:
    chip_cols = st.columns(len(_active) + 1)
    for i, (label, key) in enumerate(_active):
        with chip_cols[i]:
            if st.button(
                f"× {label}",
                key=f"jobs_chip_clear_{key}",
                use_container_width=True,
            ):
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
                st.rerun()
    with chip_cols[-1]:
        if st.button("Clear all", key="jobs_chips_clear_all", use_container_width=True):
            for k in ("type", "status", "source"):
                st.session_state[f"jobs_{k}"] = "all"
            st.session_state["jobs_search"] = ""
            st.session_state["jobs_date_from"] = ""
            st.session_state["jobs_date_to"] = ""
            st.session_state["jobs_date_from_widget"] = None
            st.session_state["jobs_date_to_widget"] = None
            st.rerun()

# ---------------------------------------------------------------------------
# Data load
# ---------------------------------------------------------------------------
try:
    _page = int(st.session_state.get("jobs_page", "1") or 1)
except (TypeError, ValueError):
    _page = 1
_page_size = 25

try:
    items, total = list_all_jobs(
        type_filter=type_filter,
        status_filter=status_filter,
        source_filter=source_filter,
        search=search,
        date_from=date_from_iso,
        date_to=date_to_iso,
        sort_by=sort_by,
        sort_dir=sort_dir,
        page=_page,
        page_size=_page_size,
    )
except Exception as exc:
    render_error_state(
        "Couldn't load jobs",
        "The job list service didn't respond.",
        error=str(exc),
        on_retry="window.location.reload()",
    )
    st.stop()

# ---------------------------------------------------------------------------
# Summary bar
# ---------------------------------------------------------------------------
running_count = sum(1 for i in items if i.status == "running")
if total > 0:
    by_type: dict[str, int] = {}
    for item in items:
        by_type[item.type] = by_type.get(item.type, 0) + 1
    summary_parts = [f"{count} {t}" for t, count in sorted(by_type.items())]
    st.caption(
        f"Showing {len(items)} of {total} · "
        + " · ".join(summary_parts)
        + (f" · {running_count} running" if running_count else "")
    )

# ---------------------------------------------------------------------------
# Empty states
# ---------------------------------------------------------------------------
if total == 0 and not _active:
    render_empty_state(
        "\U0001f4cb",
        "No jobs yet",
        "Get started by running your first pipeline or fetching data.",
        action_label="Config & Run",
        action_on_click="window.location.href='/config_run'",
    )
    st.stop()

if total == 0:
    render_empty_state(
        "\U0001f50d",
        "No jobs match your filters",
        "Try widening your filters or clearing the search.",
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
            "Status": f"{_STATUS_ICONS.get(item.status, '')} {item.status}",
            "Run ID": item.run_id[:14] + ("…" if len(item.run_id) > 14 else ""),
            "Type": f"{_TYPE_ICONS.get(item.type, '')} {item.type.replace('_', ' ').title()}",
            "Created": format_relative_time(item.created_at) if item.created_at else "—",
            "Duration": (
                format_duration(item.duration_seconds) if item.duration_seconds else ""
            ),
            "Key Metric": (
                f"{item.key_metric_label}: {item.key_metric_value}"
                if item.key_metric_label
                else "—"
            ),
            "Config": " · ".join(item.config_summary.values()) if item.config_summary else "—",
            "Source": item.source.upper(),
        }
    )

df = pd.DataFrame(rows)

# Status is rendered as a plain text column so dataframe sort works on it;
# the canonical visual badge appears in the action bar below when a row is
# selected (and via render_badge there).
event = st.dataframe(
    df,
    column_config={
        "Status": st.column_config.TextColumn("Status", width="small"),
        "Run ID": st.column_config.TextColumn("Run ID", width="small"),
        "Type": st.column_config.TextColumn("Type", width="small"),
        "Created": st.column_config.TextColumn("Created", width="small"),
        "Duration": st.column_config.TextColumn("Duration", width="small"),
        "Key Metric": st.column_config.TextColumn("Key Metric"),
        "Config": st.column_config.TextColumn("Config"),
        "Source": st.column_config.TextColumn("Source", width="small"),
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
        sel_rows = selection.get("rows")  # type: ignore[assignment]
    elif selection is not None:
        sel_rows = getattr(selection, "rows", None)
    if sel_rows:
        _selected_row = int(sel_rows[0])

if _selected_row is not None and 0 <= _selected_row < len(items):
    selected = items[_selected_row]
    st.markdown("---")
    sel_col1, sel_col2 = st.columns([6, 6])
    with sel_col1:
        render_badge("info", f"Selected: {selected.run_id}")
        st.caption(
            f"{selected.type} · {selected.status} · created "
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
                "▶ Open detail",
                key=f"jobs_open_{selected.run_id}",
                type="primary",
                use_container_width=True,
            ):
                if selected.type == "walk_forward":
                    st.session_state["wf_selected_run"] = selected.run_id
                    st.query_params["run"] = selected.run_id
                    st.switch_page("pages/walk_forward.py")
                else:
                    st.session_state["results_selected_run"] = selected.run_id
                    st.query_params["run"] = selected.run_id
                    st.switch_page("pages/results.py")
        with act_copy:
            copy_id = f"jobs_copy_field_{quote_plus(selected.run_id)}"
            st.html(
                (
                    '<button class="qv2-button qv2-button--secondary qv2-button--full" '
                    'type="button" onclick="(function() {'
                    f'const el = window.parent.document.getElementById({copy_id!r});'
                    "if (el) { el.select(); document.execCommand && document.execCommand('copy'); }"
                    '})()">📋 Copy Run ID</button>'
                    f'<input id={copy_id!r} class="qv2-sr-only" readonly '
                    f'value="{selected.run_id}" />'
                ),
                width="content",
                unsafe_allow_javascript=True,
            )

# ---------------------------------------------------------------------------
# Load more
# ---------------------------------------------------------------------------
_showing = _page * _page_size
if _showing < total:
    if st.button(f"Load more ({total - _showing} remaining)", key="jobs_load_more"):
        st.session_state["jobs_page"] = str(_page + 1)
        st.rerun()

# ---------------------------------------------------------------------------
# Auto-refresh (only when there is at least one running job)
# ---------------------------------------------------------------------------
if running_count > 0:
    autorefresh = st.checkbox(
        f"Auto-refresh every 5s while {running_count} job"
        f"{'s' if running_count != 1 else ''} running",
        value=st.session_state.get("jobs_autorefresh", "0") == "1",
        key="jobs_autorefresh_widget",
    )
    st.session_state["jobs_autorefresh"] = "1" if autorefresh else "0"
    _qp_write("autorefresh", st.session_state["jobs_autorefresh"])
    if autorefresh:
        time.sleep(5)
        st.rerun()
