"""Walk-Forward page — per-fold detail from walk_forward_report.json and fold reports."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from web.operator_ui.chart_reader import discover_charts
from web.operator_ui.job_manager import JobManager
from web.operator_ui.pages.results import _fmt_metric
from web.operator_ui.report_reader import read_fold_reports, read_walk_forward_report

st.title("Walk-Forward Detail")

jobs = JobManager.list_jobs()
wf_jobs = [j for j in jobs if j.get("mode") == "walk_forward" and j.get("run_dir")]
run_options = {j["run_dir"]: j.get("job_id", "?") for j in wf_jobs if j.get("run_dir")}

if not run_options:
    st.warning("No walk-forward runs found. Run a walk-forward first.")
    st.stop()

selected = st.selectbox("Run", options=list(run_options.keys()), format_func=lambda k: run_options[k])
run_dir = Path(selected)

wf_report = read_walk_forward_report(run_dir)
folds = wf_report.get("folds", [])

if not folds:
    # Try direct fold reports
    fold_reports = read_fold_reports(run_dir)
    if fold_reports:
        st.subheader(f"Fold Reports ({len(fold_reports)} folds)")
        for fr in fold_reports:
            with st.expander(f"Fold {fr.get('fold_index', '?')}"):
                metrics = fr.get("metrics", {})
                cols = st.columns(4)
                cols[0].metric("IC(1d)", _fmt_metric(metrics.get("ic_1d")))
                cols[1].metric("IC(5d)", _fmt_metric(metrics.get("ic_5d")))
                cols[2].metric("Return", _fmt_metric(metrics.get("annualized_return")))
                cols[3].metric("IR", _fmt_metric(metrics.get("information_ratio")))

                signal = fr.get("signal_analysis", {})
                ic_decay = signal.get("ic_decay", [])
                if ic_decay:
                    st.subheader("IC Decay")
                    import pandas as pd
                    st.dataframe(pd.DataFrame({"Lag": range(1, len(ic_decay) + 1), "IC": ic_decay}))
    else:
        st.warning("No fold reports found.")
else:
    # Per-fold from aggregate
    st.subheader(f"Folds ({len(folds)})")
    import pandas as pd
    df = pd.DataFrame([{
        "Fold": f["fold_index"],
        "IC(1d)": f.get("ic_1d"),
        "IC(5d)": f.get("ic_5d"),
        "Return": f.get("annualized_return"),
        "MaxDD": f.get("max_drawdown"),
        "IR": f.get("information_ratio"),
    } for f in folds])
    st.dataframe(df.style.format("{:.4f}"), use_container_width=True)

# Charts
charts = discover_charts(run_dir)
if charts:
    st.divider()
    st.header("Charts")
    for label, path in charts.items():
        st.subheader(label)
        st.image(str(path), use_container_width=True)
