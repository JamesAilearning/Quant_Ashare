"""Factor-level analysis — per-factor IC/IR, correlation matrix, and decay comparison.

Unlike :mod:`signal_analyzer` which evaluates the *aggregate* model prediction,
this module decomposes prediction quality at the individual Alpha158 feature
level.  It answers: which factors carry signal, which are redundant, and how
quickly each factor's predictive power decays.

Boundaries
----------
- Requires canonical qlib init (for fetching price data and dataset features).
- Importing this module does NOT import qlib or pandas. Imports are lazy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from src.core.logger import get_logger
from src.core.qlib_runtime import is_canonical_qlib_initialized

_logger = get_logger(__name__)


class FactorAnalyzerError(RuntimeError):
    """Raised on factor analysis failures."""


@dataclass(frozen=True)
class FactorAnalysisConfig:
    """Configuration for factor-level analysis."""

    # Dataset params (reuse the same splits as the pipeline)
    instruments: str = "csi300"
    feature_handler: str = "Alpha158"
    test_start: str = "2025-07-01"
    test_end: str = "2025-12-31"

    # Analysis params
    forward_period: int = 5
    ic_method: str = "rank"  # "rank" (Spearman) or "normal" (Pearson)
    max_decay_lag: int = 20
    top_n_factors: int = 20  # how many top factors to include in results


@dataclass(frozen=True)
class FactorICStats:
    """IC statistics for a single factor."""

    factor_name: str
    mean_ic: float
    std_ic: float
    ir: float  # IC / std(IC)
    ic_positive_ratio: float
    num_days: int


@dataclass(frozen=True)
class FactorAnalysisResult:
    """Complete factor analysis result."""

    # Per-factor IC stats, sorted by |mean_ic| descending
    factor_ic_stats: tuple[FactorICStats, ...]

    # Correlation matrix of top factors (factor_name → {factor_name → corr})
    correlation_matrix: Mapping[str, Mapping[str, float]]

    # IC decay for top factors (factor_name → [ic_at_lag_1, ic_at_lag_2, ...])
    ic_decay: Mapping[str, Sequence[float]]

    # Total factors analyzed
    total_factors: int


class FactorAnalyzer:
    """Analyzes individual factor quality from Alpha158 features."""

    @classmethod
    def analyze(cls, config: FactorAnalysisConfig | None = None) -> FactorAnalysisResult:
        """Run complete factor analysis.

        Steps:
          1. Build test-period dataset to get per-factor values
          2. Fetch forward returns for the test period
          3. Compute per-factor cross-sectional IC
          4. Build correlation matrix of top factors
          5. Compute IC decay for top factors
        """
        if config is None:
            config = FactorAnalysisConfig()

        cls._validate(config)

        import numpy as np
        import pandas as pd

        _logger.info("Building factor dataset for %s ~ %s...", config.test_start, config.test_end)

        # Step 1: Get factor values
        factor_df = cls._get_factor_values(config)
        factor_names = list(factor_df.columns)
        _logger.info("Loaded %d factors, %d rows", len(factor_names), len(factor_df))

        # Step 2: Get forward returns
        _logger.info("Computing %d-day forward returns...", config.forward_period)
        forward_ret = cls._get_forward_returns(factor_df, config.forward_period)

        # Step 3: Per-factor IC
        _logger.info("Computing per-factor IC (%s)...", config.ic_method)
        all_stats = cls._compute_all_factor_ic(factor_df, forward_ret, config)

        # Sort by absolute mean IC
        all_stats.sort(key=lambda s: abs(s.mean_ic), reverse=True)

        # Step 4: Top factors for correlation and decay
        top_names = [s.factor_name for s in all_stats[: config.top_n_factors]]

        _logger.info("Computing correlation matrix for top %d factors...", len(top_names))
        corr_matrix = cls._compute_correlation_matrix(factor_df, top_names)

        _logger.info("Computing IC decay (max lag %d) for top factors...", config.max_decay_lag)
        decay = cls._compute_factor_decay(factor_df, forward_ret, top_names, config)

        return FactorAnalysisResult(
            factor_ic_stats=tuple(all_stats),
            correlation_matrix=corr_matrix,
            ic_decay=decay,
            total_factors=len(factor_names),
        )

    @classmethod
    def _validate(cls, config: FactorAnalysisConfig) -> None:
        if not is_canonical_qlib_initialized():
            raise FactorAnalyzerError(
                "Canonical qlib runtime is not initialized. "
                "Call src.core.qlib_runtime.init_qlib_canonical(...) first."
            )
        if config.forward_period < 1:
            raise FactorAnalyzerError("forward_period must be >= 1.")
        if config.ic_method not in ("rank", "normal"):
            raise FactorAnalyzerError("ic_method must be 'rank' or 'normal'.")

    @classmethod
    def _get_factor_values(cls, config: FactorAnalysisConfig) -> Any:
        """Load Alpha158 features for the test period as a DataFrame."""
        from qlib.contrib.data.handler import Alpha158
        from qlib.data.dataset import DatasetH

        handler = Alpha158(
            instruments=config.instruments,
            start_time=config.test_start,
            end_time=config.test_end,
        )
        dataset = DatasetH(
            handler=handler,
            segments={"test": [config.test_start, config.test_end]},
        )
        df = dataset.prepare("test", col_set="feature")
        return df

    @classmethod
    def _get_forward_returns(cls, factor_df: Any, period: int) -> Any:
        """Compute forward returns aligned with factor_df's index."""
        import pandas as pd
        from qlib.data import D

        instruments = factor_df.index.get_level_values("instrument").unique().tolist()
        start = str(factor_df.index.get_level_values("datetime").min().date())
        # Need extra days beyond end for forward return computation
        end_dt = factor_df.index.get_level_values("datetime").max()
        from datetime import timedelta
        end_extended = str((end_dt + timedelta(days=period * 2)).date())

        close = D.features(
            instruments, ["$close"], start_time=start, end_time=end_extended
        )
        close.columns = ["close"]

        # Unstack → compute forward return → stack back
        close_unstacked = close["close"].unstack(level="instrument")
        fwd = close_unstacked.shift(-period) / close_unstacked - 1
        fwd_stacked = fwd.stack(dropna=False)
        fwd_stacked.name = "forward_ret"
        fwd_stacked.index.names = ["datetime", "instrument"]

        # Align with factor_df index
        aligned = fwd_stacked.reindex(factor_df.index)
        return aligned

    @classmethod
    def _compute_all_factor_ic(
        cls, factor_df: Any, forward_ret: Any, config: FactorAnalysisConfig
    ) -> list[FactorICStats]:
        """Compute IC stats for every factor column."""
        import numpy as np
        import pandas as pd

        results = []
        for col in factor_df.columns:
            factor_col = factor_df[col]
            merged = pd.DataFrame({"factor": factor_col, "ret": forward_ret}).dropna()

            if len(merged) < 10:
                continue

            # Cross-sectional IC per day
            def _ic_func(group: Any) -> float:
                if len(group) < 3:
                    return np.nan
                if config.ic_method == "rank":
                    return group["factor"].rank().corr(group["ret"].rank())
                return group["factor"].corr(group["ret"])

            daily_ic = merged.groupby(level="datetime").apply(_ic_func).dropna()

            if len(daily_ic) == 0:
                continue

            mean_ic = float(daily_ic.mean())
            std_ic = float(daily_ic.std()) if len(daily_ic) > 1 else 0.0
            ir = mean_ic / std_ic if std_ic > 1e-9 else 0.0

            results.append(FactorICStats(
                factor_name=str(col) if not isinstance(col, tuple) else str(col[-1]),
                mean_ic=mean_ic,
                std_ic=std_ic,
                ir=ir,
                ic_positive_ratio=float((daily_ic > 0).mean()),
                num_days=len(daily_ic),
            ))

        return results

    @classmethod
    def _compute_correlation_matrix(
        cls, factor_df: Any, factor_names: list[str]
    ) -> dict[str, dict[str, float]]:
        """Compute pairwise rank correlation between top factors."""
        import pandas as pd

        # Alpha158 columns may be tuples like ('KLEN',) — match by last element
        col_map = {}
        for col in factor_df.columns:
            name = str(col) if not isinstance(col, tuple) else str(col[-1])
            if name in factor_names:
                col_map[name] = col

        subset = factor_df[[col_map[n] for n in factor_names if n in col_map]]
        subset.columns = [n for n in factor_names if n in col_map]

        corr = subset.corr(method="spearman")

        result: dict[str, dict[str, float]] = {}
        for row_name in corr.index:
            result[str(row_name)] = {
                str(col_name): round(float(corr.loc[row_name, col_name]), 4)
                for col_name in corr.columns
            }
        return result

    @classmethod
    def _compute_factor_decay(
        cls, factor_df: Any, forward_ret: Any, factor_names: list[str],
        config: FactorAnalysisConfig,
    ) -> dict[str, list[float]]:
        """Compute IC decay for each top factor across lags 1..max_decay_lag."""
        import numpy as np
        import pandas as pd
        from qlib.data import D
        from datetime import timedelta

        instruments = factor_df.index.get_level_values("instrument").unique().tolist()
        start = str(factor_df.index.get_level_values("datetime").min().date())
        end_dt = factor_df.index.get_level_values("datetime").max()
        end_extended = str((end_dt + timedelta(days=config.max_decay_lag * 2)).date())

        close = D.features(instruments, ["$close"], start_time=start, end_time=end_extended)
        close.columns = ["close"]
        close_unstacked = close["close"].unstack(level="instrument")

        # Build column map
        col_map = {}
        for col in factor_df.columns:
            name = str(col) if not isinstance(col, tuple) else str(col[-1])
            if name in factor_names:
                col_map[name] = col

        result: dict[str, list[float]] = {}

        for fname in factor_names:
            if fname not in col_map:
                continue
            factor_col = factor_df[col_map[fname]]
            decay_values = []

            for lag in range(1, config.max_decay_lag + 1):
                fwd = close_unstacked.shift(-lag) / close_unstacked - 1
                fwd_stacked = fwd.stack(dropna=False)
                fwd_stacked.name = "ret"
                fwd_stacked.index.names = ["datetime", "instrument"]

                merged = pd.DataFrame({
                    "factor": factor_col,
                    "ret": fwd_stacked.reindex(factor_df.index),
                }).dropna()

                if len(merged) < 10:
                    decay_values.append(0.0)
                    continue

                def _ic(g: Any) -> float:
                    if len(g) < 3:
                        return np.nan
                    if config.ic_method == "rank":
                        return g["factor"].rank().corr(g["ret"].rank())
                    return g["factor"].corr(g["ret"])

                daily_ic = merged.groupby(level="datetime").apply(_ic).dropna()
                decay_values.append(float(daily_ic.mean()) if len(daily_ic) > 0 else 0.0)

            result[fname] = decay_values

        return result

    @classmethod
    def print_report(cls, result: FactorAnalysisResult) -> None:
        """Log a formatted factor analysis report."""
        log = _logger.info
        log("=" * 70)
        log("FACTOR ANALYSIS REPORT")
        log("=" * 70)

        log("Total factors analyzed: %d", result.total_factors)
        log("")
        log("Top Factors by |IC|:")
        log(f"{'Factor':>25} {'Mean IC':>10} {'Std IC':>10} {'IR':>8} {'IC>0%':>8} {'Days':>6}")
        log("-" * 70)

        for s in result.factor_ic_stats[:20]:
            log(
                f"{s.factor_name:>25} "
                f"{s.mean_ic:>10.4f} "
                f"{s.std_ic:>10.4f} "
                f"{s.ir:>8.3f} "
                f"{s.ic_positive_ratio:>7.1%} "
                f"{s.num_days:>6}"
            )

        if result.ic_decay:
            log("")
            log("IC Decay (top factors):")
            first_name = next(iter(result.ic_decay))
            max_lag = len(result.ic_decay[first_name])
            header = f"{'Factor':>25} " + " ".join(f"{i+1:>5}" for i in range(min(max_lag, 10)))
            log(header)
            log("-" * len(header))
            for fname, decay in list(result.ic_decay.items())[:10]:
                vals = " ".join(f"{v:>5.3f}" for v in decay[:10])
                log(f"{fname:>25} {vals}")

        log("=" * 70)
