"""Config & Run page for launching pipeline, walk-forward, and data jobs."""

from __future__ import annotations

import math
import os
from datetime import date
from pathlib import Path
from typing import Any

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
from web.operator_ui.config_presets import (
    CUSTOM_PRESET_NAME,
    list_preset_names,
    load_preset,
    sanitise_preset_name,
)
from web.operator_ui.job_manager import JobManager, JobManagerError
from web.operator_ui.page_header import render_breadcrumbs, render_page_header
from web.operator_ui.provider_catalog import (
    ProviderCatalogError,
    delete_provider_catalog_entry,
    list_provider_catalog_entries,
)
from web.operator_ui.training_guards import (
    FORWARD_RETURN_BUFFER_DAYS,
    ProviderMetadata,
    inspect_provider_metadata,
    provider_metadata_summary,
    validate_pipeline_training_inputs,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PRESETS_DIR = Path(__file__).resolve().parents[3] / "config" / "presets"


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
    label: str, *, default: str, metadata: ProviderMetadata,
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
        0, round(last_index * 0.55), round(last_index * 0.65),
        round(last_index * 0.78), round(last_index * 0.86), last_index,
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
            "train_start": "2022-01-01", "train_end": "2024-12-31",
            "valid_start": "2025-01-01", "valid_end": "2025-06-30",
            "test_start": "2025-07-01", "test_end": "2025-12-31",
        }
    indices = _six_increasing_indices(_safe_pipeline_last_index(calendar_dates))
    keys = ("train_start", "train_end", "valid_start", "valid_end", "test_start", "test_end")
    return {key: calendar_dates[index].isoformat() for key, index in zip(keys, indices, strict=True)}


def _walk_forward_date_defaults(metadata: ProviderMetadata) -> dict[str, str]:
    calendar_dates = metadata.calendar_dates
    if len(calendar_dates) >= 2:
        return {"overall_start": calendar_dates[0].isoformat(), "overall_end": calendar_dates[-1].isoformat()}
    return {"overall_start": "2022-01-01", "overall_end": "2026-02-28"}


def _load_preset(name: str) -> dict[str, Any]:
    return load_preset(_PRESETS_DIR, name)


def _preset_options() -> tuple[str, ...]:
    return list_preset_names(_PRESETS_DIR)


def _apply_preset(preset_name: str) -> None:
    """Apply preset values before matching widgets are instantiated."""
    preset = _load_preset(preset_name)
    if not preset:
        return
    for key, value in preset.items():
        st.session_state[f"cr_{key}"] = value
    st.session_state["cr_preset"] = preset_name


def _detect_preset() -> str:
    """Return the preset name whose values match all current fields, or 'Custom'."""
    for name in _preset_options():
        if name == CUSTOM_PRESET_NAME:
            continue
        preset = _load_preset(name)
        if not preset:
            continue
        match = True
        for key, expected in preset.items():
            current = st.session_state.get(f"cr_{key}")
            # Normalize types for comparison
            if isinstance(expected, int) and isinstance(current, str):
                try:
                    if int(current) != expected:
                        match = False
                        break
                except (ValueError, TypeError):
                    match = False
                    break
            elif isinstance(expected, float) and isinstance(current, str):
                try:
                    if float(current) != expected:
                        match = False
                        break
                except (ValueError, TypeError):
                    match = False
                    break
            elif str(current) != str(expected):
                match = False
                break
        if match:
            return name
    return "Custom"


