"""Source-level regression guards for operator UI Results rendering."""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any

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

    def test_results_page_status_header_uses_aria_live(self) -> None:
        """The status badge in the result header SHALL announce updates
        to assistive tech via ``role="status"`` + ``aria-live="polite"``.
        (The "键盘快捷键" expander was removed in UI review P1-3 — it
        documented shortcuts that don't actually fire any handler.)"""

        source = Path("web/operator_ui/pages/results.py").read_text(encoding="utf-8")

        self.assertIn('role="status"', source)
        self.assertIn('aria-live="polite"', source)

    def test_results_page_does_not_advertise_unimplemented_kbd_shortcuts(self) -> None:
        """The legacy "键盘快捷键" expander listed 6 shortcuts (?, j/k,
        r, e, 1-5, /) and immediately disclaimed that none of them
        actually worked — a tombstone disguised as a feature. UI review
        P1-3 deleted it; pin its absence so a well-meaning rewrite
        doesn't add the misleading documentation back without wiring
        the JS event listeners."""

        source = Path("web/operator_ui/pages/results.py").read_text(encoding="utf-8")

        self.assertNotIn('st.expander("键盘快捷键"', source)
        self.assertNotIn("Streamlit 没有暴露全局键盘事件接口", source)

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

    def test_results_page_guards_resolve_run_dir_against_traversal(self) -> None:
        """``_resolve_run_dir`` MUST surface ``guard_output_path`` on
        BOTH ``job.run_dir`` and ``config.output_dir`` branches before
        touching the filesystem. CLI catalog entries reach this function
        unsanitised; without the guard, ``iterdir`` would already act
        as a directory-existence probe against arbitrary paths
        (UI review P1-5)."""

        source = Path("web/operator_ui/pages/results.py").read_text(encoding="utf-8")

        self.assertIn(
            "from web.operator_ui._path_guard import guard_output_path, output_path",
            source,
        )
        self.assertIn("_is_safe_run_dir", source)
        # Both branches of _resolve_run_dir must short-circuit through
        # the helper. We scope the assert to the function body so a
        # stray ``_is_safe_run_dir`` reference elsewhere can't mask a
        # missing guard on one branch.
        func_start = source.index("def _resolve_run_dir(")
        func_end = source.index("def _is_safe_run_dir(")
        body = source[func_start:func_end]
        self.assertGreaterEqual(
            body.count("_is_safe_run_dir("), 2,
            "Both run_dir and output_dir branches must call the guard",
        )

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

    def test_max_depth_constant_is_32(self) -> None:
        """Pin the documented depth cap so a refactor cannot silently
        widen the recursion budget. See ``_FILTER_JSON_MAX_DEPTH``
        comment for the rationale (real reports nest 4-6 deep; 32 leaves
        headroom while staying well below Python's default
        recursion limit and bounding worst-case CPU)."""

        from web.operator_ui.pages.results import _FILTER_JSON_MAX_DEPTH

        self.assertEqual(_FILTER_JSON_MAX_DEPTH, 32)

    def test_caps_recursion_and_prunes_unmatched_branches_below_cap(self) -> None:
        """A 100-deep dict whose only ``needle`` match lives BELOW the cap
        MUST surface as no-result (None / empty). Returning the subtree
        unchanged at the cap (the earlier behaviour) would show the deep
        branch as a fake hit and hand the adversarial structure back to
        Streamlit's serializer — the two failure modes Codex P2 on
        PR #192 flagged.

        Adversarial input or a downstream pipeline producing unexpectedly
        nested structures shouldn't be able to hang the Streamlit session
        with a single query (UI review P1-13)."""

        from web.operator_ui.pages.results import _filter_json_by_query

        # Build a chain ``{"a": {"a": {"a": ... {"a": "needle"}}}}``
        # 100 deep. The only match for ``needle`` is at the leaf,
        # which sits well below _FILTER_JSON_MAX_DEPTH=32.
        depth = 100
        leaf: Any = "needle"
        for _ in range(depth):
            leaf = {"a": leaf}

        result = _filter_json_by_query(leaf, "needle")

        # Cap pruned the deep branch, upstream ``not in (None, {}, [])``
        # check then dropped each ancestor that had nothing else to
        # keep, so the whole filter collapses to None / empty.
        self.assertIn(result, (None, {}))

    def test_caps_recursion_preserves_matches_above_cap(self) -> None:
        """A match that sits ABOVE the cap MUST still be returned. The
        cap is for protection, not blanket filtering."""

        from web.operator_ui.pages.results import _filter_json_by_query

        # ``needle`` lives at depth 3 — well above the cap.
        payload: Any = {"a": {"b": {"needle_here": 1}}}
        for _ in range(50):
            # Pad with deep noise that never matches; the noise gets
            # pruned by the cap + downstream prune, but the shallow
            # match must still survive.
            payload = {"noise": [[[[payload]]]], **payload}

        result = _filter_json_by_query(payload, "needle")
        self.assertIsNotNone(result)

    def test_caps_matched_key_branch_at_depth(self) -> None:
        """When a query matches a shallow KEY, the matched value MUST
        also be truncated at the depth cap. Previously the matched-key
        branch returned the value unchanged, so an artifact like
        ``{"needle": <100-deep-tree>}`` would still hand the deep
        subtree to Streamlit's ``st.json`` and reproduce the exact
        serializer recursion this cap was meant to prevent (Codex P2
        follow-up on PR #192)."""

        from web.operator_ui.pages.results import (
            _FILTER_JSON_MAX_DEPTH,
            _filter_json_by_query,
        )

        # Build ``{"needle": {"x": {"x": ... {"x": "leaf"}}}}`` 50 deep.
        # "needle" matches the top-level key; the value is the deep
        # chain that must NOT pass through unchanged.
        deep_depth = 50
        deep: Any = "leaf"
        for _ in range(deep_depth):
            deep = {"x": deep}

        payload = {"needle": deep}
        result = _filter_json_by_query(payload, "needle")

        # The top-level key "needle" survives.
        self.assertIsInstance(result, dict)
        self.assertIn("needle", result)

        # Walk down the chain; we MUST hit ``None`` (the truncation
        # sentinel) before reaching the 50-deep leaf. Budget consumed
        # so far at the matched-key branch entry: 1 (top dict) → the
        # matched-key call truncates from depth 1, so we can step at
        # most ``_FILTER_JSON_MAX_DEPTH - 1`` x's before truncation.
        cursor: Any = result["needle"]
        steps = 0
        while isinstance(cursor, dict) and "x" in cursor:
            cursor = cursor["x"]
            steps += 1
            if steps > _FILTER_JSON_MAX_DEPTH + 5:
                self.fail(
                    "Walked past the depth cap without hitting truncation"
                )
        self.assertIsNone(
            cursor,
            "Matched-key branch must truncate to None at the depth cap, "
            f"not return the {deep_depth}-deep subtree unchanged",
        )

    def test_recursion_budget_survives_pathological_list(self) -> None:
        """Lists count toward the same depth budget. A deeply nested
        ``[[[...[v]...]]]`` MUST terminate cleanly (no stack overflow,
        no hang) and — since the only ``needle`` lives below the cap —
        prune to a None / empty result."""

        from web.operator_ui.pages.results import _filter_json_by_query

        depth = 80
        leaf: Any = "needle"
        for _ in range(depth):
            leaf = [leaf]

        # Must not stack-overflow or hang. The contract is "cap kicks
        # in and the unmatched-below-cap branch gets pruned" — same
        # honest empty result as the dict case above.
        result = _filter_json_by_query(leaf, "needle")
        self.assertIn(result, (None, []))


