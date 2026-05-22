"""Composite fitness function and validity filters.

Implements the v1 ``factor_mining_design.md`` §5.1 formula with the
v2 annualised cost rate per ``decisions.md`` D1. Validity filters
implement §5.2 hard constraints; invalid factors get fitness
``-inf`` so genetic selection never picks them.

No qlib import, no ``src.pit`` import. Pure metric arithmetic.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .evaluator import EvaluationResult

ANNUALISATION_DAYS = 252


@dataclass(frozen=True)
class FitnessConfig:
    """Tunable weights and validity thresholds.

    Defaults match ``factor_mining_design.md`` §5.1 and ``decisions.md``
    D1. The cost rate is the locked round-trip ratio (0.3 % per
    one-way trade × 252 trading days for the annualisation).
    """

    # v1 §5.1 weights
    w_ic: float = 1.0
    w_ir: float = 0.5
    w_rankic: float = 0.5
    w_turnover: float = 0.2
    w_corr: float = 0.8
    w_complexity: float = 0.01

    # D1 — locked annualised round-trip cost
    cost_rate: float = 0.003

    # v1 §5.2 hard-constraint thresholds
    coverage_min: float = 0.8
    variance_days_frac_min: float = 0.7
    variance_min: float = 1e-6
    extreme_outlier_frac_max: float = 0.05
    extreme_outlier_magnitude: float = 1e8


def _variance_days_frac(result: EvaluationResult, variance_min: float) -> float:
    """Fraction of dates whose cross-sectional std > ``variance_min``."""
    if result.factor_values.empty:
        return 0.0
    daily_std = result.factor_values.std(axis=1)
    if len(daily_std) == 0:
        return 0.0
    valid = (daily_std > variance_min).sum()
    return float(valid) / float(len(daily_std))


def _extreme_outlier_frac(result: EvaluationResult, magnitude: float) -> float:
    """Fraction of cells in ``factor_values`` that are non-finite OR
    whose absolute value exceeds the sanity bound ``magnitude``."""
    arr = result.factor_values.to_numpy()
    if arr.size == 0:
        return 0.0
    finite = np.isfinite(arr)
    extreme = ~finite | (np.abs(np.where(finite, arr, 0.0)) > magnitude)
    return float(extreme.sum()) / float(arr.size)


def passes_validity(result: EvaluationResult, config: FitnessConfig) -> bool:
    """v1 §5.2 hard constraints: coverage + variance + sanity.

    The data-leakage constraint from §5.2 item 3 is enforced by the
    Phase 1 grammar (scale-invariance gate); no runtime check is
    needed here.
    """
    if result.coverage < config.coverage_min:
        return False
    if _variance_days_frac(result, config.variance_min) < config.variance_days_frac_min:
        return False
    if _extreme_outlier_frac(result, config.extreme_outlier_magnitude) > config.extreme_outlier_frac_max:
        return False
    return True


def compute_fitness(
    result: EvaluationResult,
    expr_size: int,
    novelty_penalty: float,
    config: FitnessConfig | None = None,
) -> float:
    """Composite fitness per v1 §5.1 with D1 annualised cost.

    ::

        fitness = w_ic       * |ic_mean|
                + w_ir       * ir
                + w_rankic   * |rank_ic_mean|
                - w_turnover * (turnover_daily × 252 × cost_rate)
                - w_corr     * novelty_penalty
                - w_complexity * expr_size

    Invalid factors (``passes_validity`` is False) get ``-inf`` so
    GP selection never picks them. NaN IC means the factor produced
    no valid observations, which also gives ``-inf``.
    """
    cfg = config if config is not None else FitnessConfig()
    if not passes_validity(result, cfg):
        return float("-inf")
    if (
        not np.isfinite(result.ic_mean)
        or not np.isfinite(result.rank_ic_mean)
    ):
        return float("-inf")
    ir_term = 0.0 if not np.isfinite(result.ir) else result.ir
    cost_term = result.turnover_daily * ANNUALISATION_DAYS * cfg.cost_rate
    novelty = float(novelty_penalty) if np.isfinite(novelty_penalty) else 0.0
    score = (
        cfg.w_ic * abs(result.ic_mean)
        + cfg.w_ir * ir_term
        + cfg.w_rankic * abs(result.rank_ic_mean)
        - cfg.w_turnover * cost_term
        - cfg.w_corr * novelty
        - cfg.w_complexity * float(expr_size)
    )
    return float(score)


def expression_size(expr) -> int:
    """Count of AST nodes (terminals + operator calls)."""
    from .expression import OperatorCall, Terminal

    if isinstance(expr, Terminal):
        return 1
    if isinstance(expr, OperatorCall):
        return 1 + sum(expression_size(c) for c in expr.children)
    raise TypeError(f"Unsupported expression node type: {type(expr).__name__}")
