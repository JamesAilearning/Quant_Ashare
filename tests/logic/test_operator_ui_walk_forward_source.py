"""Source-level regression guards for operator UI Walk-Forward rendering."""

from __future__ import annotations

import unittest
from pathlib import Path


class WalkForwardPageSourceTests(unittest.TestCase):
    def _source(self) -> str:
        return Path("web/operator_ui/pages/walk_forward.py").read_text(encoding="utf-8")

    def test_walk_forward_page_uses_canonical_information_ratio(self) -> None:
        source = self._source()

        self.assertIn('"information_ratio"', source)
        self.assertIn('"mean_information_ratio"', source)
        self.assertIn('"MEAN IR"', source)
        self.assertIn('"IR"', source)
        self.assertNotIn('"MEAN SHARPE"', source)
        self.assertNotIn('"Sharpe"', source)
        self.assertNotIn('"sharpe"', source)
        self.assertNotIn('"sharpe_ratio"', source)

    def test_walk_forward_page_preserves_zero_metric_fallbacks(self) -> None:
        source = self._source()

        self.assertIn("def _first_metric(", source)
        self.assertIn("if value is not None:", source)
        self.assertNotIn(" or _get_metrics(", source)

    def test_walk_forward_page_summary_rows_guard_missing_metric_series(self) -> None:
        source = self._source()

        self.assertIn("def _mean(values: list[float])", source)
        self.assertIn("mean_return = _mean(return_list)", source)
        self.assertIn('"IR": format_number(_mean(ir_list)) if ir_list else MISSING', source)
        self.assertNotIn("sum(ir_list) / len(ir_list)", source)
        self.assertNotIn("sum(dd_list) / len(dd_list)", source)

    def test_walk_forward_empty_state_uses_streamlit_navigation(self) -> None:
        source = self._source()

        self.assertIn('st.button("Config & Run")', source)
        self.assertIn('st.switch_page("pages/config_run.py")', source)
        self.assertNotIn('action_label="Config & Run"', source)

    def test_walk_forward_page_surfaces_artifact_read_errors(self) -> None:
        source = self._source()

        self.assertIn("_stop_artifact_error", source)
        self.assertIn("Unable to read walk-forward report", source)
        self.assertIn("Unable to read fold reports", source)
        self.assertIn("Unable to discover walk-forward charts", source)
        self.assertNotIn("wf_report = {}", source)
        self.assertNotIn("fold_reports = []", source)
        self.assertNotIn("charts = {}", source)

    def test_walk_forward_page_keeps_worst_drawdown_fold_aligned(self) -> None:
        source = self._source()

        self.assertIn('drawdown_by_fold.append((fd["index"], fd["max_drawdown"]))', source)
        self.assertIn("min(drawdown_by_fold, key=lambda item: item[1])", source)
        self.assertNotIn("dd_list.index", source)

    def test_walk_forward_docstring_cites_option_b_contract(self) -> None:
        """The module docstring SHALL document the Option B contract
        decision (TICKET-B follow-up) so future maintainers know why no
        new walk-forward artifacts are emitted."""

        source = self._source()
        self.assertIn("Option B", source)
        self.assertIn("PR #108", source)
        self.assertIn("synthesise", source.lower())

    def test_walk_forward_bottom_uses_tabs_layout(self) -> None:
        """The bottom of the page SHALL be a tabs layout exposing
        Stitched NAV, Per-Fold Detail, Metric Bars, Logs, Config, Raw
        JSON, and Charts (TICKET-B reorg)."""

        source = self._source()
        for label in (
            '"Stitched OOS NAV"',
            '"Per-Fold Detail"',
            '"Metric Bars"',
            '"Logs"',
            '"Config"',
            '"Raw JSON"',
            '"Charts"',
        ):
            self.assertIn(label, source, f"Missing tab label {label}")
        self.assertIn("st.tabs(", source)

    def test_walk_forward_error_state_offers_retry(self) -> None:
        """``_stop_artifact_error`` SHALL pass an ``on_retry`` so the
        operator can recover from a transient read failure without
        navigating away (TICKET-B retry requirement)."""

        source = self._source()
        self.assertIn("on_retry=", source)
        self.assertIn('window.location.reload()', source)

    def test_walk_forward_synthesised_nav_helpers_exist(self) -> None:
        """Stitched NAV synthesis + log reader SHALL be implemented as
        named helpers so unit tests can target them without a Streamlit
        ScriptRunContext."""

        source = self._source()
        self.assertIn("def _synthesised_stitched_nav(", source)
        self.assertIn("def _read_log_files(", source)
        # Synthesis uses simple compounding over test window length —
        # documents the approximation explicitly.
        self.assertIn("annualised return", source)


