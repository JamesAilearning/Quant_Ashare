from __future__ import annotations

from pathlib import Path

from web.operator_ui._param_guard import sanitize


def test_comparison_url_parameter_accepts_bounded_distinct_run_ids() -> None:
    assert sanitize("run_ids", "a,b.c,d-1") == "a,b.c,d-1"
    assert sanitize("run_ids", "a,a", default="") == ""
    assert sanitize("run_ids", "a,b,c,d,e,f", default="") == ""
    assert sanitize("run_ids", "../escape,b", default="") == ""


def test_comparison_page_is_read_only_and_uses_guarded_catalog_artifacts() -> None:
    source = Path("web/operator_ui/pages/research_run_comparison.py").read_text(encoding="utf-8")

    assert "load_all_jobs_read_only" in source
    assert "from web.operator_ui.job_io import load_all_jobs\n" not in source
    assert "guard_output_path" in source
    assert "st.multiselect" in source
    assert 'st.query_params["run_ids"]' in source
    assert "max_selections=5" in source
    assert 'query_params={"run_id": run.run_id}' in source
    assert '("logs", "pipeline.log")' in source
    assert '("logs", "stdout.log")' in source
    assert '("logs", "stderr.log")' in source
    assert '_UI_JOB_LOG_ROOT' in source
    assert 'parents[3] / "output" / "operator_ui" / "jobs"' in source
    assert 'if job.source != "ui":' in source
    assert 'guard_output_path(job_dir)' in source
    assert '("runner_stdout.log",)' in source
    assert '("runner_stderr.log",)' in source
    assert "JobManager.start" not in source
    assert "st.switch_page" not in source


def test_unknown_url_run_ids_block_comparison_without_rewriting_the_request() -> None:
    source = Path("web/operator_ui/pages/research_run_comparison.py").read_text(encoding="utf-8")

    unknown_block = source[source.index("if unknown_requested:") : source.index("selected_ids =")]
    assert "无法按请求的完整选择进行对比" in unknown_block
    assert "st.stop()" in unknown_block


def test_alias_collapsed_url_run_ids_block_comparison_without_rewriting() -> None:
    source = Path("web/operator_ui/pages/research_run_comparison.py").read_text(encoding="utf-8")

    duplicate_block = source[
        source.index("if duplicate_resolved:") : source.index("selected_ids =")
    ]
    assert "指向同一份当前工件" in duplicate_block
    assert "st.stop()" in duplicate_block


def test_research_navigation_exposes_comparison_workbench() -> None:
    app_source = Path("web/operator_ui/app.py").read_text(encoding="utf-8")
    workbench_source = Path("web/operator_ui/pages/today_workbench.py").read_text(encoding="utf-8")

    assert 'research_run_comparison.py' in app_source
    assert 'title="研究运行对比"' in app_source
    assert 'research_run_comparison.py' in workbench_source
