from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from src.contracts.taxonomy_data_contract import TAXONOMY_MODE_STATIC
from src.core.attribution_industry_loader import assert_industry_config_complete_or_empty
from src.core.canonical_backtest_contract import (
    ADJUST_MODE_PRE,
    EXECUTION_PRICE_CLOSE,
    CanonicalBacktestContractError,
    CanonicalExchangeConfig,
    CanonicalExchangeCostModel,
    SUPPORTED_ADJUST_MODES,
)

class WalkForwardError(RuntimeError):
    """Raised on structural misuse of the walk-forward engine."""


@dataclass(frozen=True)
class WalkForwardConfig:
    """Configuration for walk-forward rolling backtest."""

    # Universe & features
    instruments: str = "csi300"
    feature_handler: str = "Alpha158"

    # Overall period
    overall_start: str = "2022-01-01"
    overall_end: str = "2025-12-31"

    # Window sizes (months)
    train_months: int = 24
    valid_months: int = 3
    test_months: int = 3
    step_months: int = 3  # how far to roll each iteration

    # Model config
    model_type: str = "LGBModel"
    num_boost_round: int = 1000
    early_stopping_rounds: int = 50
    learning_rate: float = 0.0421
    max_depth: int = 8
    num_leaves: int = 210
    # LGB regularisation / sampling. Defaults mirror LightGBM's own
    # defaults — adding the fields does not change behaviour for callers
    # that don't set them. See ModelTrainConfig for the rationale.
    lambda_l1: float = 0.0
    lambda_l2: float = 0.0
    min_data_in_leaf: int = 20
    feature_fraction: float = 1.0
    bagging_fraction: float = 1.0
    bagging_freq: int = 0

    # Backtest config
    benchmark_code: str = "SH000300"
    init_cash: float = 100_000_000
    topk: int = 50
    n_drop: int = 5
    commission_rate: float = 0.0005
    stamp_tax_bps: float = 10.0
    slippage_bps: float = 5.0
    min_cost: float = 5.0
    execution_price_kind: str = EXECUTION_PRICE_CLOSE
    adjust_mode: str = ADJUST_MODE_PRE
    signal_to_execution_lag: int = 1
    limit_threshold: float = 0.095

    # Cross-fold model ensemble.
    #
    # When >1, each fold's prediction is the equal-weighted mean of:
    #   1. the current fold's freshly trained model, plus
    #   2. up to ``ensemble_window - 1`` prior folds' models (loaded
    #      from their pickle artifacts and re-predicted against the
    #      current fold's dataset).
    #
    # Default ``1`` is a no-op — current behaviour preserved for any
    # existing config that doesn't set this. ``2`` averages current +
    # 1 prior; ``3`` averages current + 2 priors; etc.
    #
    # Why this matters: the first walk-forward + industry-attribution
    # run showed model selection ability flipping sign within a single
    # quarter on the same sector (semiconductors: select −0.62% in
    # fold 5, +0.88% in fold 7). Most of that flip is parameter noise
    # rather than real regime change; averaging across recent training
    # windows smooths it out.
    #
    # Caveat: prior models predict the *current* fold's processed
    # dataset. The processors (RobustZScoreNorm, etc.) were fit on
    # the current train window, not the prior model's train window —
    # so the prior models see slightly off-distribution inputs. In
    # practice the cross-sectional normalisation is stable enough
    # for this "warm ensemble" to behave well on A-share data; an
    # operator who needs strict consistency should run separate
    # backtests instead.
    #
    # Early folds (fold_index < ensemble_window - 1) gracefully
    # degrade: they use as many prior models as exist (0 or more).
    # Fold 0 always uses only its own model regardless of N.
    ensemble_window: int = 1

    # Reproducibility — seed for numpy/python random/LGB/XGB/CatBoost.
    # Mirrors PipelineConfig.seed.
    seed: int = 42

    # Performance attribution per fold.
    # ``run_attribution`` controls whether ``_run_single_fold`` calls
    # ``PerformanceAttribution.analyze`` after backtest. Default ``True``
    # because the per-fold attribution block is the main observability
    # win after PR #30 wired per-fold reports — without it the only
    # OOS-period industry decomposition is the single-fold pipeline, and
    # walk-forward fold reports would just duplicate raw IC numbers.
    run_attribution: bool = True

    # Industry taxonomy artifact for attribution (optional). All four
    # fields must be set together or all left at defaults — the partial
    # combination is rejected at ``__post_init__`` time. Populated, the
    # fold's attribution buckets render the real Shenwan industry name
    # ("白酒", "银行") instead of the board-heuristic fallback
    # ("board_SH_Main"). Mirrors the same fields on
    # :class:`PipelineConfig`; the boundary contract is shared via
    # :func:`assert_industry_config_complete_or_empty`.
    industry_artifact_path: str | None = None
    industry_manifest_path: str | None = None
    industry_taxonomy_id: str = ""
    industry_temporal_mode: str = TAXONOMY_MODE_STATIC

    # Output
    output_dir: str = "output/walk_forward"

    def __post_init__(self) -> None:
        # *Validate-only*, no field mutation. ``frozen=True`` would forbid
        # ordinary ``self.x = ...`` assignment here — if a future iteration
        # needs to coerce a value (e.g. round a float), use the
        # ``object.__setattr__(self, "name", value)`` escape hatch (see
        # ``qlib_runtime.py`` for an example) and document the coercion in
        # the field docstring; do not silently relax ``frozen``.
        #
        # Window sizes must be strictly positive. ``step_months=0`` was the
        # dangerous one: ``cursor + relativedelta(months=0)`` never advances,
        # so ``_generate_windows`` would spin forever. ``train_months=0`` is
        # also nonsense (no fit data).
        for name in ("train_months", "valid_months", "test_months", "step_months"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool):
                raise WalkForwardError(
                    f"{name} must be int; got {type(value).__name__}."
                )
            if value < 1:
                raise WalkForwardError(
                    f"{name} must be >= 1; got {value}. "
                    "A zero-length window would hang the walk-forward loop "
                    "or produce empty folds."
                )

        # Validate ISO dates up-front so misconfiguration surfaces at config
        # construction rather than deep inside ``_generate_windows``.
        try:
            start = date.fromisoformat(self.overall_start)
        except (TypeError, ValueError) as exc:
            raise WalkForwardError(
                f"overall_start must be an ISO date (YYYY-MM-DD); got "
                f"{self.overall_start!r}."
            ) from exc
        try:
            end = date.fromisoformat(self.overall_end)
        except (TypeError, ValueError) as exc:
            raise WalkForwardError(
                f"overall_end must be an ISO date (YYYY-MM-DD); got "
                f"{self.overall_end!r}."
            ) from exc
        if end <= start:
            raise WalkForwardError(
                f"overall_end ({self.overall_end}) must be strictly after "
                f"overall_start ({self.overall_start})."
            )

        # Topk / drop sanity — ``n_drop > topk`` would leave the portfolio
        # empty after the first rebalance.
        if not isinstance(self.topk, int) or isinstance(self.topk, bool) or self.topk < 1:
            raise WalkForwardError(f"topk must be a positive int; got {self.topk!r}.")
        if (
            not isinstance(self.n_drop, int)
            or isinstance(self.n_drop, bool)
            or self.n_drop < 0
        ):
            raise WalkForwardError(
                f"n_drop must be a non-negative int; got {self.n_drop!r}."
            )
        if self.n_drop >= self.topk:
            raise WalkForwardError(
                f"n_drop ({self.n_drop}) must be strictly less than "
                f"topk ({self.topk})."
            )
        if (
            not isinstance(self.signal_to_execution_lag, int)
            or isinstance(self.signal_to_execution_lag, bool)
            or self.signal_to_execution_lag < 0
        ):
            raise WalkForwardError(
                "signal_to_execution_lag must be an int >= 0; got "
                f"{self.signal_to_execution_lag!r}. Use 0 only for explicit "
                "same-day execution/no shift, and 1 for T+1 delayed execution."
            )
        if self.adjust_mode not in SUPPORTED_ADJUST_MODES:
            raise WalkForwardError(
                f"adjust_mode must be one of {SUPPORTED_ADJUST_MODES}; "
                f"got {self.adjust_mode!r}."
            )

        # Model hyperparameter sanity: reject definitely-wrong values
        # (zero/negative) at config construction — same rationale as
        # PipelineConfig.__post_init__.
        if self.num_boost_round < 1:
            raise WalkForwardError(
                f"num_boost_round must be >= 1; got {self.num_boost_round!r}."
            )
        if self.learning_rate <= 0:
            raise WalkForwardError(
                f"learning_rate must be > 0; got {self.learning_rate!r}."
            )
        if self.max_depth < 1:
            raise WalkForwardError(
                f"max_depth must be >= 1; got {self.max_depth!r}."
            )

        # ensemble_window: must be a positive int. ``1`` is the no-op
        # default; values >1 enable cross-fold averaging. Reject 0 /
        # negatives explicitly so a typo can't silently disable the
        # current fold's own predictions.
        if (
            isinstance(self.ensemble_window, bool)
            or not isinstance(self.ensemble_window, int)
            or self.ensemble_window < 1
        ):
            raise WalkForwardError(
                "ensemble_window must be an int >= 1 (1 disables ensembling); "
                f"got {self.ensemble_window!r}."
            )
        try:
            CanonicalExchangeConfig(
                freq="day",
                execution_price_kind=self.execution_price_kind,
                cost_model=CanonicalExchangeCostModel(
                    commission_rate=self.commission_rate,
                    stamp_tax_bps=self.stamp_tax_bps,
                    slippage_bps=self.slippage_bps,
                    min_cost=self.min_cost,
                ),
                limit_threshold=self.limit_threshold,
            )
        except CanonicalBacktestContractError as exc:
            raise WalkForwardError(
                f"Invalid WalkForwardConfig backtest controls: {exc}"
            ) from exc

        # Industry-taxonomy fields: same all-or-nothing contract used by
        # PipelineConfig. Catching the partial state here prevents a
        # confusing "no such file" deep inside the loader during a fold.
        assert_industry_config_complete_or_empty(
            artifact_path=self.industry_artifact_path,
            manifest_path=self.industry_manifest_path,
            taxonomy_id=self.industry_taxonomy_id,
            temporal_mode=self.industry_temporal_mode,
            error_class=WalkForwardError,
            error_prefix="WalkForwardConfig",
        )
