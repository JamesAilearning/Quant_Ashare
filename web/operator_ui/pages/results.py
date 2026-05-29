"""Results page entry — read-only dashboard for pipeline / walk-forward /
Tushare-provider artifacts.

The page implementation is split across three modules (UI review P1-1):

* ``pages/_results_helpers.py`` — pure helpers (artifact reading, format,
  status, JSON depth-cap, chart / frame readers, path safety). No
  Streamlit imports at module body.
* ``pages/_results_render.py`` — Streamlit-dispatching render functions
  (status header, KPI cards, charts, tabs, dashboards, run-not-found).
* ``pages/results.py`` (this file) — re-exports the helpers / render
  surface for tests + the module-level page dispatch.

Re-exports are deliberately broad so legacy test fixtures importing
``from web.operator_ui.pages.results import _filter_json_by_query``
(and friends) keep working unchanged.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from web.operator_ui.artifact_reader import ArtifactReadIssue
from web.operator_ui.components import render_empty_state
from web.operator_ui.job_manager import JobManager
from web.operator_ui.page_header import render_page_header

# Re-export pure helpers for the test surface. ``noqa: F401`` because
# the names are re-exported, not consumed in this module body.
from web.operator_ui.pages._results_helpers import (  # noqa: F401
    _FILTER_JSON_MAX_DEPTH,
    LOG_NAMES,
    MISSING,
    PLOTLY_BENCHMARK_COLOR,
    PLOTLY_DRAWDOWN_COLOR,
    PLOTLY_NEGATIVE_COLOR,
    PLOTLY_NEUTRAL_COLOR,
    PLOTLY_POSITIVE_COLOR,
    PLOTLY_STRATEGY_COLOR,
    _chart_by_token,
    _default_job_id,
    _filter_json_by_query,
    _finite_float,
    _first,
    _fmt_duration,
    _fmt_int,
    _fmt_number,
    _fmt_percent,
    _fmt_text,
    _is_safe_run_dir,
    _job_dir,
    _job_label,
    _log,
    _metric_color,
    _nested,
    _path_or_none,
    _read_bytes_artifact,
    _read_config,
    _read_holdings_frame,
    _read_json_artifact,
    _read_metadata,
    _read_metrics,
    _read_nav_frame,
    _read_parquet_artifact,
    _read_positions,
    _read_text_artifact,
    _read_trades_frame,
    _record_issue,
    _resolve_run_dir,
    _safe_html,
    _status_badge_variant,
    _truncate_for_st_json,
)

# Re-export render-side names + import the ones the module-level dispatch
# below actually invokes. ``noqa: F401`` for names only used by external
# tests (not consumed here).
from web.operator_ui.pages._results_render import (  # noqa: F401
    _query_run_id,
    _render_artifact_issues,
    _render_card,
    _render_charts,
    _render_config_tab,
    _render_header_actions,
    _render_holdings_tab,
    _render_interactive_charts,
    _render_kpis,
    _render_logs_tab,
    _render_monthly_returns,
    _render_pipeline_dashboard,
    _render_raw_tab,
    _render_run_not_found,
    _render_status_header,
    _render_timings_tab,
    _render_trades_tab,
    _render_tushare_provider,
    _render_walk_forward_summary,
)

render_page_header("结果", "查看流水线、滚动验证及数据源运行的产物。")
# FU-8 bundle freshness banner. **Bound to the SELECTED run's bundle**,
# not the project-default — Codex P1 on PR #169 surfaced that
# rendering with ``provider_uri=None`` here would show
# ``config.yaml``'s bundle even when the operator is inspecting a
# historical run that used a different one. The banner is rendered
# AFTER the run-selection block below (see ``render_bundle_health_banner``
# call following ``_read_config``); a future "no run selected"
# fallback could render the default at the top, but the current
# results page always has a default selected job.
from web.operator_ui.bundle_health import (  # noqa: E402, PLC0415
    render_bundle_health_banner,
)

# Detect current theme for Plotly charts
theme_detect_script = """
<script>
(function() {
  var root = window.parent.document.documentElement;
  var theme = root.getAttribute('data-qv2-theme') || 'auto';
  if (theme === 'auto' && window.matchMedia) {
    theme = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  window._qv2_resolved_theme = theme;
})();
</script>
"""
st.html(theme_detect_script, width="content", unsafe_allow_javascript=True)

jobs = JobManager.list_jobs()
viewable_jobs = [
    job for job in jobs
    if str(job.get("mode") or "") in {"pipeline", "walk_forward", "tushare_provider"}
]

if not viewable_jobs:
    render_empty_state(
        "📄",
        "暂无可查看的运行",
        "请先运行流水线、滚动验证或 Tushare 数据源作业。",
    )
    if st.button("配置运行"):
        st.switch_page("pages/config_run.py")
else:
    job_ids = [str(job.get("job_id")) for job in viewable_jobs if job.get("job_id")]
    requested_run_id = _query_run_id()
    if requested_run_id and requested_run_id not in job_ids:
        _render_run_not_found(requested_run_id)
        st.stop()
    default_job_id = requested_run_id or _default_job_id(viewable_jobs)
    default_index = job_ids.index(default_job_id) if default_job_id in job_ids else 0
    selected_job_id = st.selectbox(
        "运行",
        options=job_ids,
        index=default_index,
        format_func=lambda value: _job_label(
            next((job for job in viewable_jobs if str(job.get("job_id")) == value), {})
        ),
    )
    if selected_job_id and selected_job_id != requested_run_id:
        st.query_params["run_id"] = selected_job_id
    selected_job = next(
        (job for job in viewable_jobs if str(job.get("job_id")) == selected_job_id),
        viewable_jobs[0],
    )

    artifact_issues: list[ArtifactReadIssue] = []
    config, config_path, config_bytes = _read_config(selected_job, artifact_issues)
    run_dir = _resolve_run_dir(selected_job, config)
    mode = str(selected_job.get("mode") or "")

    # FU-8 banner bound to the selected run's bundle (Codex P1 on
    # PR #169). ``config.provider_uri`` is the value the training
    # actually saw; this is the right number to surface for
    # results-page investigation. ``provider_uri or None`` falls
    # back to the project-default lookup when the run's config
    # didn't capture a provider_uri (rare — running jobs / stub
    # configs in tests).
    render_bundle_health_banner(
        provider_uri=str(config.get("provider_uri") or "") or None,
        st=st,
    )

    # Auto-refresh for running jobs — default OFF so the operator can
    # read logs / scroll charts / copy IDs without being interrupted by
    # a forced rerun every 5 seconds. The previous implementation slept
    # + rerun()ed unconditionally, which made the page unusable for the
    # 1-8 hours a typical pipeline takes. Pattern mirrors the toggle on
    # jobs.py:543-553 so both surfaces behave the same way.
    if str(selected_job.get("status", "")).lower() == "running":
        results_auto_refresh = st.checkbox(
            "作业仍在运行 · 每 5 秒自动刷新",
            value=False,
            key="results_autorefresh",
            help=(
                "勾选后页面每 5 秒自动刷新一次，会打断当前的滚动 / 复制 / "
                "搜索操作。默认关闭。"
            ),
        )
        if results_auto_refresh:
            import time as _time
            _time.sleep(5)
            st.rerun()

    if mode == "tushare_provider":
        _render_tushare_provider(run_dir, artifact_issues)
    else:
        pipeline_report = (
            _read_json_artifact(
                run_dir / "pipeline_report.json",
                artifact_issues,
                artifact_name="pipeline_report.json",
            )
            if run_dir is not None
            else {}
        )
        wf_report: dict[str, Any] = (
            _read_json_artifact(
                run_dir / "walk_forward_report.json",
                artifact_issues,
                artifact_name="walk_forward_report.json",
            )
            if run_dir is not None
            else {}
        )

        if mode == "pipeline" or pipeline_report:
            _render_pipeline_dashboard(
                job=selected_job,
                run_dir=run_dir,
                report=pipeline_report,
                config=config,
                config_path=config_path,
                config_bytes=config_bytes,
                issues=artifact_issues,
            )
        elif mode == "walk_forward" or wf_report:
            if wf_report:
                _render_artifact_issues(artifact_issues)
                _render_walk_forward_summary(wf_report)
                _render_charts(run_dir)
            else:
                _render_artifact_issues(artifact_issues)
                st.warning("此运行目录里还没有 walk_forward_report.json。")
                _render_config_tab(config_path, config_bytes, config)
                _render_logs_tab(selected_job, artifact_issues)
        else:
            _render_artifact_issues(artifact_issues)
            st.warning("此运行目录里既没有 pipeline_report.json 也没有 walk_forward_report.json。")
