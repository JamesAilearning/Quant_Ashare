"""Source-level regression guards for operator UI Results rendering."""

from __future__ import annotations

import unittest
from pathlib import Path

try:
    import streamlit as _streamlit  # noqa: F401

    _HAS_STREAMLIT = True
except ImportError:
    _HAS_STREAMLIT = False


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
        jobs_source = Path("web/operator_ui/pages/jobs.py").read_text(encoding="utf-8")

        self.assertIn('st.query_params.get("run_id"', source)
        self.assertIn("Run not found", source)
        self.assertIn("html.escape(run_id", source)
        self.assertIn("plotly.graph_objects", source)
        self.assertIn("Strategy NAV", source)
        self.assertIn("Strategy Drawdown", source)
        self.assertIn("Monthly Returns", source)
        self.assertIn("Load more", jobs_source)
        self.assertIn("list_all_jobs", jobs_source)

    def test_results_empty_state_uses_streamlit_navigation(self) -> None:
        source = Path("web/operator_ui/pages/results.py").read_text(encoding="utf-8")

        self.assertIn('st.button("Config & Run")', source)
        self.assertIn('st.switch_page("pages/config_run.py")', source)
        self.assertNotIn("window.location.href", source)

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
        self.assertIn('pages/jobs.py', source)

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

    def test_plotly_trace_colors_use_valid_literals_not_css_variables(self) -> None:
        source = Path("web/operator_ui/pages/results.py").read_text(encoding="utf-8")

        self.assertIn("PLOTLY_STRATEGY_COLOR", source)
        self.assertIn("PLOTLY_BENCHMARK_COLOR", source)
        self.assertIn("PLOTLY_DRAWDOWN_COLOR", source)
        self.assertNotIn('"color": "var(--', source)
        self.assertNotIn('[0.0, "var(--', source)

    def test_results_page_does_not_let_stale_run_metadata_mask_job_failure(self) -> None:
        source = Path("web/operator_ui/pages/results.py").read_text(encoding="utf-8")

        self.assertIn('job_status not in {"success", "completed", "ok"}', source)
        self.assertIn('status = _fmt_text(job.get("status") or metadata.get("status"))', source)
        self.assertIn('started = _fmt_text(job.get("started_at") or metadata.get("started_at"))', source)
        self.assertIn('ended = _fmt_text(job.get("ended_at") or metadata.get("finished_at"))', source)
        self.assertIn('if str(job.get("status") or status).lower() == "failed":', source)

    def test_raw_json_tab_offers_substring_search(self) -> None:
        """The Raw JSON tab SHALL surface a search input that narrows
        each expander's payload (TICKET-R3 polish)."""

        source = Path("web/operator_ui/pages/results.py").read_text(encoding="utf-8")
        self.assertIn("Search Raw JSON", source)
        self.assertIn("_filter_json_by_query", source)
        self.assertIn("results_raw_json_query", source)

    def test_status_header_offers_one_click_copy_buttons(self) -> None:
        """The status header SHALL render one-click 📋 Copy buttons next
        to Run ID and Run directory (TICKET-R3 polish)."""

        source = Path("web/operator_ui/pages/results.py").read_text(encoding="utf-8")
        self.assertIn('copy_run_id_btn_', source)
        self.assertIn('copy_run_dir_btn_', source)
        self.assertIn('st.toast(', source)
        self.assertIn('results_clipboard_payload', source)


@unittest.skipUnless(_HAS_STREAMLIT, "streamlit not installed in this CI cell")
class FilterJsonByQueryTests(unittest.TestCase):
    def test_empty_query_returns_object_unchanged(self) -> None:
        from web.operator_ui.pages.results import _filter_json_by_query

        payload = {"a": 1, "b": {"c": 2}}
        self.assertEqual(_filter_json_by_query(payload, ""), payload)

    def test_matches_dict_key(self) -> None:
        from web.operator_ui.pages.results import _filter_json_by_query

        payload = {"sharpe_ratio": 1.5, "max_drawdown": -0.12, "annual": 0.20}
        result = _filter_json_by_query(payload, "sharpe")
        self.assertEqual(result, {"sharpe_ratio": 1.5})

    def test_matches_nested_dict(self) -> None:
        from web.operator_ui.pages.results import _filter_json_by_query

        payload = {
            "metrics": {"sharpe": 1.5, "ic_1d": 0.04},
            "config": {"model": "LGB"},
        }
        result = _filter_json_by_query(payload, "sharpe")
        self.assertEqual(result, {"metrics": {"sharpe": 1.5}})

    def test_matches_scalar_value(self) -> None:
        from web.operator_ui.pages.results import _filter_json_by_query

        # Only entries whose key OR value contain the query (substring,
        # case-insensitive) survive. "LGB" is a substring of "LGBModel"
        # but NOT of "lightgbm" — engine is therefore pruned.
        payload = {"model_type": "LGBModel", "engine": "lightgbm"}
        result = _filter_json_by_query(payload, "LGB")
        self.assertEqual(result, {"model_type": "LGBModel"})

    def test_no_match_returns_none(self) -> None:
        from web.operator_ui.pages.results import _filter_json_by_query

        payload = {"a": 1, "b": "hello"}
        result = _filter_json_by_query(payload, "zzzz")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
