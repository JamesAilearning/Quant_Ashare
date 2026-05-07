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

from src.core.logger import get_logger
from src.core.canonical_backtest_contract import (
    CANONICAL_OFFICIAL_BACKTEST_PATH,
    CANONICAL_OFFICIAL_METRIC_HELPER_CALLABLE,
    CANONICAL_OFFICIAL_METRIC_HELPER_PATH,
    OFFICIAL_METRIC_STATUS,
    CanonicalBacktestContract,
    CanonicalBacktestInput,
    CanonicalBacktestOutput,
)
from src.core.qlib_runtime import (
    get_canonical_qlib_config,
    is_canonical_qlib_initialized,
)


_logger = get_logger(__name__)


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
        # validate_input() enforces benchmark_code is non-empty as of the
        # contract level — no redundant check needed here.
        CanonicalBacktestContract.validate_input(request)

        if predictions is None or (hasattr(predictions, "empty") and predictions.empty):
            raise BacktestRunnerError("predictions must be non-empty.")

        if not is_canonical_qlib_initialized():
            raise BacktestRunnerError(
                "Canonical qlib runtime must be initialized via "
                "src.core.qlib_runtime.init_qlib_canonical(...) before "
                "running official backtests."
            )
        runtime_config = get_canonical_qlib_config()
        if runtime_config is None:
            raise BacktestRunnerError(
                "Canonical qlib runtime reports initialized but has no "
                "recorded config; refusing to produce official metrics."
            )
        if request.adjust_mode != runtime_config.data_adjust_mode:
            raise BacktestRunnerError(
                "Canonical backtest adjust_mode does not match initialized "
                "qlib provider adjustment mode: "
                f"request.adjust_mode={request.adjust_mode!r}, "
                f"runtime.data_adjust_mode={runtime_config.data_adjust_mode!r}. "
                "Official metrics require matching data-adjustment semantics."
            )

        try:
            from qlib.backtest import backtest as qlib_backtest  # type: ignore[import-not-found]
            from qlib.backtest.executor import SimulatorExecutor  # type: ignore[import-not-found]
            from qlib.contrib.strategy.signal_strategy import TopkDropoutStrategy  # type: ignore[import-not-found]
            from qlib.utils.time import Freq  # type: ignore[import-not-found]
        except ImportError as exc:
            raise BacktestRunnerError(
                "qlib is not importable; cannot run backtest."
            ) from exc

        # Official risk metrics must go through the governance-anchored helper,
        # not a direct import — this keeps the runtime path aligned with the
        # path that governance locks (see tests/governance/test_no_alt_backtest_path.py).
        if CANONICAL_OFFICIAL_METRIC_HELPER_CALLABLE is None:
            raise BacktestRunnerError(
                "Canonical metric helper "
                f"({CANONICAL_OFFICIAL_METRIC_HELPER_PATH}) is not importable; "
                "cannot compute official risk metrics."
            )
        risk_analysis = CANONICAL_OFFICIAL_METRIC_HELPER_CALLABLE

        # Map CanonicalExchangeConfig → qlib exchange_kwargs
        cost = request.exchange_config.cost_model
        stamp_tax_fraction = cost.stamp_tax_bps / 10000.0
        slippage_fraction = cost.slippage_bps / 10000.0
        exchange_kwargs = {
            "freq": request.exchange_config.freq,
            "deal_price": request.exchange_config.execution_price_kind,
            "open_cost": cost.commission_rate + slippage_fraction,
            "close_cost": cost.commission_rate + stamp_tax_fraction + slippage_fraction,
            "min_cost": cost.min_cost,
            # Use contract-provided limit_threshold so callers pick the right
            # A-share regime (main board / ChiNext / ST) rather than baking it in.
            "limit_threshold": float(request.exchange_config.limit_threshold),
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
                benchmark=request.benchmark_code,
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
            "return": _series_to_dict(report_normal["return"], name="return"),
            "bench": _series_to_dict(report_normal["bench"], name="bench"),
            "cost": _series_to_dict(report_normal["cost"], name="cost"),
        }

        positions_map = _positions_to_weight_map(positions_normal)

        report = {
            "total_days": len(report_normal),
            "start_date": str(report_normal.index.min().date()),
            "end_date": str(report_normal.index.max().date()),
            "positions_days": len(positions_map),
        }

        provenance = cls._build_provenance(request, topk, n_drop)

        return CanonicalBacktestOutput(
            metric_status=OFFICIAL_METRIC_STATUS,
            official_backtest_path=CANONICAL_OFFICIAL_BACKTEST_PATH,
            return_series=return_series,
            risk_analysis=risk_dict,
            report=report,
            provenance=provenance,
            positions=positions_map,
        )

    @staticmethod
    def _apply_lag(predictions: Any, lag: int) -> Any:
        """Shift predictions to simulate signal-to-execution lag.

        ``lag=0`` is explicit same-day execution/no shift. Positive lag
        values delay each instrument's signal by exactly ``lag`` trading rows.
        """
        if lag == 0:
            _logger.info(
                "BacktestRunner: signal_to_execution_lag=0 -> no shift applied; "
                "same-day execution was requested explicitly."
            )
            return predictions
        import pandas as pd
        if isinstance(predictions, pd.Series) and isinstance(predictions.index, pd.MultiIndex):
            # MultiIndex (datetime, instrument): shift the datetime level
            df = predictions.unstack()
            df = df.shift(lag)
            return df.stack().dropna()
        return predictions

    @staticmethod
    def _build_provenance(
        request: CanonicalBacktestInput,
        topk: int,
        n_drop: int,
    ) -> Mapping[str, Any]:
        """Build a provenance record covering the full request + strategy params.

        Previously only ``topk`` and ``n_drop`` were captured, which meant
        any new strategy hyperparameter (selection filter, risk constraints,
        etc.) added later would silently escape the fingerprint. We now hash
        the complete serialised ``request`` together with the strategy params
        so the fingerprint reflects every input that can affect results.
        """
        # Strategy params not captured by CanonicalBacktestInput.
        strategy_dict = {"topk": topk, "n_drop": n_drop}
        # Full request serialised via dataclass asdict — captures every field
        # including nested cost model and exchange config.
        request_dict = asdict(request)
        config_dict = {
            "request": request_dict,
            "strategy": strategy_dict,
        }
        config_json = json.dumps(config_dict, sort_keys=True, default=str)
        fingerprint = hashlib.sha256(config_json.encode()).hexdigest()[:16]
        return {
            "config": {**request_dict, **strategy_dict},  # flat for human readability
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

    If neither shape matches or ``to_dict`` itself raises, a
    ``BacktestRunnerError`` is raised. The previous implementation
    swallowed every exception into ``{"raw": str(df)}``, which then
    flowed downstream as *missing* metrics that callers like
    ``WalkForwardEngine`` coerced to 0.0 — a silent regression path
    for any future qlib shape change.
    """
    try:
        raw = df.to_dict()
    except Exception as exc:
        raise BacktestRunnerError(
            f"risk_analysis.to_dict() failed ({type(exc).__name__}: {exc}). "
            "qlib risk_analysis output shape may have changed; downstream "
            "metric extraction cannot proceed."
        ) from exc

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


def _series_to_dict(series: Any, *, name: str = "series") -> dict:
    """Convert a pandas-like Series to ``{date_str: float}``.

    Unknown qlib output shapes are boundary failures. Returning a raw string
    envelope would make ``CanonicalBacktestOutput.return_series`` no longer a
    structured return series while allowing downstream consumers to fail later.
    """
    if not hasattr(series, "items"):
        raise BacktestRunnerError(
            f"return_series[{name!r}] must expose .items(); got "
            f"{type(series).__name__}. qlib report output shape may have changed."
        )
    try:
        return {str(k.date()) if hasattr(k, "date") else str(k): float(v) for k, v in series.items()}
    except Exception as exc:
        raise BacktestRunnerError(
            f"Failed to serialize return_series[{name!r}] "
            f"({type(exc).__name__}: {exc}). qlib report output shape may "
            "have changed; refusing to emit an unstructured raw fallback."
        ) from exc


def _positions_to_weight_map(positions_normal: Any) -> dict:
    """Serialize qlib positions into ``{date_str: {instrument: weight}}``.

    qlib's ``positions_normal`` comes out of ``generate_portfolio_metrics`` as
    either a ``pd.Series`` indexed by timestamp whose values are ``Position``
    objects, or a plain ``dict`` with the same shape. Either way, each
    ``Position`` exposes ``position`` — a dict of ``{instrument: {amount,
    price, weight, ...}}`` plus bookkeeping keys like ``"cash"`` and
    ``"now_account_value"``.

    Error handling
    --------------
    The previous implementation wrapped the whole function in a catch-all
    that returned ``{}`` on *any* failure. Downstream (``pipeline.py``)
    then silently coerced an empty positions map to ``None``, which made
    ``PerformanceAttribution`` switch from "real-portfolio attribution"
    to a prediction-score fallback — a semantically-different run under
    the same metric name. That conflicts with the repo's "no implicit
    fallback" governance rule.

    The new contract:

    * ``None`` input → ``{}`` — this is qlib's legitimate "no positions
      generated" signal (e.g. backtest was configured without
      ``generate_portfolio_metrics=True``).
    * Non-``None`` input that *cannot be iterated* (no ``.items()`` or it
      raises) → raise ``BacktestRunnerError``. This is an upstream
      contract violation and must surface immediately.
    * Per-day rows whose shape is malformed (e.g. ``position`` isn't a
      dict) → logged at WARNING with date context and skipped; they do
      not abort the whole map but they also cannot be silently dropped.
    * Per-instrument entries with unusable weights → logged at DEBUG
      and skipped (these are common across qlib version differences).
    """
    if positions_normal is None:
        return {}

    # Outer contract: must be iterable.  The previous catch-all let
    # non-iterable inputs (e.g. ints, strings) quietly disappear.
    if not hasattr(positions_normal, "items"):
        raise BacktestRunnerError(
            "positions_to_weight_map: input is not iterable "
            f"(got {type(positions_normal).__name__}). qlib "
            "positions_normal must be a pd.Series or dict; receiving a "
            "different type indicates an upstream contract violation."
        )
    try:
        items = list(positions_normal.items())
    except Exception as exc:
        raise BacktestRunnerError(
            f"positions_to_weight_map: failed to iterate positions "
            f"({type(exc).__name__}: {exc}). qlib positions_normal shape "
            "may have changed; refusing to silently return empty map."
        ) from exc

    result: dict[str, dict[str, float]] = {}
    bookkeeping_keys = {"cash", "now_account_value"}
    skipped_days = 0

    for ts, pos in items:
        try:
            date_str = str(ts.date()) if hasattr(ts, "date") else str(ts)
            raw = getattr(pos, "position", pos)
            if not isinstance(raw, dict):
                _logger.warning(
                    "positions_to_weight_map: day %s has non-dict "
                    "position payload (%s); skipping.",
                    date_str, type(raw).__name__,
                )
                skipped_days += 1
                continue

            # Compute total value for fallback weighting
            total_value: float = 0.0
            for inst, info in raw.items():
                if inst in bookkeeping_keys or not isinstance(info, dict):
                    continue
                amt = float(info.get("amount", 0.0) or 0.0)
                price = float(info.get("price", 0.0) or 0.0)
                total_value += amt * price
            # Include cash in denominator so weights reflect NAV share
            cash = raw.get("cash")
            if isinstance(cash, (int, float)):
                total_value += float(cash)

            day_weights: dict[str, float] = {}
            for inst, info in raw.items():
                if inst in bookkeeping_keys or not isinstance(info, dict):
                    continue
                w = info.get("weight")
                if w is None and total_value > 0:
                    amt = float(info.get("amount", 0.0) or 0.0)
                    price = float(info.get("price", 0.0) or 0.0)
                    w = (amt * price) / total_value
                if w is None:
                    continue
                try:
                    day_weights[str(inst)] = float(w)
                except (TypeError, ValueError):
                    # Individual entry coerce failure — common across qlib
                    # versions; log at DEBUG so noise stays low.
                    _logger.debug(
                        "positions_to_weight_map: day %s inst %s: weight "
                        "%r is not coercible to float; skipping entry.",
                        date_str, inst, w,
                    )
                    continue

            if day_weights:
                result[date_str] = day_weights
        except Exception as exc:
            # Per-day robustness: do NOT silently continue — surface the
            # exception class and date so the caller can tell how much
            # data was actually captured.
            _logger.warning(
                "positions_to_weight_map: failed to parse day %s (%s: %s); "
                "skipping.",
                ts, type(exc).__name__, exc,
            )
            skipped_days += 1
            continue

    if skipped_days:
        _logger.warning(
            "positions_to_weight_map: %d of %d days were skipped due to "
            "malformed entries; downstream attribution based on this map "
            "will cover only %d days.",
            skipped_days, len(items), len(result),
        )
    return result
