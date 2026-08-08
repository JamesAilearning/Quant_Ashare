"""Shared boundary validators for runtime config dataclasses.

A small home for validation rules that MUST stay byte-identical across
more than one config boundary, so a rule cannot silently drift between
hand-maintained copies. Each helper takes the caller's own exception
type (``error_class``) plus an optional ``prefix`` (e.g.
``"PipelineConfig."``) so every config keeps its own exception type and
message namespace — the same pattern as
``attribution_industry_loader.assert_industry_config_complete_or_empty``.

Intentionally NOT a home for the model-hyperparameter checks: those are
deliberately layered (cheap "definitely wrong" checks at config
construction vs. the full checks in ``ModelTrainer._validate``) with
distinct exception types, and collapsing them would erase that layering
(T2-5 scope decision).
"""

from __future__ import annotations

from typing import Any

from src.core.canonical_backtest_contract import (
    SUPPORTED_ADJUST_MODES,
    CanonicalAccountConfig,
    CanonicalBacktestContractError,
    CanonicalExchangeConfig,
    CanonicalExchangeCostModel,
    resolve_stamp_tax_schedule,
)
from src.core.model_trainer import (
    GPU_SUPPORTED_MODEL_TYPES,
    SUPPORTED_COMPUTE_DEVICES,
)


def validate_label_horizon(
    label_horizon_days: int,
    feature_handler: str,
    *,
    error_class: type[Exception],
) -> None:
    """Validate the holding horizon H and its handler compatibility.

    Shared by both engine configs (#318, "two engines, one schema"). The
    handler check must fail HERE, at config construction: the walk-forward
    engine's per-fold error isolation would otherwise catch
    ``FeatureDatasetBuilder``'s rejection fold by fold and finish with an
    all-NaN placeholder report instead of failing at config load.
    """
    h = label_horizon_days
    if not isinstance(h, int) or isinstance(h, bool) or h < 1:
        raise error_class(
            f"label_horizon_days must be a positive integer (holding days, "
            f"T+1 close -> T+1+H close); got {h!r}."
        )
    if h != 1 and feature_handler != "Alpha158":
        raise error_class(
            f"label_horizon_days={h} is only supported for feature_handler="
            f"'Alpha158'; handler '{feature_handler}' defines its own "
            "label and would silently ignore the horizon. Use the default "
            "(1) or add horizon support to that handler first."
        )


def validate_compute_device(
    compute_device: str,
    model_type: str,
    *,
    error_class: type[Exception],
    prefix: str = "",
) -> None:
    """Validate the device and its model-type compatibility.

    A ``gpu`` request for a model type without a GPU path must fail loud —
    silently falling back to CPU would turn an explicit operator choice
    into an invisible no-op ("no silent fallback").
    """
    if compute_device not in SUPPORTED_COMPUTE_DEVICES:
        raise error_class(
            f"{prefix}compute_device must be one of "
            f"{SUPPORTED_COMPUTE_DEVICES}; got {compute_device!r}."
        )
    if compute_device == "gpu" and model_type not in GPU_SUPPORTED_MODEL_TYPES:
        raise error_class(
            f"{prefix}compute_device='gpu' is currently supported "
            f"only for {GPU_SUPPORTED_MODEL_TYPES}; got "
            f"model_type={model_type!r}. Refusing to silently fall "
            "back to CPU."
        )


def validate_risk_constraints_policy(
    *,
    risk_constraints_mode: str,
    risk_constraints_calibration: str,
    metrics_purpose: str,
    error_class: type[Exception],
) -> None:
    """Validate the risk-constraints policy triple and its interlock.

    ``WARN_AND_CLIP`` tolerates violations, but the clipping is POST-TRADE:
    the returns are qlib's UNCLIPPED execution, i.e. exactly the numbers
    RAISE refuses. It is therefore only legal when the run DECLARES that
    its product is out-of-fold predictions, not official metrics (codex
    #406). The calibration is a separate explicit choice (CSI800 guard-2 /
    veto-4, codex P2 on #372).
    """
    if metrics_purpose not in ("official", "predictions_only"):
        raise error_class(
            "metrics_purpose must be 'official' or "
            f"'predictions_only'; got {metrics_purpose!r}.")
    if (risk_constraints_mode == "warn_and_clip"
            and metrics_purpose != "predictions_only"):
        raise error_class(
            "risk_constraints_mode='warn_and_clip' tolerates "
            "violations, but the clipping is POST-TRADE — the "
            "returns are qlib's UNCLIPPED execution, i.e. the "
            "numbers RAISE refuses. Declare "
            "metrics_purpose='predictions_only' to state that this "
            "run's product is out-of-fold predictions, or use "
            "risk_constraints_mode='raise'.")
    if risk_constraints_mode not in ("raise", "warn_and_clip"):
        raise error_class(
            "risk_constraints_mode must be 'raise' or "
            f"'warn_and_clip'; got {risk_constraints_mode!r}.")
    if risk_constraints_calibration not in ("default", "campaign_v1"):
        raise error_class(
            "risk_constraints_calibration must be 'default' or "
            f"'campaign_v1'; got {risk_constraints_calibration!r}."
        )


