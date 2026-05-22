"""IS/OOS validator for factor pools (Phase 6).

Splits the panel + forward-return on a configured date, evaluates
each pool entry on both segments, and rejects factors whose OOS
metrics fall below thresholds. Demonstrably catches the classic
"high IS IR, ~0 OOS IR" overfit pattern.

Pool-level pairwise correlation filtering (`filter_correlated`) drops
near-duplicate factors after per-factor validation.

No qlib import, no ``src.pit`` import. Pure metric arithmetic on
already-loaded panels.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .evaluator import evaluate_factor
from .expression import Expression
from .factor_pool import FactorPool, PoolEntry


@dataclass(frozen=True)
class ValidationCriteria:
    """Per-factor and per-pool acceptance thresholds.

    Defaults match ``decisions.md`` D4 ("Manual gated promotion").
    """

    is_oos_split_date: str
    min_oos_ir: float = 0.3
    min_oos_rank_ic_mean: float = 0.02
    max_pool_correlation: float = 0.6
    min_obs_per_segment: int = 30


@dataclass(frozen=True)
class FactorValidationResult:
    """One factor's IS/OOS verdict."""

    expr_hash: int
    expr_str: str
    fitness: float
    passes: bool
    reasons: tuple[str, ...]
    is_n_obs: int
    is_ir: float
    is_rank_ic_mean: float
    oos_n_obs: int
    oos_ir: float
    oos_rank_ic_mean: float


# ---------------------------------------------------------------------------
# Segment slicing
# ---------------------------------------------------------------------------


def _split_panel(
    panel: Mapping[str, pd.DataFrame],
    forward_return: pd.DataFrame,
    split_ts: pd.Timestamp,
) -> tuple[
    dict[str, pd.DataFrame], pd.DataFrame,
    dict[str, pd.DataFrame], pd.DataFrame,
]:
    """Return (is_panel, is_fwd, oos_panel, oos_fwd) sliced on ``split_ts``."""
    is_panel: dict[str, pd.DataFrame] = {}
    oos_panel: dict[str, pd.DataFrame] = {}
    for field, df in panel.items():
        is_panel[field] = df.loc[df.index < split_ts]
        oos_panel[field] = df.loc[df.index >= split_ts]
    is_fwd = forward_return.loc[forward_return.index < split_ts]
    oos_fwd = forward_return.loc[forward_return.index >= split_ts]
    return is_panel, is_fwd, oos_panel, oos_fwd


def _ir_for_threshold(ir: float) -> float:
    """NaN → 0.0 for threshold comparisons."""
    return 0.0 if not np.isfinite(ir) else float(ir)


# ---------------------------------------------------------------------------
# Per-factor validation
# ---------------------------------------------------------------------------


def _evaluate_segment(
    expr: Expression,
    seg_panel: Mapping[str, pd.DataFrame],
    seg_fwd: pd.DataFrame,
) -> tuple[float, float, int]:
    """Returns (ir, rank_ic_mean, n_obs) for one segment.

    Best-effort: any failure inside the evaluator (e.g. empty panel)
    returns (nan, nan, 0).
    """
    try:
        result = evaluate_factor(expr, seg_panel, seg_fwd, method="rank")
    except Exception:  # noqa: BLE001 — segment may legitimately fail; we just flag
        return float("nan"), float("nan"), 0
    n_obs = int(result.n_obs_per_day_min) * 0  # use cell count below
    # Approximate n_obs as the count of joint non-NaN cells.
    factor_mask = result.factor_values.notna()
    fwd_mask = seg_fwd.reindex_like(result.factor_values).notna()
    n_obs = int((factor_mask & fwd_mask).sum().sum())
    return float(result.rank_ir), float(result.rank_ic_mean), n_obs


def validate_pool(
    pool: FactorPool,
    panel: Mapping[str, pd.DataFrame],
    forward_return: pd.DataFrame,
    criteria: ValidationCriteria,
) -> list[FactorValidationResult]:
    """Per-factor IS/OOS validation against the criteria thresholds."""
    split_ts = pd.Timestamp(criteria.is_oos_split_date)
    is_panel, is_fwd, oos_panel, oos_fwd = _split_panel(panel, forward_return, split_ts)

    results: list[FactorValidationResult] = []
    for entry in pool.all_entries():
        reasons: list[str] = []
        is_ir, is_rank_ic, is_n = _evaluate_segment(entry.expr, is_panel, is_fwd)
        oos_ir, oos_rank_ic, oos_n = _evaluate_segment(entry.expr, oos_panel, oos_fwd)

        if is_n < criteria.min_obs_per_segment:
            reasons.append("is_segment_too_short")
        if oos_n < criteria.min_obs_per_segment:
            reasons.append("oos_segment_too_short")

        # Only evaluate thresholds when both segments have data; missing
        # data dominates the failure list (don't double-fail with
        # threshold reasons when the segment was empty).
        if not reasons:
            # IR check: only fire when IR is finite AND below threshold.
            # A NaN IR with a high IC mean indicates a perfectly consistent
            # signal (IC std == 0 → IR undefined per `inventory.md` §B.4);
            # such factors are stable, not invalid, and pass the IC-mean
            # check below.
            if np.isfinite(oos_ir) and abs(oos_ir) < criteria.min_oos_ir:
                reasons.append("oos_ir_below_threshold")
            if abs(_ir_for_threshold(oos_rank_ic)) < criteria.min_oos_rank_ic_mean:
                reasons.append("oos_rank_ic_below_threshold")

        results.append(
            FactorValidationResult(
                expr_hash=entry.expr_hash,
                expr_str=entry.expr.to_qlib_string(),
                fitness=float(entry.fitness),
                passes=(not reasons),
                reasons=tuple(reasons),
                is_n_obs=is_n,
                is_ir=is_ir,
                is_rank_ic_mean=is_rank_ic,
                oos_n_obs=oos_n,
                oos_ir=oos_ir,
                oos_rank_ic_mean=oos_rank_ic,
            )
        )
    return results


