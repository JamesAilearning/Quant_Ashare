"""Development demo page for operator UI design-system tokens."""

from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

from web.operator_ui.components import (
    render_badge,
    render_empty_state,
    render_error_state,
    render_skeleton,
    render_stat_card,
)
from web.operator_ui.formatting import (
    format_date_absolute,
    format_duration,
    format_money,
    format_number,
    format_percent,
    format_relative_time,
)

from web.operator_ui.page_header import render_breadcrumbs, render_page_header

render_breadcrumbs([("System", None)])
render_page_header("Design System")
st.caption("Operator UI visual tokens and display-format examples.")

st.header("Color Tokens")

_SWATCHES = (
    ("--bg-page", "var(--bg-page)"),
    ("--bg-card", "var(--bg-card)"),
    ("--text-primary", "var(--text-primary)"),
    ("--text-secondary", "var(--text-secondary)"),
    ("--brand-primary", "var(--brand-primary)"),
    ("--positive", "var(--positive)"),
    ("--negative", "var(--negative)"),
    ("--warning", "var(--warning)"),
    ("--info", "var(--info)"),
    ("--neutral", "var(--neutral)"),
    ("--chart-strategy", "var(--chart-strategy)"),
    ("--chart-benchmark", "var(--chart-benchmark)"),
)

swatch_html = ['<div class="qv2-token-grid">']
for label, color in _SWATCHES:
    swatch_html.append(
        '<div class="qv2-swatch">'
        f'<div class="qv2-swatch-color" style="background: {color};"></div>'
        f'<div class="qv2-swatch-label">{label}</div>'
        "</div>"
    )
swatch_html.append("</div>")
st.markdown("\n".join(swatch_html), unsafe_allow_html=True)

st.header("Typography")
st.markdown(
    """
<div class="qv2-card">
  <div class="qv2-text-page-title">Page title token</div>
  <div class="qv2-text-card-label">CARD LABEL TOKEN</div>
  <div class="qv2-text-metric-primary">1,234.56</div>
  <div class="qv2-muted">Secondary copy uses muted text tokens.</div>
</div>
""",
    unsafe_allow_html=True,
)

st.header("Formatting Helpers")

now = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)
examples = [
    {"Helper": "format_percent", "Input": "0.1834", "Output": format_percent(0.1834)},
    {"Helper": "format_percent", "Input": "-0.0245", "Output": format_percent(-0.0245)},
    {"Helper": "format_number", "Input": "1234567", "Output": format_number(1_234_567)},
    {
        "Helper": "format_number(abbreviate)",
        "Input": "1234567",
        "Output": format_number(1_234_567, abbreviate=True),
    },
    {"Helper": "format_money", "Input": "1234567.5", "Output": format_money(1_234_567.5)},
    {"Helper": "format_duration", "Input": "3725", "Output": format_duration(3725)},
    {
        "Helper": "format_relative_time",
        "Input": "2026-05-21T10:30:00+00:00",
        "Output": format_relative_time("2026-05-21T10:30:00+00:00", now=now),
    },
    {
        "Helper": "format_date_absolute",
        "Input": "2026-05-21T10:30:00+00:00",
        "Output": format_date_absolute("2026-05-21T10:30:00+00:00", style="datetime"),
    },
    {"Helper": "missing value", "Input": "None", "Output": format_number(None)},
]

st.dataframe(examples, width="stretch", hide_index=True)

# ---------------------------------------------------------------------------
# Component showcase
# ---------------------------------------------------------------------------
st.header("Badges")

badge_cols = st.columns(5)
for idx, (variant, label, icon, pulse) in enumerate(
    [
        ("neutral", "Queued", "⏸", False),
        ("info", "Running", "", True),
        ("success", "Completed", "✅", False),
        ("warning", "Cancelled", "⊘", False),
        ("danger", "Failed", "❌", False),
    ]
):
    with badge_cols[idx]:
        render_badge(variant, label, icon=icon, pulse=pulse)

st.header("StatCard (KPI)")

sc_cols = st.columns(3)
with sc_cols[0]:
    render_stat_card("ANNUAL RETURN", "+18.34%", trend="up", value_color="positive")
with sc_cols[1]:
    render_stat_card(
        "MAX DRAWDOWN",
        "-12.45%",
        trend="down",
        value_color="negative",
        secondary=[("Volatility", "16.0%"), ("Duration", "28 days")],
    )
with sc_cols[2]:
    render_stat_card(
        "SHARPE RATIO",
        "1.83",
        tooltip="Risk-adjusted return. Higher is better; > 1 is good.",
    )

st.header("Skeleton")
render_skeleton("rect", height="48px")
render_skeleton("text", width="60%")
render_skeleton("text", width="80%")
render_skeleton("text", width="40%")

st.header("EmptyState")
render_empty_state(
    "🔁",
    "No walk-forward runs yet",
    "Validate your strategy across rolling time windows.",
    action_label="Start a Run",
)

st.header("ErrorState")
render_error_state(
    "Run not found",
    "We couldn't find a run with that ID. It may have been deleted.",
    error="KeyError: run_id='pipeline_xxxx_yyyy'",
    on_retry="window.location.reload()",
    variant="inline",
)

# ---------------------------------------------------------------------------
st.info(
    "This page is a QA/demo surface only. It does not read runtime artifacts "
    "or compute official metrics."
)
