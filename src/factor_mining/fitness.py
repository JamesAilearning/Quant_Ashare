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

# Recognised fitness shapes. An unknown value must never silently fall
# through to the v1 mixture — that is how a campaign ends up breeding
# against a metric other than the pre-registered one.
_IC_TERMS = frozenset({"v1_composite", "abs_rank_ic"})


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

    # ---- Campaign IC term (pv_incremental_v1) ----
    # ``"v1_composite"`` (default) = the §5.1 mixture below, unchanged.
    # ``"abs_rank_ic"`` = the FROZEN pv_incremental_v1 breeding
    # criterion: |daily cross-sectional rank-IC mean| MINUS parsimony
    # and orthogonality, and NOTHING else. Selecting the mode switches
    # the formula's SHAPE rather than asking an operator to zero six
    # legacy weights by hand — a hand-zeroed config would silently
    # diverge from the pre-registered metric that is supposed to be
    # selecting factors (codex #401 r1).
    ic_term: str = "v1_composite"
    # Thin-day floor for the BREEDING metric. 0 keeps the legacy
    # behaviour (only the IC primitive's own 3-name floor). A campaign
    # whose frozen metric drops days below ``min_names_per_day`` must
    # set it here too (codex #401 r6) — otherwise thin days steer GP
    # selection and then vanish at adjudication, so the breeding
    # metric is not the pre-registered one.
    min_names_per_day: int = 0

    # ---- Campaign orthogonality penalty (pv_incremental_v1) ----
    # INERT BY DEFAULT (weight 0.0): the v1 formula and every existing
    # pin are unchanged unless a campaign config sets these. Unlike the
    # within-generation ``w_corr`` novelty term (linear, vs GP peers),
    # this is a BANDED hinge against a fixed EXTERNAL baseline: only
    # the part of mean |rho| above ``orthogonality_band`` is penalised,
    # so a candidate may carry baseline-correlated signal up to the
    # frozen band for free and pays only for the excess. The band and
    # weight are frozen in docs/prereg/pv_incremental.yaml (0.30 / 2.0)
    # — a campaign passes them through, never hardcodes them here.
    w_orthogonality: float = 0.0
    orthogonality_band: float = 0.0


def fitness_uses_novelty(config: FitnessConfig) -> bool:
    """Whether the configured formula READS the novelty term.

    Single source of truth for engines deciding whether to spend
    O(population²) computing within-generation novelty: the
    ``abs_rank_ic`` shape discards ``novelty_penalty`` entirely, so
    keying the engine's short-circuit on ``w_corr`` alone lets the
    default 0.8 burn per-generation pairwise correlations that
    ``compute_fitness`` then throws away (the 2026-08-11 campaign
    batch abort, ledger E005). ``v1_composite`` reads the term only
    when ``w_corr`` is non-zero.
    """
    return config.ic_term != "abs_rank_ic" and config.w_corr != 0.0


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
    """Fraction of FINITE cells in ``factor_values`` whose absolute
    value exceeds the sanity bound ``magnitude``.

    The denominator is the count of finite cells, NOT the total cell
    count. This separates the sanity check from the coverage check
    (which is what `_coverage` / `coverage_min` already enforce); the
    earlier implementation counted NaN cells as "outliers", which
    double-penalised any factor that didn't fully clear `coverage_min`
    and made `extreme_outlier_frac_max=0.05` (the default) effectively
    require coverage ≥ 0.95. See the v1 §5.2 §"Sanity" requirement
    in v2-factor-mining-foundations — the original intent was a
    magnitude check on the finite values.
    """
    arr = result.factor_values.to_numpy()
    if arr.size == 0:
        return 0.0
    finite = np.isfinite(arr)
    finite_count = int(finite.sum())
    if finite_count == 0:
        # All-NaN factor: outlier fraction is undefined; report 0 so
        # the coverage check (already 0) is the binding rejection.
        return 0.0
    finite_extreme = finite & (np.abs(np.where(finite, arr, 0.0)) > magnitude)
    return float(finite_extreme.sum()) / float(finite_count)


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


def orthogonality_penalty(mean_abs_rho: float,
                          config: FitnessConfig) -> float:
    """Banded hinge on baseline correlation (pv_incremental_v1).

    ``w_orthogonality × max(0, mean|rho| − band)`` — zero when the
    weight is 0 (the default, so the v1 formula is untouched) and zero
    inside the frozen band. A non-finite rho (no overlapping days at
    all) contributes NO penalty: the campaign's IS window deliberately
    starts before the walk-forward baseline's first out-of-fold date,
    so early-window expressions are simply unpenalised rather than
    scored against a baseline that does not exist there (operator
    decision A — keep the baseline's production fold geometry rather
    than manufacture earlier folds). Coverage is reported at run level,
    never silently folded into the score.
    """
    if config.w_orthogonality == 0.0:
        return 0.0
    if not np.isfinite(mean_abs_rho):
        return 0.0
    excess = max(0.0, float(mean_abs_rho) - config.orthogonality_band)
    return config.w_orthogonality * excess


def compute_fitness(
    result: EvaluationResult,
    expr_size: int,
    novelty_penalty: float,
    config: FitnessConfig | None = None,
    baseline_mean_abs_rho: float = float("nan"),
) -> float:
    """Composite fitness per v1 §5.1 with D1 annualised cost.

    ::

        fitness = w_ic       * |ic_mean|
                + w_ir       * ir
                + w_rankic   * |rank_ic_mean|
                - w_turnover * (turnover_daily × 252 × cost_rate)
                - w_corr     * novelty_penalty
                - w_complexity * expr_size
                - w_orthogonality * max(0, baseline|rho| - band)

    The last term is INERT unless a campaign sets ``w_orthogonality``
    (default 0.0), so the v1 formula above it is unchanged.

    Invalid factors (``passes_validity`` is False) get ``-inf`` so
    GP selection never picks them. NaN IC means the factor produced
    no valid observations, which also gives ``-inf``.
    """
    cfg = config if config is not None else FitnessConfig()
    if cfg.ic_term not in _IC_TERMS:
        raise ValueError(
            f"unknown ic_term {cfg.ic_term!r}; expected one of "
            f"{sorted(_IC_TERMS)}")
    if not passes_validity(result, cfg):
        return float("-inf")
    if (
        not np.isfinite(result.ic_mean)
        or not np.isfinite(result.rank_ic_mean)
    ):
        return float("-inf")
    if cfg.ic_term == "abs_rank_ic":
        # The frozen pv_incremental_v1 breeding criterion, verbatim:
        # |daily cross-sectional rank-IC mean| − parsimony −
        # orthogonality. The v1 IR / turnover-cost / within-generation
        # novelty terms deliberately do NOT participate — the
        # pre-registered metric is exactly this and nothing else.
        return float(
            abs(result.rank_ic_mean)
            - cfg.w_complexity * float(expr_size)
            - orthogonality_penalty(baseline_mean_abs_rho, cfg)
        )
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
        - orthogonality_penalty(baseline_mean_abs_rho, cfg)
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
