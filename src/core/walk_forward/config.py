from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from src.contracts.taxonomy_data_contract import TAXONOMY_MODE_STATIC
from src.core._shared_validators import validate_n_drop, validate_topk
from src.core.attribution_industry_loader import assert_industry_config_complete_or_empty
from src.core.canonical_backtest_contract import (
    ADJUST_MODE_POST,
    ADJUST_MODE_PRE,
    EXECUTION_PRICE_CLOSE,
    SUPPORTED_ADJUST_MODES,
    CanonicalBacktestContractError,
    CanonicalExchangeConfig,
    CanonicalExchangeCostModel,
    resolve_stamp_tax_schedule,
)
from src.core.model_trainer import (
    GPU_SUPPORTED_MODEL_TYPES,
    SUPPORTED_COMPUTE_DEVICES,
    SUPPORTED_MODEL_TYPES,
)


class WalkForwardError(RuntimeError):
    """Raised on structural misuse of the walk-forward engine."""


# Feature handlers that resolve factors through the PIT layer
# (``PITDataProvider``), which pins the canonical qlib runtime to
# ``post_adjusted``. A ``WalkForwardConfig`` using one of these MUST set
# ``adjust_mode == post_adjusted`` — enforced in ``__post_init__`` (see
# v2-canonical-runtime-orchestration). A ``frozenset`` is deliberately the
# right size: do NOT grow this into a dynamic "provider declares its mode".
_PIT_FEATURE_HANDLERS = frozenset({"MinedFactor"})


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
    learning_rate: float = 0.005
    max_depth: int = 6
    num_leaves: int = 64
    # LGB regularisation / sampling. Tuned defaults that match
    # config_walk.yaml (the canonical tuned WF config). See
    # ModelTrainConfig for the best_iteration~1 rationale (C2-c).
    lambda_l1: float = 0.0
    lambda_l2: float = 1.0
    min_data_in_leaf: int = 50
    feature_fraction: float = 0.8
    bagging_fraction: float = 0.8
    bagging_freq: int = 5

    # Backtest config.
    # SH000300 is the CSI 300 PRICE index. The CANONICAL benchmark is the
    # TOTAL-RETURN index SH000300TR (audit E2 — a price-index benchmark
    # overstates excess return by ~the dividend yield). This in-code default
    # is consumed when a walk-forward config omits benchmark_code, so it must
    # be flipped to "SH000300TR" alongside the YAML presets at REGEN, once the
    # rebuilt bundle carries that instrument (see PR-E / 07_ingest_benchmark).
    benchmark_code: str = "SH000300"
    init_cash: float = 100_000_000
    topk: int = 50
    n_drop: int = 5
    commission_rate: float = 0.0005
    # CN A-share stamp tax — schedule, not scalar. See
    # PipelineConfig.stamp_tax_schedule for the format. Audit P0-4 /
    # openspec/changes/add-stamp-tax-schedule.
    stamp_tax_schedule: Any = None
    slippage_bps: float = 5.0
    min_cost: float = 5.0
    execution_price_kind: str = EXECUTION_PRICE_CLOSE
    adjust_mode: str = ADJUST_MODE_PRE
    signal_to_execution_lag: int = 1
    limit_threshold: float = 0.095
    # Tushare namechange parquet for PIT historical ST/*ST exclusion in the
    # backtest (C2-d PR2). None -> ST mask disabled (the WF universe still
    # includes ST, logged as a WARN per fold). Set to all_namechanges.parquet
    # to exclude ST point-in-time; the C1 baseline must be regenerated when
    # this is enabled (tests/regression/fixtures/README.md).
    namechange_path: str | None = None

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
    compute_device: str = "cpu"

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

    # Optional feature-dataset pickle cache directory. When set, each
    # fold's FeatureDatasetBuilder.build() consults this directory for a
    # cached FeatureDatasetResult before instantiating Alpha158 (or the
    # MinedFactor handler) — turning the 30-90s handler init + 3×
    # prepare() into a single pickle load on cache hit. See
    # ``openspec/changes/add-feature-dataset-cache/`` for the contract.
    #
    # **Three-state field** — the engine reads it as:
    #
    #   * ``None`` (default)  Not configured. The engine falls back to
    #                         the ``QLIB_DATASET_CACHE_DIR`` env var, or
    #                         disables the cache if that's also unset.
    #   * ``""``              **Explicit disable.** CLI / YAML stamped
    #                         cache-off; env var fallback is bypassed.
    #                         Use this when ``QLIB_DATASET_CACHE_DIR`` is
    #                         set globally but a specific run must avoid
    #                         the cache (e.g. you suspect stale entries
    #                         and want a clean rebuild).
    #   * non-empty string    Use this path. ``~`` is expanded.
    #
    # Operators who want **per-run isolation** can set this to
    # ``"{output_dir}/.dataset_cache"`` (cache cleared when output is
    # cleared). Operators who want **cross-run reuse** can set it to a
    # shared dir like ``"~/.cache/qlib_quant_v2/datasets/"``.
    dataset_cache_dir: str | None = None

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

        # Topk / drop sanity — shared with PipelineConfig via
        # _shared_validators (n_drop >= topk empties the portfolio after
        # the first rebalance; both validated here so the rules can't drift).
        validate_topk(self.topk, error_class=WalkForwardError)
        validate_n_drop(self.n_drop, self.topk, error_class=WalkForwardError)
        if (
            not isinstance(self.signal_to_execution_lag, int)
            or isinstance(self.signal_to_execution_lag, bool)
            or self.signal_to_execution_lag < 1
        ):
            raise WalkForwardError(
                "signal_to_execution_lag must be an int >= 1 (the TOTAL "
                "signal→fill delay; 1 = T+1 execution); got "
                f"{self.signal_to_execution_lag!r}. 0 (same-day) is rejected "
                "on the canonical path — it would publish look-ahead results "
                "as official metrics."
            )
        if self.adjust_mode not in SUPPORTED_ADJUST_MODES:
            raise WalkForwardError(
                f"adjust_mode must be one of {SUPPORTED_ADJUST_MODES}; "
                f"got {self.adjust_mode!r}."
            )
        # PIT/MinedFactor factors are built on post-adjusted PIT prices
        # (PITDataProvider pins the canonical runtime to post_adjusted). A
        # walk-forward in any other mode aborts every fold with a cryptic
        # QlibRuntimeInitError (single-canonical-runtime conflict), or would
        # silently score post-built factors against mismatched prices. Fail
        # loud at construction. See v2-canonical-runtime-orchestration.
        if (
            self.feature_handler in _PIT_FEATURE_HANDLERS
            and self.adjust_mode != ADJUST_MODE_POST
        ):
            raise WalkForwardError(
                f"feature_handler={self.feature_handler!r} resolves factors via "
                "PITDataProvider, which builds them on post-adjusted prices, so "
                "the walk-forward runtime must match. Set "
                f'adjust_mode: "{ADJUST_MODE_POST}" in the config (got '
                f"{self.adjust_mode!r})."
            )
        # ``model_type`` must be in the supported set up-front. Previously
        # this was only validated when ``compute_device == "gpu"`` (via the
        # GPU_SUPPORTED check below) and at training time inside
        # ``ModelTrainer._create_model``. CPU runs with a typo
        # (``"LGBModle"``) would pass config construction and only fail
        # after hours of feature-building. (bug.md P1-8.)
        if self.model_type not in SUPPORTED_MODEL_TYPES:
            raise WalkForwardError(
                f"model_type must be one of {SUPPORTED_MODEL_TYPES}; "
                f"got {self.model_type!r}."
            )
        if self.compute_device not in SUPPORTED_COMPUTE_DEVICES:
            raise WalkForwardError(
                f"compute_device must be one of {SUPPORTED_COMPUTE_DEVICES}; "
                f"got {self.compute_device!r}."
            )
        if (
            self.compute_device == "gpu"
            and self.model_type not in GPU_SUPPORTED_MODEL_TYPES
        ):
            raise WalkForwardError(
                "compute_device='gpu' is currently supported only for "
                f"{GPU_SUPPORTED_MODEL_TYPES}; got model_type={self.model_type!r}. "
                "Refusing to silently fall back to CPU."
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
                    stamp_tax_schedule=resolve_stamp_tax_schedule(
                        self.stamp_tax_schedule,
                    ),
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
