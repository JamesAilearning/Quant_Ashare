"""Run History page — browse catalog entries and UI-launched jobs."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from web.operator_ui.job_manager import JobManager
from web.operator_ui.page_header import render_breadcrumbs, render_page_header
from web.operator_ui.report_reader import read_all_catalog_entries

render_breadcrumbs([("History", None)])
render_page_header("Run History", "Browse catalog entries and UI-launched jobs.")

_PAGES_DIR = Path(__file__).resolve().parent

st.header("UI Jobs")
jobs = JobManager.list_jobs()
if jobs:
    import pandas as pd
    df = pd.DataFrame([{
        "Job ID": j.get("job_id", "?"),
        "Mode": j.get("mode", "?"),
        "Status": j.get("status", "?"),
        "Started": str(j.get("started_at") or "")[:19],
    } for j in jobs])
    st.dataframe(df, use_container_width=True)
    job_ids = [str(j.get("job_id")) for j in jobs if j.get("job_id")]
    selected_job_id = st.selectbox("Open job detail", job_ids)
    if st.button("Open selected job in Results"):
        st.query_params["run_id"] = selected_job_id
        st.switch_page(str(_PAGES_DIR / "results.py"))
else:
    st.info("No UI-launched jobs found.")

st.header("Catalog (CLI runs)")
entries = read_all_catalog_entries()
if entries:
    import pandas as pd
    df = pd.DataFrame([{
        "Run ID": e.get("run_id", "")[:40],
        "Engine": e.get("engine", ""),
        "Status": e.get("status", ""),
        "Completed": str(e.get("completed_at") or "")[:19],
    } for e in entries])
    st.dataframe(df, use_container_width=True)
else:
    st.info("No catalog entries found in output/runs/_index.jsonl.")

st.divider()
st.caption("Research Lab — research-only, non-canonical, coming in separate OpenSpec.")
