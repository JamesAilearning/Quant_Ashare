"""Regression tests for operator UI component helpers."""

from __future__ import annotations

import inspect
import unittest
from pathlib import Path

try:
    import streamlit  # noqa: F401

    _HAS_STREAMLIT = True
except ImportError:
    _HAS_STREAMLIT = False


class OperatorUiComponentsTests(unittest.TestCase):
    @unittest.skipUnless(_HAS_STREAMLIT, "streamlit not installed")
    def test_module_exports_all_helpers(self) -> None:
        from web.operator_ui.components import (
            render_accordion,
            render_badge,
            render_button,
            render_card,
            render_empty_state,
            render_error_state,
            render_field,
            render_icon_button,
            render_modal,
            render_progress_bar,
            render_skeleton,
            render_spinner,
            render_stat_card,
            render_table,
            render_tabs,
            render_tag,
            render_toast,
            render_tooltip,
        )

        for name, fn in [
            ("render_accordion", render_accordion),
            ("render_badge", render_badge),
            ("render_button", render_button),
            ("render_card", render_card),
            ("render_empty_state", render_empty_state),
            ("render_error_state", render_error_state),
            ("render_field", render_field),
            ("render_icon_button", render_icon_button),
            ("render_modal", render_modal),
            ("render_progress_bar", render_progress_bar),
            ("render_skeleton", render_skeleton),
            ("render_spinner", render_spinner),
            ("render_stat_card", render_stat_card),
            ("render_table", render_table),
            ("render_tabs", render_tabs),
            ("render_tag", render_tag),
            ("render_toast", render_toast),
            ("render_tooltip", render_tooltip),
        ]:
            self.assertTrue(callable(fn), f"{name} should be callable")

    @unittest.skipUnless(_HAS_STREAMLIT, "streamlit not installed")
    def test_render_badge_passes_variant_in_fstring(self) -> None:
        source = inspect.getsource(
            __import__("web.operator_ui.components", fromlist=["render_badge"]).render_badge
        )
        self.assertIn("qv2-badge--{variant}", source)

    def test_components_css_contains_all_classes(self) -> None:
        css = Path("web/operator_ui/static/theme.css").read_text(encoding="utf-8")

        for cls_name in (
            "qv2-accordion",
            "qv2-badge",
            "qv2-button",
            "qv2-card",
            "qv2-field",
            "qv2-icon-button",
            "qv2-modal",
            "qv2-progress",
            "qv2-stat-card",
            "qv2-spinner",
            "qv2-skeleton",
            "qv2-tabs",
            "qv2-tag",
            "qv2-toast",
            "qv2-tooltip",
            "qv2-empty-state",
            "qv2-error-state",
            "qv2-table",
            "qv2-badge--neutral",
            "qv2-badge--info",
            "qv2-badge--success",
            "qv2-badge--warning",
            "qv2-badge--danger",
            "qv2-shimmer",
            "qv2-pulse-dot",
        ):
            self.assertIn(cls_name, css, f"theme.css missing {cls_name}")

    def test_design_system_imports_components(self) -> None:
        source = Path("web/operator_ui/pages/design_system.py").read_text(encoding="utf-8")

        self.assertIn("from web.operator_ui.components import", source)
        for fn in (
            "render_accordion",
            "render_badge",
            "render_button",
            "render_card",
            "render_empty_state",
            "render_error_state",
            "render_field",
            "render_icon_button",
            "render_modal",
            "render_progress_bar",
            "render_skeleton",
            "render_spinner",
            "render_stat_card",
            "render_table",
            "render_tabs",
            "render_tag",
            "render_toast",
            "render_tooltip",
        ):
            self.assertIn(fn, source, f"design_system.py should call {fn}")

    @unittest.skipUnless(_HAS_STREAMLIT, "streamlit not installed")
    def test_components_module_imports_without_crashing(self) -> None:
        try:
            import web.operator_ui.components  # noqa: F401
        except Exception as exc:
            self.fail(f"components.py raised on import: {exc}")


if __name__ == "__main__":
    unittest.main()
