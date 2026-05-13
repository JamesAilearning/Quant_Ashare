"""Run History page — browse catalog entries and UI-launched jobs."""

from __future__ import annotations

import streamlit as st
from web.operator_ui.report_reader import read_all_catalog_entries
from web.operator_ui.job_manager import JobManager

st.title("Run History")

st.header("UI Jobs")
jobs = JobManager.list_jobs()
if jobs:
    import pandas as pd
    df = pd.DataFrame([{
        "Job ID": j.get("job_id", "?"),
        "Mode": j.get("mode", "?"),
        "Status": j.get("status", "?"),
        "Started": j.get("started_at", "")[:19],
    } for j in jobs])
    st.dataframe(df, use_container_width=True)
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
        "Completed": e.get("completed_at", "")[:19],
    } for e in entries])
    st.dataframe(df, use_container_width=True)
else:
    st.info("No catalog entries found in output/runs/_index.jsonl.")

st.divider()
st.caption("Research Lab — research-only, non-canonical, coming in separate OpenSpec.")
