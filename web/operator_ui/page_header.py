"""Consistent page header and breadcrumb helpers for the operator UI."""

from __future__ import annotations

import streamlit as st

from web.operator_ui.theme import SKIP_LINK_TARGET_ID


def render_page_header(
    title: str,
    subtitle: str = "",
    *,
    actions_html: str = "",
) -> None:
    """Inject a consistent page header with optional subtitle and action buttons.

    Args:
        title: Page title (displayed as ``<h1>``).
        subtitle: Optional secondary description below the title.
        actions_html: Optional raw HTML for action buttons, placed right-aligned.

    Side effect:
        Emits the skip-link target anchor (``<a id="qv2-main-content">``)
        immediately before the page title. The matching link lives in
        :func:`web.operator_ui.theme.render_skip_link` and is rendered by
        ``app.py`` before navigation; the anchor must land HERE (after
        topbar / sidebar / breadcrumb chrome but before page content) so
        the skip-link actually skips chrome. Previously both link and
        anchor lived in ``theme.SKIP_LINK_HTML`` as adjacent siblings,
        so pressing Enter scrolled to a point still above every piece
        of chrome the operator wanted to skip — silently useless (UI
        review P0-4).
    """

    parts = [
        f'<a id="{SKIP_LINK_TARGET_ID}" tabindex="-1" class="qv2-sr-only">主内容</a>',
        '<div class="qv2-page-header">',
        '<div class="qv2-page-header-main">',
        f'<h1 class="qv2-text-page-title">{title}</h1>',
    ]
    if subtitle:
        parts.append(f'<p class="qv2-text-body-sm qv2-muted">{subtitle}</p>')
    parts.append("</div>")
    if actions_html:
        parts.append(f'<div class="qv2-page-header-actions">{actions_html}</div>')
    parts.append("</div>")
    st.html("\n".join(parts), width="content", unsafe_allow_javascript=False)


def render_breadcrumbs(segments: list[tuple[str, str | None]]) -> None:
    """Inject a breadcrumb trail above the page content.

    Each ``segment`` is a ``(label, path_or_None)`` pair.  The *last*
    segment (where ``path`` is ``None``) is rendered as the current page
    without a hyperlink.

    Example::

        render_breadcrumbs([
            ("Analyze", "/results"),
            ("Results", None),
        ])
    """

    items: list[str] = []
    for i, (label, path) in enumerate(segments):
        is_last = i == len(segments) - 1
        if is_last:
            items.append(f'<li><span aria-current="page">{label}</span></li>')
        else:
            items.append(f'<li><a href="{path}">{label}</a></li>')
            items.append('<li><span class="qv2-breadcrumb-sep">/</span></li>')

    html = (
        '<nav class="qv2-breadcrumbs" aria-label="Breadcrumb">'
        "<ol>"
        + "".join(items)
        + "</ol>"
        "</nav>"
    )
    st.html(html, width="content", unsafe_allow_javascript=False)
