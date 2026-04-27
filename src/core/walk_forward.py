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

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from src.core._json_utils import _sanitize_for_json
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
from src.core.model_trainer import ModelTrainConfig, ModelTrainer, ModelTrainResult
from src.core.qlib_runtime import is_canonical_qlib_initialized
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
            or self.signal_to_execution_lag < 1
        ):
            raise WalkForwardError(
                "signal_to_execution_lag must be an int >= 1; got "
                f"{self.signal_to_execution_lag!r}."
            )
        if self.adjust_mode not in SUPPORTED_ADJUST_MODES:
            raise WalkForwardError(
                f"adjust_mode must be one of {SUPPORTED_ADJUST_MODES}; "
                f"got {self.adjust_mode!r}."
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

        # Generate fold windows
        windows = cls._generate_windows(config)
        if not windows:
            raise WalkForwardError(
                "No valid fold windows could be generated with the given config. "
                "Check that overall period is long enough for train+valid+test windows."
            )

        folds: list[WalkForwardFold] = []
        _logger.info("Starting %d folds", len(windows))
        _logger.info("=" * 60)

        for i, (train_s, train_e, valid_s, valid_e, test_s, test_e) in enumerate(windows):
            _logger.info(
                "Fold %d/%d  Train: %s~%s | Valid: %s~%s | Test: %s~%s",
                i + 1, len(windows), train_s, train_e, valid_s, valid_e, test_s, test_e,
            )

            fold = cls._run_single_fold(
                config=config,
                fold_index=i,
                train_start=train_s, train_end=train_e,
                valid_start=valid_s, valid_end=valid_e,
                test_start=test_s, test_end=test_e,
                output_dir=output_dir,
            )
            folds.append(fold)
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
            "generated_at": datetime.now().isoformat(),
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
    ) -> WalkForwardFold:
        """Execute a single train→predict→analyze→backtest fold."""
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
            config=ModelTrainConfig(
                model_type=config.model_type,
                num_boost_round=config.num_boost_round,
                early_stopping_rounds=config.early_stopping_rounds,
                learning_rate=config.learning_rate,
                max_depth=config.max_depth,
                num_leaves=config.num_leaves,
                lambda_l1=config.lambda_l1,
                lambda_l2=config.lambda_l2,
                min_data_in_leaf=config.min_data_in_leaf,
                feature_fraction=config.feature_fraction,
                bagging_fraction=config.bagging_fraction,
                bagging_freq=config.bagging_freq,
            ),
            dataset=feature_result.dataset,
            model_artifact_path=model_path,
        )

        # Signal analysis
        signal_result = SignalAnalyzer.analyze(
            predictions=model_result.predictions,
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
            predictions_ref=model_path,
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
            predictions=model_result.predictions,
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
        with open(path, "w", encoding="utf-8") as f:
            json.dump(dict(positions), f, indent=2)

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
    ) -> dict[str, Any]:
        """Build the per-fold report dict.

        Extracted from :meth:`_write_fold_report` so the schema is unit-
        testable without touching the filesystem (mirrors the same split
        already in use for ``Pipeline._attribution_to_report_dict``).
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
            "positions_path": str(positions_path) if positions_path else None,
            "generated_at": datetime.now().isoformat(),
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

        return {
            "mean_ic_1d": _nanmean(ic_1d),
            "std_ic_1d": _nanstd(ic_1d),
            "valid_folds_ic_1d": float(_valid(ic_1d)),
            "mean_ic_5d": _nanmean(ic_5d),
            "valid_folds_ic_5d": float(_valid(ic_5d)),
            "mean_annualized_return": _nanmean(returns),
            "valid_folds_annualized_return": float(_valid(returns)),
            "worst_drawdown": _nanmin(drawdowns),
            "valid_folds_max_drawdown": float(_valid(drawdowns)),
            "mean_information_ratio": _nanmean(irs),
            "valid_folds_information_ratio": float(_valid(irs)),
            "num_folds": float(len(folds)),
        }
