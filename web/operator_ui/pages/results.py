"""Results page - read-only dashboard for pipeline and walk-forward artifacts."""

from __future__ import annotations

import html
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import streamlit as st
import yaml

from web.operator_ui import artifact_reader
from web.operator_ui._path_guard import output_path
from web.operator_ui.artifact_reader import ArtifactReadIssue
from web.operator_ui.chart_reader import discover_charts
from web.operator_ui.components import render_empty_state, render_error_state
from web.operator_ui.formatting import fmt_metric
from web.operator_ui.job_manager import JobManager
from web.operator_ui.page_header import render_breadcrumbs, render_page_header
from web.operator_ui.result_exports import bundle_zip_bytes, metrics_csv_bytes, summary_pdf_bytes
from web.operator_ui.result_view_helpers import (
    LOG_LEVEL_OPTIONS,
    TIME_RANGE_OPTIONS,
    filter_log_text,
    filter_nav_frame_by_range,
    nav_y_range,
)
from web.operator_ui.training_guards import inspect_provider_metadata, provider_metadata_summary

MISSING = "N/A"
LOG_NAMES = ("stdout.log", "stderr.log", "runner_stdout.log", "runner_stderr.log")
PLOTLY_STRATEGY_COLOR = "royalblue"
PLOTLY_BENCHMARK_COLOR = "lightslategray"
PLOTLY_DRAWDOWN_COLOR = "firebrick"
PLOTLY_POSITIVE_COLOR = "seagreen"
PLOTLY_NEGATIVE_COLOR = "firebrick"
PLOTLY_NEUTRAL_COLOR = "white"


def _record_issue(
    issues: list[ArtifactReadIssue],
    result: artifact_reader.ArtifactReadResult,
) -> Any:
    if result.issue is not None:
        issues.append(result.issue)
    return result.value


def _read_json_artifact(
    path: Path | None,
    issues: list[ArtifactReadIssue],
    *,
    artifact_name: str | None = None,
) -> dict[str, Any]:
    value = _record_issue(
        issues,
        artifact_reader.read_json_artifact(path, artifact_name=artifact_name),
    )
    return value if isinstance(value, dict) else {}


def _read_parquet_artifact(
    path: Path | None,
    issues: list[ArtifactReadIssue],
    *,
    artifact_name: str | None = None,
) -> Any:
    return _record_issue(
        issues,
        artifact_reader.read_parquet_artifact(path, artifact_name=artifact_name),
    )


def _read_text_artifact(
    path: Path | None,
    issues: list[ArtifactReadIssue],
    *,
    artifact_name: str | None = None,
    tail_chars: int | None = None,
) -> str:
    value = _record_issue(
        issues,
        artifact_reader.read_text_artifact(
            path,
            artifact_name=artifact_name,
            tail_chars=tail_chars,
        ),
    )
    return str(value or "")


def _read_bytes_artifact(
    path: Path | None,
    issues: list[ArtifactReadIssue],
    *,
    artifact_name: str | None = None,
) -> bytes:
    value = _record_issue(
        issues,
        artifact_reader.read_bytes_artifact(path, artifact_name=artifact_name),
    )
    return value if isinstance(value, bytes) else b""


def _job_dir(job: Mapping[str, Any]) -> Path | None:
    config_path = _path_or_none(job.get("config_path"))
    if config_path is not None:
        return config_path.parent
    job_id = str(job.get("job_id") or "").strip()
    if not job_id:
        return None
    return output_path("operator_ui", "jobs", job_id)


def _path_or_none(value: Any) -> Path | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return Path(text)


def _read_config(
    job: Mapping[str, Any],
    issues: list[ArtifactReadIssue],
) -> tuple[dict[str, Any], Path | None, bytes]:
    config_path = _path_or_none(job.get("config_path"))
    if config_path is None:
        candidate = _job_dir(job)
        config_path = candidate / "config.yaml" if candidate is not None else None
    config_bytes = _read_bytes_artifact(config_path, issues, artifact_name="config.yaml")
    if not config_bytes:
        return {}, config_path, b""
    try:
        loaded = yaml.safe_load(config_bytes.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        issues.append(
            ArtifactReadIssue(
                artifact_name="config.yaml",
                path="" if config_path is None else str(config_path),
                error_type=type(exc).__name__,
                message=str(exc),
            )
        )
        return {}, config_path, config_bytes
    return loaded if isinstance(loaded, dict) else {}, config_path, config_bytes


def _resolve_run_dir(job: Mapping[str, Any], config: Mapping[str, Any]) -> Path | None:
    run_dir = _path_or_none(job.get("run_dir"))
    if run_dir is not None:
        return run_dir
    job_status = str(job.get("status") or "").lower()
    if job_status not in {"success", "completed", "ok"}:
        return None
    output_dir = _path_or_none(config.get("output_dir"))
    if output_dir is None:
        return None
    if str(job.get("mode") or "") == "pipeline":
        runs_dir = output_dir / "runs"
        if runs_dir.is_dir():
            candidates = [entry for entry in runs_dir.iterdir() if entry.is_dir()]
            if candidates:
                return max(candidates, key=lambda path: path.stat().st_mtime)
    return output_dir


def _nested(data: Mapping[str, Any], *path: str) -> Any:
    cur: Any = data
    for key in path:
        if not isinstance(cur, Mapping):
            return None
        cur = cur.get(key)
    return cur


def _first(data: Mapping[str, Any], paths: Sequence[Sequence[str]]) -> Any:
    for path in paths:
        value = _nested(data, *path)
        if value is not None:
            return value
    return None


def _finite_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _fmt_percent(value: Any, *, signed: bool = False) -> str:
    number = _finite_float(value)
    if number is None:
        return MISSING
    prefix = "+" if signed and number > 0 else ""
    return f"{prefix}{number * 100:.2f}%"


def _fmt_number(value: Any, *, digits: int = 2) -> str:
    number = _finite_float(value)
    if number is None:
        return MISSING
    return f"{number:.{digits}f}"


def _fmt_int(value: Any) -> str:
    number = _finite_float(value)
    if number is None:
        return MISSING
    return f"{int(number):,}"


def _fmt_text(value: Any) -> str:
    if value is None:
        return MISSING
    text = str(value).strip()
    return text if text else MISSING


def _fmt_duration(started_at: Any, ended_at: Any) -> str:
    if not started_at or not ended_at:
        return MISSING
    from datetime import datetime

    try:
        start = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(ended_at).replace("Z", "+00:00"))
    except ValueError:
        return MISSING
    seconds = max(0, int((end - start).total_seconds()))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _status_class(status: Any) -> str:
    normalized = str(status or "unknown").lower()
    if normalized in {"success", "completed", "ok"}:
        return "status-success"
    if normalized == "running":
        return "status-running"
    if normalized in {"failed", "stop_failed"}:
        return "status-failed"
    if normalized in {"stopped", "cancelled", "canceled"}:
        return "status-warning"
    return "status-muted"