def _estimate_duration(config: dict) -> str:
    """Heuristic runtime estimate."""
    instruments = str(config.get("instruments", "csi300"))
    n_stocks = 5000 if instruments == "all" else 800 if "800" in instruments else 300
    train_years = 5
    if config.get("mode") == "pipeline":
        try:
            from datetime import datetime
            ts = datetime.strptime(str(config.get("train_start", "2022-01-01")), "%Y-%m-%d")
            te = datetime.strptime(str(config.get("train_end", "2024-12-31")), "%Y-%m-%d")
            train_years = max(1, (te - ts).days / 365)
        except Exception:
            pass
    n_est = int(config.get("num_boost_round", 1000))
    device = str(config.get("compute_device", "cpu"))
    rate = 50000 if device == "gpu" else 5000
    est_seconds = n_stocks * 252 * train_years * 158 / rate * (n_est / 1000) * 1.5
    est_minutes = max(1, int(est_seconds / 60))
    if est_minutes >= 60:
        h = est_minutes // 60
        m = est_minutes % 60
        return f"~{h}h {m}m"
    return f"~{est_minutes} min"


def _prefill_config() -> dict:
    raw = st.session_state.get("prefill_config_yaml")
    if not raw:
        return {}
    try:
        loaded = yaml.safe_load(str(raw))
    except yaml.YAMLError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


# ---------------------------------------------------------------------------
# Page header
# ---------------------------------------------------------------------------
render_breadcrumbs([("Run", None)])
render_page_header("Config & Run", "Configure and launch pipeline or walk-forward runs.")

# ---------------------------------------------------------------------------
# Prefill from previous run
# ---------------------------------------------------------------------------
PREFILL_CONFIG = _prefill_config()
if PREFILL_CONFIG:
    source_job = st.session_state.get("prefill_config_source_job", "")
    st.info(f"Prefilled from previous run {source_job}. Review settings before launching.")
    prefill_token = f"{source_job}:{hash(str(st.session_state.get('prefill_config_yaml', '')))}"
    if st.session_state.get("prefill_config_applied_token") != prefill_token:
        if PREFILL_CONFIG.get("provider_uri"):
            st.session_state["cr_provider_uri"] = str(PREFILL_CONFIG["provider_uri"])
        for k, v in PREFILL_CONFIG.items():
            if k != "provider_uri" and f"cr_{k}" not in st.session_state:
                st.session_state[f"cr_{k}"] = v
        st.session_state["prefill_config_applied_token"] = prefill_token


def _cr(key: str, default=None):
    session_key = f"cr_{key}"
    prefill_value = PREFILL_CONFIG.get(key)
    if prefill_value is not None and session_key not in st.session_state:
        st.session_state[session_key] = prefill_value
    if session_key not in st.session_state:
        st.session_state[session_key] = default
    return st.session_state[session_key]


if "cr_preset_initialized" not in st.session_state:
    if not PREFILL_CONFIG:
        _apply_preset("Default")
    st.session_state["cr_preset_initialized"] = True

# ---------------------------------------------------------------------------
# Mode & Preset bar
# ---------------------------------------------------------------------------
bar_col1, bar_col2 = st.columns(2)
with bar_col2:
    preset_options = _preset_options()
    preset_idx = 1  # Default
    current_preset = st.session_state.get("cr_preset", "Default")
    if current_preset in preset_options:
        preset_idx = preset_options.index(current_preset)
    preset_choice = st.selectbox(
        "Preset",
        preset_options,
        index=preset_idx,
        key="cr_preset_selector",
        help="Smoke = quick test. Default = standard. Production = full. Custom = your own.",
    )
    if preset_choice != current_preset and preset_choice != CUSTOM_PRESET_NAME:
        _apply_preset(preset_choice)

with bar_col1:
    mode = st.selectbox(
        "Mode", ["pipeline", "walk_forward"],
        key="cr_mode",
        help="Pipeline = single train/test split. Walk-Forward = rolling folds.",
    )

# Auto-detect custom when fields diverge
_detected = _detect_preset()
st.session_state["cr_preset"] = _detected

# ---------------------------------------------------------------------------
# Two-column layout: form (left) + YAML preview (right)
# ---------------------------------------------------------------------------
form_col, preview_col = st.columns([0.62, 0.38])

