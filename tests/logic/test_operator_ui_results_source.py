"""Source-level regression guards for operator UI Results rendering."""

from __future__ import annotations

import unittest
from pathlib import Path


class ResultsPageSourceTests(unittest.TestCase):
    def test_results_page_displays_tushare_provider_artifacts(self) -> None:
        source = Path("web/operator_ui/pages/results.py").read_text(encoding="utf-8")

        self.assertIn('mode == "tushare_provider"', source)
        self.assertIn('"Tushare Provider Data"', source)
        self.assertIn("inspect_provider_metadata(str(run_dir))", source)
        self.assertIn("metadata.validation_path", source)
        self.assertIn("metadata.manifest_path", source)

    def test_results_page_keeps_provider_jobs_read_only(self) -> None:
        source = Path("web/operator_ui/pages/results.py").read_text(encoding="utf-8")

        self.assertIn("provider jobs create qlib data bundles", source)
        self.assertNotIn("Pipeline(", source)
        self.assertNotIn("WalkForwardEngine(", source)

    def test_results_page_renders_pipeline_detail_sections(self) -> None:
        source = Path("web/operator_ui/pages/results.py").read_text(encoding="utf-8")

        self.assertIn("Pipeline Result", source)
        self.assertIn('"Download config.yaml"', source)
        self.assertIn('"Holdings"', source)
        self.assertIn('"Trades"', source)
        self.assertIn('"Config"', source)
        self.assertIn('"Stage Timings"', source)
        self.assertIn('"Logs"', source)
        self.assertIn('"Raw JSON"', source)

    def test_results_page_keeps_pipeline_metrics_artifact_sourced(self) -> None:
        source = Path("web/operator_ui/pages/results.py").read_text(encoding="utf-8")

        self.assertIn("read_pipeline_report(run_dir)", source)
        self.assertIn('run_dir / "metrics.json"', source)
        self.assertIn('run_dir / "holdings.parquet"', source)
        self.assertIn('run_dir / "trades.parquet"', source)
        self.assertIn("pipeline_report.json is not available yet", source)
        self.assertIn("No generated PNG charts found yet", source)
        self.assertNotIn("risk_analysis(", source)
        self.assertNotIn("PerformanceAttribution", source)
        self.assertNotIn("SignalAnalyzer", source)

    def test_results_page_prefers_structured_artifacts_with_legacy_fallbacks(self) -> None:
        source = Path("web/operator_ui/pages/results.py").read_text(encoding="utf-8")

        self.assertIn("_read_holdings_frame(run_dir)", source)
        self.assertIn("_read_trades_frame(run_dir)", source)
        self.assertIn("_read_positions(run_dir)", source)
        self.assertIn("trades.parquet exists", source)


if __name__ == "__main__":
    unittest.main()