def _safe_html(text: Any) -> str:
    import html

    return html.escape(str(text or ""))


def _install_styles() -> None:
    st.markdown(
        """
        <style>
        .qv2-page {max-width: 1440px; margin: 0 auto;}
        .qv2-header {
            position: sticky;
            top: 0.75rem;
            z-index: 10;
            border: 1px solid var(--border-subtle);
            border-radius: var(--radius-lg);
            padding: var(--space-4) var(--space-5);
            background: var(--bg-card);
            margin-bottom: var(--space-5);
            box-shadow: var(--shadow-md);
        }
        .qv2-header-row {
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: var(--space-4);
            flex-wrap: wrap;
        }
        .qv2-run-id {
            font-size: 1.25rem;
            font-weight: var(--weight-bold);
            color: var(--text-primary);
        }
        .qv2-muted {color: var(--text-secondary); font-size: var(--text-sm);}
        .qv2-badge {
            display: inline-flex;
            align-items: center;
            border-radius: 999px;
            padding: 4px 10px;
            font-weight: var(--weight-bold);
            font-size: 0.8rem;
            margin-left: var(--space-2);
        }
        .status-success {background: var(--positive-bg); color: var(--positive-text);}
        .status-running {background: var(--info-bg); color: var(--info-text);}
        .status-failed {background: var(--negative-bg); color: var(--negative-text);}
        .status-warning {background: var(--warning-bg); color: var(--warning-text);}
        .status-muted {background: var(--neutral-bg); color: var(--neutral-text);}
        .qv2-card {
            border: 1px solid var(--border-subtle);
            border-radius: var(--radius-lg);
            background: var(--bg-card);
            padding: var(--space-5);
            min-height: 150px;
            box-shadow: var(--shadow-sm);
        }
        .qv2-card-title {
            text-transform: uppercase;
            letter-spacing: var(--tracking-wider);
            font-size: 0.78rem;
            color: var(--text-secondary);
            font-weight: var(--weight-bold);
            margin-bottom: var(--space-3);
        }
        .qv2-primary {
            font-size: 2rem;
            line-height: 1.15;
            font-weight: 800;
            color: var(--text-primary);
            margin-bottom: var(--space-1);
        }
        .qv2-positive {color: var(--positive);}
        .qv2-negative {color: var(--negative);}
        .qv2-secondary {
            color: var(--text-secondary);
            font-size: 0.92rem;
            line-height: 1.65;
        }
        .qv2-section-title {
            margin-top: 28px;
            margin-bottom: 10px;
            font-size: 1.15rem;
            font-weight: 800;
            color: var(--text-primary);
        }
        .qv2-empty {
            border: 1px dashed var(--border-medium);
            border-radius: var(--radius-lg);
            padding: var(--space-5);
            color: var(--text-secondary);
            background: var(--bg-subtle);
        }
        .qv2-error {
            border-radius: var(--radius-lg);
            padding: var(--space-3) var(--space-4);
            color: var(--negative-text);
            background: var(--negative-bg);
            border: 1px solid var(--negative);
            margin: var(--space-3) 0 var(--space-5);
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_status_header(
    *,
    job: Mapping[str, Any],
    run_dir: Path | None,
    report: Mapping[str, Any],
    metadata: Mapping[str, Any],
    config_bytes: bytes,
) -> None:
    job_id = _fmt_text(job.get("job_id"))
    status = _fmt_text(job.get("status") or metadata.get("status"))
    started = _fmt_text(job.get("started_at") or metadata.get("started_at"))
    ended = _fmt_text(job.get("ended_at") or metadata.get("finished_at"))
    duration_seconds = _finite_float(metadata.get("duration_seconds"))
    duration = (
        f"{int(duration_seconds)}s"
        if duration_seconds is not None and str(job.get("status") or "").lower() in {"success", "completed", "ok"}
        else _fmt_duration(job.get("started_at"), job.get("ended_at"))
    )
    generated_at = _fmt_text(metadata.get("finished_at") or report.get("generated_at"))
    status_class = _status_class(status)
    run_dir_text = _fmt_text(run_dir)

    st.markdown(
        f"""
        <div class="qv2-header">
          <div class="qv2-header-row">
            <div>
              <div class="qv2-run-id">
                流水线结果
                <span class="qv2-badge {status_class}" role="status" aria-live="polite">{_safe_html(status)}</span>
              </div>
              <div class="qv2-muted">作业：{_safe_html(job_id)}</div>
              <div class="qv2-muted">运行目录：{_safe_html(run_dir_text)}</div>
            </div>
            <div class="qv2-muted">
              <div>开始：{_safe_html(started)}</div>
              <div>结束：{_safe_html(ended)}</div>
              <div>耗时：{_safe_html(duration)}</div>
              <div>报告生成于：{_safe_html(generated_at)}</div>
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if str(job.get("status") or status).lower() == "failed":
        error = job.get("error") or job.get("stop_error") or f"returncode={job.get('returncode')}"
        st.markdown(
            f"<div class='qv2-error'>此作业已失败：{_safe_html(error)}</div>",
            unsafe_allow_html=True,
        )

    nav_cols = st.columns([1, 2, 3])
    with nav_cols[0]:
        if st.button("返回作业列表"):
            st.query_params.clear()
            st.switch_page("pages/jobs.py")
    with nav_cols[1]:
        rid_cols = st.columns([4, 1])
        with rid_cols[0]:
            st.text_input(
                "运行 ID（可复制）",
                value="" if job_id == MISSING else job_id,
                key=f"copy_run_id_{job_id}",
            )
        with rid_cols[1]:
            st.markdown(
                "<div style='visibility: hidden; height: 28px;'>spacer</div>",
                unsafe_allow_html=True,
            )
            if st.button(
                "📋",
                key=f"copy_run_id_btn_{job_id}",
                help="复制运行 ID 到剪贴板",
                use_container_width=True,
            ):
                st.session_state["results_clipboard_payload"] = job_id
                st.session_state["results_clipboard_toast"] = "已复制运行 ID"
    with nav_cols[2]:
        rd_cols = st.columns([4, 1])
        with rd_cols[0]:
            st.text_input(
                "运行目录（可复制）",
                value="" if run_dir_text == MISSING else run_dir_text,
                key=f"copy_run_dir_{job_id}",
            )
        with rd_cols[1]:
            st.markdown(
                "<div style='visibility: hidden; height: 28px;'>spacer</div>",
                unsafe_allow_html=True,
            )
            if st.button(
                "📋",
                key=f"copy_run_dir_btn_{job_id}",
                help="复制运行目录路径到剪贴板",
                use_container_width=True,
            ):
                st.session_state["results_clipboard_payload"] = run_dir_text
                st.session_state["results_clipboard_toast"] = "已复制运行目录"

    # Clipboard write + toast — drained on next render after a copy button click.
    _clipboard_payload = st.session_state.pop("results_clipboard_payload", "")
    _clipboard_toast = st.session_state.pop("results_clipboard_toast", "")
    if _clipboard_payload:
        import base64 as _b64

        _payload_b64 = _b64.b64encode(_clipboard_payload.encode("utf-8")).decode("ascii")
        st.html(
            (
                "<script>"
                "(function(){"
                f"var b64='{_payload_b64}';"
                "try {"
                "  var txt=atob(b64);"
                "  if (navigator.clipboard) {"
                "    navigator.clipboard.writeText(txt).catch(function(){});"
                "  } else {"
                "    var ta=window.parent.document.createElement('textarea');"
                "    ta.value=txt; ta.style.position='fixed'; ta.style.left='-9999px';"
                "    window.parent.document.body.appendChild(ta); ta.select();"
                "    try{document.execCommand('copy');}catch(e){}"
                "    window.parent.document.body.removeChild(ta);"
                "  }"
                "} catch(e) {}"
                "})()"
                "</script>"
            ),
            width="content",
            unsafe_allow_javascript=True,
        )
        if _clipboard_toast:
            st.toast(_clipboard_toast, icon="📋")

    if config_bytes:
        st.download_button(
            "下载 config.yaml",
            data=config_bytes,
            file_name="config.yaml",
            mime="text/yaml",
        )


def _render_header_actions(
    *,
    job: Mapping[str, Any],
    run_dir: Path | None,
    config_bytes: bytes,
    metrics: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> None:
    action_cols = st.columns([1, 1, 1, 1])
    with action_cols[0]:
        if st.button("用此配置重跑", disabled=not config_bytes):
            st.session_state["prefill_config_yaml"] = config_bytes.decode("utf-8", errors="replace")
            st.session_state["prefill_config_source_job"] = str(job.get("job_id") or "")
            st.switch_page(str(Path(__file__).resolve().parent / "config_run.py"))
    with action_cols[1]:
        st.download_button(
            "导出指标 CSV",
            data=metrics_csv_bytes(metrics),
            file_name=f"{job.get('job_id', 'pipeline')}_metrics.csv",
            mime="text/csv",
            disabled=not metrics,
        )
    with action_cols[2]:
        pdf_bytes = b""
        pdf_error = ""
        if metrics:
            try:
                pdf_bytes = summary_pdf_bytes(
                    run_id=str(job.get("job_id") or ""),
                    status=str(job.get("status") or metadata.get("status") or ""),
                    metrics=metrics,
                    metadata=metadata,
                )
            except RuntimeError as exc:
                pdf_error = str(exc)
        st.download_button(
            "导出 PDF 报告",
            data=pdf_bytes,
            file_name=f"{job.get('job_id', 'pipeline')}_summary.pdf",
            mime="application/pdf",
            disabled=not pdf_bytes,
            help=pdf_error or None,
        )
    with action_cols[3]:
        bundle_bytes = b""
        if run_dir is not None:
            try:
                bundle_bytes = bundle_zip_bytes(run_dir)
            except (OSError, ValueError):
                bundle_bytes = b""
        st.download_button(
            "导出完整压缩包",
            data=bundle_bytes,
            file_name=f"{job.get('job_id', 'pipeline')}_bundle.zip",
            mime="application/zip",
            disabled=not bundle_bytes,
        )

    with st.expander("键盘快捷键", expanded=False):
        st.markdown(
            """
            - `?`：在此处打开快捷键帮助。
            - `j` / `k`：在「运行」选择器里跳到下一个 / 上一个作业。
            - `r`：跳到「配置运行」页面用此作业重跑。
            - `e`：使用上方的导出按钮。
            - `1`-`5`：在「持仓 / 交易 / 配置 / 阶段耗时 / 日志」之间切换。
            - `/`：聚焦到「搜索持仓 / 搜索交易 / 搜索日志」输入框。

            Streamlit 没有暴露全局键盘事件接口，因此这些快捷键以可见按钮和
            标签的形式呈现，需要用鼠标点击。
            """
        )


def _render_artifact_issues(issues: Sequence[ArtifactReadIssue]) -> None:
    if not issues:
        return

    st.markdown(
        '<div class="qv2-section-title">产物读取问题</div>',
        unsafe_allow_html=True,
    )
    for issue in issues:
        st.error(
            f"{issue.artifact_name}: {issue.error_type}: {issue.message} "
            f"(路径：{issue.path or MISSING})"
        )


def _metric_color(value: Any, *, negative_is_bad: bool = True) -> str:
    number = _finite_float(value)
    if number is None:
        return ""
    if number < 0 and negative_is_bad:
        return " qv2-negative"
    if number > 0:
        return " qv2-positive"
    return ""


def _render_card(
    title: str,
    primary: str,
    primary_class: str,
    lines: Sequence[str],
    *,
    help_text: str,
) -> None:
    line_html = "<br>".join(_safe_html(line) for line in lines)
    st.markdown(
        f"""
        <div class="qv2-card" title="{_safe_html(help_text)}">
          <div class="qv2-card-title">{_safe_html(title)}</div>
          <div class="qv2-primary{primary_class}">{_safe_html(primary)}</div>
          <div class="qv2-secondary">{line_html}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_kpis(
    report: Mapping[str, Any],
    metrics: Mapping[str, Any],
    positions: Mapping[str, Any],
    config: Mapping[str, Any],
) -> None:
    risk = _nested(report, "risk_analysis", "excess_return_with_cost") or {}
    if not isinstance(risk, Mapping):
        risk = {}

    ann_return = _first(metrics, [("performance", "annual_return")])
    ann_return_label = "主指标：年化收益"
    if ann_return is None:
        ann_return = _first(metrics, [("performance", "annual_excess_return_with_cost")])
        ann_return_label = "主指标：扣费后年化超额收益"
    if ann_return is None:
        ann_return = risk.get("annualized_return")
    max_drawdown = _first(metrics, [("risk", "max_drawdown")])
    if max_drawdown is None:
        max_drawdown = risk.get("max_drawdown")
    information_ratio = _first(metrics, [("performance", "information_ratio")])
    if information_ratio is None:
        information_ratio = risk.get("information_ratio")
    volatility = risk.get("annualized_volatility") or risk.get("volatility")
    sharpe = risk.get("sharpe")

    config_section = report.get("config") if isinstance(report.get("config"), Mapping) else {}
    benchmark_code = _first(
        {"report": report, "config": config, "config_section": config_section},
        [
            ("config_section", "benchmark_code"),
            ("config", "benchmark_code"),
        ],
    )

    cols = st.columns(3)
    with cols[0]:
        _render_card(
            "收益",
            _fmt_percent(ann_return, signed=True),
            _metric_color(ann_return),
            [
                ann_return_label,
                f"信息比率（IR）：{_fmt_number(information_ratio)}",
                f"夏普比率：{_fmt_number(sharpe)}",
                f"基准：{_fmt_text(benchmark_code)}",
            ],
            help_text="收益卡片：年化收益、信息比率、夏普等指标，全部来源于运行产物。",
        )
    with cols[1]:
        _render_card(
            "风险",
            _fmt_percent(max_drawdown, signed=True),
            " qv2-negative" if _finite_float(max_drawdown) is not None else "",
            [
                f"年化波动率：{_fmt_percent(volatility)}",
                f"指标状态：{_fmt_text(report.get('metric_status'))}",
                f"官方回测路径：{_fmt_text(report.get('official_backtest_path'))}",
            ],
            help_text="风险卡片：最大回撤与波动率字段，来源于 metrics / report 产物。",
        )
    with cols[2]:
        position_days = _first(metrics, [("trading", "positions_days")])
        if position_days is None:
            position_days = len(positions) if positions else None
        latest_count = _first(metrics, [("trading", "latest_holding_count")])
        if positions:
            latest_key = sorted(str(key) for key in positions.keys())[-1]
            latest_positions = positions.get(latest_key)
            if latest_count is None and isinstance(latest_positions, Mapping):
                latest_count = len(latest_positions)
        _render_card(
            "交易",
            f"持仓数 {_fmt_text(config_section.get('topk') if config_section else config.get('topk'))}",
            "",
            [
                f"换出数（n_drop）：{_fmt_text(config_section.get('n_drop') if config_section else config.get('n_drop'))}",
                f"持仓天数：{_fmt_int(position_days)}",
                f"最新持仓数：{_fmt_int(latest_count)}",
            ],
            help_text="交易卡片：仅展示持仓元数据，UI 不在本地重建交易序列。"
            "「持仓数」对应 YAML 中的 topk。",
        )


def _chart_by_token(charts: Mapping[str, Path], *tokens: str) -> tuple[str, Path] | None:
    lowered_tokens = tuple(token.lower() for token in tokens)
    for label, path in charts.items():
        normalized = label.lower().replace("-", "_").replace(" ", "_")
        if any(token in normalized for token in lowered_tokens):
            return label, path
    return None


def _render_charts(run_dir: Path | None) -> None:
    st.markdown('<div class="qv2-section-title">图表</div>', unsafe_allow_html=True)
    if run_dir is None:
        st.markdown(
            '<div class="qv2-empty">作业运行目录尚未创建，图表暂不可用。</div>',
            unsafe_allow_html=True,
        )
        return

    charts = discover_charts(run_dir)
    if not charts:
        st.markdown(
            '<div class="qv2-empty">尚未发现已生成的 PNG 图表。</div>',
            unsafe_allow_html=True,
        )
        return

    equity = _chart_by_token(charts, "equity", "nav")
    drawdown = _chart_by_token(charts, "drawdown")
    monthly = _chart_by_token(charts, "monthly", "heatmap")
    used: set[str] = set()

    if equity is not None:
        label, path = equity
        used.add(label)
        st.subheader("净值曲线")
        st.image(str(path), use_container_width=True)

    chart_cols = st.columns(2)
    if drawdown is not None:
        label, path = drawdown
        used.add(label)
        with chart_cols[0]:
            st.subheader("回撤")
            st.image(str(path), use_container_width=True)
    if monthly is not None:
        label, path = monthly
        used.add(label)
        with chart_cols[1]:
            st.subheader("月度收益")
            st.image(str(path), use_container_width=True)

    remaining = [(label, path) for label, path in charts.items() if label not in used]
    if remaining:
        with st.expander("其他已生成的图表", expanded=False):
            for label, path in remaining:
                st.subheader(label)
                st.image(str(path), use_container_width=True)


def _read_positions(run_dir: Path | None, issues: list[ArtifactReadIssue]) -> dict[str, Any]:
    if run_dir is None:
        return {}
    return _read_json_artifact(run_dir / "positions.json", issues, artifact_name="positions.json")


def _read_metadata(run_dir: Path | None, issues: list[ArtifactReadIssue]) -> dict[str, Any]:
    if run_dir is None:
        return {}
    return _read_json_artifact(run_dir / "metadata.json", issues, artifact_name="metadata.json")


def _read_metrics(run_dir: Path | None, issues: list[ArtifactReadIssue]) -> dict[str, Any]:
    if run_dir is None:
        return {}
    return _read_json_artifact(run_dir / "metrics.json", issues, artifact_name="metrics.json")


def _read_holdings_frame(run_dir: Path | None, issues: list[ArtifactReadIssue]) -> Any:
    if run_dir is None:
        return None
    return _read_parquet_artifact(
        run_dir / "holdings.parquet",
        issues,
        artifact_name="holdings.parquet",
    )


def _read_trades_frame(run_dir: Path | None, issues: list[ArtifactReadIssue]) -> Any:
    if run_dir is None:
        return None
    return _read_parquet_artifact(run_dir / "trades.parquet", issues, artifact_name="trades.parquet")


def _read_nav_frame(run_dir: Path | None, issues: list[ArtifactReadIssue]) -> Any:
    if run_dir is None:
        return None
    return _read_parquet_artifact(run_dir / "nav.parquet", issues, artifact_name="nav.parquet")


def _render_holdings_tab(holdings_frame: Any, positions: Mapping[str, Any]) -> None:
    if holdings_frame is not None and not holdings_frame.empty:
        dates = sorted(str(value)[:10] for value in holdings_frame["date"].dropna().unique())
        selected_date = st.selectbox("持仓日期", dates, index=len(dates) - 1)
        search = st.text_input("搜索持仓", value="", placeholder="股票代码")
        top_n = st.number_input("显示前 N 大持仓", value=100, min_value=1, max_value=1000)
        filtered = holdings_frame[
            holdings_frame["date"].astype(str).str.slice(0, 10) == selected_date
        ]
        if search.strip():
            filtered = filtered[
                filtered["stock"].astype(str).str.contains(search.strip(), case=False, na=False)
            ]
        filtered = filtered.sort_values("rank", kind="stable").head(int(top_n))

        # Display polish:
        # 1) strip the meaningless ``00:00:00`` time component from the
        #    daily-snapshot ``date`` column so it just reads ``YYYY-MM-DD``;
        # 2) rename internal English columns to Chinese for display;
        # 3) render ``weight`` as a signed percentage instead of a 0.0499
        #    decimal so operators can eyeball position sizes at a glance.
        # We make a shallow copy of the slice so the rename / formatting
        # never mutates the cached parquet frame.
        display_df = filtered.copy()
        if "date" in display_df:
            display_df["date"] = display_df["date"].astype(str).str.slice(0, 10)
        if "weight" in display_df:
            display_df["weight"] = display_df["weight"].map(
                lambda value: _fmt_percent(value, signed=False)
            )
        display_df = display_df.rename(
            columns={"date": "日期", "stock": "股票", "weight": "权重", "rank": "排名"}
        )
        st.dataframe(display_df, use_container_width=True, hide_index=True)
        st.download_button(
            "导出持仓 CSV",
            data=filtered.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"holdings_{selected_date}.csv",
            mime="text/csv",
        )
        return

    if not positions:
        st.info("等 holdings.parquet 或 positions.json 落盘后，持仓数据会出现在这里。")
        return

    dates = sorted(str(key) for key in positions.keys())
    selected_date = st.selectbox("持仓日期", dates, index=len(dates) - 1)
    date_positions = positions.get(selected_date)
    if not isinstance(date_positions, Mapping) or not date_positions:
        st.info("该日期没有持仓记录。")
        return

    import pandas as pd

    rows = [
        {
            "标的": str(instrument),
            "权重": _fmt_percent(_finite_float(weight), signed=False),
        }
        for instrument, weight in sorted(date_positions.items(), key=lambda item: str(item[0]))
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _render_trades_tab(trades_frame: Any) -> None:
    if trades_frame is None:
        st.info("本次运行尚未生成交易日志产物。")
        return
    if trades_frame.empty:
        st.info(
            "trades.parquet 文件存在，但当前规范化运行时还未导出逐笔成交。"
        )
        return
    frame = trades_frame.copy()
    if "date" in frame and not frame.empty:
        dates = sorted(str(value)[:10] for value in frame["date"].dropna().unique())
        selected_dates = st.multiselect("交易日期", dates, default=dates)
        if selected_dates:
            frame = frame[frame["date"].astype(str).str.slice(0, 10).isin(selected_dates)]
    if "side" in frame and not frame.empty:
        sides = sorted(str(value) for value in frame["side"].dropna().unique())
        selected_sides = st.multiselect("方向", sides, default=sides)
        if selected_sides:
            frame = frame[frame["side"].astype(str).isin(selected_sides)]
    search = st.text_input("搜索交易", value="", placeholder="股票代码")
    if search.strip() and "stock" in frame:
        frame = frame[frame["stock"].astype(str).str.contains(search.strip(), case=False, na=False)]

    # Display polish: strip 00:00:00 from date, rename columns to Chinese.
    # CSV export retains the raw parquet column names + timestamps so
    # downstream tools (spreadsheets / scripts) keep working unchanged.
    display_frame = frame.copy()
    if "date" in display_frame:
        display_frame["date"] = display_frame["date"].astype(str).str.slice(0, 10)
    display_frame = display_frame.rename(
        columns={
            "date": "日期",
            "stock": "股票",
            "side": "方向",
            "price": "价格",
            "amount": "成交量",
            "weight": "权重",
        }
    )
    st.dataframe(display_frame, use_container_width=True, hide_index=True)
    st.download_button(
        "导出交易 CSV",
        data=frame.to_csv(index=False).encode("utf-8-sig"),
        file_name="trades.csv",
        mime="text/csv",
    )


def _render_interactive_charts(nav_frame: Any, run_dir: Path | None) -> None:
    st.markdown('<div class="qv2-section-title">净值曲线</div>', unsafe_allow_html=True)
    if nav_frame is None or nav_frame.empty:
        st.markdown(
            '<div class="qv2-empty">回测 NAV 产物尚未生成。</div>',
            unsafe_allow_html=True,
        )
        _render_charts(run_dir)
        return

    try:
        import plotly.graph_objects as go
    except ImportError:
        st.warning("未安装 plotly，回退到生成的 PNG 图表。")
        _render_charts(run_dir)
        return

    range_label = st.radio(
        "显示时间范围",
        TIME_RANGE_OPTIONS,
        horizontal=True,
        key="pipeline_result_time_range",
    )
    frame = filter_nav_frame_by_range(nav_frame, range_label)
    if frame is None or frame.empty:
        st.markdown(
            '<div class="qv2-empty">所选时间范围内没有净值数据。</div>',
            unsafe_allow_html=True,
        )
        return

    nav_fig = go.Figure()
    nav_fig.add_trace(go.Scatter(
        x=frame["date"],
        y=frame["strategy_nav"],
        mode="lines",
        name="策略净值",
        line={"width": 2.4, "color": PLOTLY_STRATEGY_COLOR},
    ))
    if "benchmark_nav" in frame and frame["benchmark_nav"].notna().any():
        nav_fig.add_trace(go.Scatter(
            x=frame["date"],
            y=frame["benchmark_nav"],
            mode="lines",
            name="基准净值",
            line={"width": 1.8, "color": PLOTLY_BENCHMARK_COLOR, "dash": "dash"},
        ))
    nav_axis: dict[str, Any] = {"title": "净值（×）"}
    y_range = nav_y_range(frame)
    if y_range is not None:
        nav_axis["range"] = y_range
    nav_fig.update_layout(
        height=420,
        hovermode="x unified",
        margin={"l": 36, "r": 20, "t": 20, "b": 36},
        yaxis=nav_axis,
        xaxis={"title": ""},
        legend={"orientation": "h", "yanchor": "bottom", "y": -0.24, "xanchor": "left", "x": 0},
    )
    st.plotly_chart(nav_fig, use_container_width=True)

    st.markdown('<div class="qv2-section-title">回撤</div>', unsafe_allow_html=True)
    dd_fig = go.Figure()
    if "strategy_drawdown" in frame:
        dd_fig.add_trace(go.Scatter(
            x=frame["date"],
            y=frame["strategy_drawdown"],
            mode="lines",
            name="策略回撤",
            fill="tozeroy",
            line={"width": 2.0, "color": PLOTLY_DRAWDOWN_COLOR},
        ))
    if "benchmark_drawdown" in frame and frame["benchmark_drawdown"].notna().any():
        dd_fig.add_trace(go.Scatter(
            x=frame["date"],
            y=frame["benchmark_drawdown"],
            mode="lines",
            name="基准回撤",
            line={"width": 1.5, "color": PLOTLY_BENCHMARK_COLOR, "dash": "dash"},
        ))
    dd_fig.update_layout(
        height=320,
        hovermode="x unified",
        margin={"l": 36, "r": 20, "t": 20, "b": 36},
        # Y-axis title omitted — Chinese characters rotated 90° are hard to
        # read, and the section header above already says 回撤. We keep the
        # percent tick format so the scale is unambiguous.
        yaxis={"title": "", "tickformat": ".1%"},
        xaxis={"title": ""},
        legend={"orientation": "h", "yanchor": "bottom", "y": -0.28, "xanchor": "left", "x": 0},
    )
    st.plotly_chart(dd_fig, use_container_width=True)


def _render_monthly_returns(metrics: Mapping[str, Any]) -> None:
    rows = metrics.get("monthly_returns")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)) or not rows:
        st.markdown(
            '<div class="qv2-empty">月度收益数据暂不可用。</div>',
            unsafe_allow_html=True,
        )
        return
    import pandas as pd

    frame = pd.DataFrame(rows)
    if {"month", "strategy"}.issubset(frame.columns):
        try:
            import plotly.graph_objects as go

            period_index = pd.PeriodIndex(frame["month"].astype(str), freq="M")
            # 月份在 X 轴上显示。strftime("%b") 在 Windows / Linux 不同
            # locale 下输出不一致，所以这里手动把 period_index 的月份号
            # （1..12）映射成中文标签，避免依赖系统 locale。
            _MONTH_LABELS_ZH = ("1月", "2月", "3月", "4月", "5月", "6月",
                                 "7月", "8月", "9月", "10月", "11月", "12月")
            month_order = list(_MONTH_LABELS_ZH)
            heatmap_frame = frame.assign(
                year=period_index.year,
                month_label=[_MONTH_LABELS_ZH[m - 1] for m in period_index.month],
            )
            pivot = heatmap_frame.pivot_table(
                index="year",
                columns="month_label",
                values="strategy",
                aggfunc="first",
            ).reindex(columns=month_order)
            text = pivot.apply(lambda col: col.map(lambda value: _fmt_percent(value, signed=True)))
            fig = go.Figure(data=go.Heatmap(
                z=pivot.values,
                x=list(pivot.columns),
                y=[str(value) for value in pivot.index],
                text=text.values,
                texttemplate="%{text}",
                colorscale=[
                    [0.0, PLOTLY_NEGATIVE_COLOR],
                    [0.5, PLOTLY_NEUTRAL_COLOR],
                    [1.0, PLOTLY_POSITIVE_COLOR],
                ],
                zmid=0,
                colorbar={"tickformat": ".1%"},
                hovertemplate="年份 %{y}<br>月份 %{x}<br>策略 %{z:.2%}<extra></extra>",
            ))
            fig.update_layout(
                height=260,
                margin={"l": 36, "r": 20, "t": 10, "b": 30},
                # ``xaxis.type=category`` keeps the explicit "1月..12月"
                # ordering even when only a subset of months appear; ``yaxis``
                # likewise forces categorical so a single-year run does not
                # render the year as a continuous numeric scale with bogus
                # decimal ticks like "2,025.4 / 2,025.2 / 2,025".
                xaxis={"title": "", "type": "category"},
                yaxis={"title": "", "type": "category"},
            )
            st.plotly_chart(fig, use_container_width=True)
        except (ImportError, ValueError, TypeError):
            st.info("月度热力图暂不可用，以下以表格形式展示原始数据。")

    display = frame.copy()
    for column in ("strategy", "benchmark"):
        if column in display:
            display[column] = display[column].map(lambda value: _fmt_percent(value, signed=True))
    display = display.rename(
        columns={"month": "月份", "strategy": "策略", "benchmark": "基准"}
    )
    st.dataframe(display, use_container_width=True, hide_index=True)


def _render_config_tab(config_path: Path | None, config_bytes: bytes, config: Mapping[str, Any]) -> None:
    if not config_bytes:
        st.info("本作业的 config.yaml 暂不可用。")
        if config:
            st.json(config)
        return

    st.download_button(
        "下载精确运行配置",
        data=config_bytes,
        file_name="config.yaml",
        mime="text/yaml",
        key="detail_config_download",
    )
    st.caption(f"来源：{_fmt_text(config_path)}")
    try:
        config_text = config_bytes.decode("utf-8")
    except UnicodeDecodeError:
        config_text = "<config.yaml is not valid UTF-8>"
    st.code(config_text, language="yaml")


def _render_timings_tab(job: Mapping[str, Any], report: Mapping[str, Any], metadata: Mapping[str, Any]) -> None:
    progress = job.get("progress") if isinstance(job.get("progress"), Mapping) else {}
    rows = {
        "status": job.get("status") or metadata.get("status"),
        "started_at": job.get("started_at") or metadata.get("started_at"),
        "ended_at": job.get("ended_at") or metadata.get("finished_at"),
        "duration_seconds": metadata.get("duration_seconds"),
        "progress_percent": progress.get("percent"),
        "progress_label": progress.get("label"),
        "progress_detail": progress.get("detail"),
        "report_generated_at": report.get("generated_at"),
        "stage_timings": metadata.get("stage_timings"),
    }
    st.json({key: value for key, value in rows.items() if value not in (None, "")})


def _render_logs_tab(job: Mapping[str, Any], issues: list[ArtifactReadIssue]) -> None:
    job_dir = _job_dir(job)
    if job_dir is None:
        st.info("作业日志目录暂不可用。")
        return

    search = st.text_input("搜索日志", value="", placeholder="输入文本过滤日志行")
    levels = st.multiselect(
        "严重等级",
        LOG_LEVEL_OPTIONS,
        default=list(LOG_LEVEL_OPTIONS),
        help="全选时不带等级标签的日志行也会显示。",
    )
    any_log = False
    any_match = False
    for name in LOG_NAMES:
        path = job_dir / name
        text = _read_text_artifact(path, issues, artifact_name=name, tail_chars=30_000)
        if not text:
            continue
        any_log = True
        filtered_text = filter_log_text(text, search=search, levels=levels)
        if filtered_text:
            any_match = True
        with st.expander(name, expanded=name in {"stderr.log", "runner_stderr.log"}):
            st.caption(str(path))
            st.caption(
                f"显示 {len(filtered_text.splitlines())} / {len(text.splitlines())} 行日志。"
            )
            if filtered_text:
                st.code(filtered_text, language="text")
            else:
                st.info("没有日志行符合当前筛选条件。")

    if not any_log:
        st.info("日志文件尚未生成或为空。")
    elif not any_match:
        st.info("没有日志符合当前搜索关键字和严重等级筛选。")


def _filter_json_by_query(obj: Any, query: str) -> Any:
    """Recursive substring filter for nested JSON.

    Returns a subtree containing only branches where some key, string
    value, or numeric value contains ``query`` (case-insensitive).
    Returns ``None`` when nothing matches; the caller treats ``None`` /
    empty result as "no hits".

    Designed to keep the Raw JSON tab usable on large pipeline reports
    by letting the operator narrow to a key like ``sharpe`` or
    ``drawdown`` without scrolling through hundreds of lines.
    """

    q = query.strip().lower()
    if not q:
        return obj

    if isinstance(obj, dict):
        kept: dict[str, Any] = {}
        for key, value in obj.items():
            if q in str(key).lower():
                kept[key] = value
                continue
            filtered = _filter_json_by_query(value, query)
            if filtered not in (None, {}, []):
                kept[key] = filtered
        return kept or None

    if isinstance(obj, list):
        out = []
        for entry in obj:
            filtered = _filter_json_by_query(entry, query)
            if filtered not in (None, {}, []):
                out.append(filtered)
        return out or None

    # Scalar leaves.
    if obj is None:
        return None
    return obj if q in str(obj).lower() else None


def _render_raw_tab(
    job: Mapping[str, Any],
    report: Mapping[str, Any],
    metadata: Mapping[str, Any],
    metrics: Mapping[str, Any],
    positions: Mapping[str, Any],
) -> None:
    # Searchable Raw JSON (TICKET-R3 polish). Operators frequently grep
    # the reports for a single metric like "sharpe" or "max_drawdown"
    # without wanting to dig through every artifact tree by hand.
    raw_query = st.text_input(
        "搜索原始 JSON",
        key="results_raw_json_query",
        placeholder="例如：sharpe、drawdown、fold_…",
        help="不区分大小写的子串过滤，覆盖所有 key 与标量值。留空显示全部。",
    )

    def _render_panel(label: str, payload: Mapping[str, Any] | None, empty_msg: str) -> None:
        with st.expander(f"原始 {label}", expanded=False):
            if not payload:
                st.info(empty_msg)
                return
            shown = _filter_json_by_query(dict(payload), raw_query)
            if raw_query and not shown:
                st.caption(f"在 {label} 里没有匹配 '{raw_query}' 的内容。")
            else:
                st.json(shown if shown is not None else {})

    _render_panel("metadata.json", metadata, "metadata.json 暂不可用。")
    _render_panel("metrics.json", metrics, "metrics.json 暂不可用。")
    _render_panel("pipeline_report.json", report, "pipeline_report.json 暂不可用。")
    _render_panel("作业元数据", dict(job), "作业元数据暂不可用。")
    _render_panel("positions.json", positions, "positions.json 暂不可用。")


def _render_pipeline_dashboard(
    *,
    job: Mapping[str, Any],
    run_dir: Path | None,
    report: Mapping[str, Any],
    config: Mapping[str, Any],
    config_path: Path | None,
    config_bytes: bytes,
    issues: list[ArtifactReadIssue],
) -> None:
    positions = _read_positions(run_dir, issues)
    metadata = _read_metadata(run_dir, issues)
    metrics = _read_metrics(run_dir, issues)
    holdings_frame = _read_holdings_frame(run_dir, issues)
    trades_frame = _read_trades_frame(run_dir, issues)
    nav_frame = _read_nav_frame(run_dir, issues)
    _render_status_header(
        job=job,
        run_dir=run_dir,
        report=report,
        metadata=metadata,
        config_bytes=config_bytes,
    )
    _render_artifact_issues(issues)
    _render_header_actions(
        job=job,
        run_dir=run_dir,
        config_bytes=config_bytes,
        metrics=metrics,
        metadata=metadata,
    )

    if not report:
        st.info(
            "pipeline_report.json is not available yet. The page is showing "
            "job metadata, config, logs, and any partial artifacts."
        )

    _render_kpis(report, metrics, positions, config)
    _render_interactive_charts(nav_frame, run_dir)
    st.markdown('<div class="qv2-section-title">月度收益</div>', unsafe_allow_html=True)
    _render_monthly_returns(metrics)

    tabs = st.tabs(["持仓", "交易", "配置", "阶段耗时", "日志", "原始 JSON"])
    with tabs[0]:
        _render_holdings_tab(holdings_frame, positions)
    with tabs[1]:
        _render_trades_tab(trades_frame)
    with tabs[2]:
        _render_config_tab(config_path, config_bytes, config)
    with tabs[3]:
        _render_timings_tab(job, report, metadata)
    with tabs[4]:
        _render_logs_tab(job, issues)
    with tabs[5]:
        _render_raw_tab(job, report, metadata, metrics, positions)


def _render_walk_forward_summary(wf_report: Mapping[str, Any]) -> None:
    st.header("滚动验证报告")
    agg = wf_report.get("aggregate_metrics", {}) if isinstance(wf_report.get("aggregate_metrics"), Mapping) else {}
    st.subheader("聚合指标")
    cols = st.columns(4)
    cols[0].metric("平均 IC (1d)", fmt_metric(agg.get("mean_ic_1d")))
    cols[1].metric("平均 IR", fmt_metric(agg.get("mean_information_ratio")))
    cols[2].metric("平均收益", fmt_metric(agg.get("mean_annualized_return")))
    cols[3].metric("最差回撤", fmt_metric(agg.get("worst_drawdown")))

    st.subheader("覆盖区间")
    st.json(wf_report.get("test_window_coverage", {}))

    folds = wf_report.get("folds", [])
    if folds:
        st.subheader("单折概览")
        import pandas as pd

        df = pd.DataFrame([
            {
                "折次": f["fold_index"],
                "IC(1d)": fmt_metric(f.get("ic_1d")),
                "IR": fmt_metric(f.get("information_ratio")),
                "年化收益": fmt_metric(f.get("annualized_return")),
                "最大回撤": fmt_metric(f.get("max_drawdown")),
            }
            for f in folds
        ])
        st.dataframe(df, use_container_width=True)


def _render_tushare_provider(run_dir: Path | None, issues: list[ArtifactReadIssue]) -> None:
    st.header("Tushare 数据源产物")
    if run_dir is None:
        st.info("数据源产物目录暂不可用。")
        return

    metadata = inspect_provider_metadata(str(run_dir))
    st.json(provider_metadata_summary(metadata))

    for error in metadata.errors:
        st.error(error)
    for warning in metadata.warnings:
        st.warning(warning)

    validation = _read_json_artifact(
        metadata.validation_path,
        issues,
        artifact_name="validation.json",
    )
    if validation:
        st.subheader("校验")
        st.json(validation)

    manifest = _read_json_artifact(metadata.manifest_path, issues, artifact_name="manifest.json")
    if manifest:
        st.subheader("清单")
        st.json(manifest)

    _render_artifact_issues(issues)

    st.info(
        "Tushare 数据源作业产出的是 qlib 数据包，不会生成流水线 / 滚动验证 / "
        "训练图表。把这里的 qlib_provider 路径填到训练运行的 provider_uri 即可。"
    )


def _job_label(job: Mapping[str, Any]) -> str:
    job_id = str(job.get("job_id") or "?")
    mode = str(job.get("mode") or "?")
    status = str(job.get("status") or "?")
    return f"{job_id} ({mode}, {status})"


def _query_run_id() -> str:
    value = st.query_params.get("run_id", "")
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value or "").strip()


def _default_job_id(jobs: Sequence[Mapping[str, Any]]) -> str:
    for job in jobs:
        if str(job.get("status") or "").lower() in {"success", "completed"}:
            return str(job.get("job_id") or "")
    return str(jobs[0].get("job_id") or "") if jobs else ""


def _render_run_not_found(run_id: str) -> None:
    escaped_run_id = html.escape(run_id, quote=True)
    render_error_state(
        "运行未找到",
        f"没有找到 ID 为 \"{escaped_run_id}\" 的运行记录。可能已被删除，或链接有误。",
        variant="page",
    )
    if st.button("返回作业列表"):
        st.query_params.clear()
        st.switch_page("pages/jobs.py")


_install_styles()
render_breadcrumbs([("分析", None)])
render_page_header("结果", "查看流水线、滚动验证及数据源运行的产物。")

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

    # Auto-refresh for running jobs
    if str(selected_job.get("status", "")).lower() == "running":
        import time as _time
        st.info("作业仍在运行 — 每 5 秒自动刷新一次。")
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
        wf_report = (
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
