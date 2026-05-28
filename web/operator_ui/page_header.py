"""Consistent page header helper for the operator UI.

``render_breadcrumbs`` lived here in earlier iterations but every page
called it with a single ``(label, None)`` segment — the path was always
None, so the breadcrumb rendered as plain non-clickable text instead of
nav. Operators kept trying to click the "section" label and nothing
happened. The same section is already exposed by ``st.navigation``'s
sidebar grouping, so the breadcrumb added zero information while
masquerading as navigation. UI review P1-2 deleted both the calls and
the helper.
"""

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
        topbar / sidebar chrome but before page content) so the skip-link
        actually skips chrome. Previously both link and anchor lived in
        ``theme.SKIP_LINK_HTML`` as adjacent siblings, so pressing Enter
        scrolled to a point still above every piece of chrome the
        operator wanted to skip — silently useless (UI review P0-4).
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