def validate_run(
    run_dir,
    panel: Mapping[str, pd.DataFrame],
    forward_return: pd.DataFrame,
    criteria: ValidationCriteria,
) -> list[FactorValidationResult]:
    """Convenience wrapper: load the pool from disk then validate."""
    pool = FactorPool.load(run_dir)
    return validate_pool(pool, panel, forward_return, criteria)


# ---------------------------------------------------------------------------
# Pool-level pairwise filter
# ---------------------------------------------------------------------------


def filter_correlated(
    results: list[FactorValidationResult],
    panel: Mapping[str, pd.DataFrame],
    criteria: ValidationCriteria,
    pool: FactorPool,
) -> list[FactorValidationResult]:
    """Drop factors whose correlation against a higher-fitness
    already-kept factor exceeds ``max_pool_correlation``.

    The input ``results`` are scanned in `fitness` desc order. The
    output preserves order and updates `passes` / `reasons` for any
    dropped factor.
    """
    # Map expr_hash → PoolEntry so we can re-evaluate factors against
    # the FULL panel (not just OOS) for cross-correlation purposes.
    entries_by_hash = {e.expr_hash: e for e in pool.all_entries()}

    # Build sort order by fitness desc, expr_hash asc
    sorted_results = sorted(
        results, key=lambda r: (-r.fitness, r.expr_hash),
    )

    kept_values: list[tuple[FactorValidationResult, pd.Series]] = []
    new_results_by_hash: dict[int, FactorValidationResult] = {}

    for res in sorted_results:
        if not res.passes:
            # Already failed; pass through unchanged.
            new_results_by_hash[res.expr_hash] = res
            continue
        entry = entries_by_hash.get(res.expr_hash)
        if entry is None:
            new_results_by_hash[res.expr_hash] = res
            continue
        try:
            from .evaluator import evaluate_expression  # noqa: PLC0415

            factor_values = evaluate_expression(entry.expr, panel)
        except Exception:  # noqa: BLE001
            new_results_by_hash[res.expr_hash] = res
            continue
        if not isinstance(factor_values, pd.DataFrame):
            new_results_by_hash[res.expr_hash] = res
            continue
        stacked = factor_values.stack(future_stack=True)
        max_corr = 0.0
        for _kept_res, kept_stack in kept_values:
            joined = pd.concat({"new": stacked, "old": kept_stack}, axis=1).dropna()
            if len(joined) < 3:
                continue
            corr = joined["new"].corr(joined["old"])
            if pd.notna(corr):
                max_corr = max(max_corr, abs(float(corr)))
        if max_corr > criteria.max_pool_correlation:
            new_results_by_hash[res.expr_hash] = FactorValidationResult(
                expr_hash=res.expr_hash,
                expr_str=res.expr_str,
                fitness=res.fitness,
                passes=False,
                reasons=tuple([*res.reasons, "correlated_with_higher_fitness"]),
                is_n_obs=res.is_n_obs,
                is_ir=res.is_ir,
                is_rank_ic_mean=res.is_rank_ic_mean,
                oos_n_obs=res.oos_n_obs,
                oos_ir=res.oos_ir,
                oos_rank_ic_mean=res.oos_rank_ic_mean,
            )
        else:
            kept_values.append((res, stacked))
            new_results_by_hash[res.expr_hash] = res

    # Re-order the new results to match the original input order, so
    # callers see a stable layout.
    return [new_results_by_hash[r.expr_hash] for r in results]


__all__ = [
    "FactorValidationResult",
    "ValidationCriteria",
    "filter_correlated",
    "validate_pool",
    "validate_run",
]


def _unused_entry_marker(entry: PoolEntry) -> None:
    """Kept to silence import-time linting of PoolEntry usage."""
    _ = entry
