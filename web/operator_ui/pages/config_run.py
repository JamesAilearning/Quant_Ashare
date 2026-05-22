"""Config & Run page for launching pipeline, walk-forward, and data jobs."""

from __future__ import annotations

import os
from datetime import date

import streamlit as st
import yaml

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
from web.operator_ui.page_header import render_breadcrumbs, render_page_header
from web.operator_ui.training_guards import (
    FORWARD_RETURN_BUFFER_DAYS,
    ProviderMetadata,
    inspect_provider_metadata,
    provider_metadata_summary,
    validate_pipeline_training_inputs,
)


def _parse_instruments(raw: str) -> str | list[str]:
    value = str(raw or "").strip()
    if not value or value.lower() == "all":
        return "all"
    return [item.strip() for item in value.split(",") if item.strip()]


def _trading_day_options(calendar_dates: tuple[date, ...]) -> list[str]:
    return [calendar_date.isoformat() for calendar_date in calendar_dates]


def _option_index(options: list[str], default: str) -> int:
    if default in options:
        return options.index(default)
    return 0


def _select_trading_day(
    label: str,
    *,
    default: str,
    metadata: ProviderMetadata,
) -> str:
    if not metadata.calendar_dates:
        return st.text_input(label, value=default)
    options = _trading_day_options(metadata.calendar_dates)
    return st.selectbox(
        label,
        options=options,
        index=_option_index(options, default),
        help="Only trading days from the selected provider calendar are selectable.",
    )


def _safe_pipeline_last_index(calendar_dates: tuple[date, ...]) -> int:
    if len(calendar_dates) > FORWARD_RETURN_BUFFER_DAYS + 1:
        return len(calendar_dates) - FORWARD_RETURN_BUFFER_DAYS - 1
    return max(0, len(calendar_dates) - 2)


def _six_increasing_indices(last_index: int) -> list[int]:
    if last_index < 5:
        return [min(index, max(0, last_index)) for index in range(6)]
    indices = [
        0,
        round(last_index * 0.55),
        round(last_index * 0.65),
        round(last_index * 0.78),
        round(last_index * 0.86),
        last_index,
    ]
    for index in range(1, len(indices) - 1):
        indices[index] = max(indices[index], indices[index - 1] + 1)
    for index in range(len(indices) - 2, -1, -1):
        indices[index] = min(indices[index], indices[index + 1] - 1)
    return indices


def _pipeline_date_defaults(metadata: ProviderMetadata) -> dict[str, str]:
    calendar_dates = metadata.calendar_dates
    if len(calendar_dates) < 6:
        return {
            "train_start": "2022-01-01",
            "train_end": "2024-12-31",
            "valid_start": "2025-01-01",
            "valid_end": "2025-06-30",
            "test_start": "2025-07-01",
            "test_end": "2025-12-31",
        }
    indices = _six_increasing_indices(_safe_pipeline_last_index(calendar_dates))
    keys = ("train_start", "train_end", "valid_start", "valid_end", "test_start", "test_end")
    return {
        key: calendar_dates[index].isoformat()
        for key, index in zip(keys, indices, strict=True)
    }


def _walk_forward_date_defaults(metadata: ProviderMetadata) -> dict[str, str]:
    calendar_dates = metadata.calendar_dates
    if len(calendar_dates) >= 2:
        return {
            "overall_start": calendar_dates[0].isoformat(),
            "overall_end": calendar_dates[-1].isoformat(),
        }
    return {"overall_start": "2022-01-01", "overall_end": "2026-02-28"}


render_breadcrumbs([("Run", None)])
render_page_header(
    "Config & Run",
    "Configure and launch pipeline, walk-forward, or data provider runs.",
)

mode = st.selectbox("Mode", ["pipeline", "walk_forward"])


def _prefill_config() -> dict:
    raw = st.session_state.get("prefill_config_yaml")
    if not raw:
        return {}
    try:
        loaded = yaml.safe_load(str(raw))
    except yaml.YAMLError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


PREFILL_CONFIG = _prefill_config()
if PREFILL_CONFIG:
    source_job = st.session_state.get("prefill_config_source_job", "")
    st.info(f"Prefilled from previous run {source_job}. Review settings before launching.")
    prefill_token = f"{source_job}:{hash(str(st.session_state.get('prefill_config_yaml', '')))}"
    if st.session_state.get("prefill_config_applied_token") != prefill_token:
        if PREFILL_CONFIG.get("provider_uri"):
            st.session_state["training_provider_uri"] = str(PREFILL_CONFIG["provider_uri"])
        st.session_state["prefill_config_applied_token"] = prefill_token


