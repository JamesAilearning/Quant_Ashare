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

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from src.core.qlib_runtime import is_canonical_qlib_initialized


class SignalAnalyzerError(RuntimeError):
    """Raised on structural misuse or computation failures."""


@dataclass(frozen=True)
class SignalAnalysisConfig:
    """Configuration for signal analysis."""

    # How many days forward to compute returns (default: 1-day forward)
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

    # Per-period IC stats: {period: {mean_ic, std_ic, ir, ic_positive_ratio, num_days}}
    ic_summary: Mapping[int, Mapping[str, float]]
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

        import pandas as pd
        import numpy as np

        if not isinstance(predictions, pd.Series):
            raise SignalAnalyzerError(
                f"predictions must be pd.Series, got {type(predictions).__name__}"
            )

        if not isinstance(predictions.index, pd.MultiIndex):
            raise SignalAnalyzerError(
                "predictions must have (datetime, instrument) MultiIndex"
            )

        if predictions.empty:
            raise SignalAnalyzerError("predictions Series is empty")

        # Fetch actual returns from qlib
        returns_data = cls._fetch_returns(predictions, max(config.forward_periods))

        # Compute IC for each forward period
        ic_series_dict = {}
        ic_summary_dict = {}

        for period in config.forward_periods:
            daily_ic = cls._compute_daily_ic(
                predictions, returns_data, period, config.ic_method
            )
            ic_series_dict[period] = daily_ic

            valid_ic = daily_ic.dropna()
            if len(valid_ic) > 0:
                ic_summary_dict[period] = {
                    "mean_ic": float(valid_ic.mean()),
                    "std_ic": float(valid_ic.std()),
                    "ir": float(valid_ic.mean() / valid_ic.std()) if valid_ic.std() > 0 else 0.0,
                    "ic_positive_ratio": float((valid_ic > 0).mean()),
                    "num_days": int(len(valid_ic)),
                }
            else:
                ic_summary_dict[period] = {
                    "mean_ic": 0.0, "std_ic": 0.0, "ir": 0.0,
                    "ic_positive_ratio": 0.0, "num_days": 0,
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
        """
        import pandas as pd
        from qlib.data import D  # type: ignore[import-not-found]

        instruments = predictions.index.get_level_values(1).unique().tolist()
        dates = predictions.index.get_level_values(0)
        start_date = dates.min()
        end_date = dates.max()

        # Fetch $close; extend end_date to cover forward returns
        close_df = D.features(
            instruments,
            ["$close"],
            start_time=start_date,
            end_time=pd.Timestamp(end_date) + pd.Timedelta(days=max_period * 2),
            freq="day",
        )
        close_df.columns = ["close"]
        # qlib returns (instrument, datetime) — swap to (datetime, instrument)
        close_df = close_df.swaplevel()
        close_df = close_df.sort_index()
        return close_df

    @classmethod
    def _compute_daily_ic(
        cls, predictions: Any, returns_data: Any, period: int, method: str
    ) -> Any:
        """Compute daily cross-sectional IC for a given forward period."""
        import pandas as pd
        import numpy as np

        # returns_data has (datetime, instrument) MultiIndex, 'close' column
        # Unstack instrument to get date x instrument matrix
        close = returns_data["close"].unstack(level="instrument")
        forward_ret = close.shift(-period) / close - 1
        # Stack back to (datetime, instrument)
        forward_ret_stacked = forward_ret.stack(dropna=False)
        forward_ret_stacked.name = "forward_ret"
        forward_ret_stacked.index.names = ["datetime", "instrument"]

        # Align predictions and forward returns
        pred_df = predictions.to_frame("pred")
        pred_df.index.names = ["datetime", "instrument"]

        merged = pred_df.join(forward_ret_stacked, how="inner")
        merged = merged.dropna()

        if merged.empty:
            return pd.Series(dtype=float)

        # Cross-sectional IC per day
        def _ic_func(group):
            if len(group) < 3:
                return np.nan
            if method == "rank":
                return group["pred"].rank().corr(group["forward_ret"].rank())
            else:
                return group["pred"].corr(group["forward_ret"])

        daily_ic = merged.groupby(level=0).apply(_ic_func)
        daily_ic.name = f"IC_{period}d"
        return daily_ic

    @classmethod
    def _compute_ic_decay(
        cls, predictions: Any, returns_data: Any, max_lag: int, method: str
    ) -> list[float]:
        """Compute IC at each lag from 1 to max_lag (IC decay curve).

        Pre-computes the close price matrix once and derives all forward
        returns from it, avoiding redundant unstack/shift per lag.
        """
        import pandas as pd
        import numpy as np

        close = returns_data["close"].unstack(level="instrument")
        pred_df = predictions.to_frame("pred")
        pred_df.index.names = ["datetime", "instrument"]

        decay = []
        for lag in range(1, max_lag + 1):
            forward_ret = close.shift(-lag) / close - 1
            forward_ret_stacked = forward_ret.stack(dropna=False)
            forward_ret_stacked.name = "forward_ret"
            forward_ret_stacked.index.names = ["datetime", "instrument"]

            merged = pred_df.join(forward_ret_stacked, how="inner").dropna()
            if merged.empty:
                decay.append(0.0)
                continue

            def _ic_func(group: Any) -> float:
                if len(group) < 3:
                    return np.nan
                if method == "rank":
                    return group["pred"].rank().corr(group["forward_ret"].rank())
                return group["pred"].corr(group["forward_ret"])

            daily_ic = merged.groupby(level=0).apply(_ic_func).dropna()
            decay.append(float(daily_ic.mean()) if len(daily_ic) > 0 else 0.0)
        return decay

    @classmethod
    def _compute_turnover(cls, predictions: Any, topk: int) -> dict[str, float]:
        """Compute daily portfolio turnover for top-k stocks."""
        import pandas as pd
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
        """Print a formatted signal analysis report to stdout."""
        print("\n" + "=" * 60)
        print("SIGNAL QUALITY ANALYSIS REPORT")
        print("=" * 60)

        print("\n[IC Summary]")
        print(f"{'Period':>8} {'Mean IC':>10} {'Std IC':>10} {'IR':>8} {'IC>0%':>8} {'Days':>6}")
        print("-" * 56)
        for period, stats in sorted(result.ic_summary.items()):
            print(
                f"{period:>6}d "
                f"{stats['mean_ic']:>10.4f} "
                f"{stats['std_ic']:>10.4f} "
                f"{stats['ir']:>8.3f} "
                f"{stats['ic_positive_ratio']:>7.1%} "
                f"{stats['num_days']:>6}"
            )

        print("\n[IC Decay Curve]")
        print("Lag(d): " + " ".join(f"{i+1:>5}" for i in range(len(result.ic_decay))))
        print("IC:     " + " ".join(f"{v:>5.3f}" for v in result.ic_decay))

        if result.turnover_stats:
            print("\n[Turnover]")
            print(f"  Mean daily turnover: {result.turnover_stats['mean_turnover']:.2%}")
            print(f"  Std daily turnover:  {result.turnover_stats['std_turnover']:.2%}")

        print("=" * 60)
