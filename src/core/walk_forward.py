"""Walk-forward (rolling) backtest engine.

Simulates realistic model deployment by repeatedly:
1. Training on [train_start, train_end]
2. Validating on [valid_start, valid_end]
3. Predicting + backtesting on [test_start, test_end]
4. Rolling all windows forward by `step_months`

This produces a series of non-overlapping out-of-sample periods whose
results can be stitched together for a full-period performance view.

Boundaries
----------
- Requires canonical qlib init.
- Reuses FeatureDatasetBuilder, ModelTrainer, BacktestRunner, SignalAnalyzer.
"""

from __future__ import annotations

import hashlib
import json
import pickle
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from src.contracts.taxonomy_data_contract import TAXONOMY_MODE_STATIC
from src.core._json_utils import _sanitize_for_json
from src.core.attribution_industry_loader import (
    PURPOSE_ATTRIBUTION,
    IndustryTaxonomyLoadError,
    assert_industry_config_complete_or_empty,
    resolve_industry_taxonomy,
)
from src.core.backtest_runner import BacktestRunner
from src.core.canonical_backtest_contract import (
    ADJUST_MODE_PRE,
    EXECUTION_PRICE_CLOSE,
    CanonicalAccountConfig,
    CanonicalBacktestContractError,
    CanonicalBacktestInput,
    CanonicalBacktestOutput,
    CanonicalExchangeConfig,
    CanonicalExchangeCostModel,
    SUPPORTED_ADJUST_MODES,
)
from src.core.logger import get_logger
from src.core.model_config_projection import build_model_train_config
from src.core.model_trainer import ModelTrainer, ModelTrainResult
from src.core.qlib_runtime import is_canonical_qlib_initialized
from src.core.performance_attribution import (
    AttributionConfig,
    AttributionResult,
    PerformanceAttribution,
    PerformanceAttributionError,
)
from src.core.signal_analyzer import (
    SignalAnalysisConfig,
    SignalAnalysisResult,
    SignalAnalyzer,
)
from src.data.feature_dataset_builder import FeatureDatasetBuilder, FeatureDatasetConfig

_logger = get_logger(__name__)


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


@dataclass(frozen=True)
class WalkForwardFold:
    """Result for a single fold in the walk-forward process."""

    fold_index: int
    train_period: str
    valid_period: str
    test_period: str
    ic_1d: float
    ic_5d: float
    annualized_return: float
    max_drawdown: float
    information_ratio: float
    prediction_shape: tuple[int, ...]
    # Path to the per-fold JSON report written by ``_run_single_fold``.
    # Optional so legacy callers / mock-based tests that construct a
    # fold without persisting a report (e.g. the aggregate-NaN tests
    # below) keep working unchanged.
    report_path: str | None = None


@dataclass(frozen=True)
class WalkForwardResult:
    """Aggregated walk-forward results."""

    folds: Sequence[WalkForwardFold]
    aggregate_metrics: Mapping[str, float]
    num_folds: int
    # Path to the aggregate JSON report written by ``WalkForwardEngine.run``.
    # ``None`` when the engine ran without persisting one (e.g. legacy
    # callers patched only ``_run_single_fold`` and never reach the
    # aggregate-write step).
    report_path: str | None = None