class WalkForwardSynthesisHelperTests(unittest.TestCase):
    """Unit tests for the pure helpers in walk_forward.py.

    Mirrors the LastNDaysSplitTests pattern in test_operator_ui_config_run_source:
    skip when streamlit isn't installed because the page module loads it at
    import time even though the helpers themselves are streamlit-free.
    """

    @classmethod
    def setUpClass(cls) -> None:
        try:
            import streamlit  # noqa: F401
        except ImportError as exc:
            raise unittest.SkipTest("streamlit not installed in this CI cell") from exc

    def test_synthesised_nav_drops_folds_with_missing_inputs(self) -> None:
        from web.operator_ui.pages.walk_forward import _synthesised_stitched_nav

        fold_data: list[dict] = [
            {  # well-formed
                "ordinal": 1,
                "test_start": "2024-01-01",
                "test_end": "2024-06-30",
                "annual_return": 0.10,
            },
            {  # missing annual_return
                "ordinal": 2,
                "test_start": "2024-07-01",
                "test_end": "2024-12-31",
                "annual_return": None,
            },
            {  # malformed dates
                "ordinal": 3,
                "test_start": "not-a-date",
                "test_end": "also-bad",
                "annual_return": 0.05,
            },
        ]
        timeline, nav, bands = _synthesised_stitched_nav(fold_data)
        # Only fold 1 survives — start/end pair plus one band.
        self.assertEqual(len(timeline), 2)
        self.assertEqual(len(nav), 2)
        self.assertEqual(len(bands), 1)
        self.assertEqual(bands[0][2], 1)
        self.assertAlmostEqual(nav[0], 1.0)
        self.assertGreater(nav[1], 1.0)  # positive return ⇒ NAV grows

    def test_synthesised_nav_compounds_across_folds(self) -> None:
        from web.operator_ui.pages.walk_forward import _synthesised_stitched_nav

        fold_data = [
            {
                "ordinal": 1,
                "test_start": "2024-01-01",
                "test_end": "2024-12-31",
                "annual_return": 0.20,
            },
            {
                "ordinal": 2,
                "test_start": "2025-01-01",
                "test_end": "2025-12-31",
                "annual_return": -0.10,
            },
        ]
        timeline, nav, bands = _synthesised_stitched_nav(fold_data)
        # 4 points (2 per fold), 2 bands.
        self.assertEqual(len(timeline), 4)
        self.assertEqual(len(bands), 2)
        # Fold 1: 1.0 → ~1.20. Fold 2 starts where fold 1 ends, then
        # compounds at -10% over a year → ~1.20 * 0.90 = ~1.08.
        self.assertAlmostEqual(nav[0], 1.0, places=2)
        self.assertAlmostEqual(nav[1], nav[2], places=4)  # continuity
        self.assertLess(nav[3], nav[1])  # negative return shrinks NAV

    def test_synthesised_nav_returns_empty_for_empty_input(self) -> None:
        from web.operator_ui.pages.walk_forward import _synthesised_stitched_nav

        timeline, nav, bands = _synthesised_stitched_nav([])
        self.assertEqual(timeline, [])
        self.assertEqual(nav, [])
        self.assertEqual(bands, [])

    def test_read_log_files_truncates_large_logs(self) -> None:
        import tempfile

        from web.operator_ui.pages.walk_forward import _read_log_files

        with tempfile.TemporaryDirectory() as raw:
            run_dir = Path(raw)
            big_text = "x" * (100 * 1024)  # 100 KiB, well above the 64 KiB cap
            (run_dir / "stdout.log").write_text(big_text, encoding="utf-8")
            (run_dir / "stderr.log").write_text("short log", encoding="utf-8")
            logs = _read_log_files(run_dir)

        log_map = dict(logs)
        self.assertIn("stdout.log", log_map)
        self.assertIn("stderr.log", log_map)
        # Big file SHALL surface the truncation marker, small file SHALL NOT.
        self.assertIn("[truncated", log_map["stdout.log"])
        self.assertNotIn("[truncated", log_map["stderr.log"])
        # And SHALL still be smaller than the original.
        self.assertLess(len(log_map["stdout.log"]), len(big_text))

    def test_read_log_files_returns_empty_for_missing_run_dir(self) -> None:
        from web.operator_ui.pages.walk_forward import _read_log_files

        logs = _read_log_files(Path("/nonexistent/path/that/cannot/exist"))
        self.assertEqual(logs, [])


if __name__ == "__main__":
    unittest.main()
