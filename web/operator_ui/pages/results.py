"""Results page — display pipeline or walk-forward report artifacts."""

from __future__ import annotations

from pathlib import Path

import streamlit as st
from web.operator_ui.report_reader import read_pipeline_report, read_walk_forward_report
from web.operator_ui.chart_reader import discover_charts
from web.operator_ui.job_manager import JobManager

st.title("Results")

# Pick a run dir from recent jobs or manual
jobs = JobManager.list_jobs()
completed = [j for j in jobs if j.get("run_dir")]
run_options = {j["run_dir"]: f"{j.get('job_id', '?')} ({j.get('mode', '?')})" for j in completed if j.get("run_dir")}

if not run_options:
    st.warning("No completed runs with artifacts found. Run a pipeline or walk-forward first.")
    st.stop()

selected = st.selectbox("Run", options=list(run_options.keys()), format_func=lambda k: run_options[k])
run_dir = Path(selected)

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
    st.json({k: (f"{v:.4f}" if isinstance(v, (int, float)) else str(v)) for k, v in metrics.items()})

    signal = pipeline_report.get("signal_analysis", {})
    if signal:
        st.subheader("Signal Analysis")
        ic_summary = signal.get("ic_summary", {})
        for period, stats in ic_summary.items():
            st.metric(f"IC ({period}d)", f"{stats.get('mean_ic', 'N/A'):.4f}" if isinstance(stats.get('mean_ic'), float) else "N/A")

elif wf_report:
    st.header("Walk-Forward Report")
    agg = wf_report.get("aggregate_metrics", {})
    st.subheader("Aggregate Metrics")
    cols = st.columns(4)
    cols[0].metric("Mean IC (1d)", f"{agg.get('mean_ic_1d', 0):.4f}")
    cols[1].metric("Mean IR", f"{agg.get('mean_information_ratio', 0):.4f}")
    cols[2].metric("Mean Return", f"{agg.get('mean_annualized_return', 0):.4f}")
    cols[3].metric("Worst DD", f"{agg.get('worst_drawdown', 0):.4f}")

    st.subheader("Coverage")
    st.json(wf_report.get("test_window_coverage", {}))

    folds = wf_report.get("folds", [])
    if folds:
        st.subheader("Per-Fold Summary")
        import pandas as pd
        df = pd.DataFrame([{
            "Fold": f["fold_index"],
            "IC(1d)": f.get("ic_1d"),
            "IR": f.get("information_ratio"),
            "Return": f.get("annualized_return"),
            "MaxDD": f.get("max_drawdown"),
        } for f in folds])
        st.dataframe(df.style.format("{:.4f}"), use_container_width=True)
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
