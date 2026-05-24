"""Reusable presentation components for the Streamlit operator UI.

The helpers in this module emit token-backed HTML fragments only. They do
not read runtime artifacts, launch jobs, or compute official metrics.
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from typing import Any, Literal

import streamlit as st

FeedbackVariant = Literal["neutral", "info", "success", "warning", "danger"]
ButtonVariant = Literal["primary", "secondary", "ghost", "danger"]
BadgeVariant = FeedbackVariant
StatCardTrend = Literal["up", "down", "flat"]
SkeletonVariant = Literal["text", "rect", "circle"]


def _emit(markup: str, *, allow_javascript: bool = False) -> None:
    st.html(markup, width="content", unsafe_allow_javascript=allow_javascript)


def _class_names(*items: str | None) -> str:
    return " ".join(item for item in items if item)


def _escaped_lines(lines: Sequence[Any]) -> str:
    return "".join(f"<div>{html.escape(str(line))}</div>" for line in lines)


def render_button(
    label: str,
    *,
    variant: ButtonVariant = "primary",
    icon: str = "",
    disabled: bool = False,
    loading: bool = False,
    full_width: bool = False,
    on_click: str = "",
) -> None:
    """Render a token-backed command button preview."""

    attrs = []
    if disabled:
        attrs.append("disabled")
        attrs.append('aria-disabled="true"')
    if on_click:
        attrs.append(f'onclick="{html.escape(on_click, quote=True)}"')
    cls = _class_names(
        "qv2-button",
        f"qv2-button--{variant}",
        "qv2-button--full" if full_width else None,
    )
    icon_html = ""
    if loading:
        icon_html = '<span class="qv2-spinner qv2-spinner--inline" aria-hidden="true"></span>'
    elif icon:
        icon_html = f'<span aria-hidden="true">{html.escape(icon)}</span>'
    markup = (
        f'<button class="{cls}" type="button" {" ".join(attrs)}>'
        f"{icon_html}<span>{html.escape(label)}</span>"
        "</button>"
    )
    _emit(markup, allow_javascript=bool(on_click))


def render_icon_button(
    icon: str,
    label: str,
    *,
    variant: ButtonVariant = "ghost",
    disabled: bool = False,
    on_click: str = "",
) -> None:
    """Render an accessible icon-only button preview."""

    attrs = [
        f'aria-label="{html.escape(label, quote=True)}"',
        f'title="{html.escape(label, quote=True)}"',
    ]
    if disabled:
        attrs.append("disabled")
        attrs.append('aria-disabled="true"')
    if on_click:
        attrs.append(f'onclick="{html.escape(on_click, quote=True)}"')
    cls = _class_names("qv2-icon-button", f"qv2-icon-button--{variant}")
    markup = (
        f'<button class="{cls}" type="button" {" ".join(attrs)}>'
        f'<span aria-hidden="true">{html.escape(icon)}</span>'
        "</button>"
    )
    _emit(markup, allow_javascript=bool(on_click))


def render_badge(
    variant: BadgeVariant,
    label: str,
    *,
    icon: str = "",
    pulse: bool = False,
) -> None:
    """Render a compact status badge."""

    pulse_attr = ' data-qv2-pulse="true"' if pulse else ""
    dot = '<span class="qv2-badge-dot"></span>' if pulse else ""
    icon_html = f"{html.escape(icon)} " if icon else ""
    markup = (
        f'<span class="qv2-badge qv2-badge--{variant}"{pulse_attr}>'
        f"{icon_html}{dot}{html.escape(label)}"
        "</span>"
    )
    _emit(markup)


def render_tag(
    label: str,
    *,
    variant: FeedbackVariant = "neutral",
    icon: str = "",
    removable: bool = False,
) -> None:
    """Render a compact tag/chip."""

    icon_html = f'<span aria-hidden="true">{html.escape(icon)}</span>' if icon else ""
    remove_html = '<span class="qv2-tag-remove" aria-hidden="true">&times;</span>' if removable else ""
    markup = (
        f'<span class="qv2-tag qv2-tag--{variant}">'
        f"{icon_html}<span>{html.escape(label)}</span>{remove_html}"
        "</span>"
    )
    _emit(markup)


def render_card(
    title: str,
    body: str | Sequence[Any] = "",
    *,
    footer: str = "",
    tone: FeedbackVariant | Literal["default"] = "default",
    allow_html: bool = False,
) -> None:
    """Render a simple content card."""

    if isinstance(body, str):
        body_html = body if allow_html else html.escape(body)
    else:
        body_html = _escaped_lines(body)
    footer_html = f'<div class="qv2-card-footer">{html.escape(footer)}</div>' if footer else ""
    markup = (
        f'<section class="qv2-card qv2-card--{tone}">'
        f'<div class="qv2-text-card-title">{html.escape(title)}</div>'
        f'<div class="qv2-card-body">{body_html}</div>'
        f"{footer_html}"
        "</section>"
    )
    _emit(markup)


def render_stat_card(
    label: str,
    value: str,
    *,
    trend: StatCardTrend | None = None,
    secondary: list[tuple[str, str]] | None = None,
    value_color: Literal["default", "positive", "negative", "warning"] = "default",
    tooltip: str = "",
) -> None:
    """Render a KPI-style metric card."""

    trend_map = {"up": " \u2197", "down": " \u2198", "flat": ""}
    tooltip_html = ""
    if tooltip:
        tooltip_html = (
            f'<span class="qv2-stat-card-tooltip" title="{html.escape(tooltip, quote=True)}">?</span>'
        )
    color_class = "" if value_color == "default" else f" qv2-{value_color}"
    parts = ['<div class="qv2-stat-card">']
    parts.append(f'<div class="qv2-text-card-label">{html.escape(label)} {tooltip_html}</div>')
    parts.append(
        f'<div class="qv2-text-metric-primary{color_class}">'
        f"{html.escape(value)}{trend_map.get(trend or '', '')}</div>"
    )
    if secondary:
        for sec_label, sec_value in secondary:
            parts.append(
                '<div class="qv2-stat-card-secondary">'
                f'<span class="qv2-muted">{html.escape(sec_label)}</span>'
                f'<span>{html.escape(sec_value)}</span>'
                "</div>"
            )
    parts.append("</div>")
    _emit("\n".join(parts))


def render_tabs(labels: Sequence[str], *, active_index: int = 0) -> None:
    """Render a static tablist preview."""

    if not labels:
        return
    active = min(max(active_index, 0), len(labels) - 1)
    tabs = []
    for index, label in enumerate(labels):
        selected = index == active
        tab_index = "0" if selected else "-1"
        tabs.append(
            '<button class="qv2-tab" role="tab" type="button" '
            f'aria-selected="{str(selected).lower()}" tabindex="{tab_index}">'
            f"{html.escape(label)}</button>"
        )
    _emit('<div class="qv2-tabs" role="tablist">' + "".join(tabs) + "</div>")


def render_accordion(
    title: str,
    body: str,
    *,
    expanded: bool = False,
    allow_html: bool = False,
) -> None:
    """Render a details/summary accordion section."""

    open_attr = " open" if expanded else ""
    body_html = body if allow_html else html.escape(body)
    markup = (
        f'<details class="qv2-accordion"{open_attr}>'
        f"<summary>{html.escape(title)}</summary>"
        f'<div class="qv2-accordion-body">{body_html}</div>'
        "</details>"
    )
    _emit(markup)


def render_modal(
    title: str,
    body: str,
    *,
    open: bool = True,
    footer: str = "",
) -> None:
    """Render a static modal preview for design-system QA."""

    state_cls = "" if open else " qv2-modal--closed"
    footer_html = f'<div class="qv2-modal-footer">{html.escape(footer)}</div>' if footer else ""
    markup = (
        f'<div class="qv2-modal{state_cls}" role="dialog" aria-modal="true" '
        f'aria-label="{html.escape(title, quote=True)}">'
        '<div class="qv2-modal-panel">'
        f'<div class="qv2-modal-title">{html.escape(title)}</div>'
        f'<div class="qv2-modal-body">{html.escape(body)}</div>'
        f"{footer_html}</div></div>"
    )
    _emit(markup)


def render_toast(
    message: str,
    *,
    title: str = "",
    variant: FeedbackVariant = "info",
) -> None:
    """Render a non-floating toast preview."""

    title_html = f'<div class="qv2-toast-title">{html.escape(title)}</div>' if title else ""
    markup = (
        f'<div class="qv2-toast qv2-toast--{variant}" role="status">'
        f"{title_html}<div>{html.escape(message)}</div>"
        "</div>"
    )
    _emit(markup)


def render_tooltip(label: str, tooltip: str) -> None:
    """Render an inline tooltip anchor."""

    markup = (
        '<span class="qv2-tooltip">'
        f'<span class="qv2-tooltip-anchor" tabindex="0">{html.escape(label)}</span>'
        f'<span class="qv2-tooltip-content" role="tooltip">{html.escape(tooltip)}</span>'
        "</span>"
    )
    _emit(markup)


def render_skeleton(
    variant: SkeletonVariant = "rect",
    *,
    width: str = "100%",
    height: str = "1em",
    count: int = 1,
) -> None:
    """Render shimmer loading placeholders."""

    cls = f"qv2-skeleton qv2-skeleton-{variant}"
    items = "".join(
        f'<div class="{cls}" style="width:{html.escape(width)};height:{html.escape(height)};"></div>'
        for _ in range(count)
    )
    _emit(items)


def render_progress_bar(
    value: float,
    *,
    label: str = "",
    max_value: float = 100.0,
) -> None:
    """Render a determinate progress bar."""

    denominator = max_value if max_value > 0 else 100.0
    percent = max(0.0, min(100.0, (float(value) / denominator) * 100.0))
    label_html = f'<div class="qv2-progress-label">{html.escape(label)}</div>' if label else ""
    markup = (
        '<div class="qv2-progress">'
        f"{label_html}"
        f'<div class="qv2-progress-track" role="progressbar" aria-valuemin="0" '
        f'aria-valuemax="{html.escape(str(max_value), quote=True)}" '
        f'aria-valuenow="{html.escape(str(value), quote=True)}">'
        f'<div class="qv2-progress-fill" style="width:{percent:.1f}%"></div>'
        "</div></div>"
    )
    _emit(markup)


def render_spinner(label: str = "加载中") -> None:
    """Render an indeterminate loading spinner."""

    markup = (
        '<span class="qv2-spinner-wrap" role="status">'
        '<span class="qv2-spinner" aria-hidden="true"></span>'
        f'<span>{html.escape(label)}</span>'
        "</span>"
    )
    _emit(markup)


def render_field(
    label: str,
    control_html: str,
    *,
    help_text: str = "",
    error: str = "",
    required: bool = False,
) -> None:
    """Render a token-backed form field wrapper around provided control HTML."""

    required_html = '<span class="qv2-field-required">*</span>' if required else ""
    help_html = f'<div class="qv2-field-help">{html.escape(help_text)}</div>' if help_text else ""
    error_html = f'<div class="qv2-field-error">{html.escape(error)}</div>' if error else ""
    markup = (
        '<label class="qv2-field">'
        f'<span class="qv2-field-label">{html.escape(label)}{required_html}</span>'
        f"{control_html}"
        f"{help_html}{error_html}"
        "</label>"
    )
    _emit(markup)


def render_empty_state(
    icon: str,
    title: str,
    description: str = "",
    *,
    action_label: str = "",
    action_on_click: str = "",
) -> None:
    """Render a centered empty-state placeholder."""

    parts = ['<div class="qv2-empty-state">']
    if icon:
        parts.append(f'<span class="qv2-empty-state-icon">{html.escape(icon)}</span>')
    parts.append(f'<div class="qv2-empty-state-title">{html.escape(title)}</div>')
    if description:
        parts.append(f'<div class="qv2-empty-state-desc">{html.escape(description)}</div>')
    if action_label:
        onclick = f' onclick="{html.escape(action_on_click, quote=True)}"' if action_on_click else ""
        parts.append(
            f'<button class="qv2-empty-state-action"{onclick}>{html.escape(action_label)}</button>'
        )
    parts.append("</div>")
    _emit("\n".join(parts), allow_javascript=bool(action_on_click))


def render_error_state(
    title: str = "出错了",
    description: str = "",
    *,
    error: str = "",
    on_retry: str = "",
    variant: Literal["inline", "page"] = "inline",
) -> None:
    """Render an error display with optional retry trigger."""

    cls = f"qv2-error-state qv2-error-state--{variant}"
    parts = [f'<div class="{cls}">']
    parts.append('<span class="qv2-error-state-icon">!</span>')
    parts.append(f'<div class="qv2-error-state-title">{html.escape(title)}</div>')
    if description:
        parts.append(f'<div class="qv2-error-state-desc">{html.escape(description)}</div>')
    if error:
        parts.append(
            '<details class="qv2-error-state-details">'
            "<summary>详细信息</summary>"
            f'<pre class="qv2-mono" style="font-size:var(--text-xs);overflow:auto;">{html.escape(error)}</pre>'
            "</details>"
        )
    if on_retry:
        parts.append(
            f'<button class="qv2-error-state-retry" onclick="{html.escape(on_retry, quote=True)}">重试</button>'
        )
    parts.append("</div>")
    _emit("\n".join(parts), allow_javascript=bool(on_retry))


def render_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[Any]],
    *,
    caption: str = "",
    compact: bool = False,
) -> None:
    """Render an accessible, token-backed static table."""

    cls = _class_names("qv2-table", "qv2-table--compact" if compact else None)
    caption_html = (
        f'<caption class="qv2-sr-only">{html.escape(caption)}</caption>'
        if caption
        else ""
    )
    head = "".join(f'<th scope="col">{html.escape(str(header))}</th>' for header in headers)
    body_rows = []
    for row in rows:
        cells = "".join(f"<td>{html.escape(str(value))}</td>" for value in row)
        body_rows.append(f"<tr>{cells}</tr>")
    markup = (
        f'<div class="qv2-table-wrap"><table class="{cls}">'
        f"{caption_html}<thead><tr>{head}</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody></table></div>"
    )
    _emit(markup)
