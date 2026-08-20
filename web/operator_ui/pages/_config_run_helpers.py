"""Pure helpers for the Config & Run page (UI review P1-1).

Extracted from ``pages/config_run.py`` so the page module is mostly
Streamlit dispatch (form layout, widget wiring, submission) rather
than a long mix of date-window arithmetic, embargo-aware split
construction, and duration heuristics.

Everything here is **pure** — no ``import streamlit`` at module body,
no ``st.X`` calls. That makes the embargo / split arithmetic
unit-testable in isolation, and a future refactor of the rendering
side cannot accidentally drift the calendar math.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from web.operator_ui.training_guards import (
    FORWARD_RETURN_BUFFER_DAYS,
    LABEL_LOOKAHEAD_DAYS,
    ProviderMetadata,
)


@dataclass(frozen=True)
class ConfigReviewSection:
    """One readable group from the exact emitted run configuration."""

    title: str
    rows: tuple[tuple[str, Any], ...]


@dataclass(frozen=True)
class ConfigPresetDifference:
    """One difference between an emitted value and the selected preset."""

    key: str
    emitted_value: Any | None
    emitted_present: bool
    preset_value: Any | None
    preset_present: bool


# Presentation ordering only.  These keys are read from the already-built
# payload; this module MUST NOT provide defaults or manufacture configuration
# fields, because ``config_run.py`` has one authoritative launch builder.
_CONFIG_REVIEW_SECTION_KEYS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "研究目标",
        ("mode", "model_type", "feature_handler"),
    ),
    (
        "数据范围",
        (
            "provider_uri",
            "instruments",
            "train_start",
            "train_end",
            "valid_start",
            "valid_end",
            "test_start",
            "test_end",
            "overall_start",
            "overall_end",
            "train_months",
            "valid_months",
            "test_months",
            "step_months",
            "ensemble_window",
            "namechange_path",
        ),
    ),
    (
        "策略约束",
        (
            "benchmark_code",
            "topk",
            "n_drop",
            "signal_to_execution_lag",
            "attribution_sleeve_grouping",
            "risk_constraints_enabled",
            "risk_constraints_calibration",
        ),
    ),
    (
        "高级设置",
        (
            "compute_device",
            "num_boost_round",
            "early_stopping_rounds",
            "learning_rate",
            "adjust_mode",
            "limit_threshold",
            "commission_rate",
            "slippage_bps",
            "min_cost",
            "init_cash",
            "seed",
        ),
    ),
)

# Presets intentionally travel between machines, while these values describe
# the local installation that will execute a run. They are therefore excluded
# both when a preset is saved and when a launch preview is compared with one.
_MACHINE_LOCAL_PRESET_KEYS = frozenset({"provider_uri", "namechange_path"})


def build_config_review_sections(
    emitted_config: Mapping[str, Any],
) -> tuple[ConfigReviewSection, ...]:
    """Group every emitted field for a pre-launch review.

    The helper retains all fields, including a future key that has not yet been
    assigned a presentation group.  Such fields use a stable final group so a
    UI update can never hide part of the submitted payload by omission.
    """

    seen: set[str] = set()
    sections: list[ConfigReviewSection] = []
    for title, keys in _CONFIG_REVIEW_SECTION_KEYS:
        rows = tuple(
            (key, emitted_config[key])
            for key in keys
            if key in emitted_config
        )
        seen.update(key for key, _ in rows)
        if rows:
            sections.append(ConfigReviewSection(title=title, rows=rows))

    remaining = tuple(
        (key, emitted_config[key])
        for key in sorted(set(emitted_config) - seen)
    )
    if remaining:
        sections.append(ConfigReviewSection(title="其他已提交设置", rows=remaining))
    return tuple(sections)


def config_preset_differences(
    emitted_config: Mapping[str, Any],
    preset_config: Mapping[str, Any] | None,
) -> tuple[ConfigPresetDifference, ...] | None:
    """Return stable, explicit differences from a selected preset.

    ``None`` means no loadable preset was supplied, which is different from an
    empty tuple: the latter is an affirmative proof that both mappings match.
    Keys only present on one side are preserved with a presence flag rather
    than silently treated as a default.
    """

    if preset_config is None:
        return None

    emitted_portable = {
        key: value
        for key, value in emitted_config.items()
        if key not in _MACHINE_LOCAL_PRESET_KEYS
    }
    preset_portable = {
        key: value
        for key, value in preset_config.items()
        if key not in _MACHINE_LOCAL_PRESET_KEYS
    }
    ordered_keys = list(emitted_portable)
    ordered_keys.extend(sorted(set(preset_portable) - set(emitted_portable)))
    differences: list[ConfigPresetDifference] = []
    for key in ordered_keys:
        emitted_present = key in emitted_portable
        preset_present = key in preset_portable
        emitted_value = emitted_portable.get(key)
        preset_value = preset_portable.get(key)
        if (
            emitted_present
            and preset_present
            and emitted_value == preset_value
        ):
            continue
        differences.append(
            ConfigPresetDifference(
                key=key,
                emitted_value=emitted_value,
                emitted_present=emitted_present,
                preset_value=preset_value,
                preset_present=preset_present,
            )
        )
    return tuple(differences)


def effective_preset_for_review(
    emitted_config: Mapping[str, Any],
    preset_config: Mapping[str, Any] | None,
    *,
    normalization_defaults: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Build the preset state the form would effectively submit.

    Built-in and older saved presets deliberately omit dynamic form values such
    as data windows, and portable presets must never retain machine-local
    paths. Start with the exact emitted portable fields, then apply the same
    missing-field normalization used when a preset is selected, followed by
    the explicit preset values. This makes an empty difference an attainable,
    meaningful result without inventing a provider path or date window.
    """

    if preset_config is None:
        return None

    effective = {
        key: value
        for key, value in emitted_config.items()
        if key not in _MACHINE_LOCAL_PRESET_KEYS
    }
    effective.update(
        (key, value)
        for key, value in normalization_defaults.items()
        if key not in _MACHINE_LOCAL_PRESET_KEYS
    )
    effective.update(
        (key, value)
        for key, value in preset_config.items()
        if key not in _MACHINE_LOCAL_PRESET_KEYS
    )
    return effective


