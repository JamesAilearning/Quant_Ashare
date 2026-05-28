"""Regression tests for page-header helpers and navigation shell."""

from __future__ import annotations

import unittest
from pathlib import Path

try:
    import streamlit  # noqa: F401

    _HAS_STREAMLIT = True
except ImportError:
    _HAS_STREAMLIT = False


class OperatorUiPageHeaderTests(unittest.TestCase):
    @unittest.skipUnless(_HAS_STREAMLIT, "streamlit not installed")
    def test_module_exports_public_api(self) -> None:
        """Only ``render_page_header`` remains — ``render_breadcrumbs``
        was removed in UI review P1-2 because every page called it
        with ``(label, None)`` and the helper rendered the label as
        non-clickable text masquerading as nav. The same section is
        already exposed by the sidebar grouping."""

        from web.operator_ui.page_header import render_page_header

        self.assertTrue(callable(render_page_header))

    def test_render_breadcrumbs_helper_removed(self) -> None:
        """``render_breadcrumbs`` MUST be gone from the module surface.
        Pin its absence so a well-meaning revert that "puts the helper
        back for future use" without callers reintroduces the same dead
        / misleading affordance (UI review P1-2).

        Asserted at the source-text level rather than via ``import`` so
        the check still runs in CI cells without ``streamlit`` installed
        — ``page_header.py`` ``import streamlit as st`` at module top
        would otherwise raise ``ModuleNotFoundError`` before
        ``hasattr`` could even run. (Codex P2 on PR #193.)
        """

        source = Path("web/operator_ui/page_header.py").read_text(encoding="utf-8")

        self.assertNotIn(
            "def render_breadcrumbs(",
            source,
            "render_breadcrumbs should have been removed alongside its callers",
        )

    def test_no_page_calls_render_breadcrumbs(self) -> None:
        """All page modules SHALL stop importing or calling
        ``render_breadcrumbs`` (UI review P1-2)."""

        pages_dir = Path("web/operator_ui/pages")
        offenders: list[str] = []
        for path in sorted(pages_dir.glob("*.py")):
            if path.name.startswith("_") or path.name == "__init__.py":
                continue
            source = path.read_text(encoding="utf-8")
            if "render_breadcrumbs" in source:
                offenders.append(path.name)
        self.assertEqual(
            offenders, [],
            "Pages should no longer reference render_breadcrumbs",
        )

    def test_all_pages_use_page_header(self) -> None:
        pages_dir = Path("web/operator_ui/pages")
        for path in sorted(pages_dir.glob("*.py")):
            if path.name.startswith("_") or path.name == "__init__.py":
                continue
            source = path.read_text(encoding="utf-8")
            self.assertIn(
                "render_page_header",
                source,
                f"{path.name} should call render_page_header",
            )

    def test_app_py_injects_brand_and_status_footer(self) -> None:
        source = Path("web/operator_ui/app.py").read_text(encoding="utf-8")

        self.assertIn("qv2-sidebar-brand", source)
        self.assertIn("qv2-sidebar-footer", source)
        self.assertIn("qv2-sidebar-status", source)

    def test_app_py_has_nav_icon_injection(self) -> None:
        source = Path("web/operator_ui/app.py").read_text(encoding="utf-8")

        self.assertIn("qv2-nav-icon", source)
        self.assertIn("JobManager", source)

    def test_theme_css_contains_nav_tokens(self) -> None:
        css = Path("web/operator_ui/static/theme.css").read_text(encoding="utf-8")

        self.assertIn("qv2-sidebar-brand", css)
        self.assertIn("qv2-sidebar-footer", css)
        self.assertIn("qv2-page-header", css)
        self.assertIn("qv2-sidebar-status", css)
        self.assertIn("@media (max-width: 768px)", css)

    @unittest.skipUnless(_HAS_STREAMLIT, "streamlit not installed")
    def test_page_header_module_imports_without_streamlit_context(self) -> None:
        """Module-level import should succeed even without a running Streamlit app."""
        try:
            import web.operator_ui.page_header  # noqa: F401
        except Exception as exc:
            self.fail(f"page_header.py raised on import: {exc}")

    @unittest.skipUnless(_HAS_STREAMLIT, "streamlit not installed")
    def test_page_header_emits_skip_link_target_anchor(self) -> None:
        """``render_page_header`` SHALL inject the skip-link target anchor
        (``<a id="qv2-main-content">``) at the start of the rendered HTML so
        the affordance from ``theme.render_skip_link`` actually lands past
        the topbar / sidebar chrome instead of immediately after the
        link itself (UI review P0-4).

        We patch ``streamlit.html`` to capture the markup ``render_page_header``
        emits and assert against the *rendered* string — not the source text.
        The previous source-text version of this test matched the literal
        ``id="qv2-main-content"`` that lives in the docstring rather than the
        f-string ``id="{SKIP_LINK_TARGET_ID}"`` that lives in the code body,
        so a docstring tweak would have masked a real regression (codex P2
        on PR #191)."""

        from unittest.mock import patch

        captured: list[str] = []

        def _capture(markup: str, **_: object) -> None:
            captured.append(markup)

        with patch("streamlit.html", side_effect=_capture):
            from web.operator_ui.page_header import render_page_header

            render_page_header("Test Title", subtitle="测试副标题")

        self.assertEqual(
            len(captured), 1,
            "render_page_header should emit exactly one st.html block",
        )
        html = captured[0]
        self.assertIn('id="qv2-main-content"', html)
        self.assertIn('tabindex="-1"', html)
        # The anchor MUST lead the rendered HTML — sitting before the
        # ``<div class="qv2-page-header">`` div means the skip link
        # actually skips past topbar / sidebar chrome.
        anchor_index = html.index('id="qv2-main-content"')
        header_index = html.index('class="qv2-page-header"')
        self.assertLess(
            anchor_index,
            header_index,
            "Skip-link target anchor must appear before the page-header div",
        )

    def test_all_pages_render_page_header(self) -> None:
        """Every operator-facing page module MUST call ``render_page_header``
        so the skip-link target lands on the page."""

        pages_dir = Path("web/operator_ui/pages")
        missing: list[str] = []
        for path in sorted(pages_dir.glob("*.py")):
            if path.name.startswith("_") or path.name == "__init__.py":
                continue
            source = path.read_text(encoding="utf-8")
            if "render_page_header" not in source:
                missing.append(path.name)
        self.assertEqual(
            missing, [],
            "Pages without render_page_header lose the skip-link target",
        )


if __name__ == "__main__":
    unittest.main()