def validate_csi800_expansion_guards(
    *,
    instruments: str,
    attribution_sleeve_grouping: bool,
    risk_constraints_enabled: bool,
    risk_constraints_calibration: str,
    error_class: type[Exception],
) -> None:
    """CSI800 official metrics require the full guard triple.

    v2-csi800-expansion-guards (codex #370 r6 + #372 r1): custom or copied
    campaign configs get no bypass — the tracked ``config/presets/csi800*``
    presets carry all three.
    """
    if instruments == "csi800" and not (
            attribution_sleeve_grouping
            and risk_constraints_enabled
            and risk_constraints_calibration == "campaign_v1"):
        raise error_class(
            "instruments='csi800' requires attribution_sleeve_grouping="
            "True, risk_constraints_enabled=True AND "
            "risk_constraints_calibration='campaign_v1' — official "
            "csi800 metrics without the sleeve report and the campaign "
            "constraint calibration are forbidden; presets "
            "config/presets/csi800*.yaml carry all three "
            "(v2-csi800-expansion-guards, codex #370 r6 + #372 r1; "
            "custom/copied campaign configs get no bypass)."
        )


def validate_attribution_sleeve_grouping(
    *,
    attribution_sleeve_grouping: bool,
    industry_artifact_path: str | None,
    run_attribution: bool,
    error_class: type[Exception],
) -> None:
    """Sleeve grouping is exclusive with industries and needs attribution on.

    One Brinson run takes exactly ONE grouping source; and disabling
    attribution would skip the sleeve resolution entirely and emit bare
    csi800 metrics without the mandated decomposition
    (v2-csi800-expansion-guards, codex P1 on #370).
    """
    if attribution_sleeve_grouping and industry_artifact_path:
        raise error_class(
            "attribution_sleeve_grouping and industry_artifact_path "
            "are mutually exclusive — one Brinson run takes exactly "
            "one grouping source (v2-csi800-expansion-guards)."
        )
    if attribution_sleeve_grouping and not run_attribution:
        raise error_class(
            "attribution_sleeve_grouping=True requires "
            "run_attribution=True — disabling attribution would skip "
            "the sleeve resolution entirely and emit bare csi800 "
            "metrics without the mandated decomposition "
            "(v2-csi800-expansion-guards, codex P1 on #370)."
        )


def validate_signal_to_execution_lag(
    signal_to_execution_lag: int,
    *,
    error_class: type[Exception],
    prefix: str = "",
) -> None:
    """Validate the TOTAL signal-to-fill delay is an int >= 1.

    ``0`` (same-day) is rejected on the canonical path — it would publish
    look-ahead results as official metrics. ``bool`` is rejected too: a
    copy-pasted ``lag=True`` would otherwise sail through as 1.
    """
    if (
        not isinstance(signal_to_execution_lag, int)
        or isinstance(signal_to_execution_lag, bool)
    ):
        raise error_class(
            f"{prefix}signal_to_execution_lag must be an int, not "
            f"{type(signal_to_execution_lag).__name__}; got "
            f"{signal_to_execution_lag!r}."
        )
    if signal_to_execution_lag < 1:
        raise error_class(
            f"{prefix}signal_to_execution_lag must be >= 1 (the TOTAL "
            "signal->fill delay; 1 = T+1 execution); got "
            f"{signal_to_execution_lag!r}. 0 (same-day) is rejected "
            "on the canonical path — it would publish look-ahead results "
            "as official metrics."
        )