@unittest.skipUnless(_HAS_STREAMLIT, "streamlit not installed in this CI cell")
class ResolveRunDirGuardTests(unittest.TestCase):
    """Guard ``_resolve_run_dir`` against path-traversal via CLI catalog
    entries (UI review P1-5).

    UI-launched jobs go through ``JobManager.start`` which forces
    ``run_dir`` under ``RESULT_ROOT``. CLI catalog entries in
    ``output/runs/_index.jsonl`` carry whatever path the CLI wrote with
    no schema validation; without the guard, a crafted entry could
    point at ``..\\Windows\\System32`` and downstream ``iterdir`` here
    would act as a directory-existence probe.
    """

    def test_returns_none_when_run_dir_outside_allowed_roots(self) -> None:
        import tempfile
        from unittest.mock import patch

        from web.operator_ui.pages.results import _resolve_run_dir

        with tempfile.TemporaryDirectory() as allowed, \
             tempfile.TemporaryDirectory() as outside:
            with patch(
                "web.operator_ui._path_guard._ALLOWED_ROOTS",
                (Path(allowed),),
            ):
                job = {"run_dir": str(Path(outside) / "evil")}
                result = _resolve_run_dir(job, {})
        self.assertIsNone(result)

    def test_returns_none_when_config_output_dir_outside_allowed_roots(self) -> None:
        import tempfile
        from unittest.mock import patch

        from web.operator_ui.pages.results import _resolve_run_dir

        with tempfile.TemporaryDirectory() as allowed, \
             tempfile.TemporaryDirectory() as outside:
            with patch(
                "web.operator_ui._path_guard._ALLOWED_ROOTS",
                (Path(allowed),),
            ):
                job = {"status": "completed", "mode": "pipeline"}
                config = {"output_dir": str(Path(outside) / "evil")}
                result = _resolve_run_dir(job, config)
        self.assertIsNone(result)

    def test_does_not_probe_filesystem_when_output_dir_outside_roots(self) -> None:
        """The guard MUST short-circuit BEFORE any ``iterdir`` / ``stat``
        call runs against the suspect path — those calls would already
        leak directory-existence information."""

        import tempfile
        from unittest.mock import patch

        from web.operator_ui.pages.results import _resolve_run_dir

        with tempfile.TemporaryDirectory() as allowed, \
             tempfile.TemporaryDirectory() as outside:
            with patch(
                "web.operator_ui._path_guard._ALLOWED_ROOTS",
                (Path(allowed),),
            ), patch.object(Path, "iterdir") as iterdir_spy, \
               patch.object(Path, "is_dir") as is_dir_spy:
                job = {"status": "completed", "mode": "pipeline"}
                config = {"output_dir": str(Path(outside) / "evil")}
                _resolve_run_dir(job, config)
        iterdir_spy.assert_not_called()
        is_dir_spy.assert_not_called()

    def test_returns_path_when_run_dir_under_allowed_roots(self) -> None:
        """Legitimate paths under the allowed root MUST still resolve;
        the guard is a filter, not a blanket denial."""

        import tempfile
        from unittest.mock import patch

        from web.operator_ui.pages.results import _resolve_run_dir

        with tempfile.TemporaryDirectory() as allowed:
            run_dir = Path(allowed) / "run_abc"
            run_dir.mkdir()
            with patch(
                "web.operator_ui._path_guard._ALLOWED_ROOTS",
                (Path(allowed),),
            ):
                job = {"run_dir": str(run_dir)}
                result = _resolve_run_dir(job, {})
        self.assertEqual(result, run_dir)

    def test_surfaces_legitimate_run_when_newer_symlink_is_unsafe(self) -> None:
        """When ``runs/`` contains BOTH a legitimate non-symlinked run
        AND a newer-mtime symlinked entry pointing outside allowed
        roots, the legitimate one MUST be surfaced. The earlier
        "guard the winner only" implementation returned None whenever
        the newest entry happened to be hostile, hiding valid runs
        from the operator (Codex P2 round 3 on PR #192)."""

        import os
        import tempfile
        import time
        from unittest.mock import patch

        from web.operator_ui.pages.results import _resolve_run_dir

        with tempfile.TemporaryDirectory() as allowed, \
             tempfile.TemporaryDirectory() as outside:
            output_dir = Path(allowed) / "good_output"
            output_dir.mkdir()
            runs_dir = output_dir / "runs"
            runs_dir.mkdir()
            # Legitimate, older run.
            good_run = runs_dir / "good_run"
            good_run.mkdir()
            # Target for the unsafe symlink lives outside roots.
            evil_target = Path(outside) / "evil_target"
            evil_target.mkdir()
            evil_link = runs_dir / "evil_link"
            try:
                os.symlink(evil_target, evil_link, target_is_directory=True)
            except (NotImplementedError, OSError) as exc:
                self.skipTest(f"symlinks unavailable here: {exc}")
            # Make the symlink newer so a "newest wins" lookup would
            # pick it. Touching after symlink creation is sufficient.
            time.sleep(0.01)
            os.utime(evil_link, None, follow_symlinks=False)

            with patch(
                "web.operator_ui._path_guard._ALLOWED_ROOTS",
                (Path(allowed),),
            ):
                job = {"status": "completed", "mode": "pipeline"}
                config = {"output_dir": str(output_dir)}
                result = _resolve_run_dir(job, config)

        self.assertEqual(
            result, good_run,
            "Legitimate non-symlinked run must survive a newer "
            "unsafe-symlink sibling",
        )

    def test_does_not_stat_unsafe_candidates_before_filtering(self) -> None:
        """Each candidate MUST be filtered through ``_is_safe_run_dir``
        BEFORE any ``is_dir`` / ``stat`` call runs on it, since both
        follow symlinks and would otherwise probe attacker-controlled
        targets (Codex P2 round 3 on PR #192).

        Verified by spying on ``Path.is_dir`` and ``Path.stat``:
        the count of calls hitting the evil-link path MUST be zero."""

        import os
        import tempfile
        from unittest.mock import patch

        from web.operator_ui.pages import results as results_module

        with tempfile.TemporaryDirectory() as allowed, \
             tempfile.TemporaryDirectory() as outside:
            output_dir = Path(allowed) / "good_output"
            output_dir.mkdir()
            runs_dir = output_dir / "runs"
            runs_dir.mkdir()
            evil_target = Path(outside) / "evil_target"
            evil_target.mkdir()
            evil_link = runs_dir / "evil_link"
            try:
                os.symlink(evil_target, evil_link, target_is_directory=True)
            except (NotImplementedError, OSError) as exc:
                self.skipTest(f"symlinks unavailable here: {exc}")

            real_is_dir = Path.is_dir
            real_stat = Path.stat
            evil_link_resolved = str(evil_link)
            stat_calls_on_evil: list[str] = []
            is_dir_calls_on_evil: list[str] = []

            def _stat_spy(self: Path, *args: Any, **kwargs: Any) -> Any:
                if str(self) == evil_link_resolved:
                    stat_calls_on_evil.append(str(self))
                return real_stat(self, *args, **kwargs)

            def _is_dir_spy(self: Path) -> bool:
                if str(self) == evil_link_resolved:
                    is_dir_calls_on_evil.append(str(self))
                return real_is_dir(self)

            with patch(
                "web.operator_ui._path_guard._ALLOWED_ROOTS",
                (Path(allowed),),
            ), patch.object(Path, "stat", _stat_spy), \
               patch.object(Path, "is_dir", _is_dir_spy):
                job = {"status": "completed", "mode": "pipeline"}
                config = {"output_dir": str(output_dir)}
                results_module._resolve_run_dir(job, config)

        self.assertEqual(
            is_dir_calls_on_evil, [],
            "is_dir MUST NOT run on the unsafe symlink — it follows "
            "the symlink and probes the foreign directory",
        )
        self.assertEqual(
            stat_calls_on_evil, [],
            "stat MUST NOT run on the unsafe symlink",
        )

    def test_rejects_symlink_runs_subdirectory_pointing_outside_roots(self) -> None:
        """``output_dir`` itself can pass ``guard_output_path`` while its
        ``runs/`` child is a symlink to ``/tmp/outside``. Without
        re-guarding the derived path, ``runs.is_dir()`` follows the
        symlink and ``iterdir()`` enumerates the foreign directory
        (Codex P2 follow-up on PR #192). This test creates the exact
        attack shape and asserts ``_resolve_run_dir`` returns None."""

        import os
        import tempfile
        from unittest.mock import patch

        from web.operator_ui.pages.results import _resolve_run_dir

        with tempfile.TemporaryDirectory() as allowed, \
             tempfile.TemporaryDirectory() as outside:
            output_dir = Path(allowed) / "good_output"
            output_dir.mkdir()
            # Populate ``outside`` with a fake run so iterdir would have
            # something to return if the guard were bypassed.
            evil_run = Path(outside) / "evil_run"
            evil_run.mkdir()
            try:
                os.symlink(
                    outside,
                    output_dir / "runs",
                    target_is_directory=True,
                )
            except (NotImplementedError, OSError) as exc:
                # Windows requires admin / developer mode for symlinks;
                # POSIX rarely refuses but we treat this as a platform
                # capability gate rather than a test failure.
                self.skipTest(f"symlinks unavailable here: {exc}")

            with patch(
                "web.operator_ui._path_guard._ALLOWED_ROOTS",
                (Path(allowed),),
            ):
                job = {"status": "completed", "mode": "pipeline"}
                config = {"output_dir": str(output_dir)}
                result = _resolve_run_dir(job, config)

        self.assertIsNone(
            result,
            "runs/ symlink to a path outside allowed roots must be "
            "rejected, not returned as a probe-able run directory",
        )

    def test_logs_warning_when_path_rejected(self) -> None:
        """Rejected paths SHALL be WARN-logged so the audit trail
        captures the suspect input even though the operator only sees
        an empty state downstream."""

        import tempfile
        from unittest.mock import patch

        from web.operator_ui.pages import results as results_module

        with tempfile.TemporaryDirectory() as allowed, \
             tempfile.TemporaryDirectory() as outside, \
             self.assertLogs(
                 results_module._log.name, level="WARNING"
             ) as captured_logs:
            with patch(
                "web.operator_ui._path_guard._ALLOWED_ROOTS",
                (Path(allowed),),
            ):
                job = {"run_dir": str(Path(outside) / "evil")}
                results_module._resolve_run_dir(job, {})
        # Single rejected path → one warning record. Path must appear
        # in the log message so forensics can chase the source.
        self.assertEqual(len(captured_logs.records), 1)
        self.assertIn("evil", captured_logs.records[0].getMessage())
        self.assertIn(
            "Refusing to resolve run_dir",
            captured_logs.records[0].getMessage(),
        )


if __name__ == "__main__":
    unittest.main()
