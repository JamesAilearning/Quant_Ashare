"""Config & Run page for launching pipeline, walk-forward, and data jobs."""

from __future__ import annotations

import base64
import difflib
import hashlib
from pathlib import Path
from typing import Any, cast

import streamlit as st
import yaml

from src.core.canonical_backtest_contract import (
    ADJUST_MODE_PRE,
    COMMISSION_RATE_MAX,
    SLIPPAGE_BPS_MAX,
    SUPPORTED_ADJUST_MODES,
)
from src.data.feature_dataset_builder import list_supported_feature_handlers
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
    classify_preset_names,
    frozen_preset_runner,
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
    _RUN_SCOPED_PREFILL_KEYS,
    DIVERGENCE_CHANGED,
    DIVERGENCE_MODE_INAPPLICABLE,
    DIVERGENCE_RUN_SCOPED,
    DIVERGENCE_SOURCE_MISSING,
    _calibration_seconds_per_unit,
    _estimate_duration,
    _last_n_days_split,
    _option_index,
    _pipeline_date_defaults,
    _pipeline_work_units,
    _safe_pipeline_last_index,
    _six_increasing_indices,
    _trading_day_options,
    _values_agree,
    _walk_forward_date_defaults,
    build_config_review_sections,
    config_preset_differences,
    divergences_of,
    explicitly_applied_preset_name,
    portable_config_for_preset_review,
    prefill_baseline_with_source_mode,
    prefill_divergences_from_source_run,
    snapshot_preset_for_review,
    unsupported_prefill_keys,
)
from web.operator_ui.training_guards import (
    ProviderMetadata,
    _validate_universe_benchmark_alignment,
    inspect_provider_metadata,
    non_production_bundle_error,
    provider_metadata_summary,
    validate_csi800_guard_triple,
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

# 选择器上的**显示名**。选项值仍是内置名(``load_preset`` 按它解析文件名),
# 只把标签改成不会撒谎的说法:``production.yaml`` 是 instruments=all 的日频
# 单模型全市场基线,与生产(csi800 / N5 三成员 / 周频 iso_week)无关,而操作人
# 多半只看选项、不展开帮助气泡(codex #445 r1)。
_PRESET_DISPLAY_NAMES: dict[str, str] = {
    "Smoke": "Smoke（快速冒烟）",
    "Default": "Default（标准研究配置）",
    "Production": "全市场基线（instruments=all，日频；**非**生产服务配置）",
}

# Canonical defaults for the backtest / cost-model fields (mirror PipelineConfig
# / WalkForwardConfig). SINGLE source used by the form widgets AND by preset
# apply/detect, so switching to ANY preset — built-in, or an older custom one
# saved before these fields existed — normalizes them to defaults instead of
# leaving stale advanced values from a prior selection (codex P2 on #308).
_COST_FIELD_DEFAULTS: dict[str, Any] = {
    "adjust_mode": ADJUST_MODE_PRE,
    "limit_threshold": 0.095,
    "commission_rate": 0.0005,
    "slippage_bps": 5.0,
    "min_cost": 5.0,
    "init_cash": 100_000_000.0,
    "seed": 42,
}

# The csi800 expansion-guard triple. Same single-source role as the cost
# defaults above: widgets, preset apply and preset detect all read these,
# so a preset that predates the fields normalizes instead of carrying a
# stale value from the previously selected preset.
#
# Values mirror the canonical dataclass defaults (``PipelineConfig`` /
# ``WalkForwardConfig``) — NOT the csi800 contract values. csi800 demands
# True/True/campaign_v1, but that demand belongs to the guard (which
# refuses loudly and offers the fix), not to a default that would silently
# stamp campaign semantics onto a csi300 run.
_GUARD_FIELD_DEFAULTS: dict[str, Any] = {
    "attribution_sleeve_grouping": False,
    "risk_constraints_enabled": False,
    "risk_constraints_calibration": "default",
}

#: Every field a preset switch must normalize (cost + guard).
_RESET_FIELD_DEFAULTS: dict[str, Any] = {
    **_COST_FIELD_DEFAULTS,
    **_GUARD_FIELD_DEFAULTS,
}

# ``cr_preset`` reflects whether the current fields still exactly match a
# preset.  Keep the explicitly applied preset separately, because a later
# field edit deliberately changes the selector to Custom while the final
# review still needs a truthful before-edit comparison baseline.
_REVIEW_PRESET_NAME_STATE = "cr_review_preset_name"
_REVIEW_PRESET_SNAPSHOT_STATE = "cr_review_preset_snapshot"


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
    st.session_state[_REVIEW_PRESET_NAME_STATE] = preset_name
    st.session_state.pop(_REVIEW_PRESET_SNAPSHOT_STATE, None)
    for key, value in preset.items():
        st.session_state[f"cr_{key}"] = value
    # Reset cost-model AND csi800-guard fields the preset doesn't define to
    # canonical defaults so an older / custom preset (saved before these
    # fields existed) can't carry stale advanced values over from a prior
    # selection (codex P2 on #308; guards added with the csi800 fix).
    for key, default in _RESET_FIELD_DEFAULTS.items():
        if key not in preset:
            st.session_state[f"cr_{key}"] = default
    st.session_state["cr_preset"] = preset_name


def _detect_preset() -> str:
    """Return the preset name whose values match all current fields, or 'Custom'.

    Only RUNNABLE presets are candidates: a frozen campaign file reported
    as "active" would name an option the dropdown no longer offers, so the
    selectbox would silently fall back to Default while the state said
    otherwise. It also could never be true in substance — the form has no
    widgets for the very keys (cadence / anchor / scope) that define
    those files.
    """
    runnable, _ = classify_preset_names(_PRESETS_DIR)
    for name in runnable:
        if name == CUSTOM_PRESET_NAME:
            continue
        preset = _load_preset(name)
        if not preset:
            continue
        match = True
        # Treat cost AND guard fields the preset omits as their canonical
        # defaults (mirrors _apply_preset), so a preset without these keys
        # isn't reported as the active selection while the form carries
        # non-default cost / guard values.
        effective = {**_RESET_FIELD_DEFAULTS, **preset}
        for key, expected in effective.items():
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
    """被重跑那次运行的配置,或空 dict。

    解析失败**不静默**:此前 YAMLError 与「顶层不是 dict」都直接返回空
    dict,页面于是一个字都不说——操作人以为没点中按钮,或以为预填成功了。
    失败原因写进 session,由页面响亮报出（fail-loud 纪律）。
    """
    st.session_state.pop("prefill_config_error", None)
    raw = st.session_state.get("prefill_config_yaml")
    if not raw:
        return {}
    try:
        loaded = yaml.safe_load(str(raw))
    except yaml.YAMLError as exc:
        st.session_state["prefill_config_error"] = (
            f"源运行的 config.yaml 解析失败（{type(exc).__name__}）——本次"
            f"**未预填任何字段**:{exc}"
        )
        return {}
    if not isinstance(loaded, dict):
        st.session_state["prefill_config_error"] = (
            f"源运行的 config.yaml 顶层不是映射（读到 "
            f"{type(loaded).__name__}）——本次**未预填任何字段**。"
        )
        return {}
    return loaded


# ---------------------------------------------------------------------------
# Page header
# ---------------------------------------------------------------------------
render_page_header(
    "配置运行",
    "逐步配置并启动日频研究运行；本页不会发布或修改生产 serving。",
)

# ---------------------------------------------------------------------------
# Prefill from previous run
# ---------------------------------------------------------------------------
# `_prefill_config()` 每帧都从同一份 `prefill_config_yaml` 重新解析,所以
# 失败是幂等重现的——不需要跨帧保存错误,读当帧这一次即可。
PREFILL_CONFIG = _prefill_config()
_PREFILL_ERROR = st.session_state.get("prefill_config_error")
if _PREFILL_ERROR:
    st.error(f"⚠ {_PREFILL_ERROR}")
# ---------------------------------------------------------------------------
# 本页**真正会提交**的键
#
# 这不是 PIPELINE_KEYS / WALK_FORWARD_KEYS。那两个是**后端** schema,含本页
# 任何模式下都不发的字段（`run_factor_analysis` 等）。把后端全集当成「本页
# 的字段」用,同一个错误已经在这个 change 里出现过三种形态:
#
#   * `output_dir` 被预填写进 session ⇒ 第二次重跑报一条假的「被覆盖」;
#   * 后端独有字段被复核区标成 mode_only ⇒「切模式即生效」是假的,而
#     `unsupported_prefill_keys` 同时说「本页不支持」,两句自相矛盾;
#   * 同一个字段被预填写进 session ⇒ 下次重跑又报一条假的「被覆盖」,而复核
#     区同时说「本次不会携带它」。
#
# 所以「本页发出什么」只有这一处定义,三个消费者（预填写入、复核区的
# mode_only 判定、提交前的对比基线）都从它派生。
#
# 三份常量与页面下方 `config_dict` 的字面量一一对应,由
# `test_operator_ui_config_run_source` 的 AST 守卫钉住同步——分叉的后果是
# **说错话而不报错**:某个字段被说成会生效/被覆盖,而页面照常提交,没有任何
# 东西会红。
# ---------------------------------------------------------------------------

#: 两个模式共享的提交字段（`config_dict = {...}` 字面量）。
_SHARED_EMITTED = frozenset({
    "adjust_mode", "attribution_sleeve_grouping", "benchmark_code",
    "commission_rate", "compute_device", "early_stopping_rounds",
    "feature_handler", "init_cash", "instruments", "learning_rate",
    "limit_threshold", "min_cost", "model_type", "n_drop",
    "num_boost_round", "provider_uri", "risk_constraints_calibration",
    "risk_constraints_enabled", "seed", "signal_to_execution_lag",
    "slippage_bps", "topk",
})
#: 各模式**额外**发出的字段（两个 `config_dict.update({...})` 字面量）。
_PIPELINE_ONLY_EMITTED = frozenset({
    "train_start", "train_end", "valid_start", "valid_end",
    "test_start", "test_end",
})
_WALK_FORWARD_ONLY_EMITTED = frozenset({
    "overall_start", "overall_end", "train_months", "valid_months",
    "test_months", "step_months", "ensemble_window",
})
#: 本页在**某个**模式下会提交的全部字段。`namechange_path` 由 setdefault 补
#: 上（本页无控件但确实随配置发出）;`mode` 是提交载荷的一部分。
_PAGE_EMITTED_KEYS = (
    _SHARED_EMITTED | _PIPELINE_ONLY_EMITTED | _WALK_FORWARD_ONLY_EMITTED
    | {"namechange_path", "mode"}
)

#: 预填**一次性覆盖**的键。跨模式取并集:源运行可能是另一个模式,它的键要先
#: 落进 session,`mode` 切过去时才有值可用。
#:
#: run-scoped 键（`output_dir`）**不在**上面三份常量里,所以这里不需要再减
#: 一次。刻意不加那道减法:它是 no-op,而 no-op 的兜底恰恰会掩盖「有人把
#: `output_dir` 写进 `_SHARED_EMITTED`」这种错误——那才是要修的地方。守卫在
#: 测试里响亮地钉住两者无交集（fail-loud 优于静默兜底）。
#:
#: 为什么 run-scoped 键必须缺席:`output_dir` 由 JobManager 每次注入,本页从
#: 不提交它。让它进来的话,同一会话里连着重跑两次作业,第二次会把第一次的目
#: 录报成「被覆盖」——一个本页同时声明「随运行而生、不会携带」的字段。假警
#: 告比没有警告更坏:操作人学会忽略整块。
_PREFILL_APPLICABLE_KEYS = _PAGE_EMITTED_KEYS


def _apply_prefill_to_session(
    incoming: dict[str, Any], applicable_keys: frozenset[str],
) -> list[tuple[str, Any, Any]]:
    """把预填值写进本页的字段状态,返回被覆盖的 ``(键, 旧值, 新值)``。

    顶层函数而非模块级散代码,是为了让它能被**真跑**:源码串断言看不见
    session 状态,而这里的每一条规则（覆盖而非跳过、只写已知键、只把值
    不同的记成覆盖）都只在运行时才成立或失败。

    预填**即权威**,无条件覆盖已知键。此前是条件写入（只在该字段的 session
    键尚不存在时才写）,在最常见路径上 100% 失效:``_cr()`` 只要被调用过就把
    ``cr_*`` 种满,所以操作人只要打开过一次本页,之后点「用此配置重跑」就一
    个字段也预填不进来,而横幅照说「已预填」。覆盖是安全的:点重跑本身就是
    显式指令,且时序上晚于本会话此前的任何编辑;调用方的 token 保证每份源
    载荷只应用一次,预填之后的编辑照常生效。

    只覆盖**已知键**:源 YAML 的任意键都写 ``cr_<key>`` 会撞上控件键
    （``cr_preset_selector`` / ``cr_show_diff_toggle`` 等）。
    """

    overwritten: list[tuple[str, Any, Any]] = []
    for key, value in incoming.items():
        if key not in applicable_keys:
            continue
        session_key = f"cr_{key}"
        previous = st.session_state.get(session_key)
        if session_key in st.session_state and not _values_agree(
                previous, value):
            overwritten.append((key, previous, value))
        st.session_state[session_key] = value
    return overwritten


def _prefilled_trading_day(field: str, live_default: str) -> str:
    """滚动验证窗口端点:预填过就用预填值,否则用日历重算的 live default。

    刻意**只读不写**,也刻意不走 ``_cr``。``_cr`` 会把 live default **种进**
    session 并从此粘住,于是第一帧的 no-calendar 回退被冻结、后续按 provider
    日历重算的窗口被无视（codex P2 on #300,那次改动因此被回滚）。这里没有
    预填时一个字节也不写,live default 每帧照常重算,#300 的病根不复现。

    修的是另一个缺陷:``overall_start`` / ``overall_end`` 是滚动验证窗口的
    两个**定义性**字段,预填把它们写进了 session,而控件此前从不读——于是
    「用此配置重跑」一次滚动验证运行,跑的日期区间与源运行不同,而复核区
    看不出来（它比的是控件产出的值,两侧都是 live default）。
    """

    value = st.session_state.get(f"cr_{field}")
    if isinstance(value, str) and value:
        return value
    return live_default


_prefill_overwritten: list[tuple[str, Any, Any]] = []
if PREFILL_CONFIG:
    source_job = st.session_state.get("prefill_config_source_job", "")
    prefill_token = (
        f"{source_job}:"
        f"{hashlib.md5(str(st.session_state.get('prefill_config_yaml', '')).encode('utf-8')).hexdigest()}"
    )
    if st.session_state.get("prefill_config_applied_token") != prefill_token:
        # A rerun prefill is not a preset selection. Clear any prior review
        # identity/snapshot before the automatic field matching below may label
        # it Default or Smoke.
        st.session_state.pop(_REVIEW_PRESET_NAME_STATE, None)
        st.session_state.pop(_REVIEW_PRESET_SNAPSHOT_STATE, None)
        _prefill_overwritten = _apply_prefill_to_session(
            prefill_baseline_with_source_mode(
                PREFILL_CONFIG,
                str(st.session_state.get("prefill_config_source_mode", "")),
            ),
            _PREFILL_APPLICABLE_KEYS,
        )
        st.session_state["prefill_config_applied_token"] = prefill_token
        st.session_state["prefill_overwritten_fields"] = list(
            _prefill_overwritten)
    else:
        _prefill_overwritten = list(
            st.session_state.get("prefill_overwritten_fields") or [])
    st.info(
        f"已从上一次运行 {source_job} 预填配置——本页已按该次运行**覆盖**"
        "相应字段。启动前请核对参数；页面底部的复核区会逐项列出即将提交"
        "的配置与该次运行的差异。"
    )
    if _prefill_overwritten:
        st.warning(
            f"⚠ 预填覆盖了本会话此前的 {len(_prefill_overwritten)} 个字段"
            "（点「用此配置重跑」即以源运行为准）：\n"
            + "\n".join(
                f"- `{_k}`：`{_old}` → `{_new}`"
                for _k, _old, _new in _prefill_overwritten
            )
        )


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
# Research goal & preset
# ---------------------------------------------------------------------------
st.markdown("#### ① 研究目标与预设")
bar_col1, bar_col2 = st.columns(2)
with bar_col2:
    # 只有 UI 形状的预设进下拉框。战役冻结件(无 mode 键)本页跑不了:
    # extends 不解析、rebalance_*/output_dir 无控件会被静默丢弃 —— 混在
    # 一起会让标签显示某个战役预设、发出去的却是日频 pipeline 配置。
    _runnable_presets, _frozen_presets = classify_preset_names(_PRESETS_DIR)
    preset_options = (*_runnable_presets, CUSTOM_PRESET_NAME)
    preset_idx = 1  # Default
    current_preset = st.session_state.get("cr_preset", "Default")
    if current_preset in preset_options:
        preset_idx = preset_options.index(current_preset)
    preset_choice = st.selectbox(
        "预设方案",
        preset_options,
        index=preset_idx,
        key="cr_preset_selector",
        # 选项**值**保持内置名(文件名解析依赖它),只改**显示名**:
        # 帮助文本说清了「Production 不是生产」,但选择器上仍写着
        # Production,操作人多半只看选项不看气泡(codex #445 r1)。
        format_func=lambda name: _PRESET_DISPLAY_NAMES.get(name, name),
        help=(
            "Smoke = 快速冒烟；Default = 标准研究配置；"
            "Production = **全市场基线**（instruments=all，日频单模型）；"
            "Custom = 自定义。**没有一个是生产服务配置** —— 生产跑的是 "
            "csi800 / N5 三成员 ensemble / 周频 iso_week，权威在 "
            "config/serving/csi800_n5_production.yaml，本页产出的一律是"
            "日频研究配置。"
        ),
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

# 「本页产出的是什么」——一句话把生产与研究分开。三项与生产同值
# (宇宙 csi800 / 基准 SH000906TR / topk 50)恰恰最容易让人误以为这就是
# 生产配置的可编辑副本,而唯一不同的那项(节奏)本页既看不见也改不了。
st.caption(
    "本页产出的是**日频研究配置**:没有再平衡节奏控件,提交的配置一律按 "
    "N=1 跑。生产是**周频 iso_week**(N=5,非再平衡日出 HOLD),其权威在 "
    "`config/serving/csi800_n5_production.yaml`,不经本页。"
)

if _frozen_presets:
    with st.expander(f"战役冻结件（{len(_frozen_presets)} 份，只读，不可从本页运行）"):
        st.caption(
            "这些是预注册/认证证据,不是坏文件——但**本页跑不了它们**,原因有三:"
            "① 本页产出 standalone 配置、不解析 `extends`,父配置的窗口/成本/"
            "ST 口径会丢;② `rebalance_*` / `risk_constraint_scope` / "
            "`output_dir` 等键本页没有控件,提交时被静默丢弃;③ 带 `gate3_*` "
            "的几份连命令行 runner 也会硬拒。"
        )
        # 复跑命令按**各自的实际 runner**给,不能一句话统一成
        # run_walk_forward:bootstrap 三成员与 candidate 是 pipeline 形状
        # (extends config.yaml + pipeline 窗口键),walk-forward 加载器会
        # 拒绝它们;gate3 那批根本不该给命令(codex #445 r1)。
        _by_runner: dict[str, list[str]] = {}
        for _name in _frozen_presets:
            _by_runner.setdefault(
                frozen_preset_runner(_load_preset(_name)), []
            ).append(_name)
        _RUNNER_HINTS = {
            "walk_forward": "复跑：`python scripts/run_walk_forward.py "
                            "config/presets/<name>.yaml`",
            "pipeline": "复跑：`python main.py config/presets/<name>.yaml`"
                        "（pipeline 形状，walk-forward 加载器会拒绝它们）",
            "none": "**不可复跑**：带 `gate3_*` 键，runner 硬拒——它们是预注册"
                    "裁决包，不是可跑配置。",
            "unknown": "运行方式未能从文件内容判定——请读该文件头部的运行纪律。",
        }
        for _runner in ("walk_forward", "pipeline", "none", "unknown"):
            _names = _by_runner.get(_runner)
            if not _names:
                continue
            st.caption(f"**{len(_names)} 份** · {_RUNNER_HINTS[_runner]}")
            st.code("\n".join(_names))

# ---------------------------------------------------------------------------
# Two-column layout: form (left) + YAML preview (right)
# ---------------------------------------------------------------------------
form_col, preview_col = st.columns([0.62, 0.38])

# ===== LEFT: Accordion form =====
with form_col:

    # --- Data section ---
    with st.expander("② 数据范围", expanded=True):
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
            # 预填过就用预填值,否则用 provider 日历每帧重算的 live default
            # ——`_prefilled_trading_day` 只读不写,所以 #300 那次回滚的病根
            # (`_cr` 把 live default 种住并冻结 no-calendar 回退) 不复现。
            # 不接线的后果是:重跑一次滚动验证运行,窗口的两个定义性字段仍
            # 是本机 live default,跑的区间与源运行不同(codex P1 on #471)。
            overall_start = _select_trading_day(
                "overall_start",
                default=_prefilled_trading_day(
                    "overall_start",
                    walk_forward_date_defaults["overall_start"]),
                metadata=provider_metadata,
            )
            overall_end = _select_trading_day(
                "overall_end",
                default=_prefilled_trading_day(
                    "overall_end",
                    walk_forward_date_defaults["overall_end"]),
                metadata=provider_metadata,
            )
            wf1, wf2 = st.columns(2)
            with wf1:
                train_months = st.number_input("train_months", value=_cr("train_months", 24), min_value=1, key="cr_train_months")
                valid_months = st.number_input("valid_months", value=_cr("valid_months", 3), min_value=1, key="cr_valid_months")
                test_months = st.number_input("test_months", value=_cr("test_months", 3), min_value=1, key="cr_test_months")
            with wf2:
                step_months = st.number_input("step_months", value=_cr("step_months", 3), min_value=1, key="cr_step_months")
                ensemble_window = st.number_input(
                    "ensemble_window",
                    value=_cr("ensemble_window", 1),
                    min_value=1,
                    key="cr_ensemble_window",
                    # 默认保持 dataclass 默认值 1(改成 3 会与 canonical
                    # 分叉);差异用文案说清而不是偷偷换默认。
                    help=(
                        "1 = 不做 ensemble（in-code 默认）。**正典滚动验证用 3**"
                        "（扫描证据：N=3 在 mean IC / IR / 年化上最优，7/7 折"
                        "配对符号检验 p≈0.008），当前生产模型本身也是三成员 "
                        "ensemble。要与正典基线可比请填 3。"
                    ),
                )

    # --- Strategy section ---
    with st.expander("③ 策略约束", expanded=True):
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

    # --- Advanced model / training section ---
    with st.expander("④ 高级设置 · 模型与训练", expanded=False):
        model_options = ["LGBModel", "XGBModel", "CatBoostModel"]
        model_default = _cr("model_type", "LGBModel")
        model_type = st.selectbox(
            "模型类型 (model_type)",
            model_options,
            index=model_options.index(model_default) if model_default in model_options else 0,
            key="cr_model_type",
        )
        st.markdown("##### 训练参数")
        ac1, ac2 = st.columns(2)
        with ac1:
            num_boost_round = st.number_input("迭代轮数 (num_boost_round)", value=_cr("num_boost_round", 1000), min_value=1, key="cr_num_boost_round")
            early_stopping_rounds = st.number_input("早停轮数 (early_stopping_rounds)", value=_cr("early_stopping_rounds", 50), min_value=1, key="cr_early_stopping_rounds")
        with ac2:
            learning_rate = st.number_input("学习率 (learning_rate)", value=_cr("learning_rate", 0.005), format="%.4f", key="cr_learning_rate")

    # --- Backtest / cost-model section ---
    # Cost-model + risk knobs that PipelineConfig / WalkForwardConfig accept but
    # the form previously left at their (backend) defaults. The literals below
    # mirror those dataclass defaults; the canonical contract validates the job
    # config against the same field set. Collapsed by default — most runs keep
    # the defaults. stamp_tax_schedule is intentionally NOT exposed (it's a
    # dated schedule, not a scalar; backend resolves None -> the 2023-08-28
    # reform default).
    with st.expander("④ 高级设置 · 回测 / 成本模型", expanded=False):
        bc1, bc2 = st.columns(2)
        with bc1:
            adjust_default = str(
                _cr("adjust_mode", _COST_FIELD_DEFAULTS["adjust_mode"])
            )
            # If a hand-edited preset / rerun prefill carries an unsupported
            # adjust_mode, keep it VISIBLE + selected rather than silently
            # coercing to the default — adjust_mode changes official backtest
            # semantics — and let the guard below block Run until it's fixed
            # (codex P2 on #308).
            _adjust_options = list(SUPPORTED_ADJUST_MODES)
            if adjust_default not in _adjust_options:
                _adjust_options.append(adjust_default)
            adjust_mode = st.selectbox(
                "复权模式 (adjust_mode)",
                _adjust_options,
                index=_adjust_options.index(adjust_default),
                key="cr_adjust_mode",
                help="价格复权口径，默认 pre_adjusted。",
            )
            limit_threshold = st.number_input(
                "涨跌停阈值 (limit_threshold)",
                value=float(_cr("limit_threshold", _COST_FIELD_DEFAULTS["limit_threshold"])),
                min_value=0.0, step=0.005, format="%.3f",
                key="cr_limit_threshold",
                help="主板 0.095（±10%）、创业板/科创板 0.195、ST 0.045；须匹配股票池主导板。",
            )
            commission_rate = st.number_input(
                "佣金率 (commission_rate)",
                value=float(_cr("commission_rate", _COST_FIELD_DEFAULTS["commission_rate"])),
                min_value=0.0, step=0.0001, format="%.4f",
                key="cr_commission_rate",
            )
            seed = st.number_input(
                "随机种子 (seed)",
                value=int(_cr("seed", _COST_FIELD_DEFAULTS["seed"])), min_value=0, step=1,
                key="cr_seed",
                help="numpy / 模型随机种子，影响结果可复现性。",
            )
        with bc2:
            slippage_bps = st.number_input(
                "滑点 (slippage_bps)",
                value=float(_cr("slippage_bps", _COST_FIELD_DEFAULTS["slippage_bps"])),
                min_value=0.0, step=0.5, format="%.1f",
                key="cr_slippage_bps",
                # 两个敏感带都是"正确值",取决于你在回答哪个问题——所以这里
                # 说明而不是改默认:把 in-code 默认改成 20 会让每个未声明该键
                # 的预设被静默拖进 conservative 带(csi800 扩池守卫的治理钉
                # 把 5.0 定义为 base 带的 in-code 默认)。
                help=(
                    "base 敏感带 = 5.0（in-code 默认，csi800 扩池战役的基准档）；"
                    "**认证生产口径 = 20.0**（conservative 单边，N5 晋升与 "
                    "config/serving/csi800_n5_production.yaml 同值，盈亏平衡"
                    "参考 ≈73 bps/单边）。要复现认证数字请显式填 20.0。"
                ),
            )
            min_cost = st.number_input(
                "最低单笔成本 (min_cost)",
                value=float(_cr("min_cost", _COST_FIELD_DEFAULTS["min_cost"])),
                min_value=0.0, step=1.0, format="%.1f",
                key="cr_min_cost",
            )
            init_cash = st.number_input(
                "初始资金 (init_cash)",
                value=float(_cr("init_cash", _COST_FIELD_DEFAULTS["init_cash"])),
                min_value=0.0, step=1_000_000.0, format="%.0f",
                key="cr_init_cash",
            )
        st.caption(
            "印花税按 2023-08-28 改革日程自动套用（stamp_tax_schedule），此处不暴露。"
        )

    # --- Compute section ---
    with st.expander("④ 高级设置 · 算力", expanded=False):
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

    with st.expander(
        "④ 高级设置 · csi800 扩池守卫（instruments=csi800 时为必填契约）",
        expanded=False,
    ):
        st.caption(
            "后端把这三项当作 `instruments=csi800` 的**构造前置**:缺一项即"
            "拒绝构造配置(没有 sleeve 分解与 campaign 约束的 csi800 指标,"
            "与认证数字不可比)。本区把它们摆到台面上——以前页面发出的 "
            "csi800 配置不带这三项,作业会在配置构造阶段直接死掉。"
        )
        gc1, gc2 = st.columns(2)
        with gc1:
            attribution_sleeve_grouping = st.checkbox(
                "分腿归因 (attribution_sleeve_grouping)",
                value=bool(
                    _cr(
                        "attribution_sleeve_grouping",
                        _GUARD_FIELD_DEFAULTS["attribution_sleeve_grouping"],
                    )
                ),
                key="cr_attribution_sleeve_grouping",
                help="csi800 契约要求为真;与 industry_artifact_path 互斥。",
            )
            risk_constraints_enabled = st.checkbox(
                "风控约束 (risk_constraints_enabled)",
                value=bool(
                    _cr(
                        "risk_constraints_enabled",
                        _GUARD_FIELD_DEFAULTS["risk_constraints_enabled"],
                    )
                ),
                key="cr_risk_constraints_enabled",
                help="csi800 契约要求为真。",
            )
        with gc2:
            _calibrations = ("default", "campaign_v1")
            _current_calibration = str(
                _cr(
                    "risk_constraints_calibration",
                    _GUARD_FIELD_DEFAULTS["risk_constraints_calibration"],
                )
            )
            risk_constraints_calibration = st.selectbox(
                "风控标定 (risk_constraints_calibration)",
                _calibrations,
                index=(
                    _calibrations.index(_current_calibration)
                    if _current_calibration in _calibrations
                    else 0
                ),
                key="cr_risk_constraints_calibration",
                help="csi800 契约要求 campaign_v1（认证战役标定）。",
            )

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
            metadata=provider_metadata,  # reuse the rerun's single inspect
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
    # Mode-agnostic: the csi800 triple is a construction precondition for
    # BOTH engines, so it is checked outside the mode split.
    validate_csi800_guard_triple(
        instruments,
        attribution_sleeve_grouping,
        risk_constraints_enabled,
        risk_constraints_calibration,
        guard_errors,
    )

    # feature_handler must be one registered in THIS UI process. MinedFactor
    # (and other PIT factor handlers) is only registered when
    # scripts/run_walk_forward.py binds a factor pool — the UI never does — so
    # launching it here is guaranteed to fail (WalkForwardConfig.__post_init__
    # rejects it unless adjust_mode=post_adjusted, and even then the handler is
    # unbound). A plain text_input let an operator type it and only learn after
    # a full handler init; fail loud up front. list_supported_feature_handlers()
    # is the live source of truth (also catches typos).
    _supported_handlers = list_supported_feature_handlers()
    if feature_handler and feature_handler not in _supported_handlers:
        guard_errors.append(
            f"feature_handler={feature_handler!r} 在 UI 进程不可启动（未注册）。"
            f"当前可用：{', '.join(_supported_handlers) or '（无）'}。MinedFactor 等 "
            "PIT 因子需经 scripts/run_walk_forward.py 绑定因子池后运行。"
        )

    if compute_device == "gpu" and model_type != "LGBModel":
        guard_errors.append(_GPU_ONLY_LGB_MSG)

        def _fix_gpu_model() -> None:
            st.session_state["cr_model_type"] = "LGBModel"

        auto_fixes[_GPU_ONLY_LGB_MSG] = ("切换为 LGBModel", _fix_gpu_model)

    # Cost-model range guards mirror the backend contracts so an out-of-range
    # value is blocked in the form instead of only failing at config
    # construction: limit_threshold in (0, 0.25] (Pipeline /
    # CanonicalExchangeConfig), init_cash > 0 (Pipeline / CanonicalAccountConfig).
    if not (0.0 < float(limit_threshold) <= 0.25):
        guard_errors.append(
            f"涨跌停阈值 limit_threshold 须在 (0, 0.25] 区间；当前 {limit_threshold}。"
        )
    if float(init_cash) <= 0:
        guard_errors.append(f"初始资金 init_cash 须为正；当前 {init_cash}。")
    # Upper bounds mirror CanonicalExchangeCostModel ([0, MAX]); the widget min
    # already covers >= 0, so guard the max here (codex P2 on #308).
    if float(commission_rate) > COMMISSION_RATE_MAX:
        guard_errors.append(
            f"佣金率 commission_rate 须 ≤ {COMMISSION_RATE_MAX}；当前 {commission_rate}。"
        )
    if float(slippage_bps) > SLIPPAGE_BPS_MAX:
        guard_errors.append(
            f"滑点 slippage_bps 须 ≤ {SLIPPAGE_BPS_MAX}；当前 {slippage_bps}。"
        )
    if adjust_mode not in SUPPORTED_ADJUST_MODES:
        guard_errors.append(
            f"复权模式 adjust_mode={adjust_mode!r} 无效（预设/预填带入）；"
            f"允许：{', '.join(SUPPORTED_ADJUST_MODES)}。"
        )

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
        # Backtest / cost-model knobs (⚙️ 回测 / 成本模型 expander).
        "adjust_mode": adjust_mode,
        "limit_threshold": limit_threshold,
        "commission_rate": commission_rate,
        "slippage_bps": slippage_bps,
        "min_cost": min_cost,
        "init_cash": init_cash,
        "seed": seed,
        # csi800 扩池守卫三件套。两侧 schema 都收(pipeline + walk_forward),
        # 所以放在 mode 切分之前的共享段;不带它们发出的 csi800 配置会在
        # 后端构造时 raise,而页面此前显示的是「✓ 配置有效 / 作业已启动」。
        "attribution_sleeve_grouping": attribution_sleeve_grouping,
        "risk_constraints_enabled": risk_constraints_enabled,
        "risk_constraints_calibration": risk_constraints_calibration,
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

    # --- Final review -------------------------------------------------------
    st.divider()
    st.subheader("⑤ 提交前复核")
    st.caption(
        "以下内容直接读取本次即将提交的配置。此处只会启动研究运行，"
        "不会发布模型、修改 production serving 或生成交易指令。"
    )

    _selected_preset = str(st.session_state.get("cr_preset", "Default"))
    _review_preset_name = explicitly_applied_preset_name(
        st.session_state.get(_REVIEW_PRESET_NAME_STATE),
        custom_preset_name=CUSTOM_PRESET_NAME,
    )
    _review_preset_config = (
        _load_preset(_review_preset_name)
        if _review_preset_name != CUSTOM_PRESET_NAME
        else None
    )
    _saved_review_snapshot = st.session_state.get(_REVIEW_PRESET_SNAPSHOT_STATE)
    _review_snapshot = (
        _saved_review_snapshot if isinstance(_saved_review_snapshot, dict) else None
    )
    _review_preset = snapshot_preset_for_review(
        preview_config,
        _review_preset_config or None,
        normalization_defaults=_RESET_FIELD_DEFAULTS,
        snapshot=_review_snapshot,
    )
    if _review_preset is None:
        # An unavailable preset must not leave an old baseline behind.  If the
        # same name later becomes readable again, rebuild from its current
        # contents instead of comparing against a stale session snapshot.
        st.session_state.pop(_REVIEW_PRESET_SNAPSHOT_STATE, None)
    elif _review_snapshot is None:
        st.session_state[_REVIEW_PRESET_SNAPSHOT_STATE] = dict(_review_preset)
    _review_sections = build_config_review_sections(preview_config)
    _preset_differences = config_preset_differences(
        preview_config, _review_preset,
    )
    # 比较基线要含源模式:UI 启动的运行把 mode 写进 job.json 而不是归档
    # config.yaml,只比 YAML 的话,把一次 walk_forward 重跑改成 pipeline
    # 会被说成「共有字段逐项一致」(codex P2 on #471)。
    _prefill_baseline = prefill_baseline_with_source_mode(
        PREFILL_CONFIG,
        str(st.session_state.get("prefill_config_source_mode", "")),
    )
    _unsupported_prefill = unsupported_prefill_keys(
        _prefill_baseline, preview_config,
    )
    # `mode` 是**本次提交**的一部分(`preview_config = {"mode": mode, ...}`)
    # 却不在两个 KEYS 常量里,不加就永远不参与比较。
    _review_known_keys = frozenset(known_keys) | {"mode"}
    # 「属于另一个模式」必须是**本页在那个模式下真的会发出**的键,不是后端
    # schema 的全集。用全集的话,像 `run_factor_analysis` 这种「在
    # PIPELINE_KEYS 里、但本页任何模式下都不发」的键会被标成 mode_only
    # (「切模式即生效」——假的),而 unsupported 同时说「本页不支持」:同一个
    # 自相矛盾,只是换了个来源(codex P2 on #471 r4)。
    _review_other_mode_keys = (
        _WALK_FORWARD_ONLY_EMITTED if mode == "pipeline"
        else _PIPELINE_ONLY_EMITTED
    )
    # 与**被重跑那次运行**的差异（不是与预设的差异——上面那张表比的是
    # 预设）。预填现在无条件覆盖已知键,但那只保证「预填那一刻」一致:预
    # 填之后操作人还能改任何字段。提交前把差异摊开,让「我重跑的到底是不
    # 是那次运行」有一处可核对的答案。
    _prefill_divergences = prefill_divergences_from_source_run(
        _prefill_baseline, preview_config,
        known_keys=_review_known_keys,
        other_mode_keys=_review_other_mode_keys,
    )

    with st.expander("完整提交配置（只读）", expanded=True):
        st.caption(
            f"模式：`{mode}` · 当前预设状态：`{_selected_preset}` · "
            f"复核基线：`{_review_preset_name}`"
        )
        for _section in _review_sections:
            st.markdown(f"**{_section.title}**")
            st.dataframe(
                [
                    {"配置项": key, "即将提交": str(value)}
                    for key, value in _section.rows
                ],
                hide_index=True,
                width="stretch",
            )

    with st.expander("相对预设的差异（只读）", expanded=True):
        if (
            _selected_preset == CUSTOM_PRESET_NAME
            and _review_preset_name != CUSTOM_PRESET_NAME
        ):
            st.caption(
                f"当前字段已偏离 `{_review_preset_name}`，选择器因此显示 Custom；"
                "以下仍与最近明确应用的预设对比。"
            )
        if _review_preset_name == CUSTOM_PRESET_NAME:
            st.info("尚未明确应用可读取的预设，无法确认差异。")
        elif _preset_differences is None:
            st.warning(
                f"已应用预设 `{_review_preset_name}` 无法读取，无法确认差异；"
                "不会使用默认值替代。"
            )
        elif not _preset_differences:
            st.success(f"与明确应用的预设 `{_review_preset_name}` 没有差异。")
        else:
            st.dataframe(
                [
                    {
                        "配置项": _difference.key,
                        "预设值": (
                            str(_difference.preset_value)
                            if _difference.preset_present
                            else "（预设未定义）"
                        ),
                        "即将提交": (
                            str(_difference.emitted_value)
                            if _difference.emitted_present
                            else "（本页不会提交）"
                        ),
                    }
                    for _difference in _preset_differences
                ],
                hide_index=True,
                width="stretch",
            )

    if PREFILL_CONFIG:
        _source_job = st.session_state.get("prefill_config_source_job", "")
        # 四类分开说。混成一句的话,一次老运行重跑会被十几行 schema 演进
        # 噪音淹掉真正需要确认的值改动,操作人会学会忽略整块。
        _changed = divergences_of(_prefill_divergences, DIVERGENCE_CHANGED)
        _source_missing = divergences_of(
            _prefill_divergences, DIVERGENCE_SOURCE_MISSING)
        _mode_only = divergences_of(
            _prefill_divergences, DIVERGENCE_MODE_INAPPLICABLE)
        _run_scoped = divergences_of(
            _prefill_divergences, DIVERGENCE_RUN_SCOPED)
        if _changed:
            st.warning(
                f"⚠ 即将提交的配置与被重跑的运行 `{_source_job}` **有 "
                f"{len(_changed)} 项值不同**——预填之后这些字段被改过。"
                "这不是错误，但请确认差异是你要的："
            )
            st.dataframe(
                [
                    {
                        "配置项": _d.key,
                        f"源运行（{_source_job}）": str(_d.source_value),
                        "即将提交": str(_d.emitted_value),
                    }
                    for _d in _changed
                ],
                hide_index=True,
                width="stretch",
            )
        else:
            st.caption(
                f"✓ 即将提交的配置与运行 `{_source_job}` 在两侧共有的字段上"
                "逐项一致。"
            )
        if _source_missing:
            with st.expander(
                f"源运行未记录的字段（{len(_source_missing)} 项，"
                "本次按本页当前值提交）",
                expanded=False,
            ):
                st.caption(
                    "这些键在那次运行的 config.yaml 里**不存在**——多半是它"
                    "早于该字段进入 schema。本页不推断「它当时用的是默认"
                    "值」：那等于替一次没记录的运行编造基线。"
                )
                st.dataframe(
                    [
                        {"配置项": _d.key, "即将提交": str(_d.emitted_value)}
                        for _d in _source_missing
                    ],
                    hide_index=True,
                    width="stretch",
                )
        if _mode_only:
            with st.expander(
                f"属于另一个模式的字段（{len(_mode_only)} 项，本次不提交）",
                expanded=False,
            ):
                st.caption(
                    f"当前模式是 `{mode}`，这些键属于另一模式的 schema——它们"
                    "已随预填落入本页状态，切换模式后即生效，但本次提交不含"
                    "它们。"
                )
                st.dataframe(
                    [
                        {
                            "配置项": _d.key,
                            f"源运行（{_source_job}）": str(_d.source_value),
                        }
                        for _d in _mode_only
                    ],
                    hide_index=True,
                    width="stretch",
                )
        if _run_scoped:
            st.caption(
                "· 源运行的 `"
                + "`、`".join(_d.key for _d in _run_scoped)
                + "` 随那一次运行而生（由作业管理器注入），不随配置携带。"
            )

    if _unsupported_prefill:
        st.warning(
            "历史运行中以下字段不属于本页当前支持的提交 schema，"
            "本次不会静默携带：" + ", ".join(_unsupported_prefill)
        )

    # --- Research launch controls ------------------------------------------
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
        submitted = st.button(
            "🚀 启动研究运行",
            disabled=(not provider_uri_valid or bool(guard_errors)),
            use_container_width=True,
        )
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
        # Mode-agnostic csi800 recheck (same stale-frame defense as below):
        # the operator can flip a guard checkbox and hit Run inside the
        # still-enabled frame before the rerun disables the button.
        _guard_errors_final: list[str] = []
        validate_csi800_guard_triple(
            instruments,
            attribution_sleeve_grouping,
            risk_constraints_enabled,
            risk_constraints_calibration,
            _guard_errors_final,
        )
        if _guard_errors_final:
            st.error(
                "提交前的最终校验失败，作业未启动：\n- "
                + "\n- ".join(_guard_errors_final)
            )
            st.stop()
        # Mode-agnostic: re-check feature_handler on the submit path too. The
        # operator can switch to MinedFactor / a typo and click Run within the
        # stale enabled-button frame before the rerun disables it, so the
        # render-time guard alone is not enough (codex P2 on #303).
        _final_handlers = list_supported_feature_handlers()
        if feature_handler and feature_handler not in _final_handlers:
            st.error(
                "提交前的最终校验失败，作业未启动：\n- "
                f"feature_handler={feature_handler!r} 不可启动（未注册）。"
                f"当前可用：{', '.join(_final_handlers) or '（无）'}。"
            )
            st.stop()
        # Mode-agnostic cost-model range rechecks (same stale-frame defense as
        # above): block out-of-range values before JobManager.start.
        if not (0.0 < float(limit_threshold) <= 0.25):
            st.error(
                "提交前的最终校验失败，作业未启动：\n- "
                f"limit_threshold 须在 (0, 0.25] 区间；当前 {limit_threshold}。"
            )
            st.stop()
        if float(init_cash) <= 0:
            st.error(
                "提交前的最终校验失败，作业未启动：\n- "
                f"init_cash 须为正；当前 {init_cash}。"
            )
            st.stop()
        if float(commission_rate) > COMMISSION_RATE_MAX:
            st.error(
                "提交前的最终校验失败，作业未启动：\n- "
                f"commission_rate 须 ≤ {COMMISSION_RATE_MAX}；当前 {commission_rate}。"
            )
            st.stop()
        if float(slippage_bps) > SLIPPAGE_BPS_MAX:
            st.error(
                "提交前的最终校验失败，作业未启动：\n- "
                f"slippage_bps 须 ≤ {SLIPPAGE_BPS_MAX}；当前 {slippage_bps}。"
            )
            st.stop()
        if adjust_mode not in SUPPORTED_ADJUST_MODES:
            st.error(
                "提交前的最终校验失败，作业未启动：\n- "
                f"adjust_mode={adjust_mode!r} 无效；"
                f"允许：{', '.join(SUPPORTED_ADJUST_MODES)}。"
            )
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
                metadata=provider_metadata,  # reuse the rerun's single inspect
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
                st.session_state[_REVIEW_PRESET_NAME_STATE] = safe
                st.session_state.pop(_REVIEW_PRESET_SNAPSHOT_STATE, None)
                st.session_state["cr_saving_preset"] = False
                st.rerun()
        if st.button("取消", key="cr_save_cancel"):
            st.session_state["cr_saving_preset"] = False
            st.rerun()

# ===== RIGHT: Live YAML preview =====
with preview_col:
    st.markdown("#### 完整提交 YAML（只读）")

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
        if _review_preset is None:
            st.caption(
                f"无法对比 — 已应用预设 `{_review_preset_name}` 无法读取。"
            )
        else:
            baseline_yaml = yaml.dump(
                {
                    key: value
                    for key, value in portable_config_for_preset_review(_review_preset).items()
                    if value != ""
                },
                default_flow_style=False,
                allow_unicode=True,
            )
            current_review_yaml = yaml.dump(
                {
                    key: value
                    for key, value in portable_config_for_preset_review(preview_config).items()
                    if value != ""
                },
                default_flow_style=False,
                allow_unicode=True,
            )
            diff_lines = list(
                difflib.unified_diff(
                    baseline_yaml.splitlines(),
                    current_review_yaml.splitlines(),
                    fromfile=f"{_review_preset_name}.yaml",
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