def _prefill(name: str, default):
    return PREFILL_CONFIG.get(name, default)

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

provider_metadata = inspect_provider_metadata(provider_uri)
pipeline_date_defaults = _pipeline_date_defaults(provider_metadata)
walk_forward_date_defaults = _walk_forward_date_defaults(provider_metadata)

col1, col2 = st.columns(2)

with col1:
    instruments = st.text_input("instruments", value=str(_prefill("instruments", "csi300")))
    feature_handler = st.text_input("feature_handler", value=str(_prefill("feature_handler", "Alpha158")))

    if mode == "pipeline":
        train_start = _select_trading_day(
            "train_start",
            default=str(_prefill("train_start", pipeline_date_defaults["train_start"])),
            metadata=provider_metadata,
        )
        train_end = _select_trading_day(
            "train_end",
            default=str(_prefill("train_end", pipeline_date_defaults["train_end"])),
            metadata=provider_metadata,
        )
        valid_start = _select_trading_day(
            "valid_start",
            default=str(_prefill("valid_start", pipeline_date_defaults["valid_start"])),
            metadata=provider_metadata,
        )
        valid_end = _select_trading_day(
            "valid_end",
            default=str(_prefill("valid_end", pipeline_date_defaults["valid_end"])),
            metadata=provider_metadata,
        )
        test_start = _select_trading_day(
            "test_start",
            default=str(_prefill("test_start", pipeline_date_defaults["test_start"])),
            metadata=provider_metadata,
        )
        test_end = _select_trading_day(
            "test_end",
            default=str(_prefill("test_end", pipeline_date_defaults["test_end"])),
            metadata=provider_metadata,
        )
    else:
        overall_start = _select_trading_day(
            "overall_start",
            default=walk_forward_date_defaults["overall_start"],
            metadata=provider_metadata,
        )
        overall_end = _select_trading_day(
            "overall_end",
            default=walk_forward_date_defaults["overall_end"],
            metadata=provider_metadata,
        )
        train_months = st.number_input("train_months", value=24, min_value=1)
        valid_months = st.number_input("valid_months", value=3, min_value=1)
        test_months = st.number_input("test_months", value=3, min_value=1)
        step_months = st.number_input("step_months", value=3, min_value=1)
        ensemble_window = st.number_input("ensemble_window", value=1, min_value=1)

with col2:
    model_options = ["LGBModel", "XGBModel", "CatBoostModel"]
    model_default = str(_prefill("model_type", "LGBModel"))
    model_type = st.selectbox(
        "model_type",
        model_options,
        index=model_options.index(model_default) if model_default in model_options else 0,
    )
    device_default = str(_prefill("compute_device", "cpu"))
    compute_device = st.radio(
        "compute_device",
        ["cpu", "gpu"],
        index=1 if device_default == "gpu" else 0,
        horizontal=True,
    )
    num_boost_round = st.number_input("num_boost_round", value=int(_prefill("num_boost_round", 1000)), min_value=1)
    early_stopping_rounds = st.number_input(
        "early_stopping_rounds",
        value=int(_prefill("early_stopping_rounds", 50)),
        min_value=1,
    )
    learning_rate = st.number_input("learning_rate", value=float(_prefill("learning_rate", 0.005)), format="%.4f")
    benchmark_code = st.text_input("benchmark_code", value=str(_prefill("benchmark_code", "SH000300")))
    topk = st.number_input("topk", value=int(_prefill("topk", 50)), min_value=1)
    n_drop = st.number_input("n_drop", value=int(_prefill("n_drop", 5)), min_value=0)
    signal_to_execution_lag = st.number_input(
        "signal_to_execution_lag",
        value=int(_prefill("signal_to_execution_lag", 1)),
        min_value=0,
    )

guard_errors: list[str] = []
guard_warnings: list[str] = []

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

    try:
        validate_config_keys(config, known_keys)
        job_id = JobManager.start(config, mode)
    except (ValueError, JobManagerError) as exc:
        st.error(str(exc))
        st.stop()
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
    try:
        validate_config_keys(tushare_config, TUSHARE_PROVIDER_KEYS)
        job_id = JobManager.start(tushare_config, "tushare_provider")
    except (ValueError, JobManagerError) as exc:
        st.error(str(exc))
        st.stop()
    st.success(f"Tushare ingest job started: {job_id}")
    st.info(f"After success, use output/operator_ui/results/{job_id}/qlib_provider as provider_uri.")
