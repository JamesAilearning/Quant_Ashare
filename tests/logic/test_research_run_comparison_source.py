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
    assert "JobManager.start" not in source
    assert "st.switch_page" not in source


def test_research_navigation_exposes_comparison_workbench() -> None:
    app_source = Path("web/operator_ui/app.py").read_text(encoding="utf-8")
    workbench_source = Path("web/operator_ui/pages/today_workbench.py").read_text(encoding="utf-8")

    assert 'research_run_comparison.py' in app_source
    assert 'title="研究运行对比"' in app_source
    assert 'research_run_comparison.py' in workbench_source