class WalkForwardEngine:
    """Orchestrates rolling train/predict/backtest across time."""

    @classmethod
    def run(cls, config: WalkForwardConfig) -> WalkForwardResult:
        if not is_canonical_qlib_initialized():
            raise WalkForwardError(
                "Canonical qlib runtime must be initialized before walk-forward."
            )

        from dateutil.relativedelta import relativedelta
        from pathlib import Path
        import numpy as np

        output_dir = Path(config.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        started_at = datetime.now(tz=timezone.utc).isoformat()

        # Generate fold windows
        windows = cls._generate_windows(config)
        if not windows:
            raise WalkForwardError(
                "No valid fold windows could be generated with the given config. "
                "Check that overall period is long enough for train+valid+test windows."
            )

        folds: list[WalkForwardFold] = []
        # Chronological list of model pickle refs across folds so each
        # subsequent fold can pull the most recent ``ensemble_window - 1``
        # priors. Keep real fold index beside each path; failed folds are
        # skipped, so deriving fold index from list position corrupts
        # ensemble provenance after a gap.
        prior_model_paths: list[tuple[int, str]] = []
        _logger.info("Starting %d folds", len(windows))
        _logger.info("=" * 60)

        for i, (train_s, train_e, valid_s, valid_e, test_s, test_e) in enumerate(windows):
            _logger.info(
                "Fold %d/%d  Train: %s~%s | Valid: %s~%s | Test: %s~%s",
                i + 1, len(windows), train_s, train_e, valid_s, valid_e, test_s, test_e,
            )

            # Wrap the per-fold execution so a transient failure mid-run
            # (qlib data hiccup, a corrupted prior pickle in the ensemble,
            # an out-of-disk write, etc.) does not throw away every
            # already-completed fold. The previous implementation had no
            # try/except here: fold N's exception abort the whole run,
            # the aggregate JSON never wrote, and the operator had to
            # rerun everything from fold 0.
            #
            # We record a NaN-only placeholder ``WalkForwardFold`` so:
            #   * ``_compute_aggregate`` (already NaN-tolerant) excludes
            #     the failed fold from means but still surfaces the
            #     failure via ``valid_folds_*`` counts < total.
            #   * The aggregate report renders, with the failed fold's
            #     row showing NaN metrics — visibly degraded rather
            #     than silently absent.
            #   * ``prior_model_paths`` is *not* extended for the failed
            #     fold (no model pickle was produced), so subsequent
            #     folds' ensemble naturally omits this fold.
            try:
                fold = cls._run_single_fold(
                    config=config,
                    fold_index=i,
                    train_start=train_s, train_end=train_e,
                    valid_start=valid_s, valid_end=valid_e,
                    test_start=test_s, test_end=test_e,
                    output_dir=output_dir,
                    prior_model_paths=tuple(prior_model_paths),
                )
            except Exception as exc:  # noqa: BLE001
                _logger.error(
                    "Fold %d/%d FAILED with %s: %s. Recording NaN-only "
                    "placeholder fold and continuing; the aggregate "
                    "report will surface this via valid_folds_* counts "
                    "< num_folds. Subsequent folds' ensemble window "
                    "will skip this fold.",
                    i + 1, len(windows), type(exc).__name__, exc,
                )
                folds.append(WalkForwardFold(
                    fold_index=i,
                    train_period=f"{train_s} ~ {train_e}",
                    valid_period=f"{valid_s} ~ {valid_e}",
                    test_period=f"{test_s} ~ {test_e}",
                    ic_1d=float("nan"),
                    ic_5d=float("nan"),
                    annualized_return=float("nan"),
                    max_drawdown=float("nan"),
                    information_ratio=float("nan"),
                    prediction_shape=(0,),
                ))
                # Skip the prior-model-path append so the broken fold's
                # (likely missing or partial) pickle never re-enters
                # subsequent folds' ensembles.
                continue
            folds.append(fold)
            # Mirror the path the trainer wrote to in ``_run_single_fold``;
            # keep this construction in sync with the ``model_path`` line
            # there. The duplication is intentional — the engine doesn't
            # surface ``model_path`` on ``WalkForwardFold`` (it's an
            # internal artifact path, not a metric), so reconstructing
            # it here is the cheapest way to thread it forward.
            prior_model_paths.append((i, str(output_dir / f"model_fold{i}.pkl")))
            _logger.info(
                "  IC(1d)=%.4f | Return=%.2f%% | MaxDD=%.2f%%",
                fold.ic_1d, fold.annualized_return * 100, fold.max_drawdown * 100,
            )

        # Aggregate
        aggregate = cls._compute_aggregate(folds)

        _logger.info("=" * 60)
        _logger.info("AGGREGATE RESULTS")
        _logger.info("=" * 60)
        for key, val in aggregate.items():
            _logger.info("  %s: %.4f", key, val)
        _logger.info("=" * 60)

        # Persist the aggregate report alongside the per-fold reports so
        # downstream comparison tooling has a single index file with
        # config, fold→file mapping, and aggregate metrics. Without this,
        # comparing two walk-forward runs requires loading every per-fold
        # JSON manually and re-deriving aggregates — exactly the kind of
        # friction the aggregate file is meant to remove.
        aggregate_path = output_dir / "walk_forward_report.json"
        cls._write_aggregate_report(
            path=aggregate_path,
            config=config,
            folds=folds,
            aggregate_metrics=aggregate,
        )
        _logger.info("Aggregate report: %s", aggregate_path)

        # Best-effort run catalog: append one JSONL line so operators
        # can query historical runs without find + jq. Non-fatal on
        # failure — the per-run report is the authoritative artifact.
        try:
            from src.core.run_catalog import append_run_record, build_record as build_catalog_record
            import math
            from dataclasses import asdict
            has_any_nan = any(
                math.isnan(f.ic_1d) or math.isnan(f.ic_5d)
                for f in folds
            )
            config_json = json.dumps(asdict(config), sort_keys=True, default=str)
            fingerprint = hashlib.sha256(config_json.encode()).hexdigest()[:12]
            record = build_catalog_record(
                engine="walk_forward",
                status="partial" if has_any_nan else "ok",
                started_at=started_at,
                config_fingerprint=fingerprint,
                config_summary={
                    "instruments": config.instruments,
                    "feature_handler": config.feature_handler,
                    "model_type": config.model_type,
                    "ensemble_window": config.ensemble_window,
                    "topk": config.topk,
                    "overall_start": config.overall_start,
                    "overall_end": config.overall_end,
                },
                headline_metrics={
                    "num_folds": aggregate.get("num_folds"),
                    "mean_ic_1d": aggregate.get("mean_ic_1d"),
                    "annualized_return": aggregate.get("mean_annualized_return"),
                    "worst_drawdown": aggregate.get("worst_drawdown"),
                    "mean_information_ratio": aggregate.get("mean_information_ratio"),
                },
                report_path=str(aggregate_path),
                output_dir=str(output_dir),
            )
            append_run_record(record)
        except Exception:  # noqa: BLE001
            _logger.debug("Run catalog append skipped.", exc_info=True)

        return WalkForwardResult(
            folds=folds,
            aggregate_metrics=aggregate,
            num_folds=len(folds),
            report_path=str(aggregate_path),
        )

    @classmethod
    def _build_aggregate_report(
        cls,
        *,
        config: WalkForwardConfig,
        folds: list[WalkForwardFold],
        aggregate_metrics: Mapping[str, float],
    ) -> dict[str, Any]:
        """Build the aggregate JSON report dict.

        Schema:

        - ``config``: full ``WalkForwardConfig`` snapshot so the run is
          reproducible from the report alone (no peeking at ``config.yaml``).
        - ``folds``: list of compact per-fold summaries (``fold_index``,
          test period, headline metrics, path to the per-fold report).
          Mirrors what dashboards typically render in a fold-level table.
        - ``aggregate_metrics``: cross-fold aggregates from
          ``_compute_aggregate``.
        - ``num_folds``, ``generated_at``: provenance.
        """
        return {
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
            "config": asdict(config),
            "folds": [
                {
                    "fold_index": f.fold_index,
                    "train_period": f.train_period,
                    "valid_period": f.valid_period,
                    "test_period": f.test_period,
                    "ic_1d": f.ic_1d,
                    "ic_5d": f.ic_5d,
                    "annualized_return": f.annualized_return,
                    "max_drawdown": f.max_drawdown,
                    "information_ratio": f.information_ratio,
                    "prediction_shape": list(f.prediction_shape),
                    "report_path": f.report_path,
                }
                for f in folds
            ],
            "aggregate_metrics": dict(aggregate_metrics),
            "num_folds": len(folds),
        }

    @classmethod
    def _write_aggregate_report(
        cls,
        *,
        path: Path,
        config: WalkForwardConfig,
        folds: list[WalkForwardFold],
        aggregate_metrics: Mapping[str, float],
    ) -> None:
        """Build and persist the aggregate JSON report.

        Same NaN handling as ``_write_fold_report`` — the aggregate
        metrics include ``mean_ic_1d`` etc. which are intentionally NaN
        when no fold produced a valid IC, and ``json.dump(..., allow_nan=False)``
        on a sanitised payload turns those into ``null`` rather than the
        non-standard ``NaN`` token.
        """
        report = cls._build_aggregate_report(
            config=config, folds=folds, aggregate_metrics=aggregate_metrics,
        )
        sanitised = _sanitize_for_json(report)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                sanitised, f, indent=2, ensure_ascii=False,
                default=str, allow_nan=False,
            )

    @classmethod
    def _generate_windows(cls, config: WalkForwardConfig) -> list[tuple[str, ...]]:
        """Generate (train_s, train_e, valid_s, valid_e, test_s, test_e) tuples."""
        from dateutil.relativedelta import relativedelta

        start = date.fromisoformat(config.overall_start)
        end = date.fromisoformat(config.overall_end)

        windows = []
        cursor = start

        while True:
            train_s = cursor
            train_e = train_s + relativedelta(months=config.train_months) - relativedelta(days=1)
            valid_s = train_e + relativedelta(days=1)
            valid_e = valid_s + relativedelta(months=config.valid_months) - relativedelta(days=1)
            test_s = valid_e + relativedelta(days=1)
            test_e = test_s + relativedelta(months=config.test_months) - relativedelta(days=1)

            if test_e > end:
                # Try fitting partial last fold up to overall_end
                test_e = end
                if test_s >= test_e:
                    break
                # Reject folds with test period too short to be meaningful
                # +1 because both start and end dates are inclusive
                if (test_e - test_s).days + 1 < 10:
                    break

            windows.append((
                train_s.isoformat(), train_e.isoformat(),
                valid_s.isoformat(), valid_e.isoformat(),
                test_s.isoformat(), test_e.isoformat(),
            ))

            cursor = cursor + relativedelta(months=config.step_months)

            # Safety: if test_e already reached overall_end, stop
            if test_e >= end:
                break

        return windows

    @classmethod
    def _run_single_fold(
        cls,
        config: WalkForwardConfig,
        fold_index: int,
        train_start: str, train_end: str,
        valid_start: str, valid_end: str,
        test_start: str, test_end: str,
        output_dir: Any,
        prior_model_paths: Sequence[Any] = (),
    ) -> WalkForwardFold:
        """Execute a single train→predict→analyze→backtest fold.

        ``prior_model_paths`` carries the pickle paths of every prior
        fold's model in chronological order. When
        ``config.ensemble_window > 1`` the function loads up to the
        most-recent ``ensemble_window - 1`` of those, has each one
        predict the *current* fold's test segment, and averages the
        per-instrument scores across all models (current + priors).
        Early folds with fewer priors than the window asks for
        gracefully degrade to "however many are available".
        """
        # Build features
        feature_result = FeatureDatasetBuilder.build(FeatureDatasetConfig(
            instruments=config.instruments,
            feature_handler=config.feature_handler,
            train_start=train_start,
            train_end=train_end,
            valid_start=valid_start,
            valid_end=valid_end,
            test_start=test_start,
            test_end=test_end,
        ))

        # Train model
        model_path = str(output_dir / f"model_fold{fold_index}.pkl")
        model_result = ModelTrainer.train_and_predict(
            config=build_model_train_config(config),
            dataset=feature_result.dataset,
            model_artifact_path=model_path,
        )

        # Optionally average current fold's predictions with prior
        # fold models' predictions on this dataset. Returns the
        # possibly-replaced predictions plus an ``ensemble_meta`` dict
        # that lands on the fold report so the operator can audit
        # which folds contributed.
        predictions, ensemble_meta = cls._maybe_apply_ensemble(
            current_predictions=model_result.predictions,
            current_dataset=feature_result.dataset,
            prior_model_paths=prior_model_paths,
            ensemble_window=config.ensemble_window,
            current_fold_index=fold_index,
        )
        prediction_artifact_path = output_dir / f"fold_{fold_index:02d}_predictions.pkl"
        prediction_artifact_sha = cls._write_prediction_artifact(
            prediction_artifact_path, predictions,
        )
        ensemble_meta = {
            **ensemble_meta,
            "current_model_ref": model_path,
            "prediction_artifact_path": str(prediction_artifact_path),
            "prediction_artifact_sha256": prediction_artifact_sha,
        }

        # Signal analysis
        signal_result = SignalAnalyzer.analyze(
            predictions=predictions,
            config=SignalAnalysisConfig(forward_periods=(1, 5), topk=config.topk),
        )
        # Structural: both periods we asked for must come back. Missing keys
        # signal an analyzer-layer bug, not a bad model — fall-through to
        # ``0.0`` here used to mask analyzer regressions as "this fold had
        # no IC".  Values themselves may be NaN (insufficient data) and
        # propagate through to the fold result honestly.
        missing = [p for p in (1, 5) if p not in signal_result.ic_summary]
        if missing:
            raise WalkForwardError(
                f"Fold {fold_index}: SignalAnalyzer did not return IC for "
                f"forward period(s) {missing}. Keys present: "
                f"{sorted(signal_result.ic_summary.keys())}."
            )
        ic_1d = float(signal_result.ic_summary[1]["mean_ic"])
        ic_5d = float(signal_result.ic_summary[5]["mean_ic"])

        # Backtest
        backtest_request = CanonicalBacktestInput(
            predictions_ref=str(prediction_artifact_path),
            evaluation_start=test_start,
            evaluation_end=test_end,
            account_config=CanonicalAccountConfig(init_cash=config.init_cash),
            exchange_config=CanonicalExchangeConfig(
                freq="day",
                execution_price_kind=config.execution_price_kind,
                cost_model=CanonicalExchangeCostModel(
                    commission_rate=config.commission_rate,
                    stamp_tax_bps=config.stamp_tax_bps,
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
            predictions=predictions,
            topk=config.topk,
            n_drop=config.n_drop,
        )

        ann_ret, max_dd, ir = cls._extract_cost_metrics(
            backtest_output.risk_analysis, fold_index,
        )

        # Persist a per-fold report and the positions artifact. Previously
        # the only fold-level artefact written was the model pickle, so a
        # walk-forward run produced N pkl files with no IC / return /
        # backtest detail accessible after the fact. Dashboards or diff
        # tools cannot compare two runs from the in-memory ``WalkForwardFold``
        # alone — they need the file on disk.
        positions_path: Path | None = None
        if backtest_output.positions:
            positions_path = output_dir / f"fold_{fold_index:02d}_positions.json"
            cls._write_positions(positions_path, backtest_output.positions)

        # Per-fold performance attribution. Runs after backtest so the
        # attribution engine sees the real positions / return series.
        # Same skip-but-disclose pattern as ``Pipeline.run``: degenerate
        # inputs (e.g. all-zero positions, all-non-positive predictions)
        # raise ``PerformanceAttributionError`` from the engine; we
        # downgrade to "skip + WARN + status in fold report" so a single
        # bad fold does not abort the entire walk-forward run.
        attribution_result, attribution_skipped_reason = (
            cls._run_attribution_for_fold(
                config=config,
                fold_index=fold_index,
                test_start=test_start, test_end=test_end,
                predictions=predictions,
                backtest_output=backtest_output,
            )
        )

        report_path = output_dir / f"fold_{fold_index:02d}_report.json"
        cls._write_fold_report(
            report_path=report_path,
            fold_index=fold_index,
            train_start=train_start, train_end=train_end,
            valid_start=valid_start, valid_end=valid_end,
            test_start=test_start, test_end=test_end,
            model_artifact_path=model_path,
            model_result=model_result,
            signal_result=signal_result,
            backtest_output=backtest_output,
            positions_path=positions_path,
            ic_1d=ic_1d, ic_5d=ic_5d,
            annualized_return=ann_ret, max_drawdown=max_dd,
            information_ratio=ir,
            attribution_result=attribution_result,
            attribution_skipped_reason=attribution_skipped_reason,
            ensemble_meta=ensemble_meta,
        )

        return WalkForwardFold(
            fold_index=fold_index,
            train_period=f"{train_start} ~ {train_end}",
            valid_period=f"{valid_start} ~ {valid_end}",
            test_period=f"{test_start} ~ {test_end}",
            ic_1d=ic_1d,
            ic_5d=ic_5d,
            annualized_return=ann_ret,
            max_drawdown=max_dd,
            information_ratio=ir,
            prediction_shape=model_result.prediction_shape,
            report_path=str(report_path),
        )

    @classmethod
    def _maybe_apply_ensemble(
        cls,
        *,
        current_predictions: Any,
        current_dataset: Any,
        prior_model_paths: Sequence[Any],
        ensemble_window: int,
        current_fold_index: int,
    ) -> tuple[Any, dict[str, Any]]:
        """Average current fold's predictions with up to ``N-1`` prior fold models.

        Returns ``(predictions, meta)``:

        - ``predictions``: a ``pd.Series`` aligned to ``current_predictions``'
          ``(datetime, instrument)`` index. When the window is ``1`` or no
          priors are available, this is exactly ``current_predictions``
          (same object — no copy).
        - ``meta``: a ``dict`` describing what actually happened, embedded
          in the per-fold report. Keys:

          * ``window`` — the configured ``ensemble_window``.
          * ``used`` — ``True`` iff ≥1 prior model contributed.
          * ``n_models`` — number of models averaged (current + priors).
          * ``contributing_folds`` — fold indices whose models were
            averaged in, in chronological order, with the current fold
            last.
          * ``prior_models_attempted`` — how many prior pickle paths the
            engine tried to load (``ensemble_window - 1`` capped by the
            available history).
          * ``prior_models_loaded`` — how many of those actually
            predicted successfully. A gap (e.g. corrupted pickle, model
            schema mismatch) is logged and skipped, not raised, so a
            single broken artifact does not abort the whole run.

        Why this is "warm" rather than strict
        --------------------------------------
        Each prior model was trained against its own fold's processed
        dataset (RobustZScoreNorm fit on that train window's stats); we
        run it against the *current* fold's dataset, which has different
        normalisation parameters. In practice cross-section IC is robust
        to these small distribution shifts on A-share data; in pathological
        regimes the operator should fall back to ``ensemble_window=1``
        and inspect the per-fold reports.
        """
        meta: dict[str, Any] = {
            "window": int(ensemble_window),
            "used": False,
            "n_models": 1,
            "contributing_folds": [int(current_fold_index)],
            "contributing_model_refs": [],
            "prior_models_attempted": 0,
            "prior_models_loaded": 0,
            "prior_models_index_mismatched": 0,
            "rejected_priors": [],
        }

        # No-op fast path: window 1 means "current fold only", which is
        # the legacy behaviour. Returning the current Series unchanged
        # also keeps ``ensemble_window=1`` cheap (no copy, no I/O).
        if ensemble_window <= 1:
            return current_predictions, meta

        # Window asks for priors but none exist yet (fold 0, or fold N
        # where prior pickles were not provided). Natural degradation:
        # use whatever's available — which here is just the current
        # model. Same shape as fold 0 below.
        if not prior_model_paths:
            return current_predictions, meta

        import pandas as pd

        # Pick the most-recent ``window - 1`` priors. ``prior_model_paths``
        # is in chronological (oldest-first) order; ``[-(window-1):]``
        # gives the newest ones.
        priors_to_load = list(prior_model_paths[-(ensemble_window - 1):])
        meta["prior_models_attempted"] = len(priors_to_load)

        # Stack predictions: start with the current fold's series, then
        # append each prior model's prediction over the same dataset. We
        # require exact index equality before stacking so pandas never
        # union-aligns a stale prior into a different signal universe.
        prediction_frames: list[Any] = [current_predictions.rename("m0")]
        contributing_folds: list[int] = []
        loaded = 0

        for offset, prior_ref in enumerate(priors_to_load):
            if (
                isinstance(prior_ref, tuple)
                and len(prior_ref) == 2
            ):
                prior_fold_idx, prior_path = prior_ref
            else:
                # Backward-compatible fallback for tests/direct callers that
                # still pass bare paths. Runtime ``run`` passes real refs.
                prior_fold_idx = current_fold_index - len(priors_to_load) + offset
                prior_path = prior_ref
            prior_path = str(prior_path)
            # ── provenance sidecar check ─────────────────────────
            # Read the model's provenance sidecar (written by
            # ModelTrainer.train_and_predict) before unpickling.
            # A lightgbm minor-bump can silently change booster
            # serialisation semantics — the same pickle may load
            # without error but produce semantically different
            # behaviour. We guard against that here by comparing
            # library versions.
            skip_prior = False
            sidecar_path = Path(prior_path).with_suffix(".pkl.meta.json")
            if sidecar_path.is_file():
                try:
                    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
                    import lightgbm as _lgb
                    # Check pickle integrity against the sidecar hash.
                    pkl_sha = sidecar.get("pkl_sha256")
                    if pkl_sha:
                        actual_sha = hashlib.sha256(
                            Path(prior_path).read_bytes()
                        ).hexdigest()
                        if actual_sha != pkl_sha:
                            _logger.warning(
                                "Fold %d ensemble: prior model %r sha256 "
                                "mismatch (expected %s, got %s) — pickle "
                                "replaced or corrupt. Skipping.",
                                current_fold_index, prior_path,
                                pkl_sha, actual_sha,
                            )
                            skip_prior = True
                            meta["rejected_priors"].append({
                                "fold_idx": prior_fold_idx,
                                "path": prior_path,
                                "reason": f"pkl_sha256 mismatch",
                            })
                    sidecar_lgb = sidecar.get("lightgbm_version")
                    if sidecar_lgb and sidecar_lgb != _lgb.__version__:
                        _logger.warning(
                            "Fold %d ensemble: prior model %r trained with "
                            "lightgbm %s; current is %s — skipping.",
                            current_fold_index, prior_path,
                            sidecar_lgb, _lgb.__version__,
                        )
                        skip_prior = True
                        meta["rejected_priors"].append({
                            "fold_idx": prior_fold_idx,
                            "path": prior_path,
                            "reason": f"lightgbm {sidecar_lgb} != {_lgb.__version__}",
                        })
                except Exception:
                    pass  # sidecar parse failed → load without guard
            if skip_prior:
                continue

            try:
                with open(prior_path, "rb") as f:
                    prior_model = pickle.load(f)
                # Each qlib LGBModel has ``predict(dataset, segment)``.
                # We want the test-segment scores aligned to the current
                # dataset's test slice — same as what the current model
                # produced — so use the same segment name the trainer
                # writes (``"test"``).
                prior_pred = prior_model.predict(current_dataset, "test")
            except Exception as exc:  # noqa: BLE001
                _logger.warning(
                    "Fold %d ensemble: skipping prior model %r — load/"
                    "predict failed (%s: %s). Continuing with the "
                    "remaining priors so a single bad pickle does not "
                    "abort the run.",
                    current_fold_index, prior_path, type(exc).__name__, exc,
                )
                continue

            # Coerce to Series. qlib's predict returns a Series for the
            # canonical handler, but a research-time monkey-patch could
            # in principle return a DataFrame; reject that loudly.
            if not isinstance(prior_pred, pd.Series):
                _logger.warning(
                    "Fold %d ensemble: prior model %r returned %s, "
                    "expected pd.Series. Skipping this prior.",
                    current_fold_index, prior_path,
                    type(prior_pred).__name__,
                )
                continue
            if not prior_pred.index.equals(current_predictions.index):
                meta["prior_models_index_mismatched"] = int(
                    meta["prior_models_index_mismatched"]
                ) + 1
                meta["rejected_priors"].append({
                    "fold_index": int(prior_fold_idx),
                    "model_ref": prior_path,
                    "reason": "index_mismatch",
                    "current_size": int(len(current_predictions.index)),
                    "prior_size": int(len(prior_pred.index)),
                })
                _logger.warning(
                    "Fold %d ensemble: prior model %r returned an index that "
                    "does not exactly match current predictions; skipping it "
                    "to avoid pandas union-alignment changing the signal "
                    "universe.",
                    current_fold_index, prior_path,
                )
                continue

            prediction_frames.append(
                prior_pred.rename(f"m{offset + 1}")
            )
            contributing_folds.append(int(prior_fold_idx))
            meta["contributing_model_refs"].append({
                "fold_index": int(prior_fold_idx),
                "model_ref": prior_path,
            })
            loaded += 1

        meta["prior_models_loaded"] = loaded

        if loaded == 0:
            # Every prior failed — fall back to current-fold-only. The
            # warning was already emitted per-prior above; here we just
            # surface the aggregate state in the meta block.
            return current_predictions, meta

        # ``concat(axis=1)`` aligns each model's predictions on the
        # ``(datetime, instrument)`` index. ``mean(axis=1, skipna=True)``
        # then averages across models — ``skipna`` matters because a
        # prior model can legitimately have NaN scores for instruments
        # not in its training universe (e.g. newly listed names),
        # whereas the current model has them.
        stacked = pd.concat(prediction_frames, axis=1)
        averaged = stacked.mean(axis=1, skipna=True)
        averaged = averaged.reindex(current_predictions.index)
        # The result Series has no name; rename to match the current
        # predictions' name so downstream consumers (SignalAnalyzer,
        # BacktestRunner) see the same shape.
        averaged.name = getattr(current_predictions, "name", None)

        # Order contributing_folds chronologically with current fold last
        # so the reader sees "earliest -> latest -> current" — matches
        # the semantic of "warm ensemble".
        meta["used"] = True
        meta["n_models"] = 1 + loaded
        meta["contributing_folds"] = contributing_folds + [int(current_fold_index)]

        _logger.info(
            "Fold %d ensemble: averaged %d models (current + %d priors, "
            "contributing folds %s).",
            current_fold_index, meta["n_models"], loaded,
            meta["contributing_folds"],
        )

        return averaged, meta

    @staticmethod
    def _write_prediction_artifact(path: Path, predictions: Any) -> str:
        """Persist the exact prediction Series consumed by official backtest.

        Model pickles alone are insufficient provenance once walk-forward
        ensembling is enabled: the backtest consumes the materialized signal,
        not a single model artifact. Return a SHA256 so reports can identify
        the exact bytes written.
        """
        with open(path, "wb") as f:
            pickle.dump(predictions, f)
        return hashlib.sha256(path.read_bytes()).hexdigest()

    @classmethod
    def _write_positions(
        cls,
        path: Path,
        positions: Mapping[str, Mapping[str, float]],
    ) -> None:
        """Persist the per-day portfolio weights produced by the backtest.

        Mirrors ``Pipeline.run`` step 7b: no ``default=str`` fallback —
        the contract is ``{date_str: {instrument: float}}`` and a leak of
        any other type should surface here at write-time, not weeks later
        in a dashboard.
        """
        # NaN-safe via ``_sanitize_for_json`` + ``allow_nan=False`` —
        # same convention as the per-fold and aggregate reports. A
        # leaked non-finite weight would otherwise produce the
        # non-standard ``NaN`` JSON token that strict parsers reject.
        sanitised = _sanitize_for_json(dict(positions))
        with open(path, "w", encoding="utf-8") as f:
            json.dump(sanitised, f, indent=2, allow_nan=False)

    @classmethod
    def _run_attribution_for_fold(
        cls,
        *,
        config: WalkForwardConfig,
        fold_index: int,
        test_start: str, test_end: str,
        predictions: Any,
        backtest_output: CanonicalBacktestOutput,
    ) -> tuple[AttributionResult | None, str | None]:
        """Run per-fold performance attribution; return ``(result, reason)``.

        Mirrors ``Pipeline.run`` step 7 layering exactly:

        - ``run_attribution=False`` → return ``(None,
          "disabled_by_config")``.
        - Backtest produced no positions → return ``(None,
          "no_positions_from_backtest")`` — refusing to silently fall
          back to a prediction-score proxy.
        - Industry artifact configured → resolve via the shared loader;
          a load failure aborts the run with :class:`WalkForwardError`
          (vs the soft skip for engine errors below) because it
          indicates a config / file mismatch the operator must fix
          before any fold can produce trustworthy attribution.
        - Engine raises :class:`PerformanceAttributionError` (degenerate
          inputs) → return ``(None, "engine_error: ...")`` with a
          WARNING log. This matches Pipeline's "skip + WARN" path so
          downstream comparison tools (PR #29 walk-forward-compare)
          can flag the degraded fold without aborting the rest.
        """
        if not config.run_attribution:
            return None, "disabled_by_config"

        if not backtest_output.positions:
            _logger.warning(
                "Fold %d: skipping attribution — backtest produced no "
                "positions. Refusing to fall back to prediction-score "
                "attribution (no implicit fallback).",
                fold_index,
            )
            return None, "no_positions_from_backtest"

        attribution_overrides: dict[str, Any] = {}
        if config.industry_artifact_path:
            # ``purpose=PURPOSE_ATTRIBUTION`` is the explicit "this is
            # post-hoc analysis, not training" declaration. The shared
            # loader uses the purpose enum to decide whether the
            # temporal-leakage check fires; we no longer rely on
            # ``reference_date=None`` as the implicit signal. See the
            # ``purpose`` parameter docstring in
            # :func:`resolve_industry_taxonomy` for the full
            # rationale.
            try:
                resolution = resolve_industry_taxonomy(
                    artifact_path=str(config.industry_artifact_path),
                    manifest_path=str(config.industry_manifest_path),
                    taxonomy_id=str(config.industry_taxonomy_id).strip(),
                    temporal_mode=config.industry_temporal_mode,
                    purpose=PURPOSE_ATTRIBUTION,
                )
            except IndustryTaxonomyLoadError as exc:
                # Industry-artifact load failures are config / file
                # problems — every fold will hit the same error. Promote
                # to a hard ``WalkForwardError`` rather than skipping
                # silently so the operator fixes the root cause once.
                raise WalkForwardError(
                    f"Fold {fold_index}: industry taxonomy load failed: {exc}"
                ) from exc
            for warning in resolution.warnings:
                _logger.warning(
                    "Fold %d industry taxonomy contract warning: %s",
                    fold_index, warning,
                )
            attribution_overrides["industry_map_override"] = resolution.industry_map
            attribution_overrides["industry_taxonomy_id"] = resolution.taxonomy_id

        attr_config = AttributionConfig(
            start_date=test_start,
            end_date=test_end,
            **attribution_overrides,
        )

        try:
            result = PerformanceAttribution.analyze(
                return_series=backtest_output.return_series,
                # Use the ensemble-aware predictions (same series the
                # backtest received) so attribution's universe and the
                # backtest's universe are guaranteed to match.
                predictions=predictions,
                config=attr_config,
                positions=backtest_output.positions,
            )
        except PerformanceAttributionError as exc:
            _logger.warning(
                "Fold %d: attribution skipped — engine raised %s: %s. "
                "Backtest and risk_analysis remain valid; only the "
                "sector-attribution block is absent from this fold's report.",
                fold_index, type(exc).__name__, exc,
            )
            return None, f"engine_error: {type(exc).__name__}: {exc}"

        return result, None

    @classmethod
    def _build_fold_report(
        cls,
        *,
        fold_index: int,
        train_start: str, train_end: str,
        valid_start: str, valid_end: str,
        test_start: str, test_end: str,
        model_artifact_path: str,
        model_result: ModelTrainResult,
        signal_result: SignalAnalysisResult,
        backtest_output: CanonicalBacktestOutput,
        positions_path: Path | None,
        ic_1d: float, ic_5d: float,
        annualized_return: float, max_drawdown: float,
        information_ratio: float,
        attribution_result: AttributionResult | None = None,
        attribution_skipped_reason: str | None = None,
        ensemble_meta: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build the per-fold report dict.

        Extracted from :meth:`_write_fold_report` so the schema is unit-
        testable without touching the filesystem (mirrors the same split
        already in use for ``Pipeline._attribution_to_report_dict``).

        ``ensemble_meta`` (when supplied by :meth:`_run_single_fold`)
        carries the cross-fold averaging audit trail produced by
        :meth:`_maybe_apply_ensemble`. It always lands on the report
        under ``"ensemble"`` so the downstream comparison tooling
        (PR #29 walk-forward-compare) sees a uniform shape across
        ``ensemble_window=1`` runs (``used=False, n_models=1``) and
        ensembled runs.
        """
        # ``ic_summary`` is keyed by int (forward period); JSON keys must
        # be strings, so coerce up front.
        ic_summary_serialised = {
            str(period): dict(stats)
            for period, stats in signal_result.ic_summary.items()
        }
        return {
            "fold_index": fold_index,
            "windows": {
                "train": {"start": train_start, "end": train_end},
                "valid": {"start": valid_start, "end": valid_end},
                "test":  {"start": test_start,  "end": test_end},
            },
            "model": {
                "artifact_path": model_artifact_path,
                "best_iteration": model_result.best_iteration,
                "final_valid_loss": model_result.final_valid_loss,
                "prediction_shape": list(model_result.prediction_shape),
            },
            "signal_analysis": {
                "ic_summary": ic_summary_serialised,
                "ic_decay": list(signal_result.ic_decay),
                "turnover_stats": dict(signal_result.turnover_stats),
            },
            "backtest": {
                "metric_status": backtest_output.metric_status,
                "official_backtest_path": backtest_output.official_backtest_path,
                "report": dict(backtest_output.report),
                "risk_analysis": dict(backtest_output.risk_analysis),
                "provenance": dict(backtest_output.provenance),
            },
            "metrics": {
                "ic_1d": ic_1d,
                "ic_5d": ic_5d,
                "annualized_return": annualized_return,
                "max_drawdown": max_drawdown,
                "information_ratio": information_ratio,
            },
            # Always emit the attribution block — same convention as
            # ``Pipeline._attribution_section``: ``status`` / ``skipped_reason``
            # are present whether or not the engine ran, so downstream
            # comparison tools see a uniform shape.
            "attribution": cls._attribution_section_for_fold(
                attribution_result, attribution_skipped_reason,
            ),
            # Default the ensemble block to a "no-op" shape when the caller
            # did not supply meta — this preserves report compatibility for
            # any test that constructs a report directly without going
            # through ``_run_single_fold``.
            "ensemble": (
                dict(ensemble_meta)
                if ensemble_meta is not None
                else {
                    "window": 1,
                    "used": False,
                    "n_models": 1,
                    "contributing_folds": [fold_index],
                    "contributing_model_refs": [],
                    "prior_models_attempted": 0,
                    "prior_models_loaded": 0,
                    "prior_models_index_mismatched": 0,
                    "rejected_priors": [],
                }
            ),
            "positions_path": str(positions_path) if positions_path else None,
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        }

    @staticmethod
    def _attribution_section_for_fold(
        attribution_result: AttributionResult | None,
        skipped_reason: str | None,
    ) -> dict[str, Any]:
        """Build the per-fold attribution block.

        Mirrors :meth:`Pipeline._attribution_section` so the same
        downstream consumers (``walk-forward-compare`` PR #29,
        dashboards) read the same shape regardless of which engine
        produced the report.
        """
        if attribution_result is None:
            return {
                "status": "skipped",
                "skipped_reason": skipped_reason or "unknown_reason",
            }
        return {
            "status": "ok",
            "skipped_reason": None,
            "sector_taxonomy": attribution_result.sector_taxonomy,
            "attribution_method": attribution_result.attribution_method,
            "bench_weight_method": attribution_result.bench_weight_method,
            "total_portfolio_return": attribution_result.total_portfolio_return,
            "total_benchmark_return": attribution_result.total_benchmark_return,
            "total_excess_return": attribution_result.total_excess_return,
            "allocation_effect": attribution_result.total_allocation_effect,
            "selection_effect": attribution_result.total_selection_effect,
            "interaction_effect": attribution_result.total_interaction_effect,
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
        }

    @classmethod
    def _write_fold_report(
        cls,
        *,
        report_path: Path,
        **kwargs: Any,
    ) -> None:
        """Build and persist a per-fold report at ``report_path``.

        NaN-safe: routes through :func:`_sanitize_for_json` and uses
        ``allow_nan=False`` so any leaked non-finite float surfaces as
        an error rather than silently producing non-standard JSON
        (browsers, ``jq``, strict parsers reject the bare ``NaN`` token).
        """
        report = cls._build_fold_report(**kwargs)
        sanitised = _sanitize_for_json(report)
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(
                sanitised, f, indent=2, ensure_ascii=False,
                default=str, allow_nan=False,
            )

    @staticmethod
    def _extract_cost_metrics(
        risk_analysis: Mapping[str, Any],
        fold_index: int,
    ) -> tuple[float, float, float]:
        """Extract ``(annualized_return, max_drawdown, information_ratio)``
        from a qlib ``risk_analysis`` dict, raising loudly on any shape mismatch.

        Old code used ``cost_metrics.get("annualized_return", 0.0)``, which
        meant any qlib output shape change — or a normalizer that routed
        malformed data into ``{"raw": ...}`` — silently turned every fold
        into a zero-return run. We now require the three metrics to be
        present as floats and raise if not.
        """
        if "excess_return_with_cost" not in risk_analysis:
            raise WalkForwardError(
                f"Fold {fold_index}: backtest risk_analysis has no "
                f"'excess_return_with_cost' block. Available top-level keys: "
                f"{sorted(risk_analysis.keys())}. qlib output shape may "
                "have changed."
            )
        cost_metrics = risk_analysis["excess_return_with_cost"]
        if not isinstance(cost_metrics, dict):
            raise WalkForwardError(
                f"Fold {fold_index}: 'excess_return_with_cost' is "
                f"{type(cost_metrics).__name__}, expected dict. The backtest "
                "normalizer may have failed to parse the DataFrame."
            )
        required_metrics = ("annualized_return", "max_drawdown", "information_ratio")
        missing_metrics = [m for m in required_metrics if m not in cost_metrics]
        if missing_metrics:
            raise WalkForwardError(
                f"Fold {fold_index}: risk_analysis['excess_return_with_cost'] "
                f"is missing {missing_metrics}. Keys present: "
                f"{sorted(cost_metrics.keys())}. qlib output shape may have "
                "changed; refusing to substitute 0.0 for missing metrics."
            )
        return (
            float(cost_metrics["annualized_return"]),
            float(cost_metrics["max_drawdown"]),
            float(cost_metrics["information_ratio"]),
        )

    @classmethod
    def _compute_aggregate(cls, folds: list[WalkForwardFold]) -> dict[str, float]:
        """Compute aggregate metrics across all folds, NaN-safe.

        SignalAnalyzer now surfaces "no valid IC" as ``NaN`` rather than
        silently coercing to 0.0 (P2c, batch 6). With plain ``np.mean``,
        a single NaN fold poisons every downstream aggregate — the user
        would see ``mean_ic_1d=NaN`` across an entire walk-forward study
        because one fold happened to have too-short validation data to
        compute cross-sectional IC.

        The fix is "skip-but-disclose":

        - Aggregates are computed with ``np.nan{mean,std,min}`` so NaN
          folds are excluded rather than propagated.
        - A companion ``valid_folds_<metric>`` count is written into the
          result so the caller can tell a 5/5 study apart from a 1/5
          study. Same-shape output as before (all floats), but with
          explicit provenance on how many folds fed each statistic.
        - If *every* fold is NaN for a metric, the aggregator still
          returns ``NaN`` for that metric (``np.nanmean`` of all-NaN is
          NaN by numpy convention) — a loud signal rather than a false
          zero.
        """
        import numpy as np

        if not folds:
            return {}

        ic_1d = np.asarray([f.ic_1d for f in folds], dtype=float)
        ic_5d = np.asarray([f.ic_5d for f in folds], dtype=float)
        returns = np.asarray([f.annualized_return for f in folds], dtype=float)
        drawdowns = np.asarray([f.max_drawdown for f in folds], dtype=float)
        irs = np.asarray([f.information_ratio for f in folds], dtype=float)

        import warnings

        def _nan_agg(arr: "np.ndarray", fn: Any) -> float:
            """np.nan{mean,std,min}(arr) with the all-NaN-slice
            RuntimeWarning silenced — NaN is exactly the result we want
            in those cases, the warning would just be noise.
            """
            if not arr.size:
                return float("nan")
            with np.errstate(invalid="ignore"), warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                return float(fn(arr))

        def _nanmean(arr: "np.ndarray") -> float:
            return _nan_agg(arr, np.nanmean)

        def _nanstd(arr: "np.ndarray") -> float:
            return _nan_agg(arr, np.nanstd)

        def _nanmin(arr: "np.ndarray") -> float:
            return _nan_agg(arr, np.nanmin)

        def _valid(arr: "np.ndarray") -> int:
            return int(np.count_nonzero(~np.isnan(arr)))

        def _bootstrap_mean_ci(
            arr: "np.ndarray",
            *,
            n_boot: int = 10000,
            ci: float = 0.95,
            seed: int = 42,
        ) -> tuple[float, float]:
            """95% bootstrap CI for the sample mean.

            Folds are designed non-overlapping (window boundaries never
            share the same calendar month), so ``block_size=1`` (standard
            i.i.d. bootstrap) is appropriate.  If a future change
            introduces overlap (``step_months < test_months``) this
            function should be re-tuned with ``block_size`` to match the
            maximum overlap depth.

            Returns ``(NaN, NaN)`` when fewer than 2 finite observations
            are available — a single-fold CI is not meaningful.
            """
            finite = arr[~np.isnan(arr)]
            if finite.size < 2:
                return float("nan"), float("nan")
            rng = np.random.default_rng(seed)
            boots = rng.choice(
                finite, size=(n_boot, finite.size), replace=True
            ).mean(axis=1)
            lo = float(np.percentile(boots, 100 * (1 - ci) / 2))
            hi = float(np.percentile(boots, 100 * (1 + ci) / 2))
            return lo, hi

        ci_ic_1d_lo, ci_ic_1d_hi = _bootstrap_mean_ci(ic_1d)
        ci_ic_5d_lo, ci_ic_5d_hi = _bootstrap_mean_ci(ic_5d)
        ci_ir_lo, ci_ir_hi = _bootstrap_mean_ci(irs)
        ci_ret_lo, ci_ret_hi = _bootstrap_mean_ci(returns)

        return {
            "mean_ic_1d": _nanmean(ic_1d),
            "std_ic_1d": _nanstd(ic_1d),
            "mean_ic_1d_ci_low": ci_ic_1d_lo,
            "mean_ic_1d_ci_high": ci_ic_1d_hi,
            "valid_folds_ic_1d": _valid(ic_1d),
            "mean_ic_5d": _nanmean(ic_5d),
            "std_ic_5d": _nanstd(ic_5d),
            "mean_ic_5d_ci_low": ci_ic_5d_lo,
            "mean_ic_5d_ci_high": ci_ic_5d_hi,
            "valid_folds_ic_5d": _valid(ic_5d),
            "mean_annualized_return": _nanmean(returns),
            "mean_annualized_return_ci_low": ci_ret_lo,
            "mean_annualized_return_ci_high": ci_ret_hi,
            "valid_folds_annualized_return": _valid(returns),
            "worst_drawdown": _nanmin(drawdowns),
            "valid_folds_max_drawdown": _valid(drawdowns),
            "mean_information_ratio": _nanmean(irs),
            "std_information_ratio": _nanstd(irs),
            "mean_information_ratio_ci_low": ci_ir_lo,
            "mean_information_ratio_ci_high": ci_ir_hi,
            "valid_folds_information_ratio": _valid(irs),
            "num_folds": len(folds),
            "bootstrap_seed": 42,
            "bootstrap_n": 10000,
        }
