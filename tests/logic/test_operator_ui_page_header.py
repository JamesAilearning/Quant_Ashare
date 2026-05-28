"""Regression tests for page-header helpers and navigation shell."""

from __future__ import annotations

import inspect
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
        from web.operator_ui.page_header import render_breadcrumbs, render_page_header

        self.assertTrue(callable(render_page_header))
        self.assertTrue(callable(render_breadcrumbs))

    @unittest.skipUnless(_HAS_STREAMLIT, "streamlit not installed")
    def test_render_breadcrumbs_generates_semantic_html(self) -> None:
        source = inspect.getsource(
            __import__(
                "web.operator_ui.page_header", fromlist=["render_breadcrumbs"]
            ).render_breadcrumbs,
        )

        self.assertIn("qv2-breadcrumbs", source)
        self.assertIn('aria-label="Breadcrumb"', source)
        self.assertIn('aria-current="page"', source)

    def test_all_pages_use_page_header(self) -> None:
        pages_dir = Path("web/operator_ui/pages")
        for path in sorted(pages_dir.glob("*.py")):
            if path.name.startswith("_") or path.name == "__init__.py":
                continue
            # Redirect stubs don't need a header
            if path.name == "run_history.py":
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
        self.assertIn("qv2-breadcrumbs", css)
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
        the topbar / sidebar / breadcrumb instead of immediately after the
        link itself (UI review P0-4).

        Anchors emitted from this helper land on every page, so the skip
        link works uniformly without requiring each page to opt in."""

        source = inspect.getsource(
            __import__(
                "web.operator_ui.page_header", fromlist=["render_page_header"]
            ).render_page_header,
        )
        # Source-level guard — we cannot drive Streamlit's st.html
        # capture without a ScriptRunContext, but pinning the literal
        # tag in source is enough to prevent silent regression.
        self.assertIn('id="qv2-main-content"', source)
        self.assertIn("tabindex=\"-1\"", source)
        # Must be emitted via the parts list so it lands BEFORE the
        # ``<div class="qv2-page-header">`` opening tag.
        anchor_index = source.index('id="qv2-main-content"')
        header_index = source.index('class="qv2-page-header"')
        self.assertLess(
            anchor_index,
            header_index,
            "Skip-link target anchor must appear before the page-header div",
        )

    def test_all_pages_render_page_header(self) -> None:
        """Every operator-facing page module MUST call ``render_page_header``
        so the skip-link target lands on the page. Stub redirects (e.g.,
        ``run_history.py``) are exempt — see ``test_all_pages_use_page_header``
        above for the parallel coverage."""

        pages_dir = Path("web/operator_ui/pages")
        missing: list[str] = []
        for path in sorted(pages_dir.glob("*.py")):
            if path.name.startswith("_") or path.name == "__init__.py":
                continue
            if path.name == "run_history.py":
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
