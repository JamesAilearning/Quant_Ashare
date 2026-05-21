"""Results page - read-only dashboard for pipeline and walk-forward artifacts."""

from __future__ import annotations

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
from web.operator_ui.formatting import fmt_metric
from web.operator_ui.job_manager import JobManager
from web.operator_ui.result_exports import bundle_zip_bytes, metrics_csv_bytes, summary_pdf_bytes
from web.operator_ui.training_guards import inspect_provider_metadata, provider_metadata_summary

MISSING = "N/A"
LOG_NAMES = ("stdout.log", "stderr.log", "runner_stdout.log", "runner_stderr.log")


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
            border: 1px solid #e5e7eb;
            border-radius: 12px;
            padding: 18px 20px;
            background: #ffffff;
            margin-bottom: 18px;
        }
        .qv2-header-row {
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 16px;
            flex-wrap: wrap;
        }
        .qv2-run-id {
            font-size: 1.25rem;
            font-weight: 700;
            color: #0f172a;
        }
        .qv2-muted {color: #64748b; font-size: 0.9rem;}
        .qv2-badge {
            display: inline-flex;
            align-items: center;
            border-radius: 999px;
            padding: 4px 10px;
            font-weight: 700;
            font-size: 0.8rem;
            margin-left: 8px;
        }
        .status-success {background: #dcfce7; color: #166534;}
        .status-running {background: #dbeafe; color: #1e40af;}
        .status-failed {background: #fee2e2; color: #991b1b;}
        .status-warning {background: #fef3c7; color: #92400e;}
        .status-muted {background: #f3f4f6; color: #4b5563;}
        .qv2-card {
            border: 1px solid #e5e7eb;
            border-radius: 12px;
            background: #ffffff;
            padding: 18px;
            min-height: 150px;
            box-shadow: 0 1px 3px rgba(15, 23, 42, 0.04);
        }
        .qv2-card-title {
            text-transform: uppercase;
            letter-spacing: 0.05em;
            font-size: 0.78rem;
            color: #64748b;
            font-weight: 700;
            margin-bottom: 10px;
        }
        .qv2-primary {
            font-size: 2rem;
            line-height: 1.15;
            font-weight: 800;
            color: #0f172a;
            margin-bottom: 4px;
        }
        .qv2-positive {color: #16a34a;}
        .qv2-negative {color: #dc2626;}
        .qv2-secondary {
            color: #475569;
            font-size: 0.92rem;
            line-height: 1.65;
        }
        .qv2-section-title {
            margin-top: 28px;
            margin-bottom: 10px;
            font-size: 1.15rem;
            font-weight: 800;
            color: #0f172a;
        }
        .qv2-empty {
            border: 1px dashed #cbd5e1;
            border-radius: 12px;
            padding: 20px;
            color: #64748b;
            background: #f8fafc;
        }
        .qv2-error {
            border-radius: 12px;
            padding: 14px 16px;
            color: #991b1b;
            background: #fee2e2;
            border: 1px solid #fecaca;
            margin: 12px 0 18px;
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
    status = _fmt_text(metadata.get("status") or job.get("status"))
    started = _fmt_text(metadata.get("started_at") or job.get("started_at"))
    ended = _fmt_text(metadata.get("finished_at") or job.get("ended_at"))
    duration_seconds = _finite_float(metadata.get("duration_seconds"))
    duration = (
        f"{int(duration_seconds)}s"
        if duration_seconds is not None
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
                Pipeline Result
                <span class="qv2-badge {status_class}" role="status" aria-live="polite">{_safe_html(status)}</span>
              </div>
              <div class="qv2-muted">Job: {_safe_html(job_id)}</div>
              <div class="qv2-muted">Run directory: {_safe_html(run_dir_text)}</div>
            </div>
            <div class="qv2-muted">
              <div>Started: {_safe_html(started)}</div>
              <div>Ended: {_safe_html(ended)}</div>
              <div>Duration: {_safe_html(duration)}</div>
              <div>Report generated: {_safe_html(generated_at)}</div>
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if status.lower() == "failed":
        error = job.get("error") or job.get("stop_error") or f"returncode={job.get('returncode')}"
        st.markdown(
            f"<div class='qv2-error'>This job failed: {_safe_html(error)}</div>",
            unsafe_allow_html=True,
        )

    if config_bytes:
        st.download_button(
            "Download config.yaml",
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
        if st.button("Re-run with this config", disabled=not config_bytes):
            st.session_state["prefill_config_yaml"] = config_bytes.decode("utf-8", errors="replace")
            st.session_state["prefill_config_source_job"] = str(job.get("job_id") or "")
            st.switch_page(str(Path(__file__).resolve().parent / "config_run.py"))
    with action_cols[1]:
        st.download_button(
            "Export metrics CSV",
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
                    status=str(metadata.get("status") or job.get("status") or ""),
                    metrics=metrics,
                    metadata=metadata,
                )
            except RuntimeError as exc:
                pdf_error = str(exc)
        st.download_button(
            "Export PDF report",
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
            "Export full bundle",
            data=bundle_bytes,
            file_name=f"{job.get('job_id', 'pipeline')}_bundle.zip",
            mime="application/zip",
            disabled=not bundle_bytes,
        )

    with st.expander("Keyboard shortcuts", expanded=False):
        st.markdown(
            """
            - `?`: open shortcut help in this section.
            - `j` / `k`: move to the next / previous job from the Run selector.
            - `r`: re-run this job from the Config & Run page.
            - `e`: use the export buttons above.
            - `1`-`5`: switch between Holdings, Trades, Config, Stage Timings, and Logs.

            Streamlit does not expose global key handlers without a custom
            component, so these are mirrored as visible buttons and tabs.
            """
        )


def _render_artifact_issues(issues: Sequence[ArtifactReadIssue]) -> None:
    if not issues:
        return

    st.markdown(
        '<div class="qv2-section-title">Artifact Read Issues</div>',
        unsafe_allow_html=True,
    )
    for issue in issues:
        st.error(
            f"{issue.artifact_name}: {issue.error_type}: {issue.message} "
            f"(path: {issue.path or MISSING})"
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


def _render_card(title: str, primary: str, primary_class: str, lines: Sequence[str]) -> None:
    line_html = "<br>".join(_safe_html(line) for line in lines)
    st.markdown(
        f"""
        <div class="qv2-card">
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
    ann_return_label = "Primary: annual return"
    if ann_return is None:
        ann_return = _first(metrics, [("performance", "annual_excess_return_with_cost")])
        ann_return_label = "Primary: annual excess return with cost"
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
            "Performance",
            _fmt_percent(ann_return, signed=True),
            _metric_color(ann_return),
            [
                ann_return_label,
                f"Information Ratio: {_fmt_number(information_ratio)}",
                f"Sharpe: {_fmt_number(sharpe)}",
                f"Benchmark: {_fmt_text(benchmark_code)}",
            ],
        )
    with cols[1]:
        _render_card(
            "Risk",
            _fmt_percent(max_drawdown, signed=True),
            " qv2-negative" if _finite_float(max_drawdown) is not None else "",
            [
                f"Annual volatility: {_fmt_percent(volatility)}",
                f"Metric status: {_fmt_text(report.get('metric_status'))}",
                f"Official path: {_fmt_text(report.get('official_backtest_path'))}",
            ],
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
            "Trading",
            f"TopK {_fmt_text(config_section.get('topk') if config_section else config.get('topk'))}",
            "",
            [
                f"N drop: {_fmt_text(config_section.get('n_drop') if config_section else config.get('n_drop'))}",
                f"Position days: {_fmt_int(position_days)}",
                f"Latest holdings: {_fmt_int(latest_count)}",
            ],
        )


def _chart_by_token(charts: Mapping[str, Path], *tokens: str) -> tuple[str, Path] | None:
    lowered_tokens = tuple(token.lower() for token in tokens)
    for label, path in charts.items():
        normalized = label.lower().replace("-", "_").replace(" ", "_")
        if any(token in normalized for token in lowered_tokens):
            return label, path
    return None


def _render_charts(run_dir: Path | None) -> None:
    st.markdown('<div class="qv2-section-title">Charts</div>', unsafe_allow_html=True)
    if run_dir is None:
        st.markdown(
            '<div class="qv2-empty">Charts will appear after the run directory is created.</div>',
            unsafe_allow_html=True,
        )
        return

    charts = discover_charts(run_dir)
    if not charts:
        st.markdown(
            '<div class="qv2-empty">No generated PNG charts found yet.</div>',
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
        st.subheader("Net Asset Value")
        st.image(str(path), use_container_width=True)

    chart_cols = st.columns(2)
    if drawdown is not None:
        label, path = drawdown
        used.add(label)
        with chart_cols[0]:
            st.subheader("Drawdown")
            st.image(str(path), use_container_width=True)
    if monthly is not None:
        label, path = monthly
        used.add(label)
        with chart_cols[1]:
            st.subheader("Monthly Returns")
            st.image(str(path), use_container_width=True)

    remaining = [(label, path) for label, path in charts.items() if label not in used]
    if remaining:
        with st.expander("Other generated charts", expanded=False):
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
        selected_date = st.selectbox("Position date", dates, index=len(dates) - 1)
        search = st.text_input("Search holdings", value="", placeholder="Stock code")
        top_n = st.number_input("Show top holdings", value=100, min_value=1, max_value=1000)
        filtered = holdings_frame[
            holdings_frame["date"].astype(str).str.slice(0, 10) == selected_date
        ]
        if search.strip():
            filtered = filtered[
                filtered["stock"].astype(str).str.contains(search.strip(), case=False, na=False)
            ]
        filtered = filtered.sort_values("rank", kind="stable").head(int(top_n))
        st.dataframe(filtered, use_container_width=True, hide_index=True)
        st.download_button(
            "Export holdings CSV",
            data=filtered.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"holdings_{selected_date}.csv",
            mime="text/csv",
        )
        return

    if not positions:
        st.info("Holdings will appear after holdings.parquet or positions.json is written.")
        return

    dates = sorted(str(key) for key in positions.keys())
    selected_date = st.selectbox("Position date", dates, index=len(dates) - 1)
    date_positions = positions.get(selected_date)
    if not isinstance(date_positions, Mapping) or not date_positions:
        st.info("No holdings for the selected date.")
        return

    import pandas as pd

    rows = [
        {"Instrument": str(instrument), "Weight": _finite_float(weight)}
        for instrument, weight in sorted(date_positions.items(), key=lambda item: str(item[0]))
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _render_trades_tab(trades_frame: Any) -> None:
    if trades_frame is None:
        st.info("A trade-log artifact is not available for this run yet.")
        return
    if trades_frame.empty:
        st.info(
            "trades.parquet exists, but the current canonical runtime does "
            "not expose trade-level fills yet."
        )
        return
    frame = trades_frame.copy()
    if "date" in frame and not frame.empty:
        dates = sorted(str(value)[:10] for value in frame["date"].dropna().unique())
        selected_dates = st.multiselect("Trade dates", dates, default=dates)
        if selected_dates:
            frame = frame[frame["date"].astype(str).str.slice(0, 10).isin(selected_dates)]
    if "side" in frame and not frame.empty:
        sides = sorted(str(value) for value in frame["side"].dropna().unique())
        selected_sides = st.multiselect("Side", sides, default=sides)
        if selected_sides:
            frame = frame[frame["side"].astype(str).isin(selected_sides)]
    search = st.text_input("Search trades", value="", placeholder="Stock code")
    if search.strip() and "stock" in frame:
        frame = frame[frame["stock"].astype(str).str.contains(search.strip(), case=False, na=False)]
    st.dataframe(frame, use_container_width=True, hide_index=True)
    st.download_button(
        "Export trades CSV",
        data=frame.to_csv(index=False).encode("utf-8-sig"),
        file_name="trades.csv",
        mime="text/csv",
    )


def _render_interactive_charts(nav_frame: Any, run_dir: Path | None) -> None:
    st.markdown('<div class="qv2-section-title">Net Asset Value</div>', unsafe_allow_html=True)
    if nav_frame is None or nav_frame.empty:
        st.markdown(
            '<div class="qv2-empty">Backtest NAV artifact is not available yet.</div>',
            unsafe_allow_html=True,
        )
        _render_charts(run_dir)
        return

    try:
        import plotly.graph_objects as go
    except ImportError:
        st.warning("plotly is not installed; falling back to generated PNG charts.")
        _render_charts(run_dir)
        return

    frame = nav_frame.copy()
    frame["date"] = frame["date"].astype(str).str.slice(0, 10)
    nav_fig = go.Figure()
    nav_fig.add_trace(go.Scatter(
        x=frame["date"],
        y=frame["strategy_nav"],
        mode="lines",
        name="Strategy NAV",
        line={"width": 2.4, "color": "#3B82F6"},
    ))
    if "benchmark_nav" in frame and frame["benchmark_nav"].notna().any():
        nav_fig.add_trace(go.Scatter(
            x=frame["date"],
            y=frame["benchmark_nav"],
            mode="lines",
            name="Benchmark NAV",
            line={"width": 1.8, "color": "#94A3B8", "dash": "dash"},
        ))
    nav_fig.update_layout(
        height=420,
        hovermode="x unified",
        margin={"l": 36, "r": 20, "t": 20, "b": 36},
        yaxis={"title": "NAV", "rangemode": "tozero"},
        xaxis={
            "title": "",
            "rangeselector": {
                "buttons": [
                    {"count": 1, "label": "1M", "step": "month", "stepmode": "backward"},
                    {"count": 3, "label": "3M", "step": "month", "stepmode": "backward"},
                    {"count": 6, "label": "6M", "step": "month", "stepmode": "backward"},
                    {"count": 1, "label": "1Y", "step": "year", "stepmode": "backward"},
                    {"step": "all", "label": "ALL"},
                ]
            },
        },
        legend={"orientation": "h", "yanchor": "bottom", "y": -0.24, "xanchor": "left", "x": 0},
    )
    st.plotly_chart(nav_fig, use_container_width=True)

    st.markdown('<div class="qv2-section-title">Drawdown</div>', unsafe_allow_html=True)
    dd_fig = go.Figure()
    if "strategy_drawdown" in frame:
        dd_fig.add_trace(go.Scatter(
            x=frame["date"],
            y=frame["strategy_drawdown"],
            mode="lines",
            name="Strategy Drawdown",
            fill="tozeroy",
            line={"width": 2.0, "color": "#DC2626"},
        ))
    if "benchmark_drawdown" in frame and frame["benchmark_drawdown"].notna().any():
        dd_fig.add_trace(go.Scatter(
            x=frame["date"],
            y=frame["benchmark_drawdown"],
            mode="lines",
            name="Benchmark Drawdown",
            line={"width": 1.5, "color": "#94A3B8", "dash": "dash"},
        ))
    dd_fig.update_layout(
        height=320,
        hovermode="x unified",
        margin={"l": 36, "r": 20, "t": 20, "b": 36},
        yaxis={"title": "Drawdown", "tickformat": ".1%"},
        xaxis={"title": ""},
        legend={"orientation": "h", "yanchor": "bottom", "y": -0.28, "xanchor": "left", "x": 0},
    )
    st.plotly_chart(dd_fig, use_container_width=True)


def _render_monthly_returns(metrics: Mapping[str, Any]) -> None:
    rows = metrics.get("monthly_returns")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)) or not rows:
        st.markdown(
            '<div class="qv2-empty">Monthly returns are not available yet.</div>',
            unsafe_allow_html=True,
        )
        return
    import pandas as pd

    frame = pd.DataFrame(rows)
    for column in ("strategy", "benchmark"):
        if column in frame:
            frame[column] = frame[column].map(lambda value: _fmt_percent(value, signed=True))
    st.dataframe(frame, use_container_width=True, hide_index=True)


def _render_config_tab(config_path: Path | None, config_bytes: bytes, config: Mapping[str, Any]) -> None:
    if not config_bytes:
        st.info("config.yaml is not available for this job.")
        if config:
            st.json(config)
        return

    st.download_button(
        "Download exact runtime config",
        data=config_bytes,
        file_name="config.yaml",
        mime="text/yaml",
        key="detail_config_download",
    )
    st.caption(f"Source: {_fmt_text(config_path)}")
    try:
        config_text = config_bytes.decode("utf-8")
    except UnicodeDecodeError:
        config_text = "<config.yaml is not valid UTF-8>"
    st.code(config_text, language="yaml")


def _render_timings_tab(job: Mapping[str, Any], report: Mapping[str, Any], metadata: Mapping[str, Any]) -> None:
    progress = job.get("progress") if isinstance(job.get("progress"), Mapping) else {}
    rows = {
        "status": metadata.get("status") or job.get("status"),
        "started_at": metadata.get("started_at") or job.get("started_at"),
        "ended_at": metadata.get("finished_at") or job.get("ended_at"),
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
        st.info("Job log directory is not available.")
        return

    any_log = False
    for name in LOG_NAMES:
        path = job_dir / name
        text = _read_text_artifact(path, issues, artifact_name=name, tail_chars=30_000)
        if not text:
            continue
        any_log = True
        with st.expander(name, expanded=name in {"stderr.log", "runner_stderr.log"}):
            st.caption(str(path))
            st.code(text, language="text")

    if not any_log:
        st.info("Log files are empty or have not been written yet.")


def _render_raw_tab(
    job: Mapping[str, Any],
    report: Mapping[str, Any],
    metadata: Mapping[str, Any],
    metrics: Mapping[str, Any],
    positions: Mapping[str, Any],
) -> None:
    with st.expander("Raw metadata.json", expanded=False):
        if metadata:
            st.json(metadata)
        else:
            st.info("metadata.json is not available yet.")
    with st.expander("Raw metrics.json", expanded=False):
        if metrics:
            st.json(metrics)
        else:
            st.info("metrics.json is not available yet.")
    with st.expander("Raw pipeline_report.json", expanded=False):
        if report:
            st.json(report)
        else:
            st.info("pipeline_report.json is not available yet.")
    with st.expander("Raw job metadata", expanded=False):
        st.json(dict(job))
    with st.expander("Raw positions.json", expanded=False):
        if positions:
            st.json(dict(positions))
        else:
            st.info("positions.json is not available yet.")


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
    st.markdown('<div class="qv2-section-title">Monthly Returns</div>', unsafe_allow_html=True)
    _render_monthly_returns(metrics)

    tabs = st.tabs(["Holdings", "Trades", "Config", "Stage Timings", "Logs", "Raw JSON"])
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
    st.header("Walk-Forward Report")
    agg = wf_report.get("aggregate_metrics", {}) if isinstance(wf_report.get("aggregate_metrics"), Mapping) else {}
    st.subheader("Aggregate Metrics")
    cols = st.columns(4)
    cols[0].metric("Mean IC (1d)", fmt_metric(agg.get("mean_ic_1d")))
    cols[1].metric("Mean IR", fmt_metric(agg.get("mean_information_ratio")))
    cols[2].metric("Mean Return", fmt_metric(agg.get("mean_annualized_return")))
    cols[3].metric("Worst DD", fmt_metric(agg.get("worst_drawdown")))

    st.subheader("Coverage")
    st.json(wf_report.get("test_window_coverage", {}))

    folds = wf_report.get("folds", [])
    if folds:
        st.subheader("Per-Fold Summary")
        import pandas as pd

        df = pd.DataFrame([
            {
                "Fold": f["fold_index"],
                "IC(1d)": fmt_metric(f.get("ic_1d")),
                "IR": fmt_metric(f.get("information_ratio")),
                "Return": fmt_metric(f.get("annualized_return")),
                "MaxDD": fmt_metric(f.get("max_drawdown")),
            }
            for f in folds
        ])
        st.dataframe(df, use_container_width=True)


def _render_tushare_provider(run_dir: Path | None, issues: list[ArtifactReadIssue]) -> None:
    st.header("Tushare Provider Data")
    if run_dir is None:
        st.info("Provider output directory is not available yet.")
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
        st.subheader("Validation")
        st.json(validation)

    manifest = _read_json_artifact(metadata.manifest_path, issues, artifact_name="manifest.json")
    if manifest:
        st.subheader("Manifest")
        st.json(manifest)

    _render_artifact_issues(issues)

    st.info(
        "Tushare provider jobs create qlib data bundles. They do not produce "
        "pipeline reports, walk-forward reports, or training charts. Use this "
        "qlib_provider path as provider_uri for a training run."
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
    st.error(f"Run not found: {run_id}")
    st.caption("It may have been deleted, or the link is wrong.")
    if st.button("Back to Jobs"):
        st.query_params.clear()
        st.rerun()


_install_styles()
st.title("Results")

jobs = JobManager.list_jobs()
viewable_jobs = [
    job for job in jobs
    if str(job.get("mode") or "") in {"pipeline", "walk_forward", "tushare_provider"}
]

if not viewable_jobs:
    st.warning("No UI-launched jobs found. Run a pipeline, walk-forward, or Tushare provider job first.")
else:
    job_ids = [str(job.get("job_id")) for job in viewable_jobs if job.get("job_id")]
    requested_run_id = _query_run_id()
    if requested_run_id and requested_run_id not in job_ids:
        _render_run_not_found(requested_run_id)
        st.stop()
    default_job_id = requested_run_id or _default_job_id(viewable_jobs)
    default_index = job_ids.index(default_job_id) if default_job_id in job_ids else 0
    selected_job_id = st.selectbox(
        "Run",
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
                st.warning("No walk_forward_report.json found in this run directory yet.")
                _render_config_tab(config_path, config_bytes, config)
                _render_logs_tab(selected_job, artifact_issues)
        else:
            _render_artifact_issues(artifact_issues)
            st.warning("No pipeline_report.json or walk_forward_report.json found in this run directory.")
