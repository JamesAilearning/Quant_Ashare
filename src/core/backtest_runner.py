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
import math
from collections.abc import Mapping
from dataclasses import asdict
from datetime import date
from typing import Any

from src.core.canonical_backtest_contract import (
    CANONICAL_OFFICIAL_BACKTEST_PATH,
    CANONICAL_OFFICIAL_METRIC_HELPER_CALLABLE,
    CANONICAL_OFFICIAL_METRIC_HELPER_PATH,
    OFFICIAL_METRIC_STATUS,
    CanonicalBacktestContract,
    CanonicalBacktestInput,
    CanonicalBacktestOutput,
    compute_effective_stamp_tax_bps,
)
from src.core.logger import get_logger
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
        compute_baselines: bool = True,
        pit_provider: Any | None = None,
    ) -> CanonicalBacktestOutput:
        # validate_input() enforces benchmark_code is non-empty as of the
        # contract level — no redundant check needed here.
        CanonicalBacktestContract.validate_input(request)

        if predictions is None or (hasattr(predictions, "empty") and predictions.empty):
            raise BacktestRunnerError("predictions must be non-empty.")

        # ``WalkForwardConfig`` and ``PipelineConfig`` already reject
        # ``n_drop >= topk`` at __post_init__ time, but ``BacktestRunner.run``
        # is also a public entry point used directly from research scripts.
        # Defence-in-depth: refuse the degenerate combination here so a
        # caller bypassing the config layer still gets a loud error
        # instead of a zero-position backtest that returns "valid"
        # all-zero metrics.
        if not isinstance(topk, int) or isinstance(topk, bool) or topk < 1:
            raise BacktestRunnerError(
                f"BacktestRunner.run: topk must be a positive int; got "
                f"{topk!r}."
            )
        if not isinstance(n_drop, int) or isinstance(n_drop, bool) or n_drop < 0:
            raise BacktestRunnerError(
                f"BacktestRunner.run: n_drop must be a non-negative int; "
                f"got {n_drop!r}."
            )
        if n_drop >= topk:
            raise BacktestRunnerError(
                f"BacktestRunner.run: n_drop ({n_drop}) must be strictly "
                f"less than topk ({topk}); otherwise TopkDropoutStrategy "
                "rotates out every name and the backtest returns silently "
                "with an empty portfolio."
            )

        if not is_canonical_qlib_initialized():
            raise BacktestRunnerError(
                "Canonical qlib runtime must be initialized via "
                "src.core.qlib_runtime.init_qlib_canonical(...) before "
                "running official backtests."
            )

        # Phase D.3 alignment guard: if the caller supplied a PIT
        # provider, the canonical qlib config's provider_uri MUST
        # match. Otherwise the backtest reads features through qlib
        # against a legacy provider while the operator believes PIT
        # mode is active — a silent survivorship bias on the most
        # consequential code path (real money decisions).
        if pit_provider is not None:
            cls._validate_pit_provider_alignment(pit_provider)

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
            from qlib.backtest import backtest as qlib_backtest
            from qlib.backtest.executor import SimulatorExecutor
            from qlib.contrib.strategy.signal_strategy import TopkDropoutStrategy
            from qlib.utils.time import Freq
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

        # Resolve the time-ordered stamp-tax schedule into the single
        # scalar that qlib's ``exchange_kwargs["close_cost"]``
        # accepts. The helper returns a trading-day-weighted average
        # when the backtest period crosses one or more rate
        # transitions (e.g. the 2023-08-28 CN reform 10→5 bps), AND
        # the list of transitions actually crossed. We WARN once per
        # run when crossings happen so the operator can decide
        # whether the weighted scalar is good enough or whether they
        # need to split the backtest at the transition.
        #
        # The qlib calendar IS passed so the weighting matches what
        # qlib's executor actually charges per sell. Without the
        # calendar the helper falls back to calendar-day weighting,
        # which produces a different scalar when holidays cluster
        # asymmetrically around a transition (e.g. CN long-holiday
        # in October before a hypothetical reform on Oct 15 would
        # over-weight the post-reform side because it skips the
        # holiday days on the pre-reform side). Codex P2 follow-up
        # on PR #178.
        #
        # Audit P0-4 + add-stamp-tax-schedule.
        period_start = date.fromisoformat(request.evaluation_start)
        period_end = date.fromisoformat(request.evaluation_end)
        try:
            from qlib.data import D as _qlib_D
            trading_calendar_ts = _qlib_D.calendar(
                start_time=request.evaluation_start,
                end_time=request.evaluation_end,
            )
            trading_calendar: list[date] | None = [
                ts.date() if hasattr(ts, "date") else date.fromisoformat(str(ts)[:10])
                for ts in trading_calendar_ts
            ]
        except Exception as exc:  # pragma: no cover - best-effort calendar fetch
            # If qlib.calendar fails (shouldn't, since we already
            # verified canonical init above), fall back to
            # calendar-day weighting. The WARN below documents this.
            _logger.warning(
                "BacktestRunner: failed to fetch qlib trading "
                "calendar for stamp-tax weighting (%s: %s); falling "
                "back to calendar-day weighting which may produce a "
                "slightly different scalar than the per-sell rate.",
                type(exc).__name__, exc,
            )
            trading_calendar = None
        effective_stamp_tax = compute_effective_stamp_tax_bps(
            cost.stamp_tax_schedule,
            period_start,
            period_end,
            calendar=trading_calendar,
        )
        if effective_stamp_tax.transitions:
            schedule_repr = ", ".join(
                f"{entry.effective_from.isoformat()}={entry.bps}bps"
                for entry in cost.stamp_tax_schedule
            )
            crossed_repr = ", ".join(
                entry.effective_from.isoformat()
                for entry in effective_stamp_tax.transitions
            )
            _logger.warning(
                "BacktestRunner: backtest period %s → %s crosses "
                "stamp-tax transition(s) at %s. Using trading-day-"
                "weighted scalar %.4f bps; the actual per-day cost is "
                "time-varying (full schedule: %s). To get exact "
                "per-segment costs, split the backtest at the "
                "transition(s) and reconcile the per-segment "
                "outputs externally. Audit P0-4.",
                request.evaluation_start, request.evaluation_end,
                crossed_repr, effective_stamp_tax.bps, schedule_repr,
            )
        stamp_tax_fraction = effective_stamp_tax.bps / 10000.0
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

        # Extract risk analysis. Use the *configured* exchange frequency
        # so qlib's annualisation factor matches the underlying data
        # cadence. The previous hardcoded ``freq="day"`` would have
        # over-stated annualised return / Sharpe by 2-4× if a future
        # caller ever ran an hourly or minute-level backtest.
        try:
            excess_return_without_cost = risk_analysis(
                report_normal["return"] - report_normal["bench"],
                freq=request.exchange_config.freq,
            )
            excess_return_with_cost = risk_analysis(
                report_normal["return"] - report_normal["bench"] - report_normal["cost"],
                freq=request.exchange_config.freq,
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

        # Compute equal-weight top-k baseline post-hoc from predictions +
        # qlib close prices. Avoids a second full backtest run (~50%
        # overhead) by using the same position set with 1/topk weights.
        if compute_baselines:
            try:
                eqw_returns = cls._compute_equalweight_baseline(
                    predictions=shifted_predictions,
                    topk=topk,
                    evaluation_start=request.evaluation_start,
                    evaluation_end=request.evaluation_end,
                    pit_provider=pit_provider,
                )
                if eqw_returns:
                    return_series["equalweight_topk"] = eqw_returns
            except BacktestRunnerError as exc:
                _logger.warning(
                    "Equal-weight baseline skipped: %s. Strategy "
                    "backtest and risk_analysis remain valid.",
                    exc,
                )

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
    def _compute_equalweight_baseline(
        predictions: Any,
        topk: int,
        evaluation_start: str,
        evaluation_end: str,
        pit_provider: Any | None = None,
    ) -> dict[str, float]:
        """Post-hoc equal-weight top-k daily return series.

        Replaces ``n_drop>0`` rotation with a static buy-and-hold of the
        prediction-ranked top-k — same universe, same ranking, same
        rebalance dates, but equal-weight and no dropout. This provides
        the "does alpha come from model rotation or from top-k selection?"
        decomposition.

        The computation uses qlib close prices fetched once for the full
        evaluation window. Each rebalance day picks the ``topk``
        highest-scored instruments; the day's equal-weight return is the
        arithmetic mean of those instruments' close-to-close returns.

        When ``pit_provider`` is supplied, the close-panel fetch routes
        through ``PITDataProvider.get_features`` so post-delist positions
        return NaN (mask applied at the query layer per §4.3.2), and the
        per-day equal-weight mean correctly excludes delisted tickers
        instead of silently consuming forward-filled stale values.
        Phase D.3 opt-in; default ``None`` falls through to direct
        ``qlib.data.D.features`` preserving the legacy behaviour.
        """
        try:
            import numpy as np
            import pandas as pd
        except ImportError as exc:
            raise BacktestRunnerError(
                "numpy / pandas not importable; cannot compute baseline."
            ) from exc

        if not isinstance(predictions, pd.Series) or predictions.empty:
            raise BacktestRunnerError(
                "predictions must be a non-empty pd.Series for "
                "equal-weight baseline computation."
            )
        if not isinstance(predictions.index, pd.MultiIndex):
            raise BacktestRunnerError(
                "predictions must have a (datetime, instrument) MultiIndex "
                "for equal-weight baseline computation."
            )

        # Extract per-date top-k instrument sets.
        daily_topk: dict[pd.Timestamp, set[str]] = {}
        for dt, group in predictions.groupby(level=0):
            top = group.nlargest(topk)
            daily_topk[dt] = set(top.index.get_level_values(1))

        if not daily_topk:
            raise BacktestRunnerError(
                "No daily top-k instruments could be extracted from "
                "predictions for equal-weight baseline."
            )

        all_instruments = sorted(
            {inst for names in daily_topk.values() for inst in names}
        )
        try:
            if pit_provider is not None:
                close = pit_provider.get_features(
                    ["$close"], evaluation_start, evaluation_end,
                    instruments=all_instruments,
                )
            else:
                # Legacy non-PIT path. Documented in the function
                # docstring as "Phase D.3 opt-in; default None falls
                # through to direct D.features preserving the legacy
                # behaviour". Audit P0-6 added the WARN below so the
                # bypass is observable — the silent fallthrough used
                # to leave operators unable to tell whether a backtest
                # had post-delist values leaking through window
                # operators (qlib's default min_periods does not mask
                # past delist_date — see ``src/pit/query.py``
                # docstring §4.3.2). When you want the §4.3.2 mask,
                # pass a configured ``PITDataProvider`` instance.
                # TODO(P0-6 follow-up): thread pit_provider through
                # ``Pipeline`` / ``WalkForwardEngine`` so this legacy
                # branch can be removed once all production callers
                # opt in.
                _logger.warning(
                    "BacktestRunner._compute_equalweight_baseline: "
                    "pit_provider is None — falling back to direct "
                    "qlib.data.D.features. The §4.3.2 post-delist "
                    "mask is NOT applied; window-operator outputs "
                    "for delisted tickers may leak across the "
                    "delist_date boundary. Pass a PITDataProvider "
                    "to opt into the mask. Audit P0-6."
                )
                from qlib.data import D
                close = D.features(
                    all_instruments,
                    ["$close"],
                    start_time=evaluation_start,
                    end_time=evaluation_end,
                    freq="day",
                )
        except Exception as exc:
            raise BacktestRunnerError(
                "qlib D.features() failed for equal-weight baseline: "
                f"{exc}"
            ) from exc

        if close is None or close.empty:
            raise BacktestRunnerError(
                "qlib returned no close prices for equal-weight baseline."
            )

        # Compute daily return per instrument, shifted forward by one
        # day so that a position taken at dt close earns the dt→dt+1
        # return (ret_matrix[dt+1]), not the dt-1→dt return that was
        # already priced in before rebalancing.
        close_unstacked = close.unstack(level="instrument")["$close"]
        close_unstacked = close_unstacked.sort_index()
        ret_matrix = close_unstacked.pct_change().shift(-1)
        # The last row has no next-day return; drop it.
        ret_matrix = ret_matrix.iloc[:-1].dropna(how="all")

        result: dict[str, float] = {}
        for dt, instruments in daily_topk.items():
            if dt not in ret_matrix.index:
                continue
            row = ret_matrix.loc[dt]
            valid = [row.get(inst) for inst in instruments if inst in row]
            if not valid or any(
                v is None or (isinstance(v, float) and np.isnan(v))
                for v in valid
            ):
                continue
            result[str(dt.date())] = float(np.nanmean(valid))

        return result

    @classmethod
    def _validate_pit_provider_alignment(cls, pit_provider: Any) -> None:
        """Phase D.3 alignment guard — identical contract to Phase D.2's
        :meth:`src.data.feature_dataset_builder.FeatureDatasetBuilder
        ._validate_pit_provider_alignment`. When a PIT provider is
        supplied, the canonical qlib runtime's ``provider_uri`` MUST
        match. The duplication is intentional: backtest_runner and
        feature_dataset_builder enforce the same invariant from two
        independent entry points, so a future refactor changing one
        cannot silently weaken the other.
        """
        from src.core.qlib_runtime import _normalize_provider_uri

        canonical = get_canonical_qlib_config()
        if canonical is None:
            raise BacktestRunnerError(
                "pit_provider was supplied but the canonical qlib config "
                "is unavailable despite is_canonical_qlib_initialized() "
                "returning True. Internal inconsistency — investigate "
                "qlib_runtime state."
            )
        pit_uri_raw = str(getattr(pit_provider, "_provider_uri", ""))
        if not pit_uri_raw:
            raise BacktestRunnerError(
                "pit_provider has no readable _provider_uri attribute "
                f"(got {pit_provider!r}). Expected a PITDataProvider."
            )
        pit_norm = _normalize_provider_uri(pit_uri_raw)
        if canonical.provider_uri != pit_norm:
            raise BacktestRunnerError(
                "PIT provider / qlib provider_uri mismatch — backtest "
                "would silently consume features from the wrong provider. "
                f"qlib canonical provider_uri = {canonical.provider_uri!r}; "
                f"pit_provider._provider_uri = {pit_norm!r}. "
                "Re-init qlib with the PIT-corrected provider before "
                "passing pit_provider to BacktestRunner.run()."
            )

    @staticmethod
    def _apply_lag(predictions: Any, lag: int) -> Any:
        """Shift predictions so qlib's ``TopkDropoutStrategy`` consumes
        them on the appropriate trading day.

        Semantics
        ---------
        Predictions arrive indexed by *signal date* — the day the model
        was given to score the universe. qlib's ``TopkDropoutStrategy``
        rebalances on whatever date it sees a signal for, so to encode
        a "signal at T → trade at T+lag" delay we shift the prediction
        *date stamps* forward by ``lag`` rows::

            # before shift:  index date = T  (signal date)
            # after shift:   index date = T+lag  (date strategy sees it)

        That makes ``TopkDropoutStrategy`` consume the T-day signal on
        T+lag and rebalance accordingly. Returning ``df.shift(lag)`` is
        therefore correct under the standard qlib framing — the comment
        is the precise version of "T+1 execution".

        ``lag=0`` is the explicit same-day-execution opt-in: the
        prediction's date stamp is unchanged, the strategy rebalances
        the same day. Positive ``lag`` values delay each prediction by
        exactly that many index rows (which equal trading rows when
        the index is a trading-day calendar — qlib unstacks by date so
        ``shift(lag)`` is row-wise on those dates). Negative ``lag`` is
        rejected upstream by ``PipelineConfig.__post_init__``.
        """
        # Validate predictions shape *before* the lag=0 short-circuit so
        # the same-day-execution path cannot bypass the structural
        # contract. The previous implementation skipped validation
        # whenever ``lag == 0`` — which meant a research script feeding
        # a wrong-shape Series or DataFrame to ``signal_to_execution_lag=0``
        # would still produce official metrics from qlib without any
        # complaint here, while ``lag>=1`` callers got a loud error.
        # Validate uniformly.
        import pandas as pd
        if not isinstance(predictions, pd.Series):
            raise BacktestRunnerError(
                "BacktestRunner._apply_lag: predictions must be a pandas "
                f"Series with (datetime, instrument) MultiIndex; got "
                f"{type(predictions).__name__}. Refusing to forward to "
                "qlib silently."
            )
        if not isinstance(predictions.index, pd.MultiIndex):
            raise BacktestRunnerError(
                "BacktestRunner._apply_lag: predictions Series must carry a "
                "(datetime, instrument) MultiIndex; got "
                f"{type(predictions.index).__name__}. Refusing to forward "
                "to qlib silently."
            )
        # Names matter: qlib's ``TopkDropoutStrategy`` and the unstack
        # path below access levels by *name*, so an
        # ``(instrument, datetime)``-ordered MultiIndex would silently
        # feed instruments to the date axis. Pin it.
        expected_names = ("datetime", "instrument")
        if tuple(predictions.index.names) != expected_names:
            raise BacktestRunnerError(
                "BacktestRunner._apply_lag: predictions index names must be "
                f"{expected_names}; got {tuple(predictions.index.names)!r}. "
                "Refusing to forward to qlib silently."
            )
        if not predictions.index.is_unique:
            raise BacktestRunnerError(
                "BacktestRunner._apply_lag: predictions index must be unique "
                "before unstack/lag. Duplicate (datetime, instrument) rows "
                "would make pandas raise ValueError deep in unstack and leave "
                "the official backtest boundary ambiguous."
            )

        if lag == 0:
            _logger.info(
                "BacktestRunner: signal_to_execution_lag=0 -> no shift applied; "
                "same-day execution was requested explicitly."
            )
            return predictions
        # MultiIndex (datetime, instrument): shift the datetime level.
        # ``unstack()`` pivots instrument to columns so ``shift(lag)``
        # advances every instrument's date stamps by the same number
        # of rows; ``stack().dropna()`` drops the leading rows that
        # now have no source.
        df = predictions.unstack()
        df = df.shift(lag)
        return df.stack().dropna()

    @staticmethod
    def _build_provenance(
        request: CanonicalBacktestInput,
        topk: int,
        n_drop: int,
    ) -> Mapping[str, Any]:
        """Build a provenance record covering the full request + strategy
        params *plus* the qlib runtime config the metrics depend on.

        Previously only ``topk`` and ``n_drop`` were captured, then the
        full request — but the same ``predictions_ref`` evaluated against
        a different qlib provider (different ``provider_uri`` / ``region``
        / ``data_adjust_mode``) would yield different official metrics
        and the fingerprint stayed identical, so a downstream comparison
        tool diff'ing two run reports could not tell the difference
        between "true regression" and "switched data bundle".

        We now also hash the live qlib runtime config — the
        ``runtime.data_adjust_mode`` / ``runtime.provider_uri`` /
        ``runtime.region`` triple — into the same JSON blob so the
        fingerprint changes whenever any of those change.
        """
        # Strategy params not captured by CanonicalBacktestInput.
        strategy_dict = {"topk": topk, "n_drop": n_drop}
        # Full request serialised via dataclass asdict — captures every field
        # including nested cost model and exchange config.
        request_dict = asdict(request)
        # qlib runtime config snapshot. ``run`` already verified the
        # runtime is initialised and that ``request.adjust_mode`` matches
        # ``runtime.data_adjust_mode``, so a non-None config is the
        # expected path. We tolerate ``None`` defensively rather than
        # crashing the provenance step — the metrics themselves would
        # have already failed earlier in that case.
        runtime_config = get_canonical_qlib_config()
        runtime_dict: dict[str, Any] = (
            {
                "provider_uri": runtime_config.provider_uri,
                "region": runtime_config.region,
                "data_adjust_mode": runtime_config.data_adjust_mode,
            }
            if runtime_config is not None
            else {}
        )
        config_dict = {
            "request": request_dict,
            "strategy": strategy_dict,
            "runtime": runtime_dict,
        }
        config_json = json.dumps(config_dict, sort_keys=True, default=str)
        fingerprint = hashlib.sha256(config_json.encode()).hexdigest()[:16]
        return {
            # Flat surface for human readability; includes runtime so a
            # diff between two runs can also see provider / region /
            # adjust_mode side by side without re-deriving from the
            # fingerprint alone.
            "config": {**request_dict, **strategy_dict, "runtime": runtime_dict},
            "config_fingerprint": fingerprint,
            "official_backtest_path": CANONICAL_OFFICIAL_BACKTEST_PATH,
        }


def _risk_analysis_to_flat_dict(df: Any) -> dict[str, Any]:
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
    flat: dict[str, Any] = {}
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


def _series_to_dict(series: Any, *, name: str = "series") -> dict[str, float]:
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


def _positions_to_weight_map(positions_normal: Any) -> dict[str, dict[str, float]]:
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

    Cash inclusion in the denominator
    ---------------------------------
    The total denominator includes ``raw["cash"]`` alongside the
    instruments' market value. This means per-instrument weights sum
    to ``< 1`` whenever the portfolio holds any cash — they reflect
    *NAV share*, not *equity share*.

    Downstream consequence in Brinson attribution: the sector
    decomposition (``allocation + selection + interaction``) only
    covers the equity portion, so its sum will not exactly match
    ``total_excess_return`` whenever cash > 0. That gap is the
    ``reconciliation_residual`` already surfaced on
    :class:`AttributionResult`; the
    :func:`PerformanceAttribution.print_report` method emits a
    WARNING when ``|residual| > RECONCILIATION_WARN_THRESHOLD`` so
    the gap is visible. We deliberately do *not* renormalise the
    weights to sum to 1 here — that would hide the cash position from
    NAV-aware consumers (turnover analysis, position-size limits,
    risk budgets) which is a much costlier silent change than the
    Brinson residual.
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

    def _finite_float(value: Any) -> float | None:
        if value is None:
            return None
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        return numeric if math.isfinite(numeric) else None

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
                amt = _finite_float(info.get("amount")) or 0.0
                price = _finite_float(info.get("price")) or 0.0
                total_value += amt * price
            # Include cash in denominator so weights reflect NAV share
            cash = _finite_float(raw.get("cash"))
            if cash is not None:
                total_value += cash

            day_weights: dict[str, float] = {}
            for inst, info in raw.items():
                if inst in bookkeeping_keys or not isinstance(info, dict):
                    continue
                w = info.get("weight")
                if w is None and total_value > 0:
                    amt = _finite_float(info.get("amount")) or 0.0
                    price = _finite_float(info.get("price")) or 0.0
                    w = (amt * price) / total_value
                if w is None:
                    continue
                weight = _finite_float(w)
                if weight is None:
                    # Individual entry coerce failure — common across qlib
                    # versions; log at DEBUG so noise stays low.
                    _logger.debug(
                        "positions_to_weight_map: day %s inst %s: weight "
                        "%r is not finite/coercible to float; skipping entry.",
                        date_str, inst, w,
                    )
                    continue
                day_weights[str(inst)] = weight

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
