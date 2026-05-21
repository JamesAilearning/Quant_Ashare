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

        self.assertIn('artifact_name="pipeline_report.json"', source)
        self.assertIn('run_dir / "metrics.json"', source)
        self.assertIn('run_dir / "nav.parquet"', source)
        self.assertIn('run_dir / "holdings.parquet"', source)
        self.assertIn('run_dir / "trades.parquet"', source)
        self.assertIn("pipeline_report.json is not available yet", source)
        self.assertIn("No generated PNG charts found yet", source)
        self.assertNotIn("risk_analysis(", source)
        self.assertNotIn("PerformanceAttribution", source)
        self.assertNotIn("SignalAnalyzer", source)

    def test_results_page_surfaces_artifact_read_issues(self) -> None:
        source = Path("web/operator_ui/pages/results.py").read_text(encoding="utf-8")

        self.assertIn("ArtifactReadIssue", source)
        self.assertIn("Artifact Read Issues", source)
        self.assertIn("_render_artifact_issues(issues)", source)

    def test_results_page_prefers_structured_artifacts_with_legacy_fallbacks(self) -> None:
        source = Path("web/operator_ui/pages/results.py").read_text(encoding="utf-8")

        self.assertIn("_read_holdings_frame(run_dir, issues)", source)
        self.assertIn("_read_trades_frame(run_dir, issues)", source)
        self.assertIn("_read_positions(run_dir, issues)", source)
        self.assertIn("trades.parquet exists", source)

    def test_results_page_supports_run_id_and_interactive_nav(self) -> None:
        source = Path("web/operator_ui/pages/results.py").read_text(encoding="utf-8")
        history_source = Path("web/operator_ui/pages/run_history.py").read_text(encoding="utf-8")

        self.assertIn('st.query_params.get("run_id"', source)
        self.assertIn("Run not found", source)
        self.assertIn("plotly.graph_objects", source)
        self.assertIn("Strategy NAV", source)
        self.assertIn("Strategy Drawdown", source)
        self.assertIn("Monthly Returns", source)
        self.assertIn('st.query_params["run_id"]', history_source)
        self.assertIn('st.switch_page(str(_PAGES_DIR / "results.py"))', history_source)

    def test_results_page_exposes_export_and_rerun_actions(self) -> None:
        source = Path("web/operator_ui/pages/results.py").read_text(encoding="utf-8")

        self.assertIn("Re-run with this config", source)
        self.assertIn("prefill_config_yaml", source)
        self.assertIn("Export metrics CSV", source)
        self.assertIn("Export PDF report", source)
        self.assertIn("Export full bundle", source)
        self.assertIn("metrics_csv_bytes(metrics)", source)
        self.assertIn("summary_pdf_bytes(", source)
        self.assertIn("bundle_zip_bytes(run_dir)", source)

    def test_results_page_exposes_holdings_and_trades_filters(self) -> None:
        source = Path("web/operator_ui/pages/results.py").read_text(encoding="utf-8")

        self.assertIn("Search holdings", source)
        self.assertIn("Show top holdings", source)
        self.assertIn("Export holdings CSV", source)
        self.assertIn("Trade dates", source)
        self.assertIn("Side", source)
        self.assertIn("Search trades", source)
        self.assertIn("Export trades CSV", source)

    def test_results_page_exposes_accessible_status_and_shortcut_help(self) -> None:
        source = Path("web/operator_ui/pages/results.py").read_text(encoding="utf-8")

        self.assertIn('role="status"', source)
        self.assertIn('aria-live="polite"', source)
        self.assertIn("Keyboard shortcuts", source)
        self.assertIn("Streamlit does not expose global key handlers", source)

    def test_results_page_exposes_polished_header_navigation(self) -> None:
        source = Path("web/operator_ui/pages/results.py").read_text(encoding="utf-8")

        self.assertIn("position: sticky", source)
        self.assertIn("Back to Jobs", source)
        self.assertIn("Run ID (copyable)", source)
        self.assertIn("Run directory (copyable)", source)
        self.assertIn('st.switch_page(str(Path(__file__).resolve().parent / "run_history.py"))', source)

    def test_results_page_uses_shared_nav_drawdown_time_range(self) -> None:
        source = Path("web/operator_ui/pages/results.py").read_text(encoding="utf-8")

        self.assertIn("TIME_RANGE_OPTIONS", source)
        self.assertIn("Displayed time range", source)
        self.assertIn("filter_nav_frame_by_range(nav_frame, range_label)", source)
        self.assertIn("nav_y_range(frame)", source)

    def test_results_page_renders_monthly_heatmap_and_log_filters(self) -> None:
        source = Path("web/operator_ui/pages/results.py").read_text(encoding="utf-8")

        self.assertIn("go.Heatmap(", source)
        self.assertIn("Monthly heatmap is unavailable", source)
        self.assertIn("Search logs", source)
        self.assertIn("LOG_LEVEL_OPTIONS", source)
        self.assertIn("filter_log_text(text, search=search, levels=levels)", source)

    def test_results_page_does_not_let_stale_run_metadata_mask_job_failure(self) -> None:
        source = Path("web/operator_ui/pages/results.py").read_text(encoding="utf-8")

        self.assertIn('job_status not in {"success", "completed", "ok"}', source)
        self.assertIn('status = _fmt_text(job.get("status") or metadata.get("status"))', source)
        self.assertIn('started = _fmt_text(job.get("started_at") or metadata.get("started_at"))', source)
        self.assertIn('ended = _fmt_text(job.get("ended_at") or metadata.get("finished_at"))', source)
        self.assertIn('if str(job.get("status") or status).lower() == "failed":', source)


if __name__ == "__main__":
    unittest.main()
