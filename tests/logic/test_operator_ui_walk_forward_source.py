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


if __name__ == "__main__":
    unittest.main()
