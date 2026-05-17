"""Config & Run page for launching pipeline, walk-forward, and data jobs."""

from __future__ import annotations

import os
import time

import streamlit as st

from src.core.canonical_backtest_contract import (
    ADJUST_MODE_NONE,
    ADJUST_MODE_POST,
    ADJUST_MODE_PRE,
)
from web.operator_ui.config_forms import (
    PIPELINE_KEYS,
    TUSHARE_PROVIDER_KEYS,
    WALK_FORWARD_KEYS,
    validate_config_keys,
    validate_provider_uri,
)
from web.operator_ui.job_manager import JobManager, JobManagerError
from web.operator_ui.provider_catalog import (
    ProviderCatalogError,
    delete_provider_catalog_entry,
    list_provider_catalog_entries,
)
from web.operator_ui.training_guards import (
    inspect_provider_metadata,
    provider_metadata_summary,
    validate_pipeline_training_inputs,
)


def _parse_instruments(raw: str) -> str | list[str]:
    value = str(raw or "").strip()
    if not value or value.lower() == "all":
        return "all"
    return [item.strip() for item in value.split(",") if item.strip()]


def _has_streamlit_context() -> bool:
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
    except ImportError:
        return False
    return get_script_run_ctx() is not None


st.title("Config & Run")

mode = st.selectbox("Mode", ["pipeline", "walk_forward"])

provider_entries = list_provider_catalog_entries()
if "training_provider_uri" not in st.session_state:
    st.session_state["training_provider_uri"] = ""

selected_entry = None
if provider_entries:
    provider_options = ["Manual provider_uri"] + [entry.label for entry in provider_entries]
    provider_by_label = {entry.label: entry for entry in provider_entries}
    selected_provider = st.selectbox("Saved data source", provider_options)
    if selected_provider != "Manual provider_uri":
        selected_entry = provider_by_label[selected_provider]
        st.session_state["training_provider_uri"] = selected_entry.provider_uri
        st.caption(f"Using saved qlib provider: {selected_entry.provider_uri}")
        if st.button("Delete selected saved data source", type="secondary"):
            try:
                delete_provider_catalog_entry(selected_entry.job_id)
            except ProviderCatalogError as exc:
                st.error(str(exc))
            else:
                st.session_state["training_provider_uri"] = ""
                st.success(f"Deleted saved data source: {selected_entry.job_id}")
                st.rerun()
else:
    st.caption("No saved UI-managed qlib providers found yet. Pull Tushare data or enter provider_uri manually.")

provider_uri = st.text_input(
    "provider_uri *",
    placeholder="D:/qlib_data/my_cn_data",
    key="training_provider_uri",
)
provider_uri_valid = bool(provider_uri and provider_uri.strip())
if not provider_uri_valid:
    st.warning("provider_uri is required to run.")

col1, col2 = st.columns(2)

with col1:
    instruments = st.text_input("instruments", value="csi300")
    feature_handler = st.text_input("feature_handler", value="Alpha158")

    if mode == "pipeline":
        train_start = st.text_input("train_start", value="2022-01-01")
        train_end = st.text_input("train_end", value="2024-12-31")
        valid_start = st.text_input("valid_start", value="2025-01-01")
        valid_end = st.text_input("valid_end", value="2025-06-30")
        test_start = st.text_input("test_start", value="2025-07-01")
        test_end = st.text_input("test_end", value="2025-12-31")
    else:
        overall_start = st.text_input("overall_start", value="2022-01-01")
        overall_end = st.text_input("overall_end", value="2026-02-28")
        train_months = st.number_input("train_months", value=24, min_value=1)
        valid_months = st.number_input("valid_months", value=3, min_value=1)
        test_months = st.number_input("test_months", value=3, min_value=1)
        step_months = st.number_input("step_months", value=3, min_value=1)
        ensemble_window = st.number_input("ensemble_window", value=1, min_value=1)

with col2:
    model_type = st.selectbox("model_type", ["LGBModel", "XGBModel", "CatBoostModel"])
    compute_device = st.radio("compute_device", ["cpu", "gpu"], horizontal=True)
    num_boost_round = st.number_input("num_boost_round", value=1000, min_value=1)
    early_stopping_rounds = st.number_input("early_stopping_rounds", value=50, min_value=1)
    learning_rate = st.number_input("learning_rate", value=0.005, format="%.4f")
    benchmark_code = st.text_input("benchmark_code", value="SH000300")
    topk = st.number_input("topk", value=50, min_value=1)
    n_drop = st.number_input("n_drop", value=5, min_value=0)
    signal_to_execution_lag = st.number_input("signal_to_execution_lag", value=1, min_value=0)

guard_errors: list[str] = []
guard_warnings: list[str] = []
provider_metadata = inspect_provider_metadata(provider_uri)

if provider_uri_valid:
    st.subheader("Provider Preview")
    st.json(provider_metadata_summary(provider_metadata))

if mode == "pipeline":
    guard = validate_pipeline_training_inputs(
        provider_uri=provider_uri,
        instruments=instruments,
        train_start=train_start,
        train_end=train_end,
        valid_start=valid_start,
        valid_end=valid_end,
        test_start=test_start,
        test_end=test_end,
    )
    guard_errors.extend(guard.errors)
    guard_warnings.extend(guard.warnings)
