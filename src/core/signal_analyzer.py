"""Signal quality analyzer — IC, IR, IC decay, and turnover metrics.

Evaluates prediction quality by measuring rank correlation between model
predictions and realized forward returns. This module is independent of the
backtest runner and operates purely on prediction Series + price data.

Boundaries
----------
- Requires canonical qlib init (for fetching actual returns via qlib.data.D).
- Importing this module does NOT import qlib or pandas. Imports are lazy.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from src.core._ic_utils import MIN_IC_OBSERVATIONS_PER_LAG, compute_ic_for_group
from src.core.logger import get_logger
from src.core.qlib_runtime import is_canonical_qlib_initialized

_logger = get_logger(__name__)


class SignalAnalyzerError(RuntimeError):
    """Raised on structural misuse or computation failures."""


@dataclass(frozen=True)
class SignalAnalysisConfig:
    """Configuration for signal analysis."""

    # Return-window lengths in trading days. Each period's HEADLINE IC is
    # label-aligned (PR-C): the window starts at the NEXT session after the
    # signal stamp (T+1 → T+1+period), matching the Alpha158 label and the
    # canonical backtest's T+1 fill. The legacy stamp-day window
    # (T → T+period) survives only as the labelled secondary
    # ``mean_ic_stamp_day``.
    forward_periods: tuple[int, ...] = (1, 5, 10, 20)
    # IC method: "rank" (Spearman) or "normal" (Pearson)
    ic_method: str = "rank"
    # Whether to compute turnover analysis
    compute_turnover: bool = True
    # Top-k for turnover computation
    topk: int = 50


@dataclass(frozen=True)
class SignalAnalysisResult:
    """Result of signal quality analysis."""

    # Per-period IC stats: {period: {mean_ic, std_ic, ir, ic_positive_ratio,
    # num_days, convention, mean_ic_stamp_day}} — mean_ic/std_ic/ir are the
    # label-aligned (T+1-entry) convention; mean_ic_stamp_day is the legacy
    # stamp-day secondary; convention is the str tag naming the headline
    # window (hence Any values).
    ic_summary: Mapping[int, Mapping[str, Any]]
    # Daily IC series: {period: pd.Series indexed by date}
    ic_series: Mapping[int, Any]
    # IC decay curve: list of IC values at lag 0..max(forward_periods)
    ic_decay: Sequence[float]
    # Turnover stats (empty if compute_turnover=False)
    turnover_stats: Mapping[str, float] = field(default_factory=dict)


class SignalAnalyzer:
    """Computes IC/IR metrics for model predictions."""

    @classmethod
    def analyze(
        cls,
        predictions: Any,  # pd.Series with (datetime, instrument) MultiIndex
        config: SignalAnalysisConfig | None = None,
    ) -> SignalAnalysisResult:
        """Run signal quality analysis on predictions.

        Parameters
        ----------
        predictions : pd.Series
            Model predictions with (datetime, instrument) MultiIndex.
        config : SignalAnalysisConfig, optional
            Analysis configuration. Uses defaults if None.

        Returns
        -------
        SignalAnalysisResult
        """
        if not is_canonical_qlib_initialized():
            raise SignalAnalyzerError(
                "Canonical qlib runtime must be initialized before signal analysis."
            )

        if config is None:
            config = SignalAnalysisConfig()

        if config.ic_method not in ("rank", "normal"):
            raise SignalAnalyzerError(
                f"ic_method must be 'rank' or 'normal', got '{config.ic_method}'"
            )

        if not config.forward_periods:
            raise SignalAnalyzerError(
                "forward_periods must be a non-empty tuple of positive integers."
            )
        for _p in config.forward_periods:
            # bool is a subclass of int — reject it explicitly so that
            # forward_periods=(True,) doesn't silently resolve to (1,).
            if isinstance(_p, bool) or not isinstance(_p, int) or _p < 1:
                raise SignalAnalyzerError(
                    f"forward_periods values must be positive int; got {_p!r}"
                )

        import pandas as pd

        if not isinstance(predictions, pd.Series):
            raise SignalAnalyzerError(
                f"predictions must be pd.Series, got {type(predictions).__name__}"
            )

        if not isinstance(predictions.index, pd.MultiIndex):
            raise SignalAnalyzerError(
                "predictions must have (datetime, instrument) MultiIndex"
            )

        # Level *names* AND order matter. ``_fetch_returns`` reads
        # instruments via ``get_level_values(1)`` and dates via
        # ``get_level_values(0)`` — purely positional access, so a
        # ``(instrument, datetime)`` MultiIndex would feed dates to
        # qlib as instrument codes and silently produce nonsense (or
        # crash deep inside qlib with a misleading error). Likewise
        # ``_compute_daily_ic`` groups by position. Earlier we only
        # checked name *presence*; that left swapped-order indices
        # passing through. Lock the order down at the boundary so
        # downstream positional access is safe.
        expected_index_names = ("datetime", "instrument")
        actual_index_names = tuple(predictions.index.names)
        if actual_index_names != expected_index_names:
            raise SignalAnalyzerError(
                "predictions.index.names must be exactly "
                f"{expected_index_names!r}; got {actual_index_names!r}. "
                "Order matters: SignalAnalyzer reads dates from level 0 "
                "and instruments from level 1 by position when calling "
                "qlib.D.features. A swapped or unnamed index would feed "
                "the wrong values to the data layer."
            )

        if predictions.empty:
            raise SignalAnalyzerError("predictions Series is empty")

        # Fetch actual returns from qlib. ``+1`` covers the label-aligned
        # entry offset: the headline IC's return window starts at T+1, so
        # the longest horizon needs closes through T + max_period + 1.
        returns_data = cls._fetch_returns(predictions, max(config.forward_periods) + 1)

        # Compute IC for each forward period. HEADLINE convention (PR-C,
        # audit A3): label-aligned — corr(score_T, return over
        # T+1 → T+1+period), matching both the Alpha158 training label
        # (``Ref($close,-2)/Ref($close,-1)-1`` for period=1) and the
        # T+1-close fill of the canonical backtest. The pre-PR-C
        # stamp-day convention (corr(score_T, T → T+period)) measured a
        # window no strategy can earn — it is kept per period as the
        # explicitly-labelled secondary ``mean_ic_stamp_day``.
        ic_series_dict = {}
        ic_summary_dict = {}

        for period in config.forward_periods:
            daily_ic = cls._compute_daily_ic(
                predictions, returns_data, period, config.ic_method,
                entry_offset=1,
            )
            ic_series_dict[period] = daily_ic
            stamp_day_ic = cls._compute_daily_ic(
                predictions, returns_data, period, config.ic_method,
                entry_offset=0,
            )
            stamp_day_valid = stamp_day_ic.dropna()
            mean_ic_stamp_day = (
                float(stamp_day_valid.mean()) if len(stamp_day_valid) > 0
                else float("nan")
            )

            valid_ic = daily_ic.dropna()
            if len(valid_ic) > 0:
                mean_ic = float(valid_ic.mean())
                std_ic = float(valid_ic.std())
                # A zero std is "every day's IC is identical" — IR is
                # undefined in that case, not zero. 0.0 would tell Optuna
                # / walk-forward the hyperparams yielded a "flat zero-IR
                # model", which is structurally indistinguishable from a
                # genuinely mediocre model.
                ic_summary_dict[period] = {
                    "mean_ic": mean_ic,
                    "std_ic": std_ic,
                    "ir": (mean_ic / std_ic) if std_ic > 0 else float("nan"),
                    "ic_positive_ratio": float((valid_ic > 0).mean()),
                    "num_days": int(len(valid_ic)),
                    "convention": "label_aligned_t1_entry",
                    "mean_ic_stamp_day": mean_ic_stamp_day,
                }
            else:
                # No valid IC observations (data too short, all NaN, etc.).
                # Emit NaN — downstream Optuna treats NaN trials as failed
                # rather than "scored exactly zero", and walk-forward
                # aggregation drops NaN folds instead of averaging them in.
                ic_summary_dict[period] = {
                    "mean_ic": float("nan"),
                    "std_ic": float("nan"),
                    "ir": float("nan"),
                    "ic_positive_ratio": float("nan"),
                    "num_days": 0,
                    "convention": "label_aligned_t1_entry",
                    "mean_ic_stamp_day": mean_ic_stamp_day,
                }

        # Compute IC decay
        max_lag = max(config.forward_periods)
        ic_decay = cls._compute_ic_decay(predictions, returns_data, max_lag, config.ic_method)

        # Compute turnover
        turnover_stats: dict[str, float] = {}
        if config.compute_turnover:
            turnover_stats = cls._compute_turnover(predictions, config.topk)

        return SignalAnalysisResult(
            ic_summary=ic_summary_dict,
            ic_series=ic_series_dict,
            ic_decay=ic_decay,
            turnover_stats=turnover_stats,
        )

    @classmethod
    def _fetch_returns(cls, predictions: Any, max_period: int) -> Any:
        """Fetch close-to-close returns from qlib for all instruments in predictions.

        Returns a DataFrame with (datetime, instrument) MultiIndex and 'close' column.

        WARNING — non-PIT path
        ----------------------
        Unlike ``BacktestRunner._compute_equalweight_baseline`` and
        ``FactorAnalyzer._fetch_close_panel`` (both of which accept an
        optional ``pit_provider`` to route through the §4.3.2
        post-delist mask), this method has NO PIT opt-in yet. IC and
        IC-decay diagnostics derived from this panel may include
        stale / forward-filled closes for tickers delisted within the
        window. The WARN log below makes the bypass observable in
        every run log.

        TODO(P0-6 follow-up): add ``pit_provider`` parameter to
        ``SignalAnalyzer.analyze`` and thread it through here; until
        then, treat IC numbers for portfolios with mid-period
        delistings as approximate.
        """
        from qlib.data import D

        _logger.warning(
            "SignalAnalyzer._fetch_returns: bypasses PITDataProvider "
            "(no opt-in yet) — IC / IC-decay numbers may absorb "
            "stale / forward-filled closes for tickers delisted "
            "within the prediction window. See TODO above; audit "
            "P0-6."
        )

        # Read by name (not position) so an internal caller that bypasses
        # the public ``analyze`` boundary still gets correct data. The
        # public boundary already enforces ``(datetime, instrument)``
        # order, but defence in depth: by-name access is correct
        # regardless of how the index was constructed.
        instruments = predictions.index.get_level_values("instrument").unique().tolist()
        dates = predictions.index.get_level_values("datetime")
        start_date = dates.min()
        end_date = dates.max()

        # Fetch $close; extend end_date using the *trading* calendar so
        # long holidays (Spring Festival, NDG) don't starve us of forward
        # returns. Fallback: if the calendar lookup fails for any reason,
        # pad by 3× calendar days (safer than the old 2×).
        extended_end = cls._extend_end_trading_days(end_date, max_period)
        close_df = D.features(
            instruments,
            ["$close"],
            start_time=start_date,
            end_time=extended_end,
            freq="day",
        )
        close_df.columns = ["close"]
        # qlib returns (instrument, datetime) — swap to (datetime, instrument)
        close_df = close_df.swaplevel()
        close_df = close_df.sort_index()
        return close_df

    @staticmethod
    def _extend_end_trading_days(end_date: Any, max_period: int) -> Any:
        """Thin wrapper around :func:`src.data.trading_calendar.extend_end_by_trading_days`.

        Kept as a method so existing tests that patch this attribute on
        ``SignalAnalyzer`` continue to work; the actual logic now lives
        in the shared helper to eliminate the line-for-line duplicate
        previously held by ``FactorAnalyzer``.
        """
        from src.data.trading_calendar import extend_end_by_trading_days

        return extend_end_by_trading_days(
            end_date, max_period,
            logger=_logger, caller_name="SignalAnalyzer",
        )

    @classmethod
    def _compute_daily_ic(
        cls, predictions: Any, returns_data: Any, period: int, method: str,
        *, entry_offset: int = 1,
    ) -> Any:
        """Compute daily cross-sectional IC for a given forward period.

        ``entry_offset`` anchors the return window: with the default ``1``
        the IC at stamp T correlates scores against the
        ``T+1 → T+1+period`` close return — the label-aligned convention
        (entry at the next session's close, matching the Alpha158 label and
        the canonical backtest's T+1 fill). ``entry_offset=0`` reproduces
        the legacy stamp-day window ``T → T+period`` (un-earnable by any
        lag>=1 strategy; kept only for the explicitly-labelled secondary
        metric).
        """
        import pandas as pd

        # returns_data has (datetime, instrument) MultiIndex, 'close' column
        # Unstack instrument to get date x instrument matrix
        close = returns_data["close"].unstack(level="instrument")
        entry = close.shift(-entry_offset) if entry_offset else close
        forward_ret = close.shift(-(period + entry_offset)) / entry - 1
        # Stack back to (datetime, instrument)
        # pandas 2.1+: stack(dropna=...) is deprecated; future_stack=True
        # preserves NaN and is the forward-compatible API.
        forward_ret_stacked = forward_ret.stack(future_stack=True)
        forward_ret_stacked.name = "forward_ret"

        # Align predictions and forward returns. ``_validate`` already
        # locked the index to ``(datetime, instrument)`` so we no longer
        # rename the levels here — overwriting names would mask any
        # caller bug rather than letting the boundary check catch it.
        pred_df = predictions.to_frame("pred")

        merged = pred_df.join(forward_ret_stacked, how="inner")
        merged = merged.dropna()

        if merged.empty:
            return pd.Series(dtype=float)
        if len(merged) < MIN_IC_OBSERVATIONS_PER_LAG:
            return pd.Series(dtype=float)

        # Rename "forward_ret" → "ret" so compute_ic_for_group's column
        # detection ("pred" + "ret") works without branching.
        merged = merged.rename(columns={"forward_ret": "ret"})
        # Group by name, not position — same defence-in-depth as
        # ``_fetch_returns`` above.
        daily_ic = merged.groupby(level="datetime").apply(
            lambda g: compute_ic_for_group(g, method),
            include_groups=False,
        )
        daily_ic.name = f"IC_{period}d"
        return daily_ic

    @classmethod
    def _compute_ic_decay(
        cls, predictions: Any, returns_data: Any, max_lag: int, method: str
    ) -> list[float]:
        """Compute IC at each lag from 1 to max_lag (IC decay curve).

        Pre-computes the close price matrix once and derives all forward
        returns from it, avoiding redundant unstack/shift per lag.

        Convention note (PR-C): the decay curve stays anchored at the
        signal STAMP (cumulative T → T+lag return per point) — it is a
        research diagnostic of how fast predictive power dies, not an
        earnable-return metric, and re-anchoring every point at T+1 would
        only relabel the x-axis. The headline ``ic_summary`` is the
        label-aligned (T+1-entry) convention; do not compare the two
        numerically at lag 1.
        """

        close = returns_data["close"].unstack(level="instrument")
        # Index order/names already validated at the boundary; do not
        # overwrite ``pred_df.index.names`` here — see ``_compute_daily_ic``.
        pred_df = predictions.to_frame("pred")

        decay = []
        for lag in range(1, max_lag + 1):
            forward_ret = close.shift(-lag) / close - 1
            # pandas 2.1+: stack(dropna=...) is deprecated; future_stack=True
            # preserves NaN and is the forward-compatible API.
            forward_ret_stacked = forward_ret.stack(future_stack=True)
            forward_ret_stacked.name = "forward_ret"

            merged = pred_df.join(forward_ret_stacked, how="inner").dropna()
            # Missing data at a given lag is NOT "IC = 0". Emitting NaN
            # keeps the decay curve's shape honest — a flat zero bottom
            # (old behaviour) looked like "model predictive power dies at
            # lag N" when it was really "no observations at lag N".
            if merged.empty:
                decay.append(float("nan"))
                continue
            if len(merged) < MIN_IC_OBSERVATIONS_PER_LAG:
                decay.append(float("nan"))
                continue

            merged = merged.rename(columns={"forward_ret": "ret"})
            daily_ic = merged.groupby(level="datetime").apply(
                lambda g: compute_ic_for_group(g, method),
                include_groups=False,
            ).dropna()
            decay.append(
                float(daily_ic.mean()) if len(daily_ic) > 0 else float("nan")
            )
        return decay

    @classmethod
    def _compute_turnover(cls, predictions: Any, topk: int) -> dict[str, float]:
        """Compute daily portfolio turnover for top-k stocks."""
        import numpy as np

        # Get top-k instruments per day
        pred_unstacked = predictions.unstack()
        daily_topk_sets = []

        for date in pred_unstacked.index:
            row = pred_unstacked.loc[date].dropna()
            if len(row) >= topk:
                top = set(row.nlargest(topk).index.tolist())
            else:
                top = set(row.index.tolist())
            daily_topk_sets.append(top)

        if len(daily_topk_sets) < 2:
            return {"mean_turnover": 0.0, "std_turnover": 0.0}

        # Turnover = fraction of portfolio changed each day
        turnovers = []
        for i in range(1, len(daily_topk_sets)):
            prev = daily_topk_sets[i - 1]
            curr = daily_topk_sets[i]
            if len(curr) > 0:
                changed = len(curr - prev)
                turnovers.append(changed / len(curr))

        if not turnovers:
            return {"mean_turnover": 0.0, "std_turnover": 0.0}

        return {
            "mean_turnover": float(np.mean(turnovers)),
            "std_turnover": float(np.std(turnovers)),
        }

    @classmethod
    def print_report(cls, result: SignalAnalysisResult) -> None:
        """Log a formatted signal analysis report."""
        log = _logger.info
        log("=" * 60)
        log("SIGNAL QUALITY ANALYSIS REPORT")
        log("=" * 60)

        log("[IC Summary]")
        log(f"{'Period':>8} {'Mean IC':>10} {'Std IC':>10} {'IR':>8} {'IC>0%':>8} {'Days':>6}")
        log("-" * 56)
        for period, stats in sorted(result.ic_summary.items()):
            log(
                f"{period:>6}d "
                f"{stats['mean_ic']:>10.4f} "
                f"{stats['std_ic']:>10.4f} "
                f"{stats['ir']:>8.3f} "
                f"{stats['ic_positive_ratio']:>7.1%} "
                f"{stats['num_days']:>6}"
            )

        log("[IC Decay Curve]")
        log("Lag(d): " + " ".join(f"{i+1:>5}" for i in range(len(result.ic_decay))))
        log("IC:     " + " ".join(f"{v:>5.3f}" for v in result.ic_decay))

        if result.turnover_stats:
            log("[Turnover]")
            log(f"  Mean daily turnover: {result.turnover_stats['mean_turnover']:.2%}")
            log(f"  Std daily turnover:  {result.turnover_stats['std_turnover']:.2%}")

        log("=" * 60)
