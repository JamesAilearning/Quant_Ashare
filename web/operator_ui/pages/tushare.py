"""Tushare data ingestion page.

Extracted from the Config & Run page (TICKET-C polish) so that data
ingestion lives on its own surface and never bleeds into the model-run
form.  The Tushare credentials remain environment-only — the token is
never read into the UI, the preview, or any persisted artifact (per
the project's hard rule on secrets).
"""

from __future__ import annotations

import os

import streamlit as st

from src.core.canonical_backtest_contract import (
    ADJUST_MODE_NONE,
    ADJUST_MODE_POST,
    ADJUST_MODE_PRE,
)
from web.operator_ui.components import render_badge, render_empty_state
from web.operator_ui.config_forms import (
    TUSHARE_PROVIDER_KEYS,
    validate_config_keys,
)
from web.operator_ui.job_manager import JobManager, JobManagerError
from web.operator_ui.page_header import render_breadcrumbs, render_page_header


def _parse_instruments(raw: str) -> str | list[str]:
    value = str(raw or "").strip()
    if not value or value.lower() == "all":
        return "all"
    return [item.strip() for item in value.split(",") if item.strip()]


render_breadcrumbs([("Run", None)])
render_page_header(
    "Tushare Data",
    "Pull A-share daily data into a local qlib bin store. "
    "Use the resulting path as ``provider_uri`` on the Config & Run page.",
)

# ---------------------------------------------------------------------------
# Token presence — fail loudly, never silently fall back (AGENTS.md #8).
# The token is read from os.environ ONLY at submit time, never rendered.
# ---------------------------------------------------------------------------
token_present = bool(os.environ.get("TUSHARE_TOKEN", "").strip())
if not token_present:
    render_empty_state(
        "\U0001f6e1",
        "TUSHARE_TOKEN not set",
        "Set TUSHARE_TOKEN in the operator's environment before pulling data. "
        "The token must never appear in YAML, config files, or commits.",
    )
    st.caption(
        "Tip: add `TUSHARE_TOKEN=…` to your `.env` (already gitignored) "
        "and restart `streamlit run`."
    )
    st.stop()

render_badge("success", "TUSHARE_TOKEN present")
st.caption(
    "The token stays in the operator's environment. Pull requests, "
    "logs, and saved artifacts never reproduce its value."
)

# ---------------------------------------------------------------------------
# Form
# ---------------------------------------------------------------------------
with st.form("tushare_provider_form"):
    tc1, tc2 = st.columns(2)
    with tc1:
        ts_start_date = st.text_input(
            "start_date",
            value="2025-01-01",
            help="Inclusive ISO date.",
        )
        ts_end_date = st.text_input(
            "end_date",
            value="2025-01-31",
            help="Inclusive ISO date.",
        )
        ts_instruments = st.text_input(
            "instruments",
            value="all",
            help="Use ``all`` or comma-separated qlib/Tushare codes (e.g. SH600519,SZ300750).",
        )
    with tc2:
        ts_adjust_mode = st.selectbox(
            "data_adjust_mode",
            [ADJUST_MODE_PRE, ADJUST_MODE_POST, ADJUST_MODE_NONE],
            help="Pre / post / none corresponds to qlib's adjust modes.",
        )
        include_hs300 = st.checkbox(
            "include SH000300 benchmark",
            value=True,
            help="Adds the CSI300 index series to the bin store under SH000300.",
        )
        reuse_staged = st.checkbox(
            "reuse_staged",
            value=True,
            help="Reuse previously staged Parquet files when possible.",
        )
    pull_tushare = st.form_submit_button("Pull Tushare Data")

if pull_tushare:
    tushare_config: dict = {
        "start_date": ts_start_date,
        "end_date": ts_end_date,
        "data_adjust_mode": ts_adjust_mode,
        "instruments": _parse_instruments(ts_instruments),
        "benchmark_indexes": {"SH000300": "000300.SH"} if include_hs300 else {},
        "reuse_staged": reuse_staged,
        "region": "cn",
        "freq": "day",
    }
    try:
        validate_config_keys(tushare_config, TUSHARE_PROVIDER_KEYS)
        job_id = JobManager.start(tushare_config, "tushare_provider")
    except (ValueError, JobManagerError) as exc:
        st.error(str(exc))
        st.stop()
    st.success(f"Tushare ingest job started: {job_id}")
    st.info(
        f"After success, use ``output/operator_ui/results/{job_id}/qlib_provider`` "
        "as ``provider_uri`` on the Config & Run page."
    )