else:
    guard_errors.extend(provider_metadata.errors)
    guard_warnings.extend(provider_metadata.warnings)

if compute_device == "gpu" and model_type != "LGBModel":
    guard_errors.append("GPU training is currently supported only for LGBModel.")

for error in guard_errors:
    st.error(error)
for warning in guard_warnings:
    st.warning(warning)

submitted = st.button("Run", disabled=(not provider_uri_valid or bool(guard_errors)))

if submitted:
    try:
        validate_provider_uri(provider_uri)
    except ValueError as e:
        st.error(str(e))
        st.stop()
    if compute_device == "gpu" and model_type != "LGBModel":
        st.error("GPU training is currently supported only for LGBModel.")
        st.stop()

    config: dict = {
        "provider_uri": provider_uri,
        "instruments": instruments,
        "feature_handler": feature_handler,
        "model_type": model_type,
        "compute_device": compute_device,
        "num_boost_round": num_boost_round,
        "early_stopping_rounds": early_stopping_rounds,
        "learning_rate": learning_rate,
        "benchmark_code": benchmark_code,
        "topk": topk,
        "n_drop": n_drop,
        "signal_to_execution_lag": signal_to_execution_lag,
    }

    if mode == "pipeline":
        config.update({
            "train_start": train_start, "train_end": train_end,
            "valid_start": valid_start, "valid_end": valid_end,
            "test_start": test_start, "test_end": test_end,
        })
        known_keys = PIPELINE_KEYS
    else:
        config.update({
            "overall_start": overall_start, "overall_end": overall_end,
            "train_months": train_months, "valid_months": valid_months,
            "test_months": test_months, "step_months": step_months,
            "ensemble_window": ensemble_window,
        })
        known_keys = WALK_FORWARD_KEYS

    validate_config_keys(config, known_keys)

    job_id = JobManager.start(config, mode)
    st.success(f"Job started: {job_id}")
    st.info(f"Watch output/operator_ui/jobs/{job_id}/stdout.log for logs and progress.")

st.divider()
st.subheader("Tushare Data")
token_present = bool(os.environ.get("TUSHARE_TOKEN", "").strip())
if not token_present:
    st.warning("Set TUSHARE_TOKEN in the environment before pulling Tushare data.")

with st.form("tushare_provider_form"):
    tc1, tc2 = st.columns(2)
    with tc1:
        ts_start_date = st.text_input("start_date", value="2025-01-01")
        ts_end_date = st.text_input("end_date", value="2025-01-31")
        ts_instruments = st.text_input(
            "instruments",
            value="all",
            help="Use all or comma-separated qlib/Tushare codes.",
        )
    with tc2:
        ts_adjust_mode = st.selectbox(
            "data_adjust_mode",
            [ADJUST_MODE_PRE, ADJUST_MODE_POST, ADJUST_MODE_NONE],
        )
        include_hs300 = st.checkbox("include SH000300 benchmark", value=True)
        reuse_staged = st.checkbox("reuse_staged", value=True)

    pull_tushare = st.form_submit_button("Pull Tushare Data", disabled=not token_present)

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
    validate_config_keys(tushare_config, TUSHARE_PROVIDER_KEYS)
    job_id = JobManager.start(tushare_config, "tushare_provider")
    st.success(f"Tushare ingest job started: {job_id}")
    st.info(f"After success, use output/operator_ui/results/{job_id}/qlib_provider as provider_uri.")

st.divider()
st.subheader("Recent Jobs")
jobs = JobManager.list_jobs()
if not jobs:
    st.write("No jobs yet.")
else:
    running_jobs = [j for j in jobs[:10] if j.get("status") == "running"]
    auto_refresh = st.checkbox(
        "Auto-refresh running jobs every 5 seconds",
        value=bool(running_jobs),
        disabled=not running_jobs,
    )

    for j in jobs[:10]:
        status = j.get("status", "unknown")
        progress = j.get("progress") if isinstance(j.get("progress"), dict) else {}
        percent = int(progress.get("percent", 0) or 0)
        label = str(progress.get("label") or status)
        detail = str(progress.get("detail") or "")

        col1, col2, col3, col4 = st.columns([3, 1, 1, 1])
        with col1:
            st.write(f"{j.get('job_id', '?')} - {j.get('mode', '?')}")
            st.progress(percent, text=f"{percent}% - {label}")
            if detail:
                st.caption(detail)
        with col2:
            st.write(status)
        with col3:
            if status == "running" and j.get("pid"):
                if st.button("Stop", key=f"stop_{j.get('job_id')}"):
                    try:
                        JobManager.stop(j["job_id"])
                    except JobManagerError as exc:
                        st.error(str(exc))
                    else:
                        st.rerun()
        with col4:
            job_id = str(j.get("job_id") or "")
            if st.button(
                "Delete",
                key=f"delete_{job_id}",
                disabled=status == "running" or not job_id,
                help="Stop running jobs before deleting their job record and logs.",
            ):
                try:
                    JobManager.delete(job_id)
                except JobManagerError as exc:
                    st.error(str(exc))
                else:
                    st.success(f"Deleted job: {job_id}")
                    st.rerun()

    if auto_refresh and running_jobs and _has_streamlit_context():
        time.sleep(5)
        st.rerun()
