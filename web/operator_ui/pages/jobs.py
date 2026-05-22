"""Jobs list page — unified view of UI-launched and CLI-catalogued runs."""

from __future__ import annotations

import streamlit as st

from web.operator_ui.components import render_badge, render_empty_state, render_error_state
from web.operator_ui.formatting import format_date_absolute, format_duration, format_relative_time
from web.operator_ui.job_io import list_all_jobs
from web.operator_ui.page_header import render_breadcrumbs, render_page_header

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
render_breadcrumbs([("Run", None)])
render_page_header("Jobs", "All pipeline, walk-forward, and data provider runs.")

# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
fcol1, fcol2, fcol3, fcol4 = st.columns(4)
with fcol1:
    type_filter = st.selectbox(
        "Type", ["all", "pipeline", "walk_forward", "provider"],
        key="jobs_type_filter",
    )
with fcol2:
    status_filter = st.selectbox(
        "Status", ["all", "queued", "running", "completed", "failed"],
        key="jobs_status_filter",
    )
with fcol3:
    source_filter = st.selectbox(
        "Source", ["all", "ui", "cli"],
        key="jobs_source_filter",
    )
with fcol4:
    search = st.text_input("Search", placeholder="Run ID, model, error\u2026", key="jobs_search")

# Reset page on filter change
if "jobs_page" not in st.session_state:
    st.session_state["jobs_page"] = 1

_changed = any([
    st.session_state.get("_prev_type_filter", "") != type_filter,
    st.session_state.get("_prev_status_filter", "") != status_filter,
    st.session_state.get("_prev_source_filter", "") != source_filter,
    st.session_state.get("_prev_search", "") != search,
])
if _changed:
    st.session_state["jobs_page"] = 1
st.session_state["_prev_type_filter"] = type_filter
st.session_state["_prev_status_filter"] = status_filter
st.session_state["_prev_source_filter"] = source_filter
st.session_state["_prev_search"] = search

# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
_page = st.session_state["jobs_page"]
_page_size = 25

try:
    items, total = list_all_jobs(
        type_filter=type_filter,
        status_filter=status_filter,
        source_filter=source_filter,
        search=search,
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
if total > 0:
    by_type: dict[str, int] = {}
    for item in items:
        by_type[item.type] = by_type.get(item.type, 0) + 1
    summary_parts = [f"{count} {t}" for t, count in sorted(by_type.items())]
    st.caption(f"Showing {len(items)} of {total} \u00b7 " + " \u00b7 ".join(summary_parts))

# ---------------------------------------------------------------------------
# Empty states
# ---------------------------------------------------------------------------
if total == 0 and not any([type_filter != "all", status_filter != "all", source_filter != "all", search.strip()]):
    render_empty_state(
        "\U0001f4cb",
        "No jobs yet",
        "Get started by running your first pipeline or fetching data.",
        action_label="Config & Run",
        action_on_click="",
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
import pandas as pd

_STATUS_ICONS = {
    "queued": "\u23f8",
    "running": "\U0001f504",
    "completed": "\u2705",
    "failed": "\u274c",
    "cancelled": "\u2298",
}

_VARIANT_MAP = {
    "queued": "neutral",
    "running": "info",
    "completed": "success",
    "failed": "danger",
    "cancelled": "warning",
}

_TYPE_ICONS = {
    "pipeline": "\U0001f4c4",
    "walk_forward": "\U0001f501",
    "provider": "\U0001f4e6",
}


def _status_badge(status: str) -> str:
    icon = _STATUS_ICONS.get(status, "")
    return f'<span class="qv2-badge qv2-badge--{_VARIANT_MAP.get(status, "neutral")}">{icon} {status}</span>'


def _type_cell(typ: str) -> str:
    icon = _TYPE_ICONS.get(typ, "")
    return f"{icon} {typ.replace('_', ' ').title()}"


rows: list[dict] = []
for item in items:
    created_dt = None
    if item.created_at:
        try:
            from datetime import datetime
            created_dt = datetime.fromisoformat(item.created_at)
        except Exception:
            pass

    rows.append({
        " ": _status_badge(item.status),
        "Run ID": item.run_id[:12],
        "Type": _type_cell(item.type),
        "Created": format_relative_time(item.created_at) if item.created_at else "\u2014",
        "Duration": format_duration(item.duration_seconds) if item.duration_seconds else "",
        "Key Metric": f"{item.key_metric_label}: {item.key_metric_value}" if item.key_metric_label else "\u2014",
        "Config": " \u00b7 ".join(item.config_summary.values()) if item.config_summary else "\u2014",
        "Source": item.source.upper(),
        "_run_id": item.run_id,
        "_type": item.type,
        "_created_iso": format_date_absolute(item.created_at, style="datetime") if item.created_at else "",
    })

df = pd.DataFrame(rows)
display_cols = [" ", "Run ID", "Type", "Created", "Duration", "Key Metric", "Config", "Source"]

st.dataframe(
    df[display_cols],
    column_config={
        " ": st.column_config.Column(" ", width="small"),
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
)

# Tooltip column (hidden) — show full ID + absolute time on hover via a second col?
# Streamlit doesn't support tooltips natively on dataframe cells, so we display
# truncated info. Full detail is available on the result page.

# ---------------------------------------------------------------------------
# Load more
# ---------------------------------------------------------------------------
_showing = _page * _page_size
if _showing < total:
    if st.button(f"Load more ({total - _showing} remaining)", key="jobs_load_more"):
        st.session_state["jobs_page"] = _page + 1
        st.rerun()