def snapshot_preset_for_review(
    emitted_config: Mapping[str, Any],
    preset_config: Mapping[str, Any] | None,
    *,
    normalization_defaults: Mapping[str, Any],
    snapshot: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Return the original review baseline instead of recalculating it.

    A form field edit causes the visible preset selector to become ``Custom``.
    The review must still compare that edit with the last preset the operator
    explicitly applied, not build a new baseline from the already-edited
    emitted mapping.  Callers persist the first effective mapping as
    ``snapshot`` for that explicitly applied preset.
    """

    if snapshot is not None:
        return dict(snapshot)
    return effective_preset_for_review(
        emitted_config,
        preset_config,
        normalization_defaults=normalization_defaults,
    )


def unsupported_prefill_keys(
    prefill_config: Mapping[str, Any],
    emitted_config: Mapping[str, Any],
) -> tuple[str, ...]:
    """Name prefilled fields that the current page will not submit.

    A historic configuration can contain fields unsupported by the standalone
    UI.  Reporting them is intentionally conservative: it does not add them
    to the launch payload or pretend their old semantics still apply.
    """

    return tuple(sorted(set(prefill_config) - set(emitted_config)))


def _trading_day_options(calendar_dates: tuple[date, ...]) -> list[str]:
    return [calendar_date.isoformat() for calendar_date in calendar_dates]


def _option_index(options: list[str], default: str) -> int:
    """Locate ``default`` in ``options``.

    Returns ``-1`` when ``default`` isn't present. Callers MUST treat
    that as "snap to a safe index AND tell the operator" rather than
    silently coerce — the previous ``return 0`` fallback let the UI
    swap, say, ``train_start=2022-01-01`` for ``calendar[0]=2023-06-12``
    without any visible signal, so operators chased a 'why did my run
    skip 2022?' ghost (UI review P1-9).
    """

    if default in options:
        return options.index(default)
    return -1


def _safe_pipeline_last_index(calendar_dates: tuple[date, ...]) -> int:
    if len(calendar_dates) > FORWARD_RETURN_BUFFER_DAYS + 1:
        return len(calendar_dates) - FORWARD_RETURN_BUFFER_DAYS - 1
    return max(0, len(calendar_dates) - 2)


def _six_increasing_indices(last_index: int) -> list[int]:
    """Lay out six calendar indices (train_start, train_end, valid_start,
    valid_end, test_start, test_end) across ``[0, last_index]``.

    Critical: the pairs ``(train_end, valid_start)`` and
    ``(valid_end, test_start)`` MUST be far enough apart to satisfy the
    label-lookahead embargo enforced by ``training_guards``. With
    ``LABEL_LOOKAHEAD_DAYS = 2`` we need at least
    ``LABEL_LOOKAHEAD_DAYS + 1 = 3`` calendar slots of gap on each
    segment boundary (the +1 is because moving to the next trading day
    is one step, then ``LABEL_LOOKAHEAD_DAYS`` more steps cover the
    intervening trading days that go between the two boundary dates).
    Non-boundary pairs only need strict ordering (+1).
    """

    # H=1 (default-horizon) assumption is fine HERE: this helper only lays out
    # DEFAULT form suggestions (the UI form cannot set label_horizon_days), and
    # the horizon-aware validator in training_guards is the enforcement point —
    # a suggestion too tight for a larger horizon would be refused there.
    embargo = LABEL_LOOKAHEAD_DAYS
    # Min slots needed = 1 (train_start→train_end) + embargo + 1
    # (→valid_start) + 1 (→valid_end) + embargo + 1 (→test_start) + 1
    # (→test_end). With LABEL_LOOKAHEAD_DAYS=2 this is 4 + 2*2 = 8.
    min_required = 4 + 2 * embargo
    if last_index < min_required:
        # Calendar too short to lay out a valid split. Don't fabricate a
        # fake one — callers will see the embargo validator's error and
        # be told to pull more data.
        return [min(index, max(0, last_index)) for index in range(6)]
    indices = [
        0, round(last_index * 0.55), round(last_index * 0.65),
        round(last_index * 0.78), round(last_index * 0.86), last_index,
    ]
    # Required minimum gap between each consecutive index pair. Segment
    # boundaries (idx 1→2 and 3→4) need ``embargo + 1`` so the embargo
    # validator's "trading days strictly between" count is ≥ embargo.
    min_gaps = [1, embargo + 1, 1, embargo + 1, 1]
    # Forward pass: push each index forward to satisfy its minimum gap.
    for i in range(1, 6):
        indices[i] = max(indices[i], indices[i - 1] + min_gaps[i - 1])
    # Backward pass: if forward pass overshot last_index, clip everything
    # back while preserving the same minimum gaps.
    indices[-1] = min(indices[-1], last_index)
    for i in range(4, -1, -1):
        indices[i] = min(indices[i], indices[i + 1] - min_gaps[i])
    return indices


# Static defaults used when the operator hasn't picked a provider yet
# (calendar_dates is empty / sparse). The embargo validator returns early
# in that case (no calendar to count trading days against), but once a
# real provider is selected the dates flow into the form and the embargo
# check runs against the real calendar — so we keep ≥ 2 trading days of
# slack on each boundary even in the static defaults so the natural
# weekend/holiday gaps comfortably cover the embargo.
_PIPELINE_DATE_FALLBACK: dict[str, str] = {
    "train_start": "2022-01-01",
    "train_end":   "2024-12-25",  # boundary: Dec 26-31 left as embargo
    "valid_start": "2025-01-02",
    "valid_end":   "2025-06-23",  # boundary: Jun 24-30 left as embargo
    "test_start":  "2025-07-01",
    "test_end":    "2025-12-31",
}


def _pipeline_date_defaults(metadata: ProviderMetadata) -> dict[str, str]:
    calendar_dates = metadata.calendar_dates
    if len(calendar_dates) < 6:
        return dict(_PIPELINE_DATE_FALLBACK)
    indices = _six_increasing_indices(_safe_pipeline_last_index(calendar_dates))
    keys = ("train_start", "train_end", "valid_start", "valid_end", "test_start", "test_end")
    return {key: calendar_dates[index].isoformat() for key, index in zip(keys, indices, strict=True)}


def _last_n_days_split(
    metadata: ProviderMetadata,
    n_days: int,
    ratios: tuple[float, float, float] = (0.6, 0.2, 0.2),
) -> dict[str, str] | None:
    """Split the last ``n_days`` trading days of the calendar into
    train/valid/test segments by ``ratios`` (must sum to 1.0).

    Each segment boundary leaves ``LABEL_LOOKAHEAD_DAYS`` trading days
    of embargo so the result satisfies the training_guards embargo
    validator and the quick presets don't immediately disable the Run
    button.

    Returns ``None`` when the calendar is too short or empty (also when
    the window can't fit two embargo gaps + non-empty segments).  No
    silent fallback — callers SHALL treat ``None`` as "preset
    unavailable" rather than guess.
    """

    cal = metadata.calendar_dates
    if not cal or len(cal) < 50:
        return None
    take = min(len(cal), n_days)
    sub = cal[-take:]
    n = len(sub)
    embargo = LABEL_LOOKAHEAD_DAYS
    # Minimum n: 1 train + embargo + 1 valid + embargo + 1 test = 3 + 2*embargo
    if n < 3 + 2 * embargo:
        return None

    train_end_i = max(0, int(n * ratios[0]) - 1)
    valid_start_i = train_end_i + 1 + embargo  # leaves ``embargo`` days strictly between
    # Anchor valid_end from train_end + nominal valid length, but never
    # earlier than valid_start.
    valid_end_i = max(valid_start_i, train_end_i + int(n * ratios[1]))
    test_start_i = valid_end_i + 1 + embargo
    test_end_i = n - 1

    if test_start_i >= test_end_i:
        # The valid window grew so wide that there's no room for test
        # after embargo. Pull valid_end back to fit a non-empty test
        # segment + boundary embargo.
        test_start_i = test_end_i - 1
        if test_start_i <= valid_start_i + embargo:
            # Even the minimum valid + embargo + test doesn't fit;
            # surface as "preset unavailable" rather than emit a split
            # the embargo validator will immediately reject.
            return None
        valid_end_i = test_start_i - 1 - embargo

    return {
        "train_start": sub[0].isoformat(),
        "train_end": sub[train_end_i].isoformat(),
        "valid_start": sub[valid_start_i].isoformat(),
        "valid_end": sub[valid_end_i].isoformat(),
        "test_start": sub[test_start_i].isoformat(),
        "test_end": sub[test_end_i].isoformat(),
    }


def _walk_forward_date_defaults(metadata: ProviderMetadata) -> dict[str, str]:
    calendar_dates = metadata.calendar_dates
    if len(calendar_dates) >= 2:
        return {"overall_start": calendar_dates[0].isoformat(), "overall_end": calendar_dates[-1].isoformat()}
    return {"overall_start": "2022-01-01", "overall_end": "2026-02-28"}


# Fallback throughput when we have no historical jobs to calibrate
# against — work units processed per second. The GPU number is ~10× the
# CPU number; both are deliberate order-of-magnitude guesses (UI review
# P2-6 — these were previously undocumented magic constants).
_ESTIMATE_RATE_CPU = 5000.0
_ESTIMATE_RATE_GPU = 50000.0


def _pipeline_work_units(config: dict[str, Any]) -> float:
    """Return a dimensionless 'work units' size for a pipeline config.

    Proportional to (universe size) × (trading days/yr) × (train years)
    × (Alpha158 feature count) × (boost rounds) × (backtest overhead) —
    the dominant drivers of training wall-clock. Used both for the
    formula-based estimate and to normalise historical job durations
    into a comparable seconds-per-unit rate (UI review P2-6).
    """

    instruments = str(config.get("instruments", "csi300"))
    n_stocks = 5000 if instruments == "all" else 800 if "800" in instruments else 300
    train_years = 5
    try:
        ts = datetime.strptime(str(config.get("train_start", "2022-01-01")), "%Y-%m-%d")
        te = datetime.strptime(str(config.get("train_end", "2024-12-31")), "%Y-%m-%d")
        train_years = max(1, int((te - ts).days / 365))
    except (ValueError, TypeError):
        pass
    try:
        n_est = int(config.get("num_boost_round", 1000))
    except (ValueError, TypeError):
        n_est = 1000
    # 158 ≈ Alpha158 feature count; 1.5 ≈ backtest overhead multiplier.
    return float(n_stocks) * 252.0 * float(train_years) * 158.0 * (n_est / 1000.0) * 1.5


def _calibration_seconds_per_unit(
    samples: list[tuple[dict[str, Any], float]],
) -> float | None:
    """Derive an empirical seconds-per-work-unit rate from recent runs.

    ``samples`` is ``[(config, actual_seconds), …]`` for completed
    same-mode jobs. Returns the median of ``actual_seconds / work_units``
    across valid samples, or ``None`` when there's nothing usable —
    callers fall back to the hardcoded throughput. Median (not mean) so
    a single anomalous run (machine under load, cold cache) doesn't skew
    the estimate (UI review P2-6).
    """

    rates: list[float] = []
    for cfg, actual_seconds in samples:
        if actual_seconds is None or actual_seconds <= 0:
            continue
        units = _pipeline_work_units(cfg)
        if units <= 0:
            continue
        rates.append(float(actual_seconds) / units)
    if not rates:
        return None
    rates.sort()
    mid = len(rates) // 2
    if len(rates) % 2 == 1:
        return rates[mid]
    return (rates[mid - 1] + rates[mid]) / 2.0


def _format_estimate_minutes(est_minutes: int) -> str:
    if est_minutes >= 60:
        h = est_minutes // 60
        m = est_minutes % 60
        return f"约 {h} 小时 {m} 分"
    return f"约 {est_minutes} 分钟"


def _estimate_duration(
    config: dict[str, Any],
    *,
    seconds_per_unit: float | None = None,
) -> str:
    """Heuristic runtime estimate.

    When ``seconds_per_unit`` is provided (an empirical rate from
    :func:`_calibration_seconds_per_unit` over recent jobs), the
    estimate is work units × that measured rate — far more accurate
    than the throughput formula across heterogeneous machines. Without
    it, fall back to the hardcoded CPU/GPU throughput constants. The
    return string format is unchanged so existing call sites / tests
    still see "约 N 分钟" / "约 H 小时 M 分" (UI review P2-6).
    """

    units = _pipeline_work_units(config)
    if seconds_per_unit is not None and seconds_per_unit > 0:
        est_seconds = units * seconds_per_unit
    else:
        device = str(config.get("compute_device", "cpu"))
        rate = _ESTIMATE_RATE_GPU if device == "gpu" else _ESTIMATE_RATE_CPU
        est_seconds = units / rate
    est_minutes = max(1, int(est_seconds / 60))
    return _format_estimate_minutes(est_minutes)
