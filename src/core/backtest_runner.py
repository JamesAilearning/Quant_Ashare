"""Backtest runner — implements the canonical backtest runtime.

Bridges ``CanonicalBacktestInput`` to qlib's backtest engine and
produces ``CanonicalBacktestOutput`` with official metrics.

Boundaries
----------
- Uses ``qlib.backtest.backtest`` directly — the canonical anchored callable.
- Does NOT call ``qlib.init``. Requires prior canonical init.
- All input validation is delegated to ``CanonicalBacktestContract
  .validate_input()``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any, Mapping

from src.core.canonical_backtest_contract import (
    CANONICAL_OFFICIAL_BACKTEST_PATH,
    OFFICIAL_METRIC_STATUS,
    CanonicalBacktestContract,
    CanonicalBacktestInput,
    CanonicalBacktestOutput,
)


class BacktestRunnerError(RuntimeError):
    """Raised on backtest execution failures."""


class BacktestRunner:
    """Runs the canonical backtest pipeline.

    Usage::

        output = BacktestRunner.run(
            request=CanonicalBacktestInput(...),
            predictions=model_result.predictions,
        )
        print(output.risk_analysis)
    """

    @classmethod
    def run(
        cls,
        *,
        request: CanonicalBacktestInput,
        predictions: Any,
        topk: int = 50,
        n_drop: int = 5,
    ) -> CanonicalBacktestOutput:
        CanonicalBacktestContract.validate_input(request)

        if predictions is None or (hasattr(predictions, "empty") and predictions.empty):
            raise BacktestRunnerError("predictions must be non-empty.")

        try:
            from qlib.backtest import backtest as qlib_backtest  # type: ignore[import-not-found]
            from qlib.backtest.executor import SimulatorExecutor  # type: ignore[import-not-found]
            from qlib.contrib.evaluate import risk_analysis  # type: ignore[import-not-found]
            from qlib.contrib.strategy.signal_strategy import TopkDropoutStrategy  # type: ignore[import-not-found]
            from qlib.utils.time import Freq  # type: ignore[import-not-found]
        except ImportError as exc:
            raise BacktestRunnerError(
                "qlib is not importable; cannot run backtest."
            ) from exc

        # Map CanonicalExchangeConfig → qlib exchange_kwargs
        cost = request.exchange_config.cost_model
        stamp_tax_fraction = cost.stamp_tax_bps / 10000.0
        exchange_kwargs = {
            "freq": request.exchange_config.freq,
            "deal_price": request.exchange_config.execution_price_kind,
            "open_cost": cost.commission_rate,
            "close_cost": cost.commission_rate + stamp_tax_fraction,
            "min_cost": cost.min_cost,
            "limit_threshold": 0.095,
        }

        # Apply signal_to_execution_lag by shifting predictions
        shifted_predictions = cls._apply_lag(predictions, request.signal_to_execution_lag)

        strategy = TopkDropoutStrategy(
            signal=shifted_predictions,
            topk=topk,
            n_drop=n_drop,
        )

        executor = SimulatorExecutor(
            time_per_step=request.exchange_config.freq,
            generate_portfolio_metrics=True,
        )

        try:
            portfolio_metric_dict, indicator_dict = qlib_backtest(
                start_time=request.evaluation_start,
                end_time=request.evaluation_end,
                strategy=strategy,
                executor=executor,
                account=request.account_config.init_cash,
                benchmark=request.benchmark_code or "SH000300",
                exchange_kwargs=exchange_kwargs,
            )
        except Exception as exc:
            raise BacktestRunnerError(
                f"qlib backtest execution failed: {exc}"
            ) from exc

        # Extract report from portfolio_metric_dict
        analysis_freq = "{}{}".format(*Freq.parse(request.exchange_config.freq))
        freq_result = portfolio_metric_dict.get(analysis_freq)
        if freq_result is None:
            raise BacktestRunnerError(
                f"No portfolio metrics for freq '{analysis_freq}'. "
                "Check that generate_portfolio_metrics=True."
            )
        report_normal, positions_normal = freq_result

        if report_normal is None or report_normal.empty:
            raise BacktestRunnerError(
                "Backtest produced no results. Check date ranges and predictions."
            )

        # Extract risk analysis
        try:
            excess_return_without_cost = risk_analysis(
                report_normal["return"] - report_normal["bench"],
                freq="day",
            )
            excess_return_with_cost = risk_analysis(
                report_normal["return"] - report_normal["bench"] - report_normal["cost"],
                freq="day",
            )
        except Exception as exc:
            raise BacktestRunnerError(
                f"Risk analysis extraction failed: {exc}"
            ) from exc

        risk_dict = {
            "excess_return_without_cost": _risk_analysis_to_flat_dict(excess_return_without_cost),
            "excess_return_with_cost": _risk_analysis_to_flat_dict(excess_return_with_cost),
        }

        return_series = {
            "return": _series_to_dict(report_normal["return"]),
            "bench": _series_to_dict(report_normal["bench"]),
            "cost": _series_to_dict(report_normal["cost"]),
        }

        report = {
            "total_days": len(report_normal),
            "start_date": str(report_normal.index.min().date()),
            "end_date": str(report_normal.index.max().date()),
        }

        provenance = cls._build_provenance(request, topk, n_drop)

        return CanonicalBacktestOutput(
            metric_status=OFFICIAL_METRIC_STATUS,
            official_backtest_path=CANONICAL_OFFICIAL_BACKTEST_PATH,
            return_series=return_series,
            risk_analysis=risk_dict,
            report=report,
            provenance=provenance,
        )

    @staticmethod
    def _apply_lag(predictions: Any, lag: int) -> Any:
        """Shift predictions to simulate signal-to-execution lag.

        qlib's TopkDropoutStrategy uses signal at T to trade at T.
        A lag of 1 means "signal generated at T, executed at T+1"
        which is qlib's natural behavior when predictions are indexed
        by the date the signal was generated.

        For lag > 1 we shift the predictions backward so that
        day-T's signal is only visible at day-T+(lag-1).
        """
        if lag <= 1:
            return predictions
        import pandas as pd
        if isinstance(predictions, pd.Series) and isinstance(predictions.index, pd.MultiIndex):
            # MultiIndex (datetime, instrument): shift the datetime level
            df = predictions.unstack()
            df = df.shift(lag - 1)
            return df.stack().dropna()
        return predictions

    @staticmethod
    def _build_provenance(
        request: CanonicalBacktestInput,
        topk: int,
        n_drop: int,
    ) -> Mapping[str, Any]:
        config_dict = {
            "evaluation_start": request.evaluation_start,
            "evaluation_end": request.evaluation_end,
            "init_cash": request.account_config.init_cash,
            "freq": request.exchange_config.freq,
            "execution_price_kind": request.exchange_config.execution_price_kind,
            "commission_rate": request.exchange_config.cost_model.commission_rate,
            "stamp_tax_bps": request.exchange_config.cost_model.stamp_tax_bps,
            "slippage_bps": request.exchange_config.cost_model.slippage_bps,
            "min_cost": request.exchange_config.cost_model.min_cost,
            "adjust_mode": request.adjust_mode,
            "signal_to_execution_lag": request.signal_to_execution_lag,
            "benchmark_code": request.benchmark_code,
            "topk": topk,
            "n_drop": n_drop,
        }
        config_json = json.dumps(config_dict, sort_keys=True, default=str)
        fingerprint = hashlib.sha256(config_json.encode()).hexdigest()[:16]
        return {
            "config": config_dict,
            "config_fingerprint": fingerprint,
            "official_backtest_path": CANONICAL_OFFICIAL_BACKTEST_PATH,
        }


def _risk_analysis_to_flat_dict(df: Any) -> dict:
    """Normalize a qlib risk_analysis DataFrame to a flat {metric: value} dict.

    qlib's ``risk_analysis`` can return a DataFrame in two orientations:

    Column-oriented (metric as columns, index row = "risk")::

        index  annualized_return  information_ratio  max_drawdown
        risk   -0.27              -1.05              -0.15

        df.to_dict() → {"annualized_return": {"risk": -0.27},
                         "information_ratio": {"risk": -1.05}, ...}

    Row-oriented (index = metric names, single column "risk")::

        index              risk
        annualized_return  -0.27
        information_ratio  -1.05
        max_drawdown       -0.15

        df.to_dict() → {"risk": {"annualized_return": -0.27,
                                  "information_ratio": -1.05, ...}}

    Both are normalized to ``{"annualized_return": -0.27, ...}``.
    """
    try:
        raw = df.to_dict()
        if not raw:
            return {}

        first_val = next(iter(raw.values()))

        if not isinstance(first_val, dict):
            # Already flat scalars.
            return {str(k): (float(v) if hasattr(v, "__float__") else str(v))
                    for k, v in raw.items()}

        # Detect row-oriented shape: single outer key "risk" whose value
        # is a dict of {metric_name: scalar}.
        if len(raw) == 1 and "risk" in raw and isinstance(raw["risk"], dict):
            inner = raw["risk"]
            return {str(k): (float(v) if hasattr(v, "__float__") else str(v))
                    for k, v in inner.items()}

        # Column-oriented shape: outer keys are metric names, inner dicts
        # have index labels as keys (typically a single "risk" entry).
        flat: dict = {}
        for metric, sub in raw.items():
            if not isinstance(sub, dict):
                try:
                    flat[str(metric)] = float(sub)
                except (TypeError, ValueError):
                    flat[str(metric)] = str(sub)
                continue
            # Prefer the "risk" index label; fall back to first value.
            val = sub.get("risk", next(iter(sub.values())))
            try:
                flat[str(metric)] = float(val)
            except (TypeError, ValueError):
                flat[str(metric)] = str(val)
        return flat
    except Exception:
        return {"raw": str(df)}


def _dataframe_to_dict(df: Any) -> dict:
    """Convert a pandas DataFrame to a nested dict safe for JSON."""
    try:
        return {
            str(k): {str(kk): float(vv) if hasattr(vv, "__float__") else str(vv) for kk, vv in v.items()}
            for k, v in df.to_dict().items()
        }
    except Exception:
        return {"raw": str(df)}


def _series_to_dict(series: Any) -> dict:
    """Convert a pandas Series to a dict with string keys."""
    try:
        return {str(k.date()) if hasattr(k, "date") else str(k): float(v) for k, v in series.items()}
    except Exception:
        return {"raw": str(series)}
