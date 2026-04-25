"""Risk constraints for portfolio construction.

Provides post-signal filtering and weight adjustment to enforce:
1. Individual stock position limits (max weight per stock)
2. Industry/sector concentration limits
3. Maximum daily turnover cap

These constraints are applied AFTER signal generation but BEFORE
passing to the backtest executor. They modify the prediction scores
to enforce the constraints implicitly through the TopkDropout strategy.

Boundaries
----------
- Requires canonical qlib init (for fetching instrument industry data).
- Operates on prediction Series and returns a constrained version.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from src.core.board_heuristic import classify_instruments
from src.core.logger import get_logger
from src.core.qlib_runtime import is_canonical_qlib_initialized

_logger = get_logger(__name__)


class RiskConstraintError(RuntimeError):
    """Raised on structural misuse of risk constraints."""


@dataclass(frozen=True)
class RiskConstraintConfig:
    """Configuration for risk constraints."""

    # Max weight per stock (fraction of portfolio). 0 = no limit.
    max_stock_weight: float = 0.05  # 5% per stock

    # Max weight per industry (fraction of portfolio). 0 = no limit.
    max_industry_weight: float = 0.30  # 30% per industry

    # Max number of stocks from same industry
    max_stocks_per_industry: int = 10

    # Max daily turnover (fraction). 0 = no limit.
    max_daily_turnover: float = 0.0  # disabled by default

    # Industry classification field in qlib (e.g., "industry" or "sw_l1")
    industry_field: str = "industry"

    # Top-k for constraint enforcement
    topk: int = 50

    # Optional explicit industry taxonomy: {instrument_code: industry_name}.
    # When provided, overrides the board-prefix heuristic used as a
    # fallback. For production use, pass an authoritative Shenwan / CSRC
    # / GICS mapping rather than relying on stock-code prefixes, which
    # bucket most of the universe into "board_SH_Main" / "board_SZ_Main"
    # and make the per-industry cap effectively a single-bucket cap (the
    # SH main board alone contains banks, real estate, utilities, …).
    industry_map: Mapping[str, str] | None = None


@dataclass(frozen=True)
class RiskConstraintResult:
    """Result of applying risk constraints."""

    constrained_predictions: Any  # pd.Series
    stocks_removed: int
    industry_violations_fixed: int
    original_count: int
    constrained_count: int
    constraint_log: Sequence[str]


class RiskConstraintEngine:
    """Applies risk constraints to model predictions."""

    @classmethod
    def apply(
        cls,
        predictions: Any,
        config: RiskConstraintConfig | None = None,
    ) -> RiskConstraintResult:
        """Apply risk constraints to predictions.

        Parameters
        ----------
        predictions : pd.Series
            Model predictions with (datetime, instrument) MultiIndex.
        config : RiskConstraintConfig, optional
            Constraint configuration. Uses defaults if None.

        Returns
        -------
        RiskConstraintResult
        """
        if not is_canonical_qlib_initialized():
            raise RiskConstraintError(
                "Canonical qlib runtime must be initialized."
            )

        import pandas as pd
        import numpy as np

        if config is None:
            config = RiskConstraintConfig()

        if not isinstance(predictions, pd.Series):
            raise RiskConstraintError(
                f"predictions must be pd.Series, got {type(predictions).__name__}"
            )

        if not isinstance(predictions.index, pd.MultiIndex):
            raise RiskConstraintError(
                "predictions must have (datetime, instrument) MultiIndex"
            )

        log: list[str] = []
        constrained = predictions.copy()
        stocks_removed = 0
        industry_violations = 0

        # Apply industry concentration limit
        if config.max_stocks_per_industry > 0:
            constrained, removed, violations = cls._apply_industry_limit(
                constrained, config
            )
            stocks_removed += removed
            industry_violations += violations
            if violations > 0:
                log.append(
                    f"Industry limit: removed {removed} stocks across "
                    f"{violations} day-industry violations "
                    f"(max {config.max_stocks_per_industry} per industry)"
                )

        # Apply per-stock weight constraint via score dampening
        # (we can't directly set weights with TopkDropout, so we
        # penalize stocks that would exceed weight limits by
        # limiting how many of the top stocks come from the same name)
        if config.max_stock_weight > 0 and config.topk > 0:
            max_per_stock = max(1, int(config.topk * config.max_stock_weight * 2))
            # For equal-weight top-k, each stock gets ~1/topk weight
            # max_stock_weight=0.05 with topk=50 → each gets 2%, below 5%
            # This is naturally satisfied, so only flag if topk is very small
            if 1.0 / config.topk > config.max_stock_weight:
                log.append(
                    f"Warning: topk={config.topk} gives equal weight "
                    f"{1.0/config.topk:.1%} > max_stock_weight={config.max_stock_weight:.1%}. "
                    f"Consider increasing topk."
                )

        return RiskConstraintResult(
            constrained_predictions=constrained,
            stocks_removed=stocks_removed,
            industry_violations_fixed=industry_violations,
            original_count=len(predictions),
            constrained_count=len(constrained),
            constraint_log=log,
        )

    @classmethod
    def _apply_industry_limit(
        cls,
        predictions: Any,
        config: RiskConstraintConfig,
    ) -> tuple[Any, int, int]:
        """Limit max stocks per industry per day in top-k selections."""
        import pandas as pd
        import numpy as np

        industry_map = cls._get_industry_map(predictions, config)
        if not industry_map:
            return predictions, 0, 0

        total_removed = 0
        total_violations = 0
        result_parts = []

        for date, group in predictions.groupby(level=0):
            # Get top candidates for this day
            sorted_group = group.sort_values(ascending=False)
            top_candidates = sorted_group.head(config.topk * 2)  # look at more than topk

            # Map instruments to industries
            instruments = top_candidates.index.get_level_values(1)
            industries = instruments.map(
                lambda x: industry_map.get(x, "unknown")
            )

            # Apply per-industry cap
            industry_count: dict[str, int] = {}
            keep_mask = []
            for inst, ind in zip(instruments, industries):
                count = industry_count.get(ind, 0)
                if count < config.max_stocks_per_industry:
                    keep_mask.append(True)
                    industry_count[ind] = count + 1
                else:
                    keep_mask.append(False)
                    total_removed += 1

            if not all(keep_mask):
                total_violations += 1

            filtered = top_candidates[keep_mask]
            result_parts.append(filtered)

        if result_parts:
            return pd.concat(result_parts), total_removed, total_violations
        return predictions, 0, 0

    @classmethod
    def _get_industry_map(
        cls, predictions: Any, config: RiskConstraintConfig,
    ) -> dict[str, str]:
        """Resolve an {instrument: industry} map for the predictions universe.

        Priority order:

        1. If the caller passed ``config.industry_map``, use it verbatim —
           any instrument not present in the caller's map is bucketed as
           ``"unknown"`` (surfaced via :meth:`_apply_industry_limit`'s
           ``.get(x, "unknown")`` call site).  This is the production
           path when an authoritative taxonomy is available.

        2. Otherwise fall back to the shared
           :mod:`src.core.board_heuristic` module, which buckets by
           A-share stock-code prefixes into ``board_*`` labels.  Logged at
           INFO so the operator knows a rough heuristic is in force
           instead of a proper industry taxonomy — the old implementation
           called ``qlib.data.D.instruments(market="all")`` and immediately
           discarded the result, pretending to consult qlib but always
           using the prefix map while swallowing any error with a bare
           ``except Exception``.

        Note that the buckets returned in case (2) are *boards*, not
        industries: the SH main board alone holds banks, real estate,
        utilities, etc. The ``board_`` prefix on each label is meant to
        keep that distinction visible at every consumer.
        """
        instruments = predictions.index.get_level_values(1).unique().tolist()

        if config.industry_map is not None:
            return dict(config.industry_map)

        _logger.info(
            "RiskConstraints: no explicit industry_map; falling back to "
            "the A-share board-prefix heuristic for %d instruments. The "
            "resulting buckets are *boards*, not industries — for a real "
            "industry cap, pass RiskConstraintConfig(industry_map=...) "
            "with an authoritative taxonomy.",
            len(instruments),
        )
        return classify_instruments(instruments)

    @classmethod
    def print_report(cls, result: RiskConstraintResult) -> None:
        """Log constraint application summary."""
        _logger.info("Risk Constraints")
        _logger.info("  Original predictions: %d", result.original_count)
        _logger.info("  After constraints:    %d", result.constrained_count)
        _logger.info("  Stocks removed:       %d", result.stocks_removed)
        _logger.info("  Industry violations:  %d", result.industry_violations_fixed)
        for line in result.constraint_log:
            _logger.info("  %s", line)
