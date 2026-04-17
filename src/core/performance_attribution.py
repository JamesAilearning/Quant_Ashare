"""Performance attribution — Brinson-style sector attribution and time decomposition.

Decomposes portfolio return into:
1. **Sector allocation effect** — did we over/underweight winning sectors?
2. **Stock selection effect** — did we pick winners within each sector?
3. **Interaction effect** — combined allocation × selection
4. **Time decomposition** — which calendar periods contributed most to P&L?

Boundaries
----------
- Operates on backtest return_series + portfolio positions (post-backtest).
- Requires canonical qlib init for fetching sector/industry data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from src.core.logger import get_logger
from src.core.qlib_runtime import is_canonical_qlib_initialized

_logger = get_logger(__name__)


class PerformanceAttributionError(RuntimeError):
    """Raised on attribution computation failures."""


@dataclass(frozen=True)
class AttributionConfig:
    """Configuration for performance attribution."""

    # Date range (should match backtest period)
    start_date: str = "2025-07-01"
    end_date: str = "2025-12-31"

    # NOTE: benchmark_code is intentionally absent here. The attribution
    # engine operates on ``return_series["bench"]`` produced by
    # CanonicalBacktestOutput, which already embeds the correct benchmark
    # data. Duplicating it as a config field would create an unvalidated
    # second entry point for benchmark selection with no enforcement.


@dataclass(frozen=True)
class SectorAttribution:
    """Brinson attribution for a single sector."""

    sector: str
    portfolio_weight: float
    benchmark_weight: float
    portfolio_return: float
    benchmark_return: float
    allocation_effect: float
    selection_effect: float
    interaction_effect: float
    total_effect: float


@dataclass(frozen=True)
class MonthlyReturn:
    """Return for a single month."""

    year: int
    month: int
    portfolio_return: float
    benchmark_return: float
    excess_return: float


@dataclass(frozen=True)
class AttributionResult:
    """Complete attribution result."""

    # Brinson sector attribution
    sector_attribution: tuple[SectorAttribution, ...]
    total_allocation_effect: float
    total_selection_effect: float
    total_interaction_effect: float

    # Time decomposition
    monthly_returns: tuple[MonthlyReturn, ...]

    # Summary
    total_portfolio_return: float
    total_benchmark_return: float
    total_excess_return: float


class PerformanceAttribution:
    """Brinson-style performance attribution engine."""

    @classmethod
    def analyze(
        cls,
        return_series: Mapping[str, Any],
        predictions: Any,
        config: AttributionConfig | None = None,
        positions: Mapping[str, Mapping[str, float]] | None = None,
    ) -> AttributionResult:
        """Run complete performance attribution.

        Parameters
        ----------
        return_series : dict
            From ``CanonicalBacktestOutput.return_series`` with keys
            ``"return"``, ``"bench"``, ``"cost"``.
        predictions : pd.Series
            Model predictions with ``(datetime, instrument)`` MultiIndex.
        config : AttributionConfig, optional
            Attribution configuration.
        positions : mapping, optional
            From ``CanonicalBacktestOutput.positions`` — authoritative per-day
            portfolio weights ``{date: {instrument: weight}}``. When supplied,
            Brinson weighting reflects the real topk-dropout selection rather
            than a predictions-score proxy. Pass ``None`` to fall back to
            prediction-score weighting (looser but works without a backtest).
        """
        if config is None:
            config = AttributionConfig()

        cls._validate(config, return_series, positions)

        import pandas as pd

        _logger.info("Running performance attribution %s ~ %s...", config.start_date, config.end_date)

        # Parse return series
        ret_dict = return_series["return"]
        bench_dict = return_series.get("bench", {})

        port_returns = pd.Series(
            {pd.Timestamp(k): float(v) for k, v in ret_dict.items()}
        ).sort_index()

        bench_returns = pd.Series(
            {pd.Timestamp(k): float(v) for k, v in bench_dict.items()}
        ).sort_index() if bench_dict else pd.Series(dtype=float)

        # Step 1: Brinson sector attribution
        _logger.info("Computing Brinson sector attribution...")
        sector_attr = cls._brinson_attribution(
            predictions, port_returns, bench_returns, config, positions,
        )

        total_alloc = sum(s.allocation_effect for s in sector_attr)
        total_select = sum(s.selection_effect for s in sector_attr)
        total_interact = sum(s.interaction_effect for s in sector_attr)

        # Step 2: Time decomposition
        _logger.info("Computing monthly time decomposition...")
        monthly = cls._monthly_decomposition(port_returns, bench_returns)

        # Total returns
        total_port = float((1 + port_returns).prod() - 1) if len(port_returns) > 0 else 0.0
        total_bench = float((1 + bench_returns).prod() - 1) if len(bench_returns) > 0 else 0.0

        return AttributionResult(
            sector_attribution=tuple(sector_attr),
            total_allocation_effect=total_alloc,
            total_selection_effect=total_select,
            total_interaction_effect=total_interact,
            monthly_returns=tuple(monthly),
            total_portfolio_return=total_port,
            total_benchmark_return=total_bench,
            total_excess_return=total_port - total_bench,
        )

    @classmethod
    def _validate(
        cls,
        config: AttributionConfig,
        return_series: Mapping[str, Any],
        positions: Mapping[str, Mapping[str, float]] | None,
    ) -> None:
        if not is_canonical_qlib_initialized():
            raise PerformanceAttributionError(
                "Canonical qlib runtime is not initialized."
            )
        if "return" not in return_series:
            raise PerformanceAttributionError(
                "return_series must contain 'return' key."
            )
        # Explicit empty positions dict is a caller error, not a signal to fall
        # back silently to prediction-score weights.  Pass None to opt into the
        # predictions fallback intentionally; pass a non-empty dict for real
        # positions.  This upholds the project's "no implicit fallback" rule.
        if positions is not None and len(positions) == 0:
            raise PerformanceAttributionError(
                "positions was supplied as an empty dict. "
                "Pass positions=None to use the predictions-score fallback, "
                "or supply the non-empty positions map from CanonicalBacktestOutput."
            )

    @classmethod
    def _brinson_attribution(
        cls,
        predictions: Any,
        port_returns: Any,
        bench_returns: Any,
        config: AttributionConfig,
        positions: Mapping[str, Mapping[str, float]] | None = None,
    ) -> list[SectorAttribution]:
        """Brinson-Fachler single-period attribution by sector.

        Portfolio weights are derived from ``positions`` (time-averaged real
        holdings) when available — this matches the actual topk-dropout
        selection. Otherwise we fall back to a prediction-score proxy,
        clipping negatives to zero so ranked-low names do not leak weight.
        """
        import pandas as pd
        import numpy as np

        # Instruments universe: union of predictions and held positions
        pred_instruments = predictions.index.get_level_values("instrument").unique().tolist()
        held_instruments: list[str] = []
        if positions:
            seen: set[str] = set()
            for day_map in positions.values():
                for inst in day_map:
                    if inst not in seen:
                        seen.add(inst)
                        held_instruments.append(inst)
        instruments = sorted(set(pred_instruments) | set(held_instruments))
        sector_map = cls._get_sector_map(instruments, config)

        # Portfolio weights: prefer real positions, fall back to prediction scores.
        if positions:
            # Time-average the actual per-day weights
            weight_sum: dict[str, float] = {}
            day_count = 0
            for day_map in positions.values():
                if not day_map:
                    continue
                day_count += 1
                for inst, w in day_map.items():
                    try:
                        weight_sum[inst] = weight_sum.get(inst, 0.0) + float(w)
                    except (TypeError, ValueError):
                        continue
            if day_count == 0 or not weight_sum:
                # positions was non-empty at validation time but every day
                # deserialized to zero weights — treat as corrupted input.
                raise PerformanceAttributionError(
                    "positions was provided but all entries yielded zero usable "
                    "weights after deserialization. Check the positions map from "
                    "CanonicalBacktestOutput for corruption."
                )
            raw = pd.Series({k: v / day_count for k, v in weight_sum.items()})
            total = float(raw.sum())
            port_weights = raw / total if total > 0 else raw
        else:
            port_weights = cls._predictions_to_weights(predictions)

        # Benchmark weights: equal weight across all instruments
        n_instruments = len(instruments)
        bench_weights = pd.Series(1.0 / n_instruments, index=instruments)

        # Get per-instrument returns over the period
        inst_returns = cls._get_instrument_returns(instruments, config)

        # Aggregate by sector
        sectors = sorted(set(sector_map.values()))
        results = []

        # Overall benchmark return for BF model (compound, consistent with portfolio)
        total_bench_ret = float((1 + bench_returns).prod() - 1) if len(bench_returns) > 0 else 0.0

        for sector in sectors:
            sector_instruments = [i for i in instruments if sector_map.get(i) == sector]
            if not sector_instruments:
                continue

            # Portfolio weight in this sector
            w_p = float(port_weights.reindex(sector_instruments).sum())
            # Benchmark weight in this sector
            w_b = float(bench_weights.reindex(sector_instruments).sum())

            # Sector return in portfolio (weighted avg of instrument returns)
            sector_port_w = port_weights.reindex(sector_instruments).dropna()
            sector_inst_r = inst_returns.reindex(sector_instruments).dropna()
            common = sector_port_w.index.intersection(sector_inst_r.index)

            if len(common) > 0 and w_p > 1e-9:
                r_p = float((sector_port_w[common] * sector_inst_r[common]).sum() / w_p)
            else:
                r_p = 0.0

            # Sector return in benchmark (equal-weighted)
            sector_bench_w = bench_weights.reindex(sector_instruments).dropna()
            common_b = sector_bench_w.index.intersection(sector_inst_r.index)
            if len(common_b) > 0 and w_b > 1e-9:
                r_b = float((sector_bench_w[common_b] * sector_inst_r[common_b]).sum() / w_b)
            else:
                r_b = 0.0

            # Brinson-Fachler decomposition
            allocation = (w_p - w_b) * (r_b - total_bench_ret)
            selection = w_b * (r_p - r_b)
            interaction = (w_p - w_b) * (r_p - r_b)
            total = allocation + selection + interaction

            results.append(SectorAttribution(
                sector=sector,
                portfolio_weight=round(w_p, 4),
                benchmark_weight=round(w_b, 4),
                portfolio_return=round(r_p, 4),
                benchmark_return=round(r_b, 4),
                allocation_effect=round(allocation, 6),
                selection_effect=round(selection, 6),
                interaction_effect=round(interaction, 6),
                total_effect=round(total, 6),
            ))

        # Sort by absolute total effect
        results.sort(key=lambda s: abs(s.total_effect), reverse=True)
        return results

    @staticmethod
    def _predictions_to_weights(predictions: Any) -> Any:
        """Fallback: convert prediction scores to long-only weights.

        Clips negative scores to zero so that names the model ranks poorly
        do not absorb portfolio weight. Only if all scores are non-positive
        do we fall back to a small epsilon to keep the weights finite.
        """
        import pandas as pd

        avg_pred = predictions.groupby(level="instrument").mean()
        clipped = avg_pred.clip(lower=0.0)
        total = float(clipped.sum())
        if total > 0:
            return clipped / total
        # All non-positive: fall back to uniform so downstream math stays sane
        n = len(avg_pred)
        return pd.Series(1.0 / n, index=avg_pred.index) if n > 0 else pd.Series(dtype=float)

    @classmethod
    def _get_sector_map(cls, instruments: list[str], config: AttributionConfig) -> dict[str, str]:
        """Get instrument → sector mapping via A-share code heuristic.

        Previously this had a ``use_code_based_sectors=False`` branch that
        attempted to load ``$industry`` from qlib, but qlib's standard CN
        data bundle does not include an industry provider — the branch was
        dead code that could only silently fall back to the code-based path
        anyway. Removed to avoid confusion and maintenance burden. A real
        industry loader should be added as a separate feature when an
        industry data source is confirmed available.
        """
        return cls._code_based_sector_map(instruments)

    @staticmethod
    def _code_based_sector_map(instruments: list[str]) -> dict[str, str]:
        """A-share code-based sector heuristic (same as risk_constraints)."""
        sector_map = {}
        for inst in instruments:
            code = inst.replace("SH", "").replace("SZ", "")
            if code.startswith("688"):
                sector_map[inst] = "STAR"
            elif code.startswith("300") or code.startswith("301"):
                sector_map[inst] = "ChiNext"
            elif code.startswith("002"):
                sector_map[inst] = "SME"
            elif code.startswith("600") or code.startswith("601") or code.startswith("603"):
                sector_map[inst] = "SH_Main"
            elif code.startswith("000") or code.startswith("001"):
                sector_map[inst] = "SZ_Main"
            else:
                sector_map[inst] = "Other"
        return sector_map

    @classmethod
    def _get_instrument_returns(cls, instruments: list[str], config: AttributionConfig) -> Any:
        """Get total return per instrument over the attribution period."""
        import pandas as pd
        from qlib.data import D

        close = D.features(
            instruments, ["$close"],
            start_time=config.start_date, end_time=config.end_date,
        )
        close.columns = ["close"]

        # Total return = last_close / first_close - 1
        result = {}
        for inst in instruments:
            try:
                inst_close = close.xs(inst, level="instrument")["close"].dropna()
                if len(inst_close) >= 2:
                    result[inst] = float(inst_close.iloc[-1] / inst_close.iloc[0] - 1)
            except (KeyError, IndexError):
                continue

        return pd.Series(result)

    @classmethod
    def _monthly_decomposition(cls, port_returns: Any, bench_returns: Any) -> list[MonthlyReturn]:
        """Decompose returns by calendar month."""
        import pandas as pd

        if port_returns.empty:
            return []

        # Group by year-month
        port_monthly = port_returns.groupby(
            [port_returns.index.year, port_returns.index.month]
        ).apply(lambda x: float((1 + x).prod() - 1))

        bench_monthly = bench_returns.groupby(
            [bench_returns.index.year, bench_returns.index.month]
        ).apply(lambda x: float((1 + x).prod() - 1)) if len(bench_returns) > 0 else pd.Series(dtype=float)

        results = []
        for (year, month), port_r in port_monthly.items():
            bench_r = float(bench_monthly.get((year, month), 0.0))
            results.append(MonthlyReturn(
                year=int(year),
                month=int(month),
                portfolio_return=round(port_r, 6),
                benchmark_return=round(bench_r, 6),
                excess_return=round(port_r - bench_r, 6),
            ))

        return results

    @classmethod
    def print_report(cls, result: AttributionResult) -> None:
        """Log a formatted attribution report."""
        log = _logger.info
        log("=" * 75)
        log("PERFORMANCE ATTRIBUTION REPORT")
        log("=" * 75)

        log("Overall:")
        log("  Portfolio return:  %.2f%%", result.total_portfolio_return * 100)
        log("  Benchmark return:  %.2f%%", result.total_benchmark_return * 100)
        log("  Excess return:     %.2f%%", result.total_excess_return * 100)
        log("")
        log("Brinson Decomposition:")
        log("  Allocation effect: %+.4f", result.total_allocation_effect)
        log("  Selection effect:  %+.4f", result.total_selection_effect)
        log("  Interaction effect:%+.4f", result.total_interaction_effect)

        log("")
        log("Sector Attribution:")
        log(f"{'Sector':>12} {'Wt_P':>7} {'Wt_B':>7} {'Ret_P':>8} {'Ret_B':>8} "
            f"{'Alloc':>9} {'Select':>9} {'Total':>9}")
        log("-" * 75)
        for s in result.sector_attribution:
            log(
                f"{s.sector:>12} "
                f"{s.portfolio_weight:>6.1%} "
                f"{s.benchmark_weight:>6.1%} "
                f"{s.portfolio_return:>7.2%} "
                f"{s.benchmark_return:>7.2%} "
                f"{s.allocation_effect:>+9.4f} "
                f"{s.selection_effect:>+9.4f} "
                f"{s.total_effect:>+9.4f}"
            )

        if result.monthly_returns:
            log("")
            log("Monthly Returns:")
            log(f"{'Month':>10} {'Portfolio':>10} {'Benchmark':>10} {'Excess':>10}")
            log("-" * 42)
            for m in result.monthly_returns:
                log(
                    f"{m.year}-{m.month:02d}    "
                    f"{m.portfolio_return:>9.2%} "
                    f"{m.benchmark_return:>9.2%} "
                    f"{m.excess_return:>+9.2%}"
                )

        log("=" * 75)
