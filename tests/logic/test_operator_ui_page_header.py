"""Regression tests for page-header helpers and navigation shell."""

from __future__ import annotations

import inspect
import unittest
from pathlib import Path


class OperatorUiPageHeaderTests(unittest.TestCase):
    def test_module_exports_public_api(self) -> None:
        from web.operator_ui.page_header import render_breadcrumbs, render_page_header

        self.assertTrue(callable(render_page_header))
        self.assertTrue(callable(render_breadcrumbs))

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

    def test_page_header_module_imports_without_streamlit_context(self) -> None:
        """Module-level import should succeed even without a running Streamlit app."""
        try:
            import web.operator_ui.page_header  # noqa: F401
        except Exception as exc:
            self.fail(f"page_header.py raised on import: {exc}")


if __name__ == "__main__":
    unittest.main()
