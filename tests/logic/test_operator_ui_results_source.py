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
        self.assertIn("Tushare 数据源产物", source)
        self.assertIn("inspect_provider_metadata(str(run_dir))", source)
        self.assertIn("metadata.validation_path", source)
        self.assertIn("metadata.manifest_path", source)

    def test_results_page_keeps_provider_jobs_read_only(self) -> None:
        source = Path("web/operator_ui/pages/results.py").read_text(encoding="utf-8")

        self.assertIn("Tushare 数据源作业产出的是 qlib 数据包", source)
        self.assertNotIn("Pipeline(", source)
        self.assertNotIn("WalkForwardEngine(", source)

    def test_results_page_renders_pipeline_detail_sections(self) -> None:
        source = Path("web/operator_ui/pages/results.py").read_text(encoding="utf-8")

        self.assertIn("流水线结果", source)
        self.assertIn('"下载 config.yaml"', source)
        self.assertIn('"持仓"', source)
        self.assertIn('"交易"', source)
        self.assertIn('"配置"', source)
        self.assertIn('"阶段耗时"', source)
        self.assertIn('"日志"', source)
        self.assertIn('"原始 JSON"', source)

    def test_results_page_keeps_pipeline_metrics_artifact_sourced(self) -> None:
        source = Path("web/operator_ui/pages/results.py").read_text(encoding="utf-8")

        self.assertIn('artifact_name="pipeline_report.json"', source)
        self.assertIn('run_dir / "metrics.json"', source)
        self.assertIn('run_dir / "nav.parquet"', source)
        self.assertIn('run_dir / "holdings.parquet"', source)
        self.assertIn('run_dir / "trades.parquet"', source)
        self.assertIn("pipeline_report.json 暂不可用", source)
        self.assertIn("尚未发现已生成的 PNG 图表", source)
        self.assertNotIn("risk_analysis(", source)
        self.assertNotIn("PerformanceAttribution", source)
        self.assertNotIn("SignalAnalyzer", source)

    def test_results_page_surfaces_artifact_read_issues(self) -> None:
        source = Path("web/operator_ui/pages/results.py").read_text(encoding="utf-8")

        self.assertIn("ArtifactReadIssue", source)
        self.assertIn("产物读取问题", source)
        self.assertIn("_render_artifact_issues(issues)", source)

    def test_results_page_prefers_structured_artifacts_with_legacy_fallbacks(self) -> None:
        source = Path("web/operator_ui/pages/results.py").read_text(encoding="utf-8")

        self.assertIn("_read_holdings_frame(run_dir, issues)", source)
        self.assertIn("_read_trades_frame(run_dir, issues)", source)
        self.assertIn("_read_positions(run_dir, issues)", source)
        self.assertIn("trades.parquet 文件存在", source)

    def test_results_page_supports_run_id_and_interactive_nav(self) -> None:
        source = Path("web/operator_ui/pages/results.py").read_text(encoding="utf-8")
        jobs_source = Path("web/operator_ui/pages/jobs.py").read_text(encoding="utf-8")

        self.assertIn('st.query_params.get("run_id"', source)
        self.assertIn("运行未找到", source)
        self.assertIn("html.escape(run_id", source)
        self.assertIn("plotly.graph_objects", source)
        self.assertIn("策略净值", source)
        self.assertIn("策略回撤", source)
        self.assertIn("月度收益", source)
        self.assertIn("加载更多", jobs_source)
        self.assertIn("list_all_jobs", jobs_source)

    def test_results_empty_state_uses_streamlit_navigation(self) -> None:
        source = Path("web/operator_ui/pages/results.py").read_text(encoding="utf-8")

        self.assertIn('st.button("配置运行")', source)
        self.assertIn('st.switch_page("pages/config_run.py")', source)
        self.assertNotIn("window.location.href", source)

    def test_results_page_exposes_export_and_rerun_actions(self) -> None:
        source = Path("web/operator_ui/pages/results.py").read_text(encoding="utf-8")

        self.assertIn("用此配置重跑", source)
        self.assertIn("prefill_config_yaml", source)
        self.assertIn("导出指标 CSV", source)
        self.assertIn("导出 PDF 报告", source)
        self.assertIn("导出完整压缩包", source)
        self.assertIn("metrics_csv_bytes(metrics)", source)
        self.assertIn("summary_pdf_bytes(", source)
        self.assertIn("bundle_zip_bytes(run_dir)", source)

    def test_results_page_exposes_holdings_and_trades_filters(self) -> None:
        source = Path("web/operator_ui/pages/results.py").read_text(encoding="utf-8")

        self.assertIn("搜索持仓", source)
        self.assertIn("显示前 N 大持仓", source)
        self.assertIn("导出持仓 CSV", source)
        self.assertIn("交易日期", source)
        self.assertIn("方向", source)
        self.assertIn("搜索交易", source)
        self.assertIn("导出交易 CSV", source)

    def test_results_page_exposes_accessible_status_and_shortcut_help(self) -> None:
        source = Path("web/operator_ui/pages/results.py").read_text(encoding="utf-8")

        self.assertIn('role="status"', source)
        self.assertIn('aria-live="polite"', source)
        self.assertIn("键盘快捷键", source)
        self.assertIn("Streamlit 没有暴露全局键盘事件接口", source)

    def test_results_page_exposes_polished_header_navigation(self) -> None:
        source = Path("web/operator_ui/pages/results.py").read_text(encoding="utf-8")

        self.assertIn("position: sticky", source)
        self.assertIn("返回作业列表", source)
        self.assertIn("运行 ID（可复制）", source)
        self.assertIn("运行目录（可复制）", source)
        self.assertIn('pages/jobs.py', source)

    def test_results_page_uses_shared_nav_drawdown_time_range(self) -> None:
        source = Path("web/operator_ui/pages/results.py").read_text(encoding="utf-8")

        self.assertIn("TIME_RANGE_OPTIONS", source)
        self.assertIn("显示时间范围", source)
        self.assertIn("filter_nav_frame_by_range(nav_frame, range_label)", source)
        self.assertIn("nav_y_range(frame)", source)

    def test_results_page_renders_monthly_heatmap_and_log_filters(self) -> None:
        source = Path("web/operator_ui/pages/results.py").read_text(encoding="utf-8")

        self.assertIn("go.Heatmap(", source)
        self.assertIn("月度热力图暂不可用", source)
        self.assertIn("搜索日志", source)
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
        self.assertIn("搜索原始 JSON", source)
        self.assertIn("_filter_json_by_query", source)
        self.assertIn("results_raw_json_query", source)

    def test_results_page_handles_bundle_too_large_with_filesystem_hint(self) -> None:
        """When ``bundle_zip_bytes`` rejects an oversize run, the page MUST
        surface the size + the filesystem path so the operator can package
        the run by hand instead of seeing a silently-disabled button.

        See ``BundleTooLargeError`` in ``web/operator_ui/result_exports.py``
        for why we cap at 500 MiB (1-5 GiB pipeline runs would OOM the
        Streamlit server)."""

        source = Path("web/operator_ui/pages/results.py").read_text(encoding="utf-8")

        self.assertIn("BundleTooLargeError", source)
        self.assertIn("except BundleTooLargeError", source)
        # The hint must mention the filesystem path so operators know what
        # to package manually.
        self.assertIn("exc.run_dir", source)
        # The hint must be surfaced both inline (caption under the button)
        # and via the button's help tooltip.
        self.assertIn("st.caption(bundle_too_large_message)", source)

    def test_results_page_auto_refresh_is_opt_in_with_default_off(self) -> None:
        """Running-job auto-refresh SHALL be a default-OFF checkbox toggle,
        not an unconditional ``time.sleep(5) + st.rerun()`` (UI review P0-1).

        The previous implementation locked the operator out of the page
        for the entire duration of a running job (1-8 hours for a typical
        pipeline) — reading logs, scrolling charts, copying IDs all got
        eaten by the next forced rerun. Mirrors the toggle pattern from
        ``jobs.py:543-553`` so both surfaces behave the same way."""

        source = Path("web/operator_ui/pages/results.py").read_text(encoding="utf-8")

        # Toggle widget must exist with a stable key.
        self.assertIn('key="results_autorefresh"', source)
        # The sleep + rerun MUST be guarded by the checkbox value.
        self.assertIn("if results_auto_refresh:", source)
        # User-visible label mentioning the 5-second cadence.
        self.assertIn("每 5 秒自动刷新", source)
        # Belt-and-braces: the running-status branch MUST instantiate the
        # checkbox with ``value=False`` (default off). We scope the assert
        # to the lines around ``results_autorefresh`` so an unrelated
        # ``value=False`` elsewhere in the file cannot mask a regression.
        idx = source.index('key="results_autorefresh"')
        # 400 chars of context covers the st.checkbox(...) call site.
        window = source[max(0, idx - 400): idx + 200]
        self.assertIn("value=False", window)

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
