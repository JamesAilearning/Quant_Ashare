"""Results page entry — read-only dashboard for pipeline / walk-forward
artifacts.

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

import os
from pathlib import Path
from typing import Any

import streamlit as st

from web.operator_ui.artifact_reader import ArtifactReadIssue
from web.operator_ui.components import render_empty_state
from web.operator_ui.job_io import (
    anchored_run_dir,
    canonical_dir_key,
    fold_catalog_by_dir,
    load_all_jobs,
)
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
    _render_walk_forward_summary,
)

render_page_header("结果", "查看流水线、滚动验证运行的产物。")
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
    if str(job.get("mode") or "") in {"pipeline", "walk_forward"}
]

# CLI 跑出来的流水线也要能打开。作业页把它们列出来并路由到**本页**,而本页
# 此前只认 UI 作业目录,于是那些行点「查看详情」得到「运行未找到」——这正是
# 本 change 的 delta 所禁止的死链(codex #444 r5)。做法与 walk_forward.py
# 对称:只收产物在 output 树内的行(判据同一套,见 job_io.run_dir_is_inspectable)。
_cli_pipeline = load_all_jobs(type_filter="pipeline", source_filter="cli")
#: (mode, 目录规范键) → 选择器里代表它的 job_id。**必须带 mode**:UI 的滚动
#: 验证作业与 CLI 的流水线记录可能落在同一个 output_dir,只按目录合并会让
#: 流水线 id 被别名到那条滚动验证作业上,点开渲染的是另一种模式的报告
#: (codex #444 r11)。
_dir_owner: dict[tuple[str, str], str] = {}
#: 已知 id → 选择器里的 job_id。UI 启动的流水线会**同时**留下一条 UI 作业和
#: 一条目录记录,指向同一个 output_dir(JobManager 把结果目录写进
#: config["output_dir"],引擎再按它编目)——本机实测 13 条目录记录里有 4 条
#: 是这种镜像。别名让两个 id 都跳得对,同时只保留 UI 那条(它带 config_path)。
_run_id_alias: dict[str, str] = {}
#: 产物被同目录更晚运行覆盖的 id → 现在占着该目录的 job_id。这类 id 不进
#: 静默别名表:静默跳过去等于让操作人以为看的是自己点的那次(codex #444 r2)。
_superseded_owner: dict[str, str] = {}


def _dir_key(run_dir: str | Path) -> str:
    """目录的比较键——与折叠**同一套规范化**。

    自己 normcase 一遍不够:符号链接/联接根的两种拼写指向同一份产物,UI 作业
    记 `output_link/...` 而它的目录镜像记解析形时,合并会漏配、同一份产物多出
    一个选择器条目(codex #444 r10)。边界外的路径没有别名问题,退回纯词法键。
    """
    text = str(run_dir)
    return canonical_dir_key(text) or os.path.normcase(str(anchored_run_dir(text)))


for _job in viewable_jobs:
    _rd = str(_job.get("run_dir") or "")
    if _rd:
        _dir_owner.setdefault(
            (str(_job.get("mode") or ""), _dir_key(_rd)),
            str(_job.get("job_id") or ""),
        )

# 折叠(锚定 / 首条即最新 / 被覆盖者只计数不别名)只有一份实现,与
# walk_forward.py 共用——这三条各自都被审查抓到过一次(#444 r1/r2/r4),
# 两页各写一份必然分叉。
_folded = fold_catalog_by_dir(_cli_pipeline)
for _row in _folded.newest:
    _resolved = _folded.dir_of_run[_row.run_id]
    _key = ("pipeline", _dir_key(_resolved))
    _owner = _dir_owner.get(_key)
    if _owner is None:
        # 纯 CLI 运行:自己进选择器。config_path 指向运行目录里的 config.yaml
        # (CLI 把配置写在产物目录内),这样配置页与日志页也一并对上——不指的话
        # ``_job_dir`` 会退回 output/operator_ui/jobs/<cli-id>,那个目录不存在。
        viewable_jobs.append(
            {
                "job_id": _row.run_id,
                "mode": "pipeline",
                "status": _row.status,
                "run_dir": str(_resolved),
                "config_path": str(_resolved / "config.yaml"),
                "started_at": _row.started_at,
                "ended_at": _row.finished_at,
                "error": _row.error_message,
                "source": "cli",
            }
        )
        _dir_owner[_key] = _row.run_id
        _owner = _row.run_id
    _run_id_alias.setdefault(_row.run_id, _owner)

# 被覆盖的 id 单列一张表,**在** newest 建完之后填——纯 CLI 目录的占位者正是
# 上面那轮才写进 _dir_owner 的,先填就会把它们漏成「运行未找到」。
for _run_id, _dir in _folded.superseded_dir_of_run.items():
    _replaced_by = _dir_owner.get(("pipeline", _dir_key(_dir)))
    if _replaced_by:
        _superseded_owner.setdefault(_run_id, _replaced_by)

if not viewable_jobs:
    render_empty_state(
        "📄",
        "暂无可查看的运行",
        "请先运行流水线或滚动验证作业。",
    )
    if st.button("配置运行"):
        st.switch_page("pages/config_run.py")
else:
    job_ids = [str(job.get("job_id")) for job in viewable_jobs if job.get("job_id")]
    requested_run_id = _query_run_id()
    # 请求的 id 先按原样命中,再走别名(同一次调用的 UI id ↔ CLI id),最后
    # 才是「被覆盖」的解释路径。三条分开,才不会把「产物被覆盖」说成
    # 「运行未找到」——后者会让操作人以为记录被删了。
    _selected_run_id = requested_run_id
    if requested_run_id and requested_run_id not in job_ids:
        _alias = _run_id_alias.get(requested_run_id, "")
        _replacement = _superseded_owner.get(requested_run_id, "")
        if _alias:
            _selected_run_id = _alias
        elif _replacement:
            st.warning(
                f"⚠ 请求的运行 `{requested_run_id}` 的产物已被覆盖——同一个 "
                "preset 反复跑会把报告写回**同一个** output_dir,盘上只剩最新"
                f"一份。下方显示的是现在占着该目录的 `{_replacement}`,"
                "**不是**你点的那次。"
            )
            _selected_run_id = _replacement
        else:
            _render_run_not_found(requested_run_id)
            st.stop()
    default_job_id = _selected_run_id or _default_job_id(viewable_jobs)
    default_index = job_ids.index(default_job_id) if default_job_id in job_ids else 0
    selected_job_id = st.selectbox(
        "运行",
        options=job_ids,
        index=default_index,
        format_func=lambda value: _job_label(
            next((job for job in viewable_jobs if str(job.get("job_id")) == value), {})
        ),
    )
    # 比的是**解析后**的 id,不是原始请求。比原始请求的话,别名/被覆盖两条
    # 路一进来就会改写 query_params → 触发重跑 → 上面的告警只闪一下就没了,
    # 等于把「你看的不是你点的那次」这句话吞掉。只有操作人自己换选择器时
    # 才该改写 URL。
    if selected_job_id and selected_job_id != _selected_run_id:
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

    # (tushare_provider mode + its inspection view were retired in U3 — only
    # pipeline / walk_forward jobs render here now.)
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