def validate_backtest_controls(
    *,
    init_cash: float,
    execution_price_kind: str,
    commission_rate: float,
    stamp_tax_schedule: Any,
    slippage_bps: float,
    min_cost: float,
    limit_threshold: float,
    adjust_mode: str,
    error_class: type[Exception],
    prefix: str = "",
) -> None:
    """Validate the canonical backtest controls AT CONFIG CONSTRUCTION.

    The canonical contract is the single source of truth for these bounds
    (``init_cash > 0``, ``limit_threshold`` in (0, 0.25], the cost-model
    shape, and the stamp-tax schedule's ordering / duplicate-date rules),
    so this helper CONSTRUCTS throwaway contract objects instead of
    re-stating the bounds — a hand-copied literal is exactly what drifts.

    Every value here would be validated eventually, at backtest
    construction; catching it now is the whole point of the config-shape
    sieve — otherwise the operator waits through feature build + model
    train + predict to learn that ``init_cash`` was 0. Both engines call
    this, so neither can fail LATER than the other on the same typo
    ("two engines, one schema").
    """
    if adjust_mode not in SUPPORTED_ADJUST_MODES:
        raise error_class(
            f"{prefix}adjust_mode must be one of {SUPPORTED_ADJUST_MODES}; "
            f"got {adjust_mode!r}."
        )
    try:
        CanonicalAccountConfig(init_cash=init_cash)
        CanonicalExchangeConfig(
            freq="day",
            execution_price_kind=execution_price_kind,
            cost_model=CanonicalExchangeCostModel(
                commission_rate=commission_rate,
                stamp_tax_schedule=resolve_stamp_tax_schedule(
                    stamp_tax_schedule,
                ),
                slippage_bps=slippage_bps,
                min_cost=min_cost,
            ),
            limit_threshold=limit_threshold,
        )
    except CanonicalBacktestContractError as exc:
        # Name the covered field surface: the contract's own message uses
        # its class names (``StampTaxScheduleEntry.bps``), so without this
        # the operator (and every grep / test that looks for the config
        # field name) cannot tell WHICH config key to fix.
        raise error_class(
            f"{prefix}backtest controls failed validation "
            "(init_cash / execution_price_kind / commission_rate / "
            "stamp_tax_schedule / slippage_bps / min_cost / "
            f"limit_threshold): {exc}"
        ) from exc


def validate_topk(
    topk: int,
    *,
    error_class: type[Exception],
    prefix: str = "",
) -> None:
    """Validate ``topk`` is a positive int.

    Shared by ``PipelineConfig`` and ``WalkForwardConfig``. The
    ``isinstance`` guards reject ``bool`` (a copy-pasted ``topk=True``
    would otherwise satisfy ``topk >= 1``) and non-int values (which
    would raise a cryptic ``TypeError`` deep in a later comparison such
    as ``n_drop >= topk``). ``error_class`` is the caller's exception
    type; ``prefix`` is prepended to the field name.
    """
    if not isinstance(topk, int) or isinstance(topk, bool) or topk < 1:
        raise error_class(
            f"{prefix}topk must be a positive int; got {topk!r}."
        )


def validate_n_drop(
    n_drop: int,
    topk: int,
    *,
    error_class: type[Exception],
    prefix: str = "",
) -> None:
    """Validate ``n_drop`` is a non-negative int strictly less than ``topk``.

    Shared by ``PipelineConfig`` and ``WalkForwardConfig`` so a
    copy-pasted ``topk=10, n_drop=10`` is rejected identically on both
    paths — ``n_drop >= topk`` empties a ``TopkDropoutStrategy`` portfolio
    after the first rebalance. The ``isinstance`` guards stay (the field
    is annotated ``int`` but the value originates from YAML / operator
    input). ``error_class`` is the caller's exception type; ``prefix`` is
    prepended to the field name (``"PipelineConfig."`` / ``""``).
    """
    if not isinstance(n_drop, int) or isinstance(n_drop, bool) or n_drop < 0:
        raise error_class(
            f"{prefix}n_drop must be a non-negative int; got {n_drop!r}."
        )
    if n_drop >= topk:
        raise error_class(
            f"{prefix}n_drop ({n_drop}) must be strictly less than "
            f"topk ({topk}); otherwise TopkDropoutStrategy would empty "
            "the portfolio after the first rebalance."
        )
