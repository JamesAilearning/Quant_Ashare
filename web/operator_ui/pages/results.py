"""Results page — display pipeline or walk-forward report artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import streamlit as st

from web.operator_ui._path_guard import guard_output_path
from web.operator_ui.chart_reader import discover_charts
from web.operator_ui.formatting import fmt_metric
from web.operator_ui.job_manager import JobManager
from web.operator_ui.report_reader import read_pipeline_report, read_walk_forward_report
from web.operator_ui.training_guards import inspect_provider_metadata, provider_metadata_summary


def _read_json_artifact(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    guard_output_path(path)
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))

st.title("Results")

# Pick a run dir from recent jobs or manual
jobs = JobManager.list_jobs()
completed = [j for j in jobs if j.get("run_dir")]
run_options = {j["run_dir"]: f"{j.get('job_id', '?')} ({j.get('mode', '?')})" for j in completed if j.get("run_dir")}

if not run_options:
    st.warning("No completed runs with artifacts found. Run a pipeline or walk-forward first.")
    st.stop()
    run_options = {str(Path("output").resolve()): "bare import placeholder"}

selected = st.selectbox("Run", options=list(run_options.keys()), format_func=lambda k: run_options[k])
if selected is None:
    selected = next(iter(run_options))
run_dir = Path(selected)
selected_job = next((j for j in completed if j.get("run_dir") == selected), {})

if selected_job.get("mode") == "tushare_provider":
    st.header("Tushare Provider Data")
    metadata = inspect_provider_metadata(str(run_dir))
    st.json(provider_metadata_summary(metadata))

    for error in metadata.errors:
        st.error(error)
    for warning in metadata.warnings:
        st.warning(warning)

    validation = _read_json_artifact(metadata.validation_path)
    if validation:
        st.subheader("Validation")
        st.json(validation)

    manifest = _read_json_artifact(metadata.manifest_path)
    if manifest:
        st.subheader("Manifest")
        st.json(manifest)

    st.info(
        "Tushare provider jobs create qlib data bundles. They do not produce "
        "pipeline reports, walk-forward reports, or training charts. Use this "
        "qlib_provider path as provider_uri for a training run."
    )
    st.stop()

# Detect report type
pipeline_report = read_pipeline_report(run_dir)
wf_report = read_walk_forward_report(run_dir)

if pipeline_report:
    st.header("Pipeline Report")
    risk = pipeline_report.get("risk_analysis", {}).get("excess_return_with_cost", {})
    metrics = {
        "Annualized Return": risk.get("annualized_return"),
        "Max Drawdown": risk.get("max_drawdown"),
        "Information Ratio": risk.get("information_ratio"),
    }
    st.json({k: fmt_metric(v) for k, v in metrics.items()})

    signal = pipeline_report.get("signal_analysis", {})
    if signal:
        st.subheader("Signal Analysis")
        ic_summary = signal.get("ic_summary", {})
        for period, stats in ic_summary.items():
            st.metric(f"IC ({period}d)", fmt_metric(stats.get("mean_ic")))

elif wf_report:
    st.header("Walk-Forward Report")
    agg = wf_report.get("aggregate_metrics", {})
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
        df = pd.DataFrame([{
            "Fold": f["fold_index"],
            "IC(1d)": fmt_metric(f.get("ic_1d")),
            "IR": fmt_metric(f.get("information_ratio")),
            "Return": fmt_metric(f.get("annualized_return")),
            "MaxDD": fmt_metric(f.get("max_drawdown")),
        } for f in folds])
        st.dataframe(df, use_container_width=True)
else:
    st.warning("No pipeline_report.json or walk_forward_report.json found in this run directory.")

# Charts
st.divider()
st.header("Charts")
charts = discover_charts(run_dir)
if charts:
    for label, path in charts.items():
        st.subheader(label)
        st.image(str(path), use_container_width=True)
else:
    st.info("No PNG charts found in this run directory.")
