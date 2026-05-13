"""Config & Run page — configure and launch pipeline or walk-forward runs."""

from __future__ import annotations

import streamlit as st

from web.operator_ui.config_forms import (
    PIPELINE_KEYS,
    WALK_FORWARD_KEYS,
    validate_config_keys,
    validate_provider_uri,
)
from web.operator_ui.job_manager import JobManager

st.title("Config & Run")

mode = st.selectbox("Mode", ["pipeline", "walk_forward"])

with st.form("run_form"):
    col1, col2 = st.columns(2)

    with col1:
        provider_uri = st.text_input("provider_uri *", value="", placeholder="D:/qlib_data/my_cn_data")
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
        num_boost_round = st.number_input("num_boost_round", value=1000, min_value=1)
        early_stopping_rounds = st.number_input("early_stopping_rounds", value=50, min_value=0)
        learning_rate = st.number_input("learning_rate", value=0.005, format="%.4f")
        benchmark_code = st.text_input("benchmark_code", value="SH000300")
        topk = st.number_input("topk", value=50, min_value=1)
        n_drop = st.number_input("n_drop", value=5, min_value=0)
        signal_to_execution_lag = st.number_input("signal_to_execution_lag", value=1, min_value=0)

    provider_uri_valid = bool(provider_uri and provider_uri.strip())
    if not provider_uri_valid:
        st.warning("provider_uri is required to run.")

    submitted = st.form_submit_button("Run", disabled=not provider_uri_valid)

if submitted:
    try:
        validate_provider_uri(provider_uri)
    except ValueError as e:
        st.error(str(e))
        st.stop()

    config: dict = {"provider_uri": provider_uri, "instruments": instruments, "feature_handler": feature_handler}

    if mode == "pipeline":
        config.update({
            "train_start": train_start, "train_end": train_end,
            "valid_start": valid_start, "valid_end": valid_end,
            "test_start": test_start, "test_end": test_end,
        })
        validate_config_keys(config, PIPELINE_KEYS)
    else:
        config.update({
            "overall_start": overall_start, "overall_end": overall_end,
            "train_months": train_months, "valid_months": valid_months,
            "test_months": test_months, "step_months": step_months,
            "ensemble_window": ensemble_window,
        })
        validate_config_keys(config, WALK_FORWARD_KEYS)

    config["model_type"] = model_type
    config["num_boost_round"] = num_boost_round
    config["early_stopping_rounds"] = early_stopping_rounds
    config["learning_rate"] = learning_rate
    config["benchmark_code"] = benchmark_code
    config["topk"] = topk
    config["n_drop"] = n_drop
    config["signal_to_execution_lag"] = signal_to_execution_lag

    job_id = JobManager.start(config, mode)
    st.success(f"Job started: {job_id}")
    st.info(f"Watch output/operator_ui/jobs/{job_id}/stdout.log for progress.")

# Show running jobs
st.divider()
st.subheader("Recent Jobs")
jobs = JobManager.list_jobs()
if not jobs:
    st.write("No jobs yet.")
else:
    for j in jobs[:10]:
        status = j.get("status", "unknown")
        emoji = {"running": "🔄", "success": "✅", "failed": "❌", "stopped": "⏹️"}.get(status, "❓")
        col1, col2, col3 = st.columns([3, 1, 1])
        with col1:
            st.write(f"{emoji} {j.get('job_id', '?')} — {j.get('mode', '?')}")
        with col2:
            st.write(status)
        with col3:
            if status == "running" and j.get("pid"):
                if st.button("⏹️ Stop", key=f"stop_{j.get('job_id')}"):
                    JobManager.stop(j["job_id"])
                    st.rerun()