# ===== LEFT: Accordion form =====
with form_col:

    # --- Data section ---
    with st.expander("📊 Data", expanded=True):
        provider_entries = list_provider_catalog_entries()
        selected_entry = None
        if provider_entries:
            provider_options = ["Manual provider_uri"] + [e.label for e in provider_entries]
            provider_by_label = {e.label: e for e in provider_entries}
            selected_label = st.selectbox("Saved data source", provider_options, key="cr_provider_label")
            if selected_label != "Manual provider_uri":
                selected_entry = provider_by_label[selected_label]
                st.session_state["cr_provider_uri"] = selected_entry.provider_uri
                st.caption(f"Using: {selected_entry.provider_uri}")
                if st.button("🗑 Delete selected data source", key="cr_del_provider"):
                    try:
                        delete_provider_catalog_entry(selected_entry.job_id)
                    except ProviderCatalogError as exc:
                        st.error(str(exc))
                    else:
                        st.session_state["cr_provider_uri"] = ""
                        st.success("Deleted.")
                        st.rerun()
            else:
                selected_entry = None
        else:
            st.caption("No saved providers. Enter URI manually or fetch Tushare data below.")

        provider_uri = st.text_input(
            "provider_uri *",
            placeholder="D:/qlib_data/my_cn_data",
            key="cr_provider_uri",
        )
        provider_uri_valid = bool(provider_uri and provider_uri.strip())

        instruments = st.text_input("instruments", value=_cr("instruments", "csi300"), key="cr_instruments")

        feature_handler = st.text_input(
            "feature_handler",
            value=_cr("feature_handler", "Alpha158"),
            key="cr_feature_handler",
        )

        provider_metadata = inspect_provider_metadata(provider_uri)
        pipeline_date_defaults = _pipeline_date_defaults(provider_metadata)
        walk_forward_date_defaults = _walk_forward_date_defaults(provider_metadata)

        if mode == "pipeline":
            dc1, dc2 = st.columns(2)
            with dc1:
                train_start = _select_trading_day(
                    "train_start",
                    default=_cr("train_start", pipeline_date_defaults["train_start"]),
                    metadata=provider_metadata,
                )
                valid_start = _select_trading_day(
                    "valid_start",
                    default=_cr("valid_start", pipeline_date_defaults["valid_start"]),
                    metadata=provider_metadata,
                )
                test_start = _select_trading_day(
                    "test_start",
                    default=_cr("test_start", pipeline_date_defaults["test_start"]),
                    metadata=provider_metadata,
                )
            with dc2:
                train_end = _select_trading_day(
                    "train_end",
                    default=_cr("train_end", pipeline_date_defaults["train_end"]),
                    metadata=provider_metadata,
                )
                valid_end = _select_trading_day(
                    "valid_end",
                    default=_cr("valid_end", pipeline_date_defaults["valid_end"]),
                    metadata=provider_metadata,
                )
                test_end = _select_trading_day(
                    "test_end",
                    default=_cr("test_end", pipeline_date_defaults["test_end"]),
                    metadata=provider_metadata,
                )
        else:
            overall_start = _select_trading_day(
                "overall_start", default=walk_forward_date_defaults["overall_start"],
                metadata=provider_metadata,
            )
            overall_end = _select_trading_day(
                "overall_end", default=walk_forward_date_defaults["overall_end"],
                metadata=provider_metadata,
            )
            wf1, wf2 = st.columns(2)
            with wf1:
                train_months = st.number_input("train_months", value=_cr("train_months", 24), min_value=1, key="cr_train_months")
                valid_months = st.number_input("valid_months", value=_cr("valid_months", 3), min_value=1, key="cr_valid_months")
                test_months = st.number_input("test_months", value=_cr("test_months", 3), min_value=1, key="cr_test_months")
            with wf2:
                step_months = st.number_input("step_months", value=_cr("step_months", 3), min_value=1, key="cr_step_months")
                ensemble_window = st.number_input("ensemble_window", value=_cr("ensemble_window", 1), min_value=1, key="cr_ensemble_window")

    # --- Model section ---
    with st.expander("🧠 Model", expanded=True):
        model_options = ["LGBModel", "XGBModel", "CatBoostModel"]
        model_default = _cr("model_type", "LGBModel")
        model_type = st.selectbox(
            "model_type", model_options,
            index=model_options.index(model_default) if model_default in model_options else 0,
            key="cr_model_type",
        )
        with st.expander("Advanced parameters", expanded=False):
            ac1, ac2 = st.columns(2)
            with ac1:
                num_boost_round = st.number_input("num_boost_round", value=_cr("num_boost_round", 1000), min_value=1, key="cr_num_boost_round")
                early_stopping_rounds = st.number_input("early_stopping_rounds", value=_cr("early_stopping_rounds", 50), min_value=1, key="cr_early_stopping_rounds")
            with ac2:
                learning_rate = st.number_input("learning_rate", value=_cr("learning_rate", 0.005), format="%.4f", key="cr_learning_rate")

    # --- Strategy section ---
    with st.expander("💹 Strategy", expanded=True):
        sc1, sc2 = st.columns(2)
        with sc1:
            topk = st.number_input("topk", value=_cr("topk", 50), min_value=1, key="cr_topk")
            n_drop = st.number_input("n_drop", value=_cr("n_drop", 5), min_value=0, key="cr_n_drop")
        with sc2:
            signal_to_execution_lag = st.number_input("signal_to_execution_lag", value=_cr("signal_to_execution_lag", 1), min_value=0, key="cr_signal_to_execution_lag")
            benchmark_code = st.text_input("benchmark_code", value=_cr("benchmark_code", "SH000300"), key="cr_benchmark_code")

    # --- Compute section ---
    with st.expander("⚙️ Compute", expanded=True):
        cc1, cc2 = st.columns(2)
        with cc1:
            device_default = _cr("compute_device", "cpu")
            compute_device = st.radio("compute_device", ["cpu", "gpu"], index=1 if device_default == "gpu" else 0, horizontal=True, key="cr_compute_device")
        with cc2:
            st.caption("Workers: auto")

    # --- Validation ---
    guard_errors: list[str] = []
    guard_warnings: list[str] = []

    if mode == "pipeline":
        guard = validate_pipeline_training_inputs(
            provider_uri=provider_uri, instruments=instruments,
            train_start=train_start, train_end=train_end,
            valid_start=valid_start, valid_end=valid_end,
            test_start=test_start, test_end=test_end,
        )
        guard_errors.extend(guard.errors)
        guard_warnings.extend(guard.warnings)
    else:
        guard_errors.extend(provider_metadata.errors)
        guard_warnings.extend(provider_metadata.warnings)

    if compute_device == "gpu" and model_type != "LGBModel":
        guard_errors.append("GPU training is currently supported only for LGBModel.")

    # Build run config separately from the UI preview; mode is selected outside
    # the runtime config schema and passed to JobManager.start as its own value.
    config_dict: dict = {
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
        config_dict.update({
            "train_start": train_start, "train_end": train_end,
            "valid_start": valid_start, "valid_end": valid_end,
            "test_start": test_start, "test_end": test_end,
        })
        known_keys = PIPELINE_KEYS
    else:
        config_dict.update({
            "overall_start": overall_start, "overall_end": overall_end,
            "train_months": train_months, "valid_months": valid_months,
            "test_months": test_months, "step_months": step_months,
            "ensemble_window": ensemble_window,
        })
        known_keys = WALK_FORWARD_KEYS

    preview_config = {"mode": mode, **config_dict}
    yaml_text = yaml.dump({k: v for k, v in preview_config.items() if v != ""}, default_flow_style=False, allow_unicode=True)
    estimated = _estimate_duration(preview_config)

    # --- Sticky run bar ---
    st.divider()
    status_col, btn_col = st.columns([3, 2])
    with status_col:
        if guard_errors:
            st.error(f"✗ {len(guard_errors)} error(s) — fix before running")
            for err in guard_errors:
                st.caption(f"  • {err}")
        elif guard_warnings:
            st.warning(f"⚠ {len(guard_warnings)} warning(s)")
        else:
            st.success("✓ Config is valid")
        st.caption(f"Est. duration: {estimated}")

    with btn_col:
        submitted = st.button("🚀 Run", disabled=(not provider_uri_valid or bool(guard_errors)), use_container_width=True)
        if st.button("💾 Save as preset", use_container_width=True):
            st.session_state["cr_saving_preset"] = True

    if submitted:
        try:
            validate_provider_uri(provider_uri)
        except ValueError as e:
            st.error(str(e))
            st.stop()
        if compute_device == "gpu" and model_type != "LGBModel":
            st.error("GPU training is currently supported only for LGBModel.")
            st.stop()
        try:
            validate_config_keys(config_dict, known_keys)
            job_id = JobManager.start(config_dict, mode)
        except (ValueError, JobManagerError) as exc:
            st.error(str(exc))
            st.stop()
        st.success(f"Job started: {job_id}")
        st.info(f"Watch output/operator_ui/jobs/{job_id}/stdout.log for logs and progress.")

    if st.session_state.get("cr_saving_preset"):
        save_name = st.text_input("Preset name", value="my_preset", key="cr_save_name")
        if st.button("Confirm save", key="cr_save_confirm"):
            safe = sanitise_preset_name(save_name).lower()
            if not safe:
                st.error("Preset name must contain at least one letter or digit.")
            else:
                save_path = _PRESETS_DIR / f"{safe}.yaml"
                save_path.parent.mkdir(parents=True, exist_ok=True)
                save_path.write_text(
                    yaml.dump(preview_config, default_flow_style=False, allow_unicode=True),
                    encoding="utf-8",
                )
                st.success(f"Saved as {safe}")
                st.session_state["cr_preset"] = safe
                st.session_state["cr_saving_preset"] = False
                st.rerun()
        if st.button("Cancel", key="cr_save_cancel"):
            st.session_state["cr_saving_preset"] = False
            st.rerun()

# ===== RIGHT: Live YAML preview =====
with preview_col:
    st.markdown("#### Config Preview")
    st.code(yaml_text, language="yaml")

# ---------------------------------------------------------------------------
# Provider Preview (below main form)
# ---------------------------------------------------------------------------
if provider_uri_valid:
    with st.expander("📋 Provider Preview", expanded=False):
        st.json(provider_metadata_summary(provider_metadata))

# ---------------------------------------------------------------------------
# Tushare Data (collapsed)
# ---------------------------------------------------------------------------
with st.expander("📦 Tushare Data", expanded=False):
    token_present = bool(os.environ.get("TUSHARE_TOKEN", "").strip())
    if not token_present:
        st.warning("Set TUSHARE_TOKEN in the environment before pulling Tushare data.")

    with st.form("tushare_provider_form"):
        tc1, tc2 = st.columns(2)
        with tc1:
            ts_start_date = st.text_input("start_date", value="2025-01-01")
            ts_end_date = st.text_input("end_date", value="2025-01-31")
            ts_instruments = st.text_input(
                "instruments", value="all",
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
            "start_date": ts_start_date, "end_date": ts_end_date,
            "data_adjust_mode": ts_adjust_mode,
            "instruments": _parse_instruments(ts_instruments),
            "benchmark_indexes": {"SH000300": "000300.SH"} if include_hs300 else {},
            "reuse_staged": reuse_staged,
            "region": "cn", "freq": "day",
        }
        try:
            validate_config_keys(tushare_config, TUSHARE_PROVIDER_KEYS)
            job_id = JobManager.start(tushare_config, "tushare_provider")
        except (ValueError, JobManagerError) as exc:
            st.error(str(exc))
            st.stop()
        st.success(f"Tushare ingest job started: {job_id}")
        st.info(f"After success, use output/operator_ui/results/{job_id}/qlib_provider as provider_uri.")
