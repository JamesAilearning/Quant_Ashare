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

    @unittest.skipUnless(_HAS_STREAMLIT, "streamlit not installed")
    def test_progress_bar_aria_values_are_integers(self) -> None:
        """ARIA ``aria-valuenow`` / ``aria-valuemax`` MUST render as
        integers on the 0–100 percent scale (UI review P2-8). Older
        screen readers read fractional digits awkwardly; the percent
        string inside the visual bar already conveys precision."""

        from unittest.mock import patch

        captured: list[str] = []

        def _capture(markup: str, **_: object) -> None:
            captured.append(markup)

        with patch("streamlit.html", side_effect=_capture):
            from web.operator_ui.components import render_progress_bar

            # 62.7 — not a tie so Python's banker-rounding doesn't bite
            # us (``round(62.5) == 62`` would otherwise mask the fix).
            render_progress_bar(62.7, max_value=100.0, label="进度")

        self.assertEqual(len(captured), 1)
        html_text = captured[0]
        # Integer rounded value (not 62.7 / not "62.7").
        self.assertIn('aria-valuenow="63"', html_text)
        self.assertIn('aria-valuemax="100"', html_text)
        # Defence in depth: no float-string for either ARIA attribute.
        self.assertNotIn("aria-valuenow=\"62.7\"", html_text)
        self.assertNotIn("aria-valuemax=\"100.0\"", html_text)
        # The visible width inside the track still carries the precise
        # percent so operators see exact progress.
        self.assertIn("width:62.7%", html_text)

    @unittest.skipUnless(_HAS_STREAMLIT, "streamlit not installed")
    def test_progress_bar_aria_uses_percent_scale_for_fractional_units(self) -> None:
        """Codex follow-up on PR #205: a fractional caller scale like
        ``render_progress_bar(0.25, max_value=1.0)`` must announce ARIA
        on the 0–100 percent scale (``aria-valuenow="25"
        aria-valuemax="100"``) — NOT ``round(0.25)=0`` of ``round(1.0)=1``
        which told assistive tech "0% progress" while the bar visibly
        showed 25%."""

        from unittest.mock import patch

        captured: list[str] = []

        def _capture(markup: str, **_: object) -> None:
            captured.append(markup)

        with patch("streamlit.html", side_effect=_capture):
            from web.operator_ui.components import render_progress_bar

            render_progress_bar(0.25, max_value=1.0)

        html_text = captured[0]
        self.assertIn('aria-valuenow="25"', html_text)
        self.assertIn('aria-valuemax="100"', html_text)
        # The misleading raw-unit rounding must be gone.
        self.assertNotIn('aria-valuenow="0"', html_text)
        self.assertNotIn('aria-valuemax="1"', html_text)
        self.assertIn("width:25.0%", html_text)


class ResultsCardTooltipA11yTests(unittest.TestCase):
    """UI review P2-2: ``_render_card`` used to put its help text in the
    HTML ``title=`` attribute only. ``title=`` fires on mouse hover,
    not on keyboard focus, so keyboard-only and screen-reader users
    never saw the explanation. The fix moves the help text onto a
    focusable ``ⓘ`` tooltip anchor with ``aria-label`` (read by screen
    readers on focus), ``title=`` (kept for mouse hover), and
    ``tabindex="0"`` (Tab-reachable)."""

    def test_card_emits_focusable_tooltip_anchor_with_aria_label(self) -> None:
        render_source = Path(
            "web/operator_ui/pages/_results_render.py"
        ).read_text(encoding="utf-8")

        # Focusable anchor exists.
        self.assertIn('class="qv2-r-card-tooltip"', render_source)
        self.assertIn('tabindex="0"', render_source)
        # Anchor announces via aria-label (not just title=).
        self.assertIn('aria-label="{escaped_help}"', render_source)
        # The card itself no longer carries ``title=`` — moved onto the
        # focusable anchor.
        self.assertNotIn('"qv2-r-card" title="', render_source)

    def test_theme_css_styles_card_tooltip_with_focus_indicator(self) -> None:
        css = Path("web/operator_ui/static/theme.css").read_text(encoding="utf-8")

        self.assertIn(".qv2-r-card-tooltip {", css)
        # Focus-visible outline so keyboard users see where they are.
        self.assertIn(".qv2-r-card-tooltip:focus-visible {", css)


class StatCardTooltipA11yTests(unittest.TestCase):
    """``render_stat_card`` backs the jobs / walk-forward KPI cards. Its help
    tooltip must be keyboard / screen-reader accessible like ``_render_card``'s
    (UI review P2-2): a focusable anchor with ``role=note`` + ``aria-label``,
    not just a mouse-hover ``title=``. Asserted on the emitted markup."""

    def _emit_for(self, **kwargs: object) -> str:
        from unittest.mock import patch

        captured: list[str] = []
        with patch(
            "web.operator_ui.components._emit",
            side_effect=lambda markup, **_k: captured.append(markup),
        ):
            from web.operator_ui.components import render_stat_card
            render_stat_card("年化收益", "12.3%", **kwargs)  # type: ignore[arg-type]
        return "\n".join(captured)

    @unittest.skipUnless(_HAS_STREAMLIT, "streamlit not installed")
    def test_tooltip_anchor_is_focusable_with_aria_label(self) -> None:
        markup = self._emit_for(tooltip="说明文本")
        self.assertIn('class="qv2-stat-card-tooltip"', markup)
        self.assertIn('tabindex="0"', markup)
        self.assertIn('role="note"', markup)
        self.assertIn('aria-label="说明文本"', markup)

    @unittest.skipUnless(_HAS_STREAMLIT, "streamlit not installed")
    def test_no_tooltip_means_no_anchor(self) -> None:
        # Without a tooltip, no focusable element is emitted at all.
        markup = self._emit_for(tooltip="")
        self.assertNotIn("qv2-stat-card-tooltip", markup)

    def test_theme_css_styles_stat_card_tooltip_focus(self) -> None:
        css = Path("web/operator_ui/static/theme.css").read_text(encoding="utf-8")
        self.assertIn(".qv2-stat-card-tooltip:focus-visible {", css)


class PlotlyReducedMotionTests(unittest.TestCase):
    """UI review P2-9: server-rendered Plotly transitions (relayout /
    range-slider drag) bypassed the ``prefers-reduced-motion`` CSS hook
    so vestibular-sensitive users still got motion. Every interactive
    chart now passes ``transition={"duration": 0}`` to
    ``fig.update_layout`` so the relayout animation stays put."""

    def test_results_render_charts_disable_plotly_transition(self) -> None:
        source = Path(
            "web/operator_ui/pages/_results_render.py"
        ).read_text(encoding="utf-8")

        # All three ``fig.update_layout`` calls (nav, drawdown, monthly
        # heatmap) carry the transition-disable kwarg.
        self.assertGreaterEqual(source.count('transition={"duration": 0}'), 3)

    def test_walk_forward_charts_disable_plotly_transition(self) -> None:
        source = Path(
            "web/operator_ui/pages/walk_forward.py"
        ).read_text(encoding="utf-8")

        # Four charts on the walk-forward page (stitched NAV + 3 metric
        # bar charts) all carry the transition-disable kwarg.
        self.assertGreaterEqual(source.count('transition={"duration": 0}'), 4)


if __name__ == "__main__":
    unittest.main()
