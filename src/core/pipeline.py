"""V2 Quantitative Trading Pipeline — orchestrates the full workflow.

init → features → model → signal → backtest → factor analysis → attribution → report

All steps are wired through V2's contract and governance system.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from src.contracts.taxonomy_data_contract import TAXONOMY_MODE_STATIC
from src.core._json_utils import _sanitize_for_json, sha256_canonical
from src.core._shared_validators import validate_n_drop, validate_topk
from src.core.attribution_industry_loader import (
    PURPOSE_ATTRIBUTION,
    IndustryTaxonomyLoadError,
    assert_industry_config_complete_or_empty,
    resolve_industry_taxonomy,
)
from src.core.attribution_sleeve_loader import (
    SleeveResolutionError,
    resolve_sleeve_map,
    sleeve_turnover,
)
from src.core.backtest_runner import BacktestRunner
from src.core.canonical_backtest_contract import (
    ADJUST_MODE_PRE,
    CanonicalAccountConfig,
    CanonicalBacktestContractError,
    CanonicalBacktestInput,
    CanonicalBacktestOutput,
    CanonicalExchangeConfig,
    CanonicalExchangeCostModel,
    resolve_stamp_tax_schedule,
)
from src.core.factor_analyzer import FactorAnalysisConfig, FactorAnalysisResult, FactorAnalyzer
from src.core.git_provenance import capture_git_provenance
from src.core.logger import get_logger
from src.core.model_config_projection import build_model_train_config
from src.core.model_trainer import (
    GPU_SUPPORTED_MODEL_TYPES,
    SUPPORTED_COMPUTE_DEVICES,
    ModelTrainer,
    ModelTrainResult,
)
from src.core.performance_attribution import (
    AttributionConfig,
    AttributionResult,
    PerformanceAttribution,
    PerformanceAttributionError,
)
from src.core.pipeline_result_artifacts import write_pipeline_result_artifacts
from src.core.qlib_runtime import (
    QlibRuntimeConfig,
    init_qlib_canonical,
    provider_uri_guard_message,
)
from src.core.risk_constraints import (
    MinimalRiskConstraints,
    campaign_risk_constraints_v1,
)
from src.core.run_catalog import append_run_record
from src.core.run_catalog import build_record as build_catalog_record
from src.core.signal_analyzer import SignalAnalysisConfig, SignalAnalysisResult, SignalAnalyzer
from src.core.visualizer import ResultVisualizer, VisualizerConfig
from src.data.feature_dataset_builder import FeatureDatasetBuilder, FeatureDatasetConfig, FeatureDatasetResult

# Re-export the shared sanitizer at the previous public symbol so
# existing imports / tests that look up ``Pipeline``/``pipeline._sanitize_for_json``
# keep working unchanged. The implementation now lives in ``_json_utils``
# so ``walk_forward`` can call the same code without duplicating it.
_logger = get_logger(__name__)


class PipelineError(RuntimeError):
    """Raised on pipeline orchestration failures."""


@dataclass(frozen=True)
class PipelineConfig:
    """Complete pipeline configuration."""

    # qlib runtime
    provider_uri: str
    region: str = "cn"

    # features
    instruments: str = "csi300"
    feature_handler: str = "Alpha158"
    # Holding horizon H in trading days (buy T+1 close, sell T+1+H close).
    # H=1 = today's 2-day Alpha158 label, byte-identical. Two engines, one
    # schema: same field/semantics as WalkForwardConfig.label_horizon_days.
    label_horizon_days: int = 1
    # Audit P2 (add-pit-analyzer-routing): same field/semantics as
    # WalkForwardConfig.delisted_registry_path (two engines, one schema).
    # Empty = legacy WARN path (identity-preserving); non-empty = ONE
    # PITDataProvider built at run start, missing registry fails loud.
    delisted_registry_path: str = ""
    train_start: str = "2022-01-01"
    train_end: str = "2024-12-31"
    valid_start: str = "2025-01-01"
    valid_end: str = "2025-06-30"
    test_start: str = "2025-07-01"
    test_end: str = "2025-12-31"

    # model
    model_type: str = "LGBModel"
    num_boost_round: int = 1000
    early_stopping_rounds: int = 50
    learning_rate: float = 0.005
    max_depth: int = 6
    num_leaves: int = 64
    # LGB regularisation / sampling. Tuned defaults that match
    # config_walk.yaml and keep the booster training past the
    # best_iteration~1 plateau (C2-c). These are NOT LightGBM's neutral
    # defaults — the old neutral set let under-specified configs inherit
    # unregularised, over-wide trees.
    lambda_l1: float = 0.0
    lambda_l2: float = 1.0
    min_data_in_leaf: int = 50
    feature_fraction: float = 0.8
    bagging_fraction: float = 0.8
    bagging_freq: int = 5

    # backtest
    # Canonical total-return benchmark (REGEN-2, applied). SH000300 is the REGEN-A price control.
    benchmark_code: str = "SH000300TR"
    init_cash: float = 100_000_000
    commission_rate: float = 0.0005
    # CN A-share stamp tax — represented as a time-ordered schedule
    # rather than a single scalar so the 2023-08-28 reform (0.1% →
    # 0.05% sell-only) is modelled correctly on backtest windows that
    # cross the transition. ``None`` resolves to
    # ``CN_STAMP_TAX_SCHEDULE_DEFAULT`` (recommended). A custom value
    # must be a sequence of ``{effective_from: ISO-date, bps: float}``
    # mappings; see audit P0-4 / openspec/changes/add-stamp-tax-schedule
    # for the format. The legacy scalar key ``stamp_tax_bps`` is no
    # longer accepted — the YAML loader raises with a migration message.
    stamp_tax_schedule: Any = None
    slippage_bps: float = 5.0
    min_cost: float = 5.0
    execution_price_kind: str = "close"
    adjust_mode: str = ADJUST_MODE_PRE
    signal_to_execution_lag: int = 1
    topk: int = 50
    n_drop: int = 5
    # Tushare namechange parquet for PIT historical ST/*ST exclusion in the
    # backtest (C2-d PR2). None -> ST mask disabled (the backtest universe
    # still includes ST, logged as a WARN). Set to all_namechanges.parquet to
    # exclude ST point-in-time.
    namechange_path: str | None = None
    # A-share price-move bound: 0.095 = main board ±10%,
    # 0.195 = ChiNext/STAR ±20%, 0.045 = ST ±5%. Must match the
    # dominant board of the universe; canonical contract bounds check.
    limit_threshold: float = 0.095

    # reproducibility — seed for numpy/python random/LGB/XGB/CatBoost
    seed: int = 42
    compute_device: str = "cpu"

    # factor analysis
    run_factor_analysis: bool = True
    factor_forward_period: int = 5
    factor_top_n: int = 20
    factor_max_decay_lag: int = 20

    # performance attribution
    run_attribution: bool = True
    industry_artifact_path: str | None = None
    industry_manifest_path: str | None = None
    industry_taxonomy_id: str = ""
    industry_temporal_mode: str = TAXONOMY_MODE_STATIC
    # CSI800 expansion guard-2 (v2-csi800-expansion-guards): Brinson
    # grouping by csi300/csi500 membership sleeves instead of industries
    # (mutually exclusive with the industry artifact — one run, one
    # grouping source), and mandatory position-level risk constraints
    # (MinimalRiskConstraints defaults threaded into BacktestRunner.run,
    # effective values recorded in provenance — veto-4).
    attribution_sleeve_grouping: bool = False
    risk_constraints_enabled: bool = False
    # Which constraint calibration ``risk_constraints_enabled`` supplies
    # (codex P2 on #372): "default" = the P0-1 class defaults (0.40
    # board cap, 1% cash floor — the pre-campaign semantics); the
    # campaign calibration is an EXPLICIT opt-in, never silently
    # substituted for a non-campaign run.
    risk_constraints_calibration: str = "default"

    # output
    output_dir: str = "output"

    def __post_init__(self) -> None:
        # *Validate-only*, no field mutation (frozen=True). Catch the
        # cheap, definitely-wrong combinations at the boundary so the
        # operator does not have to wait for ``FeatureDatasetBuilder._validate``
        # or ``CanonicalBacktestInput`` to surface them deep in the run.
        # Heavier semantic checks (date format, ISO calendar feasibility,
        # qlib bundle alignment) stay where they were — this is just the
        # config-shape sieve.
        if not self.provider_uri:
            raise PipelineError(
                "PipelineConfig.provider_uri must be a non-empty path; "
                "qlib needs an explicit data bundle location."
            )
        if not self.benchmark_code:
            raise PipelineError(
                "PipelineConfig.benchmark_code must be non-empty; the "
                "canonical backtest contract requires a benchmark."
            )
        if self.attribution_sleeve_grouping and self.industry_artifact_path:
            raise PipelineError(
                "attribution_sleeve_grouping and industry_artifact_path "
                "are mutually exclusive — one Brinson run takes exactly "
                "one grouping source (v2-csi800-expansion-guards)."
            )
        if self.attribution_sleeve_grouping and not self.run_attribution:
            raise PipelineError(
                "attribution_sleeve_grouping=True requires "
                "run_attribution=True — disabling attribution would skip "
                "the sleeve resolution entirely and emit bare csi800 "
                "metrics without the mandated decomposition "
                "(v2-csi800-expansion-guards, codex P1 on #370)."
            )
        if self.risk_constraints_calibration not in ("default",
                                                     "campaign_v1"):
            raise PipelineError(
                "risk_constraints_calibration must be 'default' or "
                f"'campaign_v1'; got {self.risk_constraints_calibration!r}."
            )
        if self.instruments == "csi800" and not (
                self.attribution_sleeve_grouping
                and self.risk_constraints_enabled
                and self.risk_constraints_calibration == "campaign_v1"):
            raise PipelineError(
                "instruments='csi800' requires attribution_sleeve_grouping="
                "True, risk_constraints_enabled=True AND "
                "risk_constraints_calibration='campaign_v1' — official "
                "csi800 metrics without the sleeve report and the campaign "
                "constraint calibration are forbidden; presets "
                "config/presets/csi800*.yaml carry all three "
                "(v2-csi800-expansion-guards, codex #370 r6 + #372 r1; "
                "custom/copied campaign configs get no bypass)."
            )
        h = self.label_horizon_days
        if not isinstance(h, int) or isinstance(h, bool) or h < 1:
            raise PipelineError(
                f"label_horizon_days must be a positive integer (holding days, "
                f"T+1 close -> T+1+H close); got {h!r}."
            )
        if h != 1 and self.feature_handler != "Alpha158":
            # Same up-front refusal as WalkForwardConfig (two engines, one
            # schema): fail at config construction, not deep inside the run
            # (codex P2 on #318).
            raise PipelineError(
                f"label_horizon_days={h} is only supported for feature_handler="
                f"'Alpha158'; handler '{self.feature_handler}' defines its own "
                "label and would silently ignore the horizon. Use the default "
                "(1) or add horizon support to that handler first."
            )
        if self.compute_device not in SUPPORTED_COMPUTE_DEVICES:
            raise PipelineError(
                f"PipelineConfig.compute_device must be one of "
                f"{SUPPORTED_COMPUTE_DEVICES}; got {self.compute_device!r}."
            )
        if (
            self.compute_device == "gpu"
            and self.model_type not in GPU_SUPPORTED_MODEL_TYPES
        ):
            raise PipelineError(
                "PipelineConfig.compute_device='gpu' is currently supported "
                f"only for {GPU_SUPPORTED_MODEL_TYPES}; got "
                f"model_type={self.model_type!r}. Refusing to silently fall "
                "back to CPU."
            )
        # Window order: train < valid < test. The downstream feature
        # builder validates date *format*; here we just check ordering
        # so a transposed train/test window does not waste an entire
        # pipeline run before failing.
        windows = (
            ("train", self.train_start, self.train_end),
            ("valid", self.valid_start, self.valid_end),
            ("test", self.test_start, self.test_end),
        )
        for name, start, end in windows:
            if not start or not end:
                raise PipelineError(
                    f"PipelineConfig.{name}_start / {name}_end must both be "
                    f"non-empty; got start={start!r}, end={end!r}."
                )
            try:
                start_d = date.fromisoformat(str(start))
                end_d = date.fromisoformat(str(end))
            except ValueError as exc:
                raise PipelineError(
                    f"PipelineConfig.{name}_start / {name}_end must be strict "
                    f"ISO dates in YYYY-MM-DD format; got start={start!r}, "
                    f"end={end!r}."
                ) from exc
            if start_d >= end_d:
                raise PipelineError(
                    f"PipelineConfig.{name}_start ({start}) must be strictly "
                    f"less than {name}_end ({end})."
                )
        # Cross-window order: train_end < valid_start < test_start.
        # The per-window check above only validates each window
        # internally; overlapping windows silently leak training data
        # into validation / test sets, inflating backtest metrics.
        for prev_end_attr, next_start_attr in (
            ("train_end", "valid_start"),
            ("valid_end", "test_start"),
        ):
            prev_end_d = date.fromisoformat(str(getattr(self, prev_end_attr)))
            next_start_d = date.fromisoformat(str(getattr(self, next_start_attr)))
            if prev_end_d >= next_start_d:
                raise PipelineError(
                    f"PipelineConfig.{prev_end_attr} "
                    f"({getattr(self, prev_end_attr)}) must be strictly "
                    f"before {next_start_attr} "
                    f"({getattr(self, next_start_attr)})."
                )
        # Numeric sanity: positive cash, non-negative cost components,
        # topk ≥ 1.
        if self.init_cash <= 0:
            raise PipelineError(
                f"PipelineConfig.init_cash must be positive; got {self.init_cash!r}."
            )
        validate_topk(self.topk, error_class=PipelineError, prefix="PipelineConfig.")
        # Cost / fee parameters must be non-negative. Negative
        # commission / stamp tax / slippage / min_cost would silently
        # *add* return rather than subtract it — backtest looks better
        # than reality. ``CanonicalExchangeCostModel.__post_init__``
        # would catch these at backtest-construction time, but by then
        # the feature build + model train + predict steps have already
        # run for several minutes; failing here at config construction
        # avoids the wasted compute.
        # ``stamp_tax_schedule`` has its own validation below — it is
        # a sequence-of-mappings shape, not a scalar — so the per-field
        # numeric check applies only to the remaining three scalar
        # cost knobs. Audit P0-4 (add-stamp-tax-schedule).
        for name in ("commission_rate", "slippage_bps", "min_cost"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise PipelineError(
                    f"PipelineConfig.{name} must be a real number; got "
                    f"{type(value).__name__} ({value!r})."
                )
            if value < 0:
                raise PipelineError(
                    f"PipelineConfig.{name} must be >= 0 to avoid silently "
                    f"inflating returns by negative cost; got {value!r}."
                )
        # Resolve AND fully validate the stamp-tax schedule now so a
        # malformed value in the YAML config fails AT CONFIG
        # CONSTRUCTION, not after several minutes of feature build.
        # ``resolve_stamp_tax_schedule`` alone only coerces the
        # YAML shape — ordering / duplicate-date checks live on
        # ``CanonicalExchangeCostModel._validate_stamp_tax_schedule``.
        # Construct a throwaway cost-model with the resolved schedule
        # so BOTH validators fire here, matching the pattern
        # ``WalkForwardConfig.__post_init__`` uses to validate its
        # backtest controls. Codex P2 follow-up on PR #178.
        try:
            resolved_schedule = resolve_stamp_tax_schedule(
                self.stamp_tax_schedule,
            )
            CanonicalExchangeCostModel(
                commission_rate=self.commission_rate,
                stamp_tax_schedule=resolved_schedule,
                slippage_bps=self.slippage_bps,
                min_cost=self.min_cost,
            )
        except CanonicalBacktestContractError as exc:
            raise PipelineError(
                f"PipelineConfig.stamp_tax_schedule failed validation: {exc}"
            ) from exc
        # n_drop validity + n_drop < topk lock-step — shared with
        # WalkForwardConfig via _shared_validators so a copy-pasted
        # ``topk=10, n_drop=10`` can't slip through one path while being
        # rejected on the other.
        validate_n_drop(
            self.n_drop, self.topk,
            error_class=PipelineError,
            prefix="PipelineConfig.",
        )
        if (
            not isinstance(self.limit_threshold, (int, float))
            or isinstance(self.limit_threshold, bool)
        ):
            raise PipelineError(
                "PipelineConfig.limit_threshold must be a real number; got "
                f"{type(self.limit_threshold).__name__}."
            )
        if not (0.0 < float(self.limit_threshold) <= 0.25):
            raise PipelineError(
                "PipelineConfig.limit_threshold must be in (0, 0.25]; got "
                f"{self.limit_threshold!r}."
            )
        if isinstance(self.signal_to_execution_lag, bool) or not isinstance(
            self.signal_to_execution_lag,
            int,
        ):
            raise PipelineError(
                "PipelineConfig.signal_to_execution_lag must be an int, not "
                f"{type(self.signal_to_execution_lag).__name__}; got "
                f"{self.signal_to_execution_lag!r}."
            )
        if self.signal_to_execution_lag < 1:
            raise PipelineError(
                "PipelineConfig.signal_to_execution_lag must be >= 1 (the "
                "TOTAL signal→fill delay; 1 = T+1 execution); got "
                f"{self.signal_to_execution_lag!r}. 0 (same-day) is rejected "
                "on the canonical path — it would publish look-ahead results "
                "as official metrics."
            )
        # Model hyperparameter sanity: reject definitely-wrong values
        # (zero/negative) at config construction so the operator does not
        # wait for dataset build + model init to discover them. Heavier
        # checks (LGB num_leaves <= 2^max_depth, CatBoost depth <= 16)
        # are deferred to ModelTrainer._validate.
        if self.num_boost_round < 1:
            raise PipelineError(
                f"PipelineConfig.num_boost_round must be >= 1; got "
                f"{self.num_boost_round!r}."
            )
        if self.learning_rate <= 0:
            raise PipelineError(
                f"PipelineConfig.learning_rate must be > 0; got "
                f"{self.learning_rate!r}."
            )
        if self.max_depth < 1:
            raise PipelineError(
                f"PipelineConfig.max_depth must be >= 1; got "
                f"{self.max_depth!r}."
            )
        # Industry-taxonomy fields: enforce all-or-nothing + supported
        # ``temporal_mode``. Same boundary contract as
        # ``WalkForwardConfig.__post_init__`` so the two configs cannot
        # diverge on partial-config rejection.
        assert_industry_config_complete_or_empty(
            artifact_path=self.industry_artifact_path,
            manifest_path=self.industry_manifest_path,
            taxonomy_id=self.industry_taxonomy_id,
            temporal_mode=self.industry_temporal_mode,
            error_class=PipelineError,
            error_prefix="PipelineConfig",
        )


@dataclass(frozen=True)
class PipelineResult:
    """Pipeline execution result."""

    feature_result: FeatureDatasetResult
    model_result: ModelTrainResult
    signal_analysis: SignalAnalysisResult
    backtest_output: CanonicalBacktestOutput
    factor_analysis: FactorAnalysisResult | None
    attribution: AttributionResult | None
    report_path: str


class Pipeline:
    """Orchestrates the full V2 quantitative trading pipeline."""

    @classmethod
    def run(cls, config: PipelineConfig) -> PipelineResult:
        # Fail loud on a missing / misconfigured bundle BEFORE creating a run
        # directory or initializing qlib: a non-existent provider_uri otherwise
        # surfaces as an obscure qlib error deep in feature loading, not a clear
        # "set QUANT_PROVIDER_URI" up front (and avoids littering an empty run
        # dir for a doomed run).
        guard_message = provider_uri_guard_message(config.provider_uri)
        if guard_message is not None:
            raise PipelineError(guard_message)
        # Per-run output directory: output/runs/{timestamp}_{fingerprint}/
        # Prevents successive runs from silently overwriting each other.
        # The fingerprint is computed from the config so re-running with
        # identical settings is visible in the directory name.
        root_dir = Path(config.output_dir)
        output_dir = cls._make_run_dir(root_dir, config)
        # exist_ok=False: if our microsecond timestamp somehow still collides
        # (extreme race on very coarse clocks), fail loud rather than clobber
        # an earlier run's artifacts.
        output_dir.mkdir(parents=True, exist_ok=False)
        _logger.info("Run directory: %s", output_dir)
        started_at = datetime.now(tz=timezone.utc).isoformat()
        # Captured at RUN START, not at report-write time: if HEAD advances while the run
        # executes, a write-time capture would attribute the artifacts to code that did
        # not produce them — and the pre-registration ancestor gate could mistake a plan
        # committed mid-run for one that predates the run (codex P1 on #313).
        git_provenance = capture_git_provenance()

        # Step 1: Initialize qlib (or validate config matches existing init)
        _logger.info("Initializing qlib runtime...")
        requested_config = QlibRuntimeConfig(
            provider_uri=config.provider_uri,
            region=config.region,
            data_adjust_mode=config.adjust_mode,
        )
        # init_qlib_canonical is idempotent for same config, raises on mismatch
        init_qlib_canonical(requested_config)

        # Audit P2 (add-pit-analyzer-routing): ONE optional PIT provider for the
        # run (empty registry path -> None = legacy WARN path). Bound to THIS
        # runtime's labels so the provider's init is an idempotent no-op.
        from src.core.pit_wiring import build_pit_provider
        pit_provider = build_pit_provider(
            delisted_registry_path=config.delisted_registry_path,
            # the NORMALIZED runtime value (QlibRuntimeConfig.__post_init__
            # expands ~/relative/casing), same as the engine's canonical_cfg
            # source — never the raw config string (codex P2 on #320)
            provider_uri=requested_config.provider_uri,
            data_adjust_mode=config.adjust_mode,
            region=config.region,
        )

        # Step 2: Build feature dataset
        _logger.info("Building feature dataset...")
        feature_result = FeatureDatasetBuilder.build(FeatureDatasetConfig(
            instruments=config.instruments,
            feature_handler=config.feature_handler,
            train_start=config.train_start,
            train_end=config.train_end,
            valid_start=config.valid_start,
            valid_end=config.valid_end,
            test_start=config.test_start,
            test_end=config.test_end,
            label_horizon_days=config.label_horizon_days,
        ))
        _logger.info(
            "  Train: %s, Valid: %s, Test: %s",
            feature_result.train_shape, feature_result.valid_shape, feature_result.test_shape,
        )

        # Step 3: Train model
        _logger.info("Training model...")
        model_artifact_path = str(output_dir / "artifacts" / "model.pkl")
        model_result = ModelTrainer.train_and_predict(
            config=build_model_train_config(config),
            dataset=feature_result.dataset,
            model_artifact_path=model_artifact_path,
        )
        _logger.info("  Predictions: %s", model_result.prediction_shape)

        # Step 4: Signal quality analysis
        _logger.info("Analyzing signal quality...")
        signal_result = SignalAnalyzer.analyze(
            predictions=model_result.predictions,
            config=SignalAnalysisConfig(topk=config.topk),
            pit_provider=pit_provider,
        )
        SignalAnalyzer.print_report(signal_result)

        # Step 5: Run canonical backtest
        _logger.info("Running canonical backtest...")
        # predictions_ref is a provenance marker (where the model artifact lives),
        # not consumed by BacktestRunner — predictions are passed directly below.
        backtest_request = CanonicalBacktestInput(
            predictions_ref=model_artifact_path,
            evaluation_start=config.test_start,
            evaluation_end=config.test_end,
            account_config=CanonicalAccountConfig(init_cash=config.init_cash),
            exchange_config=CanonicalExchangeConfig(
                freq="day",
                execution_price_kind=config.execution_price_kind,
                cost_model=CanonicalExchangeCostModel(
                    commission_rate=config.commission_rate,
                    stamp_tax_schedule=resolve_stamp_tax_schedule(
                        config.stamp_tax_schedule,
                    ),
                    slippage_bps=config.slippage_bps,
                    min_cost=config.min_cost,
                ),
                limit_threshold=config.limit_threshold,
            ),
            adjust_mode=config.adjust_mode,
            signal_to_execution_lag=config.signal_to_execution_lag,
            benchmark_code=config.benchmark_code,
        )

        backtest_output = BacktestRunner.run(
            request=backtest_request,
            predictions=model_result.predictions,
            topk=config.topk,
            n_drop=config.n_drop,
            # (b) Step 2 (codex P1 on #365): carry the run's universe so the
            # LOAD-time canonical-benchmark check can flag a MIS-PAIRED
            # canonical code (csi800 on the csi300 basket or vice versa),
            # not just out-of-set codes.
            universe_hint=config.instruments,
            # CSI800 guard-2 (veto-4): calibration is an EXPLICIT config
            # choice (codex P2 on #372) — campaign presets opt into
            # campaign_v1 (name/leverage strict, board/cash structural
            # mismatches disabled with rationale); everything else keeps
            # the P0-1 defaults. Effective values land in provenance.
            risk_constraints=(
                (campaign_risk_constraints_v1()
                 if config.risk_constraints_calibration == "campaign_v1"
                 else MinimalRiskConstraints())
                if config.risk_constraints_enabled else None),
            # Audit P2 tail (P0-6 follow-up): thread the run-level PIT
            # provider into the backtest (microstructure mask + equal-weight
            # baseline raw-field fetches take the §4.3.2 layer instead of
            # the WARN fallback). None preserves the legacy path bit-for-bit.
            pit_provider=pit_provider,
            namechange_path=config.namechange_path,
            # Official single-fold path: ST exclusion is mandatory, aligned
            # with walk-forward + live recommend — a missing namechange_path
            # fails loud rather than silently including ST (audit E1 / PR-F).
            require_st_mask=True,
            # Per-run dir (output/runs/{ts}_{uniq}_{fp}), NOT config.output_dir —
            # so the audit lands with the run's other artifacts and a re-run
            # cannot overwrite it (Codex P2 on #223).
            st_audit_path=str(output_dir / "st_mask_audit.csv"),
        )

        # Step 6: Factor analysis (optional)
        factor_result: FactorAnalysisResult | None = None
        factor_skipped_reason: str | None = None
        if config.run_factor_analysis:
            _logger.info("Running factor analysis...")
            # Reuse the Alpha158 dataset already built in step 2 — otherwise
            # FactorAnalyzer would rebuild the (expensive) handler from zero.
            try:
                factor_result = FactorAnalyzer.analyze(
                    FactorAnalysisConfig(
                        instruments=config.instruments,
                        feature_handler=config.feature_handler,
                        test_start=config.test_start,
                        test_end=config.test_end,
                        forward_period=config.factor_forward_period,
                        top_n_factors=config.factor_top_n,
                        max_decay_lag=config.factor_max_decay_lag,
                    ),
                    dataset=feature_result.dataset,
                )
                FactorAnalyzer.print_report(factor_result)
            except Exception as exc:  # noqa: BLE001
                factor_result = None
                factor_skipped_reason = f"{type(exc).__name__}: {exc}"
                _logger.warning(
                    "Factor analysis skipped after successful backtest: %s. "
                    "Pipeline report will still be written with a skipped "
                    "factor_analysis block.",
                    factor_skipped_reason,
                )
        else:
            factor_skipped_reason = "disabled_by_config"

        # Step 7a: Persist positions artifact (authoritative portfolio
        # weights) *before* attribution. Previously this lived after the
        # attribution step; if attribution raised any exception other
        # than ``PerformanceAttributionError`` (e.g. a NameError in
        # downstream changes, a missing dependency on first run) the
        # positions JSON would never be written and the entire backtest
        # output would be lost — even though the backtest itself
        # finished successfully. Persisting first means a hard failure
        # later still leaves positions on disk for inspection.
        positions_sha256: str | None = None
        if backtest_output.positions:
            positions_path, positions_sha256 = cls._persist_positions(
                output_dir, backtest_output.positions,
            )
            _logger.info(
                "  Positions: %s (%d days)",
                positions_path, len(backtest_output.positions),
            )

        # Step 7b: Performance attribution (optional)
        attribution_result: AttributionResult | None = None
        # Track *why* attribution was skipped (if it was). Persisted to
        # the JSON report so downstream consumers / dashboards can tell
        # "attribution absent because we didn't run it" from "attribution
        # absent because the engine refused degenerate input" — both used
        # to look identical in the report (no ``attribution`` block at
        # all), even though only the second is a degraded run.
        attribution_skipped_reason: str | None = None
        sleeve_turnover_block: dict[str, dict[str, float]] | None = None
        if not config.run_attribution:
            attribution_skipped_reason = "disabled_by_config"
        else:
            # ``run_attribution`` is bool; the previous ``elif config.run_attribution``
            # was logically equivalent to ``else`` and only added noise
            # for a reader walking the branch tree. Plain ``else`` makes
            # the intent obvious.
            if not backtest_output.positions:
                # guard-2 (codex P1 on #370 r4): with sleeve grouping on,
                # EVERY inability-to-produce-the-sleeve-report path is
                # fatal — not just map construction.
                if cls._attribution_failure_is_fatal(config):
                    raise PipelineError(
                        "attribution_sleeve_grouping=True but the backtest "
                        "produced no positions map — the mandatory sleeve "
                        "report cannot be built; refusing to emit bare "
                        "csi800 metrics (v2-csi800-expansion-guards)."
                    )
                # The previous implementation silently coerced ``positions`` to
                # ``None`` here, which flipped PerformanceAttribution into its
                # prediction-score fallback mode — a semantically-different
                # attribution under the same metric name. That violates the
                # repo's "no implicit fallback" rule (see backtest_runner
                # ``_positions_to_weight_map`` docstring for the full chain).
                # We now skip the step explicitly and log loudly.
                attribution_skipped_reason = "no_positions_from_backtest"
                _logger.warning(
                    "Skipping performance attribution: backtest produced no "
                    "positions map (len=%d). Attribution is configured as "
                    "position-based — refusing to silently fall back to "
                    "prediction-score attribution. Check backtest_runner "
                    "logs for per-day position parse warnings.",
                    len(backtest_output.positions) if backtest_output.positions else 0,
                )
            else:
                _logger.info("Running performance attribution...")
                try:
                    attribution_config = cls._build_attribution_config(config)
                except PipelineError as exc:
                    # guard-2 (codex P1 on #370): sleeve-coverage
                    # failures are FATAL — the csi800 campaign contract
                    # forbids bare performance numbers without the
                    # sleeve report, so the industry-taxonomy soft-skip
                    # below must not swallow them.
                    if cls._attribution_failure_is_fatal(config):
                        raise
                    # ``_build_attribution_config`` re-raises
                    # :class:`IndustryTaxonomyLoadError` as
                    # :class:`PipelineError`. The previous implementation
                    # let that bubble out of ``run``, killing the entire
                    # pipeline — including the report-write step — even
                    # though the backtest had already finished
                    # successfully. Treat a taxonomy failure the same as
                    # a degenerate-input PerformanceAttributionError:
                    # skip + WARN, so the run still produces a usable
                    # report (with an explicit ``skipped_reason`` in the
                    # attribution block).
                    attribution_result = None
                    attribution_skipped_reason = (
                        f"taxonomy_load_failed: {exc}"
                    )
                    _logger.warning(
                        "Performance attribution skipped — industry taxonomy "
                        "load failed: %s. Backtest and risk_analysis remain "
                        "valid; only the sector-attribution block is absent "
                        "from the report.",
                        exc,
                    )
                    attribution_config = None
                if attribution_config is None:
                    pass  # already handled above
                else:
                    (attribution_result,
                     attribution_skipped_reason) = cls._run_attribution_engine(
                        config=config,
                        attribution_config=attribution_config,
                        backtest_output=backtest_output,
                        predictions=model_result.predictions,
                        pit_provider=pit_provider,
                    )
                    # guard-2 (codex P1 on #370 r5): pipeline runs persist
                    # the per-sleeve turnover too — schema parity with the
                    # walk-forward fold report.
                    if (attribution_result is not None
                            and config.attribution_sleeve_grouping):
                        sleeve_turnover_block = sleeve_turnover(
                            backtest_output.positions,
                            attribution_config.industry_map_override or {},
                        )

        # Step 8: Write report
        report_path = str(output_dir / "pipeline_report.json")
        try:
            cls._write_report(
                report_path, config, feature_result, model_result,
                signal_result, backtest_output, factor_result, attribution_result,
                attribution_skipped_reason=attribution_skipped_reason,
                factor_skipped_reason=factor_skipped_reason,
                git_provenance=git_provenance,  # captured at run start, not write time
                sleeve_turnover=sleeve_turnover_block,
                positions_sha256=positions_sha256,
            )
            _logger.info("  Report: %s", report_path)
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "Report write failed: %s: %s. Backtest results and "
                "positions are already persisted; report-only step skipped.",
                type(exc).__name__, exc,
            )

        # Step 9: Print summary
        cls._print_summary(backtest_output)

        # Step 10: Generate charts
        _logger.info("Generating performance charts...")
        charts_dir = str(output_dir / "charts")
        try:
            ResultVisualizer.generate(
                return_series=backtest_output.return_series,
                config=VisualizerConfig(output_dir=charts_dir),
            )
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "Chart generation skipped after successful report write: "
                "%s: %s.",
                type(exc).__name__, exc,
            )

        try:
            artifact_paths = write_pipeline_result_artifacts(
                output_dir,
                config=config,
                backtest_output=backtest_output,
                predictions=model_result.predictions,
                started_at=started_at,
                report_path=report_path,
                model_artifact_path=model_result.model_artifact_path,
                status="completed",
                git_provenance=git_provenance,  # same run-start capture as the report
            )
            _logger.info(
                "  Result artifacts: %s",
                ", ".join(sorted(Path(path).name for path in artifact_paths.values())),
            )
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "Structured result artifact generation skipped after "
                "successful report write: %s: %s.",
                type(exc).__name__, exc,
            )

        cls._append_catalog_entry(
            output_dir, config, report_path, backtest_output,
            signal_result, started_at,
        )

        return PipelineResult(
            feature_result=feature_result,
            model_result=model_result,
            signal_analysis=signal_result,
            backtest_output=backtest_output,
            factor_analysis=factor_result,
            attribution=attribution_result,
            report_path=report_path,
        )

    @staticmethod
    def _append_catalog_entry(
        output_dir: Path,
        config: PipelineConfig,
        report_path: str,
        backtest_output: Any,
        signal_result: Any,
        started_at: str,
        *,
        status: str = "ok",
    ) -> None:
        """Append a run-catalog record for a completed pipeline run."""
        try:
            fingerprint = sha256_canonical(asdict(config), length=16)

            record = build_catalog_record(
                engine="pipeline",
                status=status,
                started_at=started_at,
                config_fingerprint=fingerprint,
                config_summary={
                    "instruments": config.instruments,
                    "feature_handler": config.feature_handler,
                    "label_horizon_days": config.label_horizon_days,
                    "delisted_registry_path": config.delisted_registry_path or None,
                    "model_type": config.model_type,
                    "topk": config.topk,
                },
                headline_metrics={
                    "mean_ic_1d": (
                        signal_result.ic_summary.get(1, {}).get("mean_ic")
                        if signal_result else None
                    ),
                    "annualized_return": (
                        backtest_output.risk_analysis.get(
                            "excess_return_with_cost", {}
                        ).get("annualized_return")
                    ),
                },
                report_path=report_path,
                output_dir=str(output_dir),
            )
            append_run_record(record)
        except Exception:  # noqa: BLE001 — catalog is best-effort
            _logger.debug("Run catalog append skipped.", exc_info=True)

    @staticmethod
    def _make_run_dir(root_dir: Path, config: PipelineConfig) -> Path:
        """Return ``root_dir / runs / {timestamp}_{uniq}_{fingerprint}``.

        The fingerprint hashes the config dict so identical re-runs produce a
        stable suffix; the timestamp prefix (microsecond resolution) plus an
        8-hex random tag guarantees uniqueness under rapid-fire runs.
        Callers must create the directory with ``exist_ok=False`` so an
        unexpected collision surfaces as an error rather than silently
        overwriting a prior run's artifacts.
        """
        import uuid

        # The previous tail used ``perf_counter_ns() % 1_000_000`` as a
        # "6-digit ns jitter" — but ``perf_counter_ns`` is a monotonic CPU
        # counter, not wall-clock nanoseconds, so the value modulo 1e6 had
        # no clean semantic relationship with the microsecond timestamp it
        # was concatenated to. Worse, on coarse OS clocks (Windows in
        # particular) two near-simultaneous calls could land on the same
        # microsecond bucket *and* the same perf-counter modulus, producing
        # a directory collision after Path raises. ``uuid4().hex[:8]`` is
        # 32 bits of randomness — more than enough for this scope, and
        # the semantics are unambiguous (a tag, not a time).
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        uniq = uuid.uuid4().hex[:8]
        fingerprint = sha256_canonical(asdict(config), length=12)
        return root_dir / "runs" / f"{timestamp}_{uniq}_{fingerprint}"

    @classmethod
    def _persist_positions(
        cls,
        output_dir: Path,
        positions: Mapping[str, Mapping[str, float]],
    ) -> tuple[Path, str]:
        """Persist ``positions.json`` and return ``(path, sha256)`` of
        the PERSISTED bytes — "stamp what was written": the digest is
        computed by re-reading the file after the write, so it binds the
        bytes on disk, not an in-memory serialization that could diverge
        (attestation, 2026-07-17-csi800-cadence-campaign DP-5; same-name
        field as the walk-forward fold report per the two-engine schema
        symmetry rule in AGENTS.md).

        No ``default=str`` fallback for the contract types: positions is
        documented as ``{date_str: {instrument: float}}`` per
        CanonicalBacktestOutput, so plain JSON should serialise without
        coercion — falling back to ``str(...)`` would silently turn an
        unexpected type (numpy.float64 leaking through, a pandas
        Timestamp date key, a non-string instrument id, …) into a
        stringified value downstream consumers would have to
        special-case. NaN-safe via ``_sanitize_for_json`` +
        ``allow_nan=False``: the same convention the per-fold /
        aggregate reports use.
        """
        positions_path = output_dir / "positions.json"
        sanitised_positions = _sanitize_for_json(dict(positions))
        with open(positions_path, "w", encoding="utf-8") as f:
            json.dump(sanitised_positions, f, indent=2, allow_nan=False)
        return positions_path, hashlib.sha256(
            positions_path.read_bytes()).hexdigest()

    @staticmethod
    def _write_report(
        path: str,
        config: PipelineConfig,
        feature_result: FeatureDatasetResult,
        model_result: ModelTrainResult,
        signal_result: SignalAnalysisResult,
        backtest_output: CanonicalBacktestOutput,
        factor_result: FactorAnalysisResult | None = None,
        attribution_result: AttributionResult | None = None,
        attribution_skipped_reason: str | None = None,
        factor_skipped_reason: str | None = None,
        git_provenance: Mapping[str, Any] | None = None,
        sleeve_turnover: Mapping[str, Mapping[str, float]] | None = None,
        positions_sha256: str | None = None,
    ) -> None:
        # Two engines, one schema: pipeline_report.json and walk_forward_report.json
        # carry the SAME top-level git_commit / git_dirty provenance fields, so the
        # run-comparison pre-registration gate reads one provenance path regardless of
        # engine. Injectable (mirroring build_aggregate_report) — the impurity lives at
        # the run() call site; a caller that omits it gets null fields, deterministic.
        gp = git_provenance or {}
        report: dict[str, Any] = {
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
            "git_commit": gp.get("commit"),
            "git_dirty": gp.get("dirty"),
            "metric_status": backtest_output.metric_status,
            "official_backtest_path": backtest_output.official_backtest_path,
            "config": {
                "instruments": config.instruments,
                "feature_handler": config.feature_handler,
                # Two engines, one schema: walk_forward_report.json carries this
                # via asdict(config); a non-default single-fold run must be
                # distinguishable from H=1 to report consumers (codex P2 #318).
                "label_horizon_days": config.label_horizon_days,
                # PIT-routing governance status (codex P2 #320 r3): non-empty =
                # attribution used the PIT post-delist mask; null = legacy WARN
                # path. Walk-forward reports carry this via asdict(config).
                "delisted_registry_path": config.delisted_registry_path or None,
                "train_period": f"{config.train_start} ~ {config.train_end}",
                "valid_period": f"{config.valid_start} ~ {config.valid_end}",
                "test_period": f"{config.test_start} ~ {config.test_end}",
                "model_type": config.model_type,
                "benchmark_code": config.benchmark_code,
                "topk": config.topk,
                "n_drop": config.n_drop,
                "industry_taxonomy_id": config.industry_taxonomy_id or None,
                # CSI800 guard-2 provenance (codex P1 on #372 r2): the
                # report must declare which guards/calibration the run
                # used — walk-forward carries these via asdict(config);
                # the pipeline projection mirrors them explicitly.
                "attribution_sleeve_grouping": config.attribution_sleeve_grouping,
                "risk_constraints_enabled": config.risk_constraints_enabled,
                "risk_constraints_calibration": config.risk_constraints_calibration,
            },
            "dataset": {
                "train_shape": list(feature_result.train_shape),
                "valid_shape": list(feature_result.valid_shape),
                "test_shape": list(feature_result.test_shape),
            },
            "model": {
                "prediction_shape": list(model_result.prediction_shape),
                "model_artifact_path": model_result.model_artifact_path,
            },
            "signal_analysis": Pipeline._signal_analysis_section(signal_result),
            "backtest": {
                "report": backtest_output.report,
                "provenance": dict(backtest_output.provenance),
            },
            "risk_analysis": dict(backtest_output.risk_analysis),
        }

        if factor_result is not None:
            report["factor_analysis"] = {
                "status": "ok",
                "skipped_reason": None,
                "total_factors": factor_result.total_factors,
                "top_factors": [
                    {
                        "name": s.factor_name, "mean_ic": s.mean_ic,
                        "std_ic": s.std_ic, "ir": s.ir,
                        "ic_positive_ratio": s.ic_positive_ratio,
                    }
                    for s in factor_result.factor_ic_stats[:20]
                ],
                "ic_decay": dict(factor_result.ic_decay),
            }
        else:
            report["factor_analysis"] = {
                "status": "skipped",
                "skipped_reason": factor_skipped_reason,
            }

        # CSI800 guard-2 (codex P1 on #370 r5): pipeline reports carry
        # the per-sleeve turnover too — the two runtime report schemas
        # stay parallel (explicit null when sleeve grouping is off, same
        # convention as the walk-forward fold report).
        report["sleeve_turnover"] = (
            {k: dict(v) for k, v in sleeve_turnover.items()}
            if sleeve_turnover is not None else None
        )
        # Attestation digest of the persisted positions.json bytes —
        # same-name field as the walk-forward fold report (two-engine
        # schema symmetry; explicit None when no positions persisted).
        report["positions_sha256"] = positions_sha256
        report["attribution"] = Pipeline._attribution_section(
            attribution_result, attribution_skipped_reason,
        )

        # Standard JSON does not allow NaN/Inf — Python's default
        # ``json.dump`` happily emits the literal token ``NaN`` which
        # downstream parsers (browsers, ``jq``, strict libraries) reject.
        # SignalAnalyzer and (now) FactorAnalyzer use NaN to mark
        # *undefined* IR (zero or single-day std). Replace those NaNs
        # with JSON-standard ``null`` recursively before writing, and set
        # ``allow_nan=False`` so any remaining NaN trips a loud error
        # instead of silently producing non-standard JSON.
        sanitized = _sanitize_for_json(report)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                sanitized, f, indent=2, ensure_ascii=False,
                default=str, allow_nan=False,
            )

    @staticmethod
    def _signal_analysis_section(signal_result: SignalAnalysisResult) -> dict[str, Any]:
        """Build the ``signal_analysis`` block of the JSON report.

        Why this is its own method
        --------------------------
        ``ic_summary`` is keyed by int forward-period in memory.
        Without explicit coercion, ``json.dump`` silently stringifies
        the keys on write — so the on-disk JSON has ``"1"`` / ``"5"``
        but the in-memory dict has ``1`` / ``5``, and a single test
        that exercises both paths would have to special-case the
        round-trip mismatch. Coercing here aligns:

        - the in-memory dict the helper returns,
        - the bytes ``json.dump`` writes,
        - the dict ``json.load`` parses back,

        all to ``str`` keys.

        Mirrors the explicit ``str(period)`` coercion already done in
        ``walk_forward._build_fold_report`` so the two writers stay
        consistent.
        """
        return {
            "ic_summary": {
                str(period): dict(stats)
                for period, stats in signal_result.ic_summary.items()
            },
            "ic_decay": list(signal_result.ic_decay),
            "turnover": dict(signal_result.turnover_stats),
        }

    @staticmethod
    def _attribution_failure_is_fatal(config: PipelineConfig) -> bool:
        """Whether an attribution-config load failure must ABORT the run.

        The industry-taxonomy path soft-skips (backtest metrics stay
        valid, only the sector block is absent). With sleeve grouping
        enabled the calculus flips (guard-2, codex P1 on #370): the
        v2-csi800-expansion-guards contract forbids emitting bare
        performance numbers without the sleeve report, so a sleeve
        resolution failure (stale/missing membership coverage) fails
        the whole run instead of degrading it."""
        return config.attribution_sleeve_grouping

    @classmethod
    def _run_attribution_engine(
        cls,
        *,
        config: PipelineConfig,
        attribution_config: AttributionConfig,
        backtest_output: CanonicalBacktestOutput,
        predictions: Any,
        pit_provider: Any | None,
    ) -> tuple[AttributionResult | None, str | None]:
        """Invoke the attribution engine under the guard-2 failure policy.

        Extracted from ``run()`` so the policy is directly unit-testable
        (codex P1 on #370 r5): with ``attribution_sleeve_grouping`` on,
        EVERY engine failure — ``PerformanceAttributionError`` and the
        broad catch-all alike — raises ``PipelineError`` instead of
        downgrading to a skipped block; without it, both downgrade to
        "skip + WARN" exactly as before."""
        try:
            result = PerformanceAttribution.analyze(
                return_series=backtest_output.return_series,
                predictions=predictions,
                config=attribution_config,
                positions=backtest_output.positions,
                pit_provider=pit_provider,
            )
            PerformanceAttribution.print_report(result)
            return result, None
        except PerformanceAttributionError as exc:
            if cls._attribution_failure_is_fatal(config):
                raise PipelineError(
                    "attribution_sleeve_grouping=True but the attribution "
                    f"engine failed ({exc}) — the mandatory sleeve report "
                    "cannot be built; refusing to emit bare csi800 metrics "
                    "(v2-csi800-expansion-guards)."
                ) from exc
            # Degenerate inputs (e.g. all-non-positive predictions,
            # all-zero position weights) raise from the attribution engine
            # by design. Downgrade to "skipped with loud WARNING" so the
            # run still finishes (backtest + report are already valid).
            _logger.warning(
                "Performance attribution skipped — engine raised %s: %s. "
                "Backtest and risk_analysis remain valid; only the "
                "sector-attribution block is absent from the report.",
                type(exc).__name__, exc,
            )
            return None, f"engine_error: {type(exc).__name__}: {exc}"
        except Exception as exc:  # noqa: BLE001
            # guard-2 (codex P1 on #370 r5): the fatal predicate covers
            # the catch-all too — a bare ValueError inside the engine must
            # not smuggle a sleeve-less csi800 report past the contract.
            if cls._attribution_failure_is_fatal(config):
                raise PipelineError(
                    "attribution_sleeve_grouping=True but attribution "
                    f"failed unexpectedly ({type(exc).__name__}: {exc}) — "
                    "the mandatory sleeve report cannot be built; refusing "
                    "to emit bare csi800 metrics "
                    "(v2-csi800-expansion-guards)."
                ) from exc
            # Catch-all for non-PerformanceAttributionError failures inside
            # the attribution engine — bare ValueError from float(v),
            # RuntimeError/KeyError from qlib D.features(), pandas groupby
            # ValueError, etc. Same downgrade pattern as FactorAnalyzer and
            # ResultVisualizer: skip + WARN, preserve backtest.
            _logger.warning(
                "Performance attribution skipped — unexpected error in "
                "engine: %s: %s. Backtest and risk_analysis remain valid.",
                type(exc).__name__, exc,
            )
            return None, f"unexpected_error: {type(exc).__name__}: {exc}"

    @staticmethod
    def _build_attribution_config(config: PipelineConfig) -> AttributionConfig:
        """Build attribution config, optionally with a validated taxonomy map.

        Delegates the load + contract validation to
        :func:`resolve_industry_taxonomy` so the same logic is shared
        with ``WalkForwardEngine``. Pipeline-specific behaviour stays
        here: catching the shared :class:`IndustryTaxonomyLoadError`
        and re-raising as :class:`PipelineError` (so the rest of
        Pipeline can ``except PipelineError`` cleanly), and logging
        contract warnings via the pipeline's own logger so they land
        in the run's log file.
        """
        # Type widened to ``Any`` so the ``**base`` splat below matches
        # AttributionConfig's Mapping-valued fields (industry_map_override,
        # etc.); the literal values are still strings.
        base: dict[str, Any] = {
            "start_date": config.test_start,
            "end_date": config.test_end,
        }
        if config.attribution_sleeve_grouping:
            # CSI800 guard-2: membership sleeves as the Brinson grouping.
            # Coverage violations (as_of past a sleeve's snapshot bound)
            # fail LOUD — never a silently-stale sleeve report.
            try:
                sleeves = resolve_sleeve_map(
                    config.provider_uri, config.test_start)
            except SleeveResolutionError as exc:
                raise PipelineError(str(exc)) from exc
            _logger.info(
                "Attribution sleeve grouping: csi300=%d csi500=%d "
                "as_of=%s coverage_end=%s",
                sleeves.n_csi300, sleeves.n_csi500,
                sleeves.as_of, sleeves.coverage_end,
            )
            return AttributionConfig(
                **base,
                industry_map_override=sleeves.sleeve_map,
                industry_taxonomy_id=sleeves.taxonomy_id,
            )
        if not config.industry_artifact_path:
            return AttributionConfig(**base)

        # ``purpose=PURPOSE_ATTRIBUTION`` is the explicit "post-hoc
        # analysis, not training" declaration — same convention as
        # ``WalkForwardEngine._run_attribution_for_fold``. The shared
        # loader uses ``purpose`` to decide the temporal-leakage policy
        # so callers cannot accidentally mix it up by toggling
        # ``reference_date``.
        try:
            resolution = resolve_industry_taxonomy(
                artifact_path=str(config.industry_artifact_path),
                manifest_path=str(config.industry_manifest_path),
                taxonomy_id=str(config.industry_taxonomy_id).strip(),
                temporal_mode=config.industry_temporal_mode,
                purpose=PURPOSE_ATTRIBUTION,
            )
        except IndustryTaxonomyLoadError as exc:
            raise PipelineError(str(exc)) from exc

        for warning in resolution.warnings:
            _logger.warning(
                "Industry taxonomy contract warning for attribution: %s",
                warning,
            )

        return AttributionConfig(
            **base,
            industry_map_override=resolution.industry_map,
            industry_taxonomy_id=resolution.taxonomy_id,
        )

    @staticmethod
    def _attribution_section(
        attribution_result: AttributionResult | None,
        skipped_reason: str | None,
    ) -> dict[str, Any]:
        """Build the ``attribution`` block of the JSON report.

        Always emits a dict with a ``status`` field. Previously a missing
        ``attribution_result`` silently dropped the entire ``attribution``
        block, so the JSON consumer could not tell:

        - ``run_attribution=False`` (intentional disable)
        - "no positions returned by backtest" (degraded run)
        - "attribution engine refused degenerate input" (degraded run)
        - "attribution succeeded" (normal)

        all four collapsed to "no attribution key in report" or had only
        the third surfaced via WARNING logs. Now every case lands in a
        machine-readable ``status`` + ``skipped_reason`` pair so
        dashboards can surface degraded runs instead of treating them
        as missing data.
        """
        if attribution_result is not None:
            block = Pipeline._attribution_to_report_dict(attribution_result)
            block["status"] = "ok"
            block["skipped_reason"] = None
            return block
        return {
            "status": "skipped",
            "skipped_reason": skipped_reason or "unknown_reason",
        }

    @staticmethod
    def _attribution_to_report_dict(attribution_result: AttributionResult) -> dict[str, Any]:
        """Serialize an :class:`AttributionResult` to the JSON-report dict.

        Extracted so the JSON contract (which methodology fields land in
        ``pipeline_report.json``) is unit-testable without a full E2E
        run. The methodology / provenance fields below were surfaced in
        ``PerformanceAttribution.print_report`` log lines but were
        previously missing from the JSON — JSON consumers (dashboards,
        downstream scripts) had no way to tell whether sector buckets were
        boards vs. industries, whether the benchmark was equal-weighted
        vs. cap-weighted, or whether the Brinson sum reconciles with the
        compounded excess return. Persist them so the caveats travel
        with the data.
        """
        return {
            "total_portfolio_return": attribution_result.total_portfolio_return,
            "total_benchmark_return": attribution_result.total_benchmark_return,
            "total_excess_return": attribution_result.total_excess_return,
            "allocation_effect": attribution_result.total_allocation_effect,
            "selection_effect": attribution_result.total_selection_effect,
            "interaction_effect": attribution_result.total_interaction_effect,
            "attribution_method": attribution_result.attribution_method,
            "sector_taxonomy": attribution_result.sector_taxonomy,
            "bench_weight_method": attribution_result.bench_weight_method,
            # WHERE the weights came from (codex P2 #332): without this,
            # persisted reports conflate automatic PIT $circ_mv weights with
            # caller-supplied ones under the same market_cap label.
            "bench_weight_source": attribution_result.bench_weight_source,
            "sector_effects_sum": attribution_result.sector_effects_sum,
            "reconciliation_residual": attribution_result.reconciliation_residual,
            "sector_attribution": [
                {
                    "sector": s.sector,
                    "portfolio_weight": s.portfolio_weight,
                    "benchmark_weight": s.benchmark_weight,
                    "allocation_effect": s.allocation_effect,
                    "selection_effect": s.selection_effect,
                    "total_effect": s.total_effect,
                }
                for s in attribution_result.sector_attribution
            ],
            "monthly_returns": [
                {
                    "month": f"{m.year}-{m.month:02d}",
                    "portfolio": m.portfolio_return,
                    "benchmark": m.benchmark_return,
                    "excess": m.excess_return,
                }
                for m in attribution_result.monthly_returns
            ],
        }

    @staticmethod
    def _print_summary(output: CanonicalBacktestOutput) -> None:
        log = _logger.info
        log("=" * 60)
        log("  V2 Pipeline Results")
        log("=" * 60)
        log(f"  Metric Status: {output.metric_status}")
        log(f"  Backtest Path: {output.official_backtest_path}")
        log(f"  Trading Days:  {output.report.get('total_days', 'N/A')}")
        log(f"  Period:        {output.report.get('start_date')} ~ {output.report.get('end_date')}")

        risk = output.risk_analysis
        for label in ("excess_return_without_cost", "excess_return_with_cost"):
            section = risk.get(label, {})
            if section:
                log(f"  [{label}]")
                for key in ("annualized_return", "information_ratio", "max_drawdown"):
                    val = section.get(key, "N/A")
                    if isinstance(val, float):
                        log(f"    {key}: {val:.4f}")
                    else:
                        log(f"    {key}: {val}")
        log("=" * 60)
