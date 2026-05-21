"""Development demo page for operator UI design-system tokens."""

from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

from web.operator_ui.formatting import (
    format_date_absolute,
    format_duration,
    format_money,
    format_number,
    format_percent,
    format_relative_time,
)

st.title("Design System")
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

st.info(
    "This page is a QA/demo surface only. It does not read runtime artifacts "
    "or compute official metrics."
)
