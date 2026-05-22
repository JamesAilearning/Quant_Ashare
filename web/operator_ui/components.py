"""Reusable UI component helpers for the operator UI.

These helpers produce HTML fragments via ``st.html()`` and rely on CSS
classes defined in ``theme.css``.  They are intentionally free of
business logic — only display concerns.
"""

from __future__ import annotations

import html
from typing import Literal

import streamlit as st

# ---------------------------------------------------------------------------
# Badge
# ---------------------------------------------------------------------------

BadgeVariant = Literal["neutral", "info", "success", "warning", "danger"]


def render_badge(
    variant: BadgeVariant,
    label: str,
    *,
    icon: str = "",
    pulse: bool = False,
) -> None:
    """Render a compact status badge.

    Args:
        variant: Semantic colour variant.
        label: Short human-readable text (e.g. ``"Running"``).
        icon: Optional emoji prefix (e.g. ``"✅"``).
        pulse: When ``True`` the badge dot animates (useful for *running*).
    """
    pulse_attr = ' data-qv2-pulse="true"' if pulse else ""
    icon_span = f'<span class="qv2-badge-dot"></span>' if pulse else ""
    html = (
        f'<span class="qv2-badge qv2-badge--{variant}"{pulse_attr}>'
        f"{icon} "
        f"{icon_span}"
        f"{label}"
        "</span>"
    )
    st.html(html, width="content", unsafe_allow_javascript=False)


# ---------------------------------------------------------------------------
# StatCard (KPI)
# ---------------------------------------------------------------------------

StatCardTrend = Literal["up", "down", "flat"]


def render_stat_card(
    label: str,
    value: str,
    *,
    trend: StatCardTrend | None = None,
    secondary: list[tuple[str, str]] | None = None,
    value_color: Literal["default", "positive", "negative", "warning"] = "default",
    tooltip: str = "",
) -> None:
    """Render a KPI-style metric card.

    Args:
        label: Uppercase label displayed above the value (e.g. ``"ANNUAL RETURN"``).
        value: Pre-formatted primary metric (e.g. ``"+18.34%"``).
        trend: Optional directional arrow: ``"up"`` → ↗, ``"down"`` → ↘.
        secondary: Optional list of ``(label, value)`` sub-rows.
        value_color: Semantic colour override for the primary value.
        tooltip: Optional help text shown on hover (rendered as a tooltip anchor).
    """
    trend_map = {"up": " \u2197", "down": " \u2198", "flat": ""}
    trend_html = trend_map.get(trend or "", "")
    tooltip_html = ""
    if tooltip:
        tooltip_html = (
            f'<span class="qv2-stat-card-tooltip" title="{tooltip}">ⓘ</span>'
        )
    color_class = "" if value_color == "default" else f" qv2-{value_color}"

    parts = ['<div class="qv2-stat-card">']
    parts.append(f'<div class="qv2-text-card-label">{label} {tooltip_html}</div>')
    parts.append(
        f'<div class="qv2-text-metric-primary{color_class}">{value}{trend_html}</div>'
    )
    if secondary:
        for sec_label, sec_value in secondary:
            parts.append(
                '<div class="qv2-stat-card-secondary">'
                f'<span class="qv2-muted">{sec_label}</span>'
                f'<span>{sec_value}</span>'
                "</div>"
            )
    parts.append("</div>")
    st.html("\n".join(parts), width="content", unsafe_allow_javascript=False)


# ---------------------------------------------------------------------------
# Skeleton
# ---------------------------------------------------------------------------

SkeletonVariant = Literal["text", "rect", "circle"]


def render_skeleton(
    variant: SkeletonVariant = "rect",
    *,
    width: str = "100%",
    height: str = "1em",
    count: int = 1,
) -> None:
    """Render shimmer loading placeholders.

    Args:
        variant: Shape — ``"text"`` for a line, ``"rect"`` for a block,
            ``"circle"`` for a circular avatar.
        width: CSS width (e.g. ``"60%"`` or ``"200px"``).
        height: CSS height.
        count: Number of repeated skeleton elements.
    """
    cls = f"qv2-skeleton qv2-skeleton-{variant}"
    items = "".join(
        f'<div class="{cls}" style="width:{width};height:{height};"></div>'
        for _ in range(count)
    )
    st.html(items, width="content", unsafe_allow_javascript=False)


# ---------------------------------------------------------------------------
# EmptyState
# ---------------------------------------------------------------------------


def render_empty_state(
    icon: str,
    title: str,
    description: str = "",
    *,
    action_label: str = "",
    action_on_click: str = "",
) -> None:
    """Render a centered empty-state placeholder.

    The ``action_on_click`` string is raw JavaScript executed on button
    click (e.g. to navigate via ``st.query_params``).
    """
    parts = ['<div class="qv2-empty-state">']
    if icon:
        parts.append(f'<span class="qv2-empty-state-icon">{icon}</span>')
    parts.append(f'<div class="qv2-empty-state-title">{title}</div>')
    if description:
        parts.append(f'<div class="qv2-empty-state-desc">{description}</div>')
    if action_label:
        onclick = f' onclick="{action_on_click}"' if action_on_click else ""
        parts.append(
            f'<button class="qv2-empty-state-action"{onclick}>{action_label}</button>'
        )
    parts.append("</div>")
    st.html("\n".join(parts), width="content", unsafe_allow_javascript=bool(action_on_click))


# ---------------------------------------------------------------------------
# ErrorState
# ---------------------------------------------------------------------------


def render_error_state(
    title: str = "Something went wrong",
    description: str = "",
    *,
    error: str = "",
    on_retry: str = "",
    variant: Literal["inline", "page"] = "inline",
) -> None:
    """Render an error display with optional retry trigger.

    Args:
        title: Short error heading.
        description: Human-readable explanation.
        error: Technical details (hidden behind a disclosure).
        on_retry: JavaScript string executed on retry button click.
        variant: ``"inline"`` for within a section, ``"page"`` for full-page.
    """
    cls = f"qv2-error-state qv2-error-state--{variant}"
    parts = [f'<div class="{cls}">']
    parts.append(f'<span class="qv2-error-state-icon">⚠</span>')
    parts.append(f'<div class="qv2-error-state-title">{title}</div>')
    if description:
        parts.append(f'<div class="qv2-error-state-desc">{description}</div>')
    if error:
        parts.append(
            '<details class="qv2-error-state-details">'
            "<summary>Technical details</summary>"
            f'<pre class="qv2-mono" style="font-size:var(--text-xs);overflow:auto;">{html.escape(error)}</pre>'
            "</details>"
        )
    if on_retry:
        parts.append(
            f'<button class="qv2-error-state-retry" onclick="{on_retry}">Retry</button>'
        )
    parts.append("</div>")
    st.html("\n".join(parts), width="content", unsafe_allow_javascript=bool(on_retry))
