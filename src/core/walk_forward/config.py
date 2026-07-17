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
    # Holding horizon H in trading days (buy T+1 close, sell T+1+H close).
    # H=1 = today's 2-day Alpha158 label, byte-identical (REGEN-2 anchor).
    # Threaded to the feature dataset (label expression + cache key), the
    # fold-gap embargo (H+1 trading days), and the resume fingerprint.
    label_horizon_days: int = 1
    # Audit P2 (add-pit-analyzer-routing): path to the Phase A.2 delisted
    # registry parquet. Empty (default) = no PIT provider constructed, the
    # analyzers run their legacy WARN path — today's behavior, identity-
    # preserving. Non-empty = the engine constructs ONE PITDataProvider at
    # run start (missing/malformed registry FAILS LOUD at construction, never
    # a silent fall-through) and threads it to analyzers accepting
    # pit_provider (PR-1: PerformanceAttribution; PR-2: SignalAnalyzer).
    delisted_registry_path: str = ""

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
    # CANONICAL benchmark = the TOTAL-RETURN index SH000300TR (dividends reinvested),
    # APPLIED at REGEN-2 (the rebuilt bundle carries it). This in-code default is
    # consumed when a walk-forward config omits benchmark_code. SH000300 is the PRICE
    # index, preserved only as the REGEN-A control (see PR-E / 07_ingest_benchmark).
    benchmark_code: str = "SH000300TR"
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
    # backtest (C2-d PR2). On the official walk-forward path the mask is
    # MANDATORY (audit E1 / PR-F): the engine passes
    # ``require_st_mask=True`` and a missing/blank path is a HARD error —
    # NOT a WARN — unless ``st_mask_mode`` explicitly opts out (below).
    namechange_path: str | None = None
    # ST-exclusion mode for the official backtest. Audit E1 / PR-F made the
    # ST mask mandatory on the walk-forward path; the run-comparison
    # runbook's isolated label experiments (阶段6 label-horizon campaign)
    # need an EXPLICIT, stamped opt-out — never a silent one:
    #   * "required" (default) — official semantics, byte-identical to
    #     pre-field behavior: missing namechange_path fails loud.
    #   * "off_experiment"     — pre-registered experiment runs ONLY
    #     (ST-off on BOTH sides per docs/run-comparison-runbook.md). The
    #     universe keeps ST names and BacktestRunner WARNs per fold; the
    #     mode rides into walk_forward_report.json via the embedded config
    #     (and the resume fingerprint), so a comparison can PROVE both
    #     sides ran the same ST handling. Combining it with a set
    #     namechange_path is contradictory and rejected at construction.
    st_mask_mode: str = "required"

    # 阶段7 (add-rebalance-cadence, Route A signal thinning): portfolio
    # rebalance cadence. THE REBALANCE DAY IS THE SIGNAL-STAMP DAY; THE
    # FILL STILL HAPPENS AT T+signal_to_execution_lag (thinning changes
    # WHICH days carry a signal, never the execution timing) — but ONLY at
    # signal_to_execution_lag=1: N>1 with lag>1 is refused (the thinning
    # precedes the position-based lag restamp, which is calendar-correct
    # only on a dense daily series; see __post_init__ and codex P1 on #336).
    # On days
    # without a signal stamp, qlib's TopkDropoutStrategy emits zero orders
    # and the portfolio holds while still accruing market-value returns —
    # third-party behavior pinned by the cadence CONTRACT test against the
    # committed mini-bundle, never merely trusted.
    #   * rebalance_cadence_days=1 (default): today's daily rebalance,
    #     byte-identical — no filter is constructed at all.
    #   * N>1 with rebalance_anchor="fold_phase": signals kept on every Nth
    #     trading day of the evaluation window, starting at day
    #     `rebalance_phase` (0 <= phase < N). Per-fold phase reset is the
    #     MECHANISM-experiment semantics (each fold starts from cash; 23
    #     folds' phase heterogeneity dilutes weekday effects).
    #   * rebalance_anchor="iso_week": signals kept on the FIRST trading
    #     day of each ISO week — the deployable calendar semantics (the 7b
    #     escalation form pre-commits the winning arm's ST-on re-verify to
    #     this anchor). Requires the nominal N=5 and phase=0: a real week
    #     carries 3-5 trading days, so N/phase have no derivational meaning
    #     under this anchor and any other value would be a silently-ignored
    #     lie.
    rebalance_cadence_days: int = 1
    rebalance_phase: int = 0
    rebalance_anchor: str = "fold_phase"

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

    # CSI800 expansion guard-2 (v2-csi800-expansion-guards): Brinson
    # grouping by csi300/csi500 membership sleeves (mutually exclusive
    # with the industry artifact — one run, one grouping source), and
    # mandatory position-level risk constraints (MinimalRiskConstraints
    # defaults threaded into BacktestRunner.run per fold, effective
    # values recorded in each fold's backtest provenance — veto-4).
    attribution_sleeve_grouping: bool = False
    risk_constraints_enabled: bool = False

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

        if self.attribution_sleeve_grouping and self.industry_artifact_path:
            raise WalkForwardError(
                "attribution_sleeve_grouping and industry_artifact_path "
                "are mutually exclusive — one Brinson run takes exactly "
                "one grouping source (v2-csi800-expansion-guards)."
            )

        if self.st_mask_mode not in ("required", "off_experiment"):
            raise WalkForwardError(
                f"st_mask_mode must be 'required' or 'off_experiment'; got "
                f"{self.st_mask_mode!r}. 'required' is the official path "
                "(audit E1 / PR-F); 'off_experiment' is ONLY for "
                "pre-registered isolated experiments (ST-off on both sides, "
                "docs/run-comparison-runbook.md)."
            )
        if self.st_mask_mode == "off_experiment" and (self.namechange_path or "").strip():
            raise WalkForwardError(
                "st_mask_mode='off_experiment' with a namechange_path set is "
                f"contradictory (namechange_path={self.namechange_path!r}): an "
                "ST-off experiment run must not silently carry ST-mask inputs "
                "— one variable at a time (docs/run-comparison-runbook.md). "
                "Drop/blank namechange_path for the experiment, or use "
                "st_mask_mode='required' for official semantics."
            )

        n_cad = self.rebalance_cadence_days
        if not isinstance(n_cad, int) or isinstance(n_cad, bool) or n_cad < 1:
            raise WalkForwardError(
                f"rebalance_cadence_days must be a positive integer (trading "
                f"days between rebalances; 1 = daily); got {n_cad!r}."
            )
        p_cad = self.rebalance_phase
        if (
            not isinstance(p_cad, int)
            or isinstance(p_cad, bool)
            or not 0 <= p_cad < n_cad
        ):
            raise WalkForwardError(
                f"rebalance_phase must be an integer in [0, "
                f"rebalance_cadence_days); got phase={p_cad!r} with "
                f"N={n_cad}. In particular N=1 requires phase=0 — a phase "
                "under daily cadence is a meaningless combination and must "
                "never pass silently."
            )
        if self.rebalance_anchor not in ("fold_phase", "iso_week"):
            raise WalkForwardError(
                f"rebalance_anchor must be 'fold_phase' or 'iso_week'; got "
                f"{self.rebalance_anchor!r}."
            )
        if self.rebalance_anchor == "iso_week" and (n_cad != 5 or p_cad != 0):
            raise WalkForwardError(
                "rebalance_anchor='iso_week' requires the nominal "
                "rebalance_cadence_days=5 and rebalance_phase=0: the ISO-week "
                "anchor derives rebalance days from the week structure (a "
                "real week carries 3-5 trading days), so N/phase have no "
                f"derivational meaning under it — got N={n_cad}, "
                f"phase={p_cad}; any other value would be a silently-ignored "
                "lie."
            )
        # The lag interaction (codex P1 on #336): thinning happens before the
        # position-based _apply_lag restamp, which equals a trading-day shift
        # only on a dense daily calendar. N>1 with lag>1 would restamp a
        # signal ~N days out instead of T+lag — refused rather than silently
        # producing wrong fills. lag=1 (no restamp) is the canonical path and
        # the only one the cadence campaign uses.
        # The isinstance guard (codex P2 on #336) keeps a MALFORMED lag (a
        # quoted string / None from YAML) on the dedicated lag validator
        # below — which raises WalkForwardError — instead of a raw TypeError
        # from the ``> 1`` comparison here.
        if (
            n_cad > 1
            and isinstance(self.signal_to_execution_lag, int)
            and not isinstance(self.signal_to_execution_lag, bool)
            and self.signal_to_execution_lag > 1
        ):
            raise WalkForwardError(
                f"rebalance_cadence_days={n_cad} (>1) with "
                f"signal_to_execution_lag={self.signal_to_execution_lag} (>1) "
                "is not jointly supported: the thinning-before-lag restamp "
                "would land the fill ~N trading days out instead of at "
                "T+lag. Use signal_to_execution_lag=1 with a non-daily "
                "cadence (the canonical path)."
            )

        h = self.label_horizon_days
        if not isinstance(h, int) or isinstance(h, bool) or h < 1:
            raise WalkForwardError(
                f"label_horizon_days must be a positive integer (holding days, "
                f"T+1 close -> T+1+H close); got {h!r}."
            )
        if h != 1 and self.feature_handler != "Alpha158":
            # Must refuse HERE, at config construction: the engine's per-fold
            # error isolation would otherwise catch FeatureDatasetBuilder's
            # rejection fold by fold and finish with an all-NaN placeholder
            # report instead of failing at config load (codex P2 on #318).
            raise WalkForwardError(
                f"label_horizon_days={h} is only supported for feature_handler="
                f"'Alpha158'; handler '{self.feature_handler}' defines its own "
                "label and would silently ignore the horizon. Use the default "
                "(1) or add horizon support to that handler first."
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

    @property
    def requires_st_mask(self) -> bool:
        """What the engine passes to ``BacktestRunner.run(require_st_mask=)``.

        ``True`` on the official path (``st_mask_mode="required"``, audit
        E1 / PR-F: a missing namechange_path fails LOUD). ``False`` only for
        ``"off_experiment"`` — the explicit, validated, report-stamped
        experiment opt-out; BacktestRunner then takes its research WARN path.
        """
        return self.st_mask_mode == "required"
