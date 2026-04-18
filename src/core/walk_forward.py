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

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Mapping, Sequence

from src.core.logger import get_logger
from src.core.qlib_runtime import is_canonical_qlib_initialized

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

    # Backtest config
    benchmark_code: str = "SH000300"
    init_cash: float = 100_000_000
    topk: int = 50
    n_drop: int = 5
    commission_rate: float = 0.0005
    stamp_tax_bps: float = 10.0
    slippage_bps: float = 5.0

    # Output
    output_dir: str = "output/walk_forward"

    def __post_init__(self) -> None:
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


@dataclass(frozen=True)
class WalkForwardResult:
    """Aggregated walk-forward results."""

    folds: Sequence[WalkForwardFold]
    aggregate_metrics: Mapping[str, float]
    num_folds: int


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

        return WalkForwardResult(
            folds=folds,
            aggregate_metrics=aggregate,
            num_folds=len(folds),
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
        from pathlib import Path

        from src.core.backtest_runner import BacktestRunner
        from src.core.canonical_backtest_contract import (
            ADJUST_MODE_PRE,
            CanonicalAccountConfig,
            CanonicalBacktestInput,
            CanonicalExchangeConfig,
            CanonicalExchangeCostModel,
        )
        from src.core.model_trainer import ModelTrainConfig, ModelTrainer
        from src.core.signal_analyzer import SignalAnalysisConfig, SignalAnalyzer
        from src.data.feature_dataset_builder import FeatureDatasetBuilder, FeatureDatasetConfig

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
                execution_price_kind="close",
                cost_model=CanonicalExchangeCostModel(
                    commission_rate=config.commission_rate,
                    stamp_tax_bps=config.stamp_tax_bps,
                    slippage_bps=config.slippage_bps,
                    min_cost=5.0,
                ),
            ),
            adjust_mode=ADJUST_MODE_PRE,
            signal_to_execution_lag=1,
            benchmark_code=config.benchmark_code,
        )

        backtest_output = BacktestRunner.run(
            request=backtest_request,
            predictions=model_result.predictions,
            topk=config.topk,
            n_drop=config.n_drop,
        )

        # risk_analysis["excess_return_with_cost"] is now a flat {metric: value} dict
        # (normalized by backtest_runner._risk_analysis_to_flat_dict)
        risk = backtest_output.risk_analysis
        cost_metrics = risk.get("excess_return_with_cost", {})
        ann_ret = float(cost_metrics.get("annualized_return", 0.0))
        max_dd = float(cost_metrics.get("max_drawdown", 0.0))
        ir = float(cost_metrics.get("information_ratio", 0.0))

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
        )

    @classmethod
    def _compute_aggregate(cls, folds: list[WalkForwardFold]) -> dict[str, float]:
        """Compute aggregate metrics across all folds."""
        import numpy as np

        if not folds:
            return {}

        ic_1d = [f.ic_1d for f in folds]
        ic_5d = [f.ic_5d for f in folds]
        returns = [f.annualized_return for f in folds]
        drawdowns = [f.max_drawdown for f in folds]
        irs = [f.information_ratio for f in folds]

        return {
            "mean_ic_1d": float(np.mean(ic_1d)),
            "std_ic_1d": float(np.std(ic_1d)),
            "mean_ic_5d": float(np.mean(ic_5d)),
            "mean_annualized_return": float(np.mean(returns)),
            "worst_drawdown": float(np.min(drawdowns)),
            "mean_information_ratio": float(np.mean(irs)),
            "num_folds": float(len(folds)),
        }
