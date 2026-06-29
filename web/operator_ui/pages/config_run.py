"""Config & Run page for launching pipeline, walk-forward, and data jobs."""

from __future__ import annotations

import base64
import difflib
import hashlib
from pathlib import Path
from typing import Any, cast

import streamlit as st
import yaml

from web.operator_ui.bundle_health import resolve_default_provider_uri
from web.operator_ui.config_forms import (
    PIPELINE_KEYS,
    WALK_FORWARD_KEYS,
    resolve_namechange_path,
    validate_config_keys,
    validate_provider_uri,
)
from web.operator_ui.config_presets import (
    CUSTOM_PRESET_NAME,
    list_preset_names,
    load_preset,
    sanitise_preset_name,
)
from web.operator_ui.job_manager import JobManager, JobManagerError, JobMode
from web.operator_ui.page_header import render_page_header

# Pure helpers + constants moved to ``_config_run_helpers`` in UI review
# P1-1. Re-exported here so legacy tests that do
# ``from web.operator_ui.pages.config_run import _last_n_days_split``
# (and friends) keep working unchanged. ``noqa: F401`` because the names
# are exposed for callers and consumed by the page body below. Sits in
# the top import block (rather than after ``_PRESETS_DIR``) so that
# running ``ruff check`` against this file alone doesn't trip E402
# "Module level import not at top of file" — Codex P2 on PR #202.
from web.operator_ui.pages._config_run_helpers import (  # noqa: F401
    _PIPELINE_DATE_FALLBACK,
    _calibration_seconds_per_unit,
    _estimate_duration,
    _last_n_days_split,
    _option_index,
    _pipeline_date_defaults,
    _pipeline_work_units,
    _safe_pipeline_last_index,
    _six_increasing_indices,
    _trading_day_options,
    _walk_forward_date_defaults,
)
from web.operator_ui.training_guards import (
    ProviderMetadata,
    _validate_universe_benchmark_alignment,
    inspect_provider_metadata,
    non_production_bundle_error,
    provider_metadata_summary,
    validate_pipeline_training_inputs,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PRESETS_DIR = Path(__file__).resolve().parents[3] / "config" / "presets"

# How many recent completed pipeline jobs to calibrate the duration
# estimate against. A small window keeps the estimate responsive to the
# current machine without over-weighting one outlier (UI review P2-6).
_ESTIMATE_CALIBRATION_WINDOW = 5

# GPU is only wired for LGBModel. Single source for the guard message so the
# pre-submit validation and the final-guard re-check (intentionally duplicated
# predicate) can never drift on wording.
_GPU_ONLY_LGB_MSG = "目前仅 LGBModel 支持 GPU 训练。"


def _duration_seconds(started_at: Any, ended_at: Any) -> float | None:
    """Parse two ISO timestamps into an elapsed-seconds float, or None."""

    if not started_at or not ended_at:
        return None
    from datetime import datetime as _dt

    try:
        start = _dt.fromisoformat(str(started_at).replace("Z", "+00:00"))
        end = _dt.fromisoformat(str(ended_at).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    seconds = (end - start).total_seconds()
    return seconds if seconds > 0 else None


def _gather_calibration_seconds_per_unit() -> float | None:
    """Build a seconds-per-work-unit rate from recent completed pipeline
    jobs so the duration estimate reflects the actual machine rather than
    a hardcoded throughput constant (UI review P2-6).

    Best-effort: any read / parse failure just drops that sample. Returns
    None when there's no usable history, in which case ``_estimate_duration``
    falls back to its formula.
    """

    try:
        jobs = JobManager.list_jobs()
    except Exception:  # noqa: BLE001 — estimate calibration must never break the form
        return None

    samples: list[tuple[dict[str, Any], float]] = []
    for job in jobs:
        if str(job.get("mode") or "") != "pipeline":
            continue
        if str(job.get("status") or "").lower() not in {"success", "completed", "ok"}:
            continue
        seconds = _duration_seconds(job.get("started_at"), job.get("ended_at"))
        if seconds is None:
            continue
        config_path = job.get("config_path")
        if not config_path:
            continue
        try:
            loaded = yaml.safe_load(Path(str(config_path)).read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if isinstance(loaded, dict):
            samples.append((loaded, seconds))
        if len(samples) >= _ESTIMATE_CALIBRATION_WINDOW:
            break

    return _calibration_seconds_per_unit(samples)


def _select_trading_day(
    label: str, *, default: str, metadata: ProviderMetadata,
) -> str:
    # ``st.text_input`` / ``st.selectbox`` return ``str`` at runtime
    # but the streamlit stubs across versions disagree: some declare
    # the return as ``Any`` (CI's older stubs → no-any-return), newer
    # stubs declare it as ``str`` (so a cast would be redundant). A
    # narrow ignore that covers both:
    if not metadata.calendar_dates:
        return st.text_input(label, value=default)  # type: ignore[no-any-return,unused-ignore]
    options = _trading_day_options(metadata.calendar_dates)
    resolved_index = _option_index(options, default)
    if resolved_index < 0:
        # The configured / preset default falls outside the active
        # provider's calendar. Snap to the earliest available date and
        # surface a warning so the operator knows the date they
        # configured isn't what's about to train (UI review P1-9).
        st.warning(
            f"⚠ `{label}` 的默认值 **{default}** 不在所选数据源的交易日历内 "
            f"(`{options[0]}` ~ `{options[-1]}`)，已替换为 **{options[0]}**。"
            "请确认时间窗，或重建覆盖更长区间的生产 bundle（scripts/data_pipeline/）。"
        )
        resolved_index = 0
    return st.selectbox(  # type: ignore[no-any-return,unused-ignore]
        label,
        options=options,
        index=resolved_index,
        help="仅可在所选数据源日历内的交易日中选择。",
    )


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


def _prefill_config() -> dict[str, Any]:
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
render_page_header("配置运行", "配置并启动流水线或滚动验证作业。")

# ---------------------------------------------------------------------------
# Prefill from previous run
# ---------------------------------------------------------------------------
PREFILL_CONFIG = _prefill_config()
if PREFILL_CONFIG:
    source_job = st.session_state.get("prefill_config_source_job", "")
    st.info(f"已从上一次运行 {source_job} 预填配置。启动前请核对参数。")
    prefill_token = f"{source_job}:{hash(str(st.session_state.get('prefill_config_yaml', '')))}"
    if st.session_state.get("prefill_config_applied_token") != prefill_token:
        if PREFILL_CONFIG.get("provider_uri"):
            st.session_state["cr_provider_uri"] = str(PREFILL_CONFIG["provider_uri"])
        for k, v in PREFILL_CONFIG.items():
            if k != "provider_uri" and f"cr_{k}" not in st.session_state:
                st.session_state[f"cr_{k}"] = v
        st.session_state["prefill_config_applied_token"] = prefill_token


def _cr(key: str, default: Any = None) -> Any:
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
        "预设方案",
        preset_options,
        index=preset_idx,
        key="cr_preset_selector",
        help="Smoke = 快速冒烟；Default = 标准；Production = 全量生产；Custom = 自定义。",
    )
    if preset_choice != current_preset and preset_choice != CUSTOM_PRESET_NAME:
        _apply_preset(preset_choice)

with bar_col1:
    # The selectbox below restricts ``mode`` to two of the three
    # ``JobMode`` literals at runtime. ``cast`` narrows ``str`` →
    # ``JobMode`` so the downstream ``JobManager.start(config_dict,
    # mode)`` call type-checks; runtime path is unchanged.
    mode = cast(
        JobMode,
        st.selectbox(
            "模式",
            ["pipeline", "walk_forward"],
            key="cr_mode",
            format_func=lambda v: "流水线" if v == "pipeline" else "滚动验证",
            help="流水线 = 单次训练/测试划分；滚动验证 = 多折滚动。",
        ),
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
    with st.expander("📊 数据", expanded=True):
        # The publisher / UI Tushare ingest + its saved-provider catalog were
        # retired (unify U3). Point provider_uri at a PRODUCTION bundle built by
        # the data-pipeline scripts (scripts/data_pipeline/); QUANT_PROVIDER_URI
        # is the env default for that bundle (ops Phase 1).
        provider_uri = st.text_input(
            "provider_uri *",
            # Prefill the canonical default (config.yaml ${QUANT_PROVIDER_URI:-…}),
            # mirroring the 数据检视 page — a rerun/preset value still wins via
            # _cr. The old placeholder pointed at the legacy NON-PIT bundle
            # (my_cn_data); the system now runs on the PIT bundle.
            value=_cr("provider_uri", resolve_default_provider_uri() or ""),
            placeholder="${QUANT_PROVIDER_URI:-D:/qlib_data/my_cn_data_pit}",
            key="cr_provider_uri",
            help="默认解析 config.yaml / QUANT_PROVIDER_URI（PIT 生产 bundle）；"
                 "每次运行可覆盖，预设不保存此机器本地路径。",
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
            # --- Quick date presets ----------------------------------------
            # Mechanical helpers for common operator needs. Each preset
            # writes the six pipeline date keys to session_state and reruns
            # so the date widgets pick up the new values on next render.
            st.caption("日期范围快捷预设：")
            qd_cols = st.columns(4)

            def _apply_pipeline_dates(values: dict[str, str] | None) -> None:
                if not values:
                    return
                for k, v in values.items():
                    st.session_state[f"cr_{k}"] = v

            with qd_cols[0]:
                if st.button(
                    "全部历史",
                    key="cr_qd_full",
                    use_container_width=True,
                    help="使用数据源全量日历，按 55/65/78/86 比例切分。",
                ):
                    _apply_pipeline_dates(_pipeline_date_defaults(provider_metadata))
                    st.rerun()
            with qd_cols[1]:
                if st.button(
                    "最近 5 年 (3+1+1)",
                    key="cr_qd_5y",
                    use_container_width=True,
                    help="最近 5 个交易年，按 60/20/20 切分训练/验证/测试。",
                ):
                    _apply_pipeline_dates(_last_n_days_split(provider_metadata, 252 * 5))
                    st.rerun()
            with qd_cols[2]:
                if st.button(
                    "最近 3 年 (1.8+0.6+0.6)",
                    key="cr_qd_3y",
                    use_container_width=True,
                    help="最近 3 个交易年，按 60/20/20 切分训练/验证/测试。",
                ):
                    _apply_pipeline_dates(_last_n_days_split(provider_metadata, 252 * 3))
                    st.rerun()
            with qd_cols[3]:
                if st.button(
                    "重置为预设值",
                    key="cr_qd_reset",
                    use_container_width=True,
                    help="重新读取当前预设方案的日期值。",
                ):
                    _active_preset = st.session_state.get("cr_preset", "Default")
                    if _active_preset != CUSTOM_PRESET_NAME:
                        _preset_values = _load_preset(_active_preset) or {}
                        _date_only = {
                            k: v
                            for k, v in _preset_values.items()
                            if k
                            in (
                                "train_start",
                                "train_end",
                                "valid_start",
                                "valid_end",
                                "test_start",
                                "test_end",
                            )
                        }
                        _apply_pipeline_dates(_date_only)
                        st.rerun()

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
            # NOTE: these read the LIVE default recomputed from the current
            # provider's calendar each rerun (NOT via _cr). Routing them through
            # _cr to honour preset/rerun prefill regressed provider-tracking —
            # _cr seed-and-sticks the (provider-dependent) default, freezing a
            # first-render no-calendar fallback and ignoring the recomputed
            # window (codex P2 on #300). Honouring preset/prefill here without
            # losing provider-tracking needs a separate, runtime-verified fix.
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
    with st.expander("🧠 模型", expanded=True):
        model_options = ["LGBModel", "XGBModel", "CatBoostModel"]
        model_default = _cr("model_type", "LGBModel")
        model_type = st.selectbox(
            "模型类型 (model_type)",
            model_options,
            index=model_options.index(model_default) if model_default in model_options else 0,
            key="cr_model_type",
        )
        with st.expander("高级参数", expanded=False):
            ac1, ac2 = st.columns(2)
            with ac1:
                num_boost_round = st.number_input("迭代轮数 (num_boost_round)", value=_cr("num_boost_round", 1000), min_value=1, key="cr_num_boost_round")
                early_stopping_rounds = st.number_input("早停轮数 (early_stopping_rounds)", value=_cr("early_stopping_rounds", 50), min_value=1, key="cr_early_stopping_rounds")
            with ac2:
                learning_rate = st.number_input("学习率 (learning_rate)", value=_cr("learning_rate", 0.005), format="%.4f", key="cr_learning_rate")

    # --- Strategy section ---
    with st.expander("💹 策略", expanded=True):
        sc1, sc2 = st.columns(2)
        with sc1:
            topk = st.number_input("持仓数 (topk)", value=_cr("topk", 50), min_value=1, key="cr_topk")
            n_drop = st.number_input("调仓换出数 (n_drop)", value=_cr("n_drop", 5), min_value=0, key="cr_n_drop")
        with sc2:
            signal_to_execution_lag = st.number_input(
                "信号到执行延迟 (signal_to_execution_lag)",
                value=_cr("signal_to_execution_lag", 1),
                min_value=1,
                key="cr_signal_to_execution_lag",
                help="总延迟（含 qlib 内建一日位移）：1 = T+1 执行。0（当日执行=前视）在正典路径被拒绝。",
            )
            benchmark_code = st.text_input("基准代码 (benchmark_code)", value=_cr("benchmark_code", "SH000300TR"), key="cr_benchmark_code")

    # --- Compute section ---
    with st.expander("⚙️ 算力", expanded=True):
        cc1, cc2 = st.columns(2)
        with cc1:
            device_default = _cr("compute_device", "cpu")
            compute_device = st.radio(
                "计算设备 (compute_device)",
                ["cpu", "gpu"],
                index=1 if device_default == "gpu" else 0,
                horizontal=True,
                key="cr_compute_device",
            )
        with cc2:
            st.caption("Workers：auto")

    # --- Validation ---
    guard_errors: list[str] = []
    guard_warnings: list[str] = []
    # ``auto_fixes`` is parallel to guard_errors: when an error has a known
    # mechanical resolution, we register a (label, callable) pair so the
    # status panel can render a single-click fix. This keeps the existing
    # guard_errors list-of-strings API intact while letting the UI offer
    # to apply common fixes.
    auto_fixes: dict[str, tuple[str, Any]] = {}

    if mode == "pipeline":
        guard = validate_pipeline_training_inputs(
            provider_uri=provider_uri, instruments=instruments,
            train_start=train_start, train_end=train_end,
            valid_start=valid_start, valid_end=valid_end,
            test_start=test_start, test_end=test_end,
            benchmark_code=benchmark_code,
        )
        guard_errors.extend(guard.errors)
        guard_warnings.extend(guard.warnings)
    else:
        guard_errors.extend(provider_metadata.errors)
        guard_warnings.extend(provider_metadata.warnings)
        # walk_forward does NOT run validate_pipeline_training_inputs, so the
        # mode-agnostic checks that live in that guard must be applied here too,
        # or a rolling-validation launch bypasses them:
        #   - the non-production-bundle refusal (codex P1 on PR #231), and
        #   - the universe/benchmark mismatch warning (instruments=all against a
        #     major index inflates "excess vs benchmark" — same pitfall in WF as
        #     in pipeline). Pipeline-only date/embargo checks stay out: WF has
        #     its own rolling-window semantics.
        _wf_non_production_msg = non_production_bundle_error(provider_uri)
        if _wf_non_production_msg:
            guard_errors.append(_wf_non_production_msg)
        _validate_universe_benchmark_alignment(
            instruments, benchmark_code, guard_warnings,
        )

    if compute_device == "gpu" and model_type != "LGBModel":
        guard_errors.append(_GPU_ONLY_LGB_MSG)

        def _fix_gpu_model() -> None:
            st.session_state["cr_model_type"] = "LGBModel"

        auto_fixes[_GPU_ONLY_LGB_MSG] = ("切换为 LGBModel", _fix_gpu_model)

    # Build run config separately from the UI preview; mode is selected outside
    # the runtime config schema and passed to JobManager.start as its own value.
    config_dict: dict[str, Any] = {
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

    # ST/*ST exclusion parity (PR-F, audit E1): both official backtest paths
    # now hard-require a non-empty namechange_path (require_st_mask=True), and
    # this UI emits a STANDALONE job config the runner does not env-expand — so
    # without this the UI run would RAISE after a full train. Operator overrides
    # via QUANT_NAMECHANGE_PATH. setdefault so an explicit value (future widget)
    # still wins.
    config_dict.setdefault("namechange_path", resolve_namechange_path())

    preview_config = {"mode": mode, **config_dict}
    yaml_text = yaml.dump({k: v for k, v in preview_config.items() if v != ""}, default_flow_style=False, allow_unicode=True)
    # Calibrate the estimate against recent completed pipeline jobs when
    # available (UI review P2-6); falls back to the formula otherwise.
    _calibration_rate = _gather_calibration_seconds_per_unit()
    estimated = _estimate_duration(preview_config, seconds_per_unit=_calibration_rate)

    # --- Sticky run bar ---
    st.divider()
    status_col, btn_col = st.columns([3, 2])
    with status_col:
        if guard_errors:
            st.error(f"✗ 共 {len(guard_errors)} 个错误 — 运行前请先修复")
            for err in guard_errors:
                fix = auto_fixes.get(err)
                if fix is None:
                    st.caption(f"  • {err}")
                else:
                    fix_label, fix_callable = fix
                    err_col, fix_col = st.columns([4, 2])
                    with err_col:
                        st.caption(f"  • {err}")
                    with fix_col:
                        # ``hash(err)`` varies across processes (PYTHONHASHSEED),
                        # so a server restart re-keyed the auto-fix button and
                        # any session_state tied to the old key was orphaned.
                        # A stable content hash keeps the widget key constant
                        # for the same error text across restarts (UI review
                        # P2-10).
                        err_key = hashlib.md5(
                            err.encode("utf-8"), usedforsecurity=False
                        ).hexdigest()[:10]
                        # on_click CALLBACK: the fix mutates a widget-bound key
                        # (e.g. _fix_gpu_model sets cr_model_type, the model-type
                        # selectbox key) — legal in a callback (runs before the
                        # widget is re-instantiated), whereas the old inline call
                        # crashed with StreamlitAPIException on Streamlit 1.57
                        # (audit G). No st.rerun() — callbacks auto-rerun.
                        st.button(
                            fix_label,
                            key=f"cr_fix_{err_key}",
                            use_container_width=True,
                            on_click=fix_callable,
                        )
        elif guard_warnings:
            st.warning(f"⚠ 共 {len(guard_warnings)} 个警告")
            for warn in guard_warnings:
                st.caption(f"  • {warn}")
        else:
            st.success("✓ 配置有效")
        st.caption(f"预估耗时：{estimated}")

    with btn_col:
        submitted = st.button("🚀 运行", disabled=(not provider_uri_valid or bool(guard_errors)), use_container_width=True)
        if st.button("💾 保存为预设", use_container_width=True):
            st.session_state["cr_saving_preset"] = True

    if submitted:
        try:
            validate_provider_uri(provider_uri)
        except ValueError as e:
            st.error(str(e))
            st.stop()
        if compute_device == "gpu" and model_type != "LGBModel":
            st.error(_GPU_ONLY_LGB_MSG)
            st.stop()
        # Belt-and-braces: re-run the same guard logic that disables the
        # Run button. Streamlit's rerun cycle can lose a race between
        # editing a field and clicking Run — e.g. the operator types
        # ``instruments=csi800`` and clicks before validation reruns,
        # so guard_errors looks empty for one frame and the button is
        # accidentally enabled. Doing the check here catches the stale
        # frame and surfaces the actual error instead of launching a
        # job that will fail in qlib with a confusing missing-file trace.
        #
        # Mode-agnostic: refuse a non-production UI inspection bundle on EVERY
        # launch path. The pipeline-only recheck below would otherwise let a
        # walk_forward launch slip through (codex P1 on PR #231).
        _np_msg = non_production_bundle_error(provider_uri)
        if _np_msg:
            st.error("提交前的最终校验失败，作业未启动：\n- " + _np_msg)
            st.stop()
        if mode == "pipeline":
            _final_guard = validate_pipeline_training_inputs(
                provider_uri=provider_uri,
                instruments=instruments,
                train_start=train_start,
                train_end=train_end,
                valid_start=valid_start,
                valid_end=valid_end,
                test_start=test_start,
                test_end=test_end,
            )
            if _final_guard.errors:
                st.error(
                    "提交前的最终校验失败，作业未启动：\n- "
                    + "\n- ".join(_final_guard.errors)
                )
                st.stop()
        try:
            validate_config_keys(config_dict, known_keys)
            job_id = JobManager.start(config_dict, mode)
        except (ValueError, JobManagerError) as exc:
            st.error(str(exc))
            st.stop()
        st.success(f"作业已启动：{job_id}")
        st.info(f"日志和进度请关注 output/operator_ui/jobs/{job_id}/stdout.log")

    if st.session_state.get("cr_saving_preset"):
        save_name = st.text_input("预设名称", value="my_preset", key="cr_save_name")
        if st.button("确认保存", key="cr_save_confirm"):
            safe = sanitise_preset_name(save_name).lower()
            if not safe:
                st.error("预设名称至少需要一个字母或数字。")
            else:
                save_path = _PRESETS_DIR / f"{safe}.yaml"
                save_path.parent.mkdir(parents=True, exist_ok=True)
                # Presets are portable: never bake machine-local paths into a
                # saved preset (the tracked built-ins omit them). provider_uri is
                # resolved from QUANT_PROVIDER_URI / config.yaml each session, and
                # namechange_path from QUANT_NAMECHANGE_PATH — baking either pins
                # one machine's layout, and a saved inspection-bundle provider_uri
                # gets the preset rejected at launch by the non-production guard.
                preset_to_save = {
                    k: v for k, v in preview_config.items()
                    if k not in ("provider_uri", "namechange_path")
                }
                save_path.write_text(
                    yaml.dump(preset_to_save, default_flow_style=False, allow_unicode=True),
                    encoding="utf-8",
                )
                st.success(f"已保存为 {safe}")
                st.session_state["cr_preset"] = safe
                st.session_state["cr_saving_preset"] = False
                st.rerun()
        if st.button("取消", key="cr_save_cancel"):
            st.session_state["cr_saving_preset"] = False
            st.rerun()

# ===== RIGHT: Live YAML preview =====
with preview_col:
    st.markdown("#### 配置预览")

    # --- Preview actions: copy + diff toggle ---------------------------------
    # Two buttons; both bind directly to session_state flags consumed below
    # the YAML rendering. We snapshot the YAML at click time so a later widget
    # change doesn't shift the copied payload.
    preview_a, preview_b = st.columns(2)
    with preview_a:
        copy_clicked = st.button(
            "📋 复制 YAML",
            key="cr_copy_yaml_btn",
            use_container_width=True,
            help="把预览中的 YAML 复制到剪贴板。",
        )
    with preview_b:
        show_diff = st.toggle(
            "与预设差异对比",
            key="cr_show_diff_toggle",
            value=st.session_state.get("cr_show_diff_toggle", False),
            help="对比当前 YAML 和活跃预设的差异，便于看清你改了哪些字段。",
        )

    if copy_clicked:
        st.session_state["cr_copy_yaml_payload"] = base64.b64encode(
            yaml_text.encode("utf-8")
        ).decode("ascii")

    st.code(yaml_text, language="yaml")

    # --- Diff vs preset ------------------------------------------------------
    if show_diff:
        _diff_baseline = _load_preset(st.session_state.get("cr_preset", "Default"))
        if not _diff_baseline:
            st.caption(
                "无法对比 — 当前预设为 Custom 或加载失败。"
            )
        else:
            _baseline_preview = {"mode": mode, **_diff_baseline}
            baseline_yaml = yaml.dump(
                {k: v for k, v in _baseline_preview.items() if v != ""},
                default_flow_style=False,
                allow_unicode=True,
            )
            diff_lines = list(
                difflib.unified_diff(
                    baseline_yaml.splitlines(),
                    yaml_text.splitlines(),
                    fromfile=f"{st.session_state.get('cr_preset', 'Default')}.yaml",
                    tofile="current",
                    lineterm="",
                )
            )
            if not diff_lines:
                st.caption("✓ 与预设无差异。")
            else:
                st.code("\n".join(diff_lines), language="diff")

    # --- Clipboard write (after the preview so the toast follows the action) -
    if st.session_state.get("cr_copy_yaml_payload"):
        _payload = st.session_state.pop("cr_copy_yaml_payload")
        st.html(
            (
                "<script>"
                "(function(){"
                f"var b64='{_payload}';"
                "try {"
                "  var yaml=atob(b64);"
                "  if (navigator.clipboard) {"
                "    navigator.clipboard.writeText(yaml).catch(function(){});"
                "  } else {"
                "    var ta=window.parent.document.createElement('textarea');"
                "    ta.value=yaml; ta.style.position='fixed'; ta.style.left='-9999px';"
                "    window.parent.document.body.appendChild(ta); ta.select();"
                "    try{document.execCommand('copy');}catch(e){}"
                "    window.parent.document.body.removeChild(ta);"
                "  }"
                "} catch(e) {}"
                "})()"
                "</script>"
            ),
            width="content",
            unsafe_allow_javascript=True,
        )
        st.toast("已复制 YAML 到剪贴板", icon="📋")

# ---------------------------------------------------------------------------
# Provider Preview (below main form)
# ---------------------------------------------------------------------------
if provider_uri_valid:
    with st.expander("📋 数据源信息预览", expanded=False):
        st.json(provider_metadata_summary(provider_metadata))

# ---------------------------------------------------------------------------
# Data ingestion is NOT done in the UI. The Tushare publisher + its ingest page
# were retired (unify U3) — production qlib bundles are built by the data-pipeline
# scripts (scripts/data_pipeline/); point ``provider_uri`` at one
# (QUANT_PROVIDER_URI is its env default, ops Phase 1).
# ---------------------------------------------------------------------------
