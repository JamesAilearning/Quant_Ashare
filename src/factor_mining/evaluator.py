"""Factor evaluator: recursive walker + IC / IR / RankIC / turnover.

Phase 2 module. Takes a Phase 1 ``Expression`` and a panel loaded by
the Phase 2 ``FactorMiningDataView`` and produces an
``EvaluationResult`` carrying the metrics fitness consumes.

No qlib import, no ``src.pit`` import. The IC primitive is reused
from ``src.core._ic_utils.compute_ic_for_group`` per
``inventory.md`` §B.3 recommendation.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.core._ic_utils import compute_ic_for_group

from .expression import Expression, OperatorCall, Terminal
from .grammar import REGISTRY

__all__ = [
    "EvaluationResult",
    "PanelLike",
    "WalkResult",
    "evaluate_expression",
    "evaluate_factor",
    "max_abs_corr",
]

PanelLike = Mapping[str, pd.DataFrame]
WalkResult = pd.DataFrame | int


def max_abs_corr(
    new_stack: pd.Series,
    other_stacks: Iterable[pd.Series],
    *,
    min_overlap: int = 3,
) -> float:
    """Maximum |Pearson correlation| of ``new_stack`` against each series in
    ``other_stacks`` (both already ``.stack()``-ed to a flat ``(date,
    instrument)`` index). Shared by the GP novelty penalty, the validator's
    pool-correlation gate, and ``FactorPool.correlation_with`` — each keeps its
    own outer guards (the GP w_corr / OOM short-circuit, the spec contract on
    ``correlation_with``); only this inner pairwise loop is shared.

    A pair with fewer than ``min_overlap`` jointly-non-NaN cells is skipped. The
    correlation is admitted only when ``np.isfinite`` — NOT ``pd.notna``: the two
    diverge on ±inf (``pd.notna(inf)`` is True), and a degenerate value must
    never poison the maximum, so this NEVER returns a non-finite float. Returns
    0.0 when no eligible pair correlates. Pure pandas/numpy — no qlib / PIT (D5).
    """
    max_abs = 0.0
    for other_stack in other_stacks:
        joined = pd.concat({"new": new_stack, "old": other_stack}, axis=1).dropna()
        if len(joined) < min_overlap:
            continue
        corr = joined["new"].corr(joined["old"])
        if np.isfinite(corr):
            max_abs = max(max_abs, abs(float(corr)))
    return max_abs


def evaluate_expression(expr: Expression, panel: PanelLike) -> WalkResult:
    """Recursively evaluate ``expr`` against the loaded ``panel``.

    Terminal nodes resolve to:
    - feature names (``$close`` etc.) → the corresponding DataFrame
      from ``panel`` (date × ticker);
    - integer window literals (``"20"``) → the integer value (consumed
      by ``ts_*`` operators' second argument).

    OperatorCall nodes resolve to ``REGISTRY.get(op).compute_fn(*children)``.
    The walker is single-pass and stateless; the GP engine (Phase 3)
    is the natural place to add subtree caching.
    """
    if isinstance(expr, Terminal):
        if expr.name.startswith("$"):
            if expr.name not in panel:
                raise KeyError(
                    f"feature {expr.name!r} not present in the panel; "
                    f"available: {sorted(panel.keys())}"
                )
            return panel[expr.name]
        if expr.name.isdigit():
            return int(expr.name)
        raise ValueError(f"Cannot evaluate terminal {expr.name!r}")
    if isinstance(expr, OperatorCall):
        op = REGISTRY.get(expr.op_name)
        if op is None:  # pragma: no cover — guarded at construction
            raise ValueError(f"Unknown operator at evaluate time: {expr.op_name!r}")
        args = [evaluate_expression(c, panel) for c in expr.children]
        return op.compute_fn(*args)
    raise TypeError(f"Cannot evaluate node of type {type(expr).__name__}")


@dataclass(frozen=True)
class EvaluationResult:
    """Per-factor metric bundle produced by ``evaluate_factor``."""

    factor_values: pd.DataFrame
    ic_mean: float
    ic_std: float
    ir: float
    rank_ic_mean: float
    rank_ic_std: float
    rank_ir: float
    turnover_daily: float
    coverage: float
    n_obs_per_day_min: int


def _ic_per_day(
    factor_values: pd.DataFrame,
    forward_return: pd.DataFrame,
    method: str,
) -> pd.Series:
    """Per-date cross-sectional IC via the shared primitive in
    ``src.core._ic_utils``.

    Returns a Series indexed by date; NaN for dates with fewer than
    ``MIN_IC_OBSERVATIONS_PER_LAG`` observations (handled inside
    ``compute_ic_for_group``).
    """
    if factor_values.empty or forward_return.empty:
        return pd.Series(dtype=float)
    f = factor_values.stack(future_stack=True)
    r = forward_return.stack(future_stack=True)
    f.index = f.index.set_names(["datetime", "instrument"])
    r.index = r.index.set_names(["datetime", "instrument"])
    df = pd.DataFrame({"factor": f, "ret": r}).dropna()
    if df.empty:
        return pd.Series(dtype=float)
    return df.groupby(level="datetime", sort=True).apply(
        lambda g: compute_ic_for_group(g, method)
    )


def _ir(ic_mean: float, ic_std: float) -> float:
    """IR convention: NaN when ``|ic_std| < 1e-9`` (per ``inventory.md`` §B.4).

    The two existing analyzers (``signal_analyzer``, ``factor_analyzer``)
    use the same convention so factor-mining fitness numbers stay
    comparable to model-level IR.
    """
    if not np.isfinite(ic_std) or abs(ic_std) < 1e-9:
        return float("nan")
    return float(ic_mean) / float(ic_std)


def _turnover_daily(factor_values: pd.DataFrame) -> float:
    """Mean absolute day-over-day change, averaged across (date, ticker).

    For a cs_rank-normalised factor in [-0.5, 0.5] this lives in
    [0, 1]; the fitness function multiplies by ``252 × cost_rate`` to
    annualise per ``decisions.md`` D1.
    """
    if len(factor_values) < 2:
        return 0.0
    diff = factor_values.diff().abs()
    stacked = diff.stack(future_stack=True)
    if stacked.empty:
        return 0.0
    val = float(stacked.mean())
    return val if np.isfinite(val) else 0.0


def _coverage(
    factor_values: pd.DataFrame,
    universe_mask: pd.DataFrame | None = None,
) -> float:
    """Fraction of factor cells that are non-NaN.

    When ``universe_mask`` is supplied (a boolean date × ticker frame of
    universe membership), coverage is measured **relative to member
    cells only**: the denominator is the count of (date, ticker) cells
    where the ticker is a universe member on that day, and the numerator
    is the count of those member cells that also carry a finite factor
    value.

    This is the correct denominator for a survivorship-corrected PIT
    panel. Such a panel is the *union* of every ticker that was ever a
    member over the window, so on any given day a large fraction of the
    union columns are legitimately NaN simply because those tickers are
    not members that day (not yet listed, rotated out, or delisted).
    Counting those non-member cells as "missing coverage" makes
    ``coverage_min`` unsatisfiable on real data: even a perfect factor
    like ``cs_rank($close)`` scores ~0.62 union-coverage and fails the
    0.8 gate, so every GP candidate is rejected (n_invalid == population).
    Members-only, that same factor scores ~0.99.

    The denominator is computed over the MASK's own domain, and the factor
    is aligned ONTO the mask (not the reverse): a member (date, ticker) that
    the factor panel omits entirely — e.g. a member ticker/row the PIT
    provider drops because it is all-missing, while ``universe_mask`` still
    reports it in-universe — stays in the denominator as *uncovered*
    (reindex → NaN → not finite) instead of being silently dropped, which
    would inflate coverage (Codex P2 on #217).

    When ``universe_mask`` is None (synthetic / dense panels, or any
    caller that does not supply membership), the denominator is ALL
    cells — the original behaviour, preserved for backward compatibility.
    """
    if factor_values.empty:
        return 0.0
    if universe_mask is None:
        arr = factor_values.to_numpy()
        finite = np.isfinite(arr)
        return float(finite.sum()) / float(arr.size) if arr.size > 0 else 0.0
    mask = universe_mask.fillna(False).to_numpy(dtype=bool)
    denom = int(mask.sum())
    if denom == 0:
        return 0.0
    # Align the factor onto the mask's (index, columns) so member cells the
    # factor omits become NaN (uncovered) rather than shrinking the denom.
    aligned = factor_values.reindex(
        index=universe_mask.index, columns=universe_mask.columns
    )
    finite = np.isfinite(aligned.to_numpy())
    num = int((finite & mask).sum())
    return float(num) / float(denom)


def _n_obs_per_day_min(
    factor_values: pd.DataFrame, forward_return: pd.DataFrame,
) -> int:
    """Minimum count of jointly-observed (factor, fwd_ret) cells per
    day. Useful for spotting days where the cross-section is too thin
    to compute a meaningful IC."""
    if factor_values.empty or forward_return.empty:
        return 0
    f_mask = factor_values.notna()
    r_mask = forward_return.reindex_like(factor_values).notna()
    both = (f_mask & r_mask).sum(axis=1)
    return int(both.min()) if len(both) > 0 else 0


def evaluate_factor(
    expr: Expression,
    panel: PanelLike,
    forward_return: pd.DataFrame,
    *,
    method: str = "rank",
    universe_mask: pd.DataFrame | None = None,
) -> EvaluationResult:
    """Walk ``expr``, compute its factor values, then produce the
    full metric bundle against ``forward_return``.

    Parameters
    ----------
    expr
        A Phase 1 ``Expression`` whose root type is
        ``ExprType("CSF", "PURE")`` (the grammar enforces this).
    panel
        Mapping of field-name → date × ticker ``DataFrame``, as
        produced by ``FactorMiningDataView.load_panel``.
    forward_return
        date × ticker forward-return panel, as produced by
        ``FactorMiningDataView.forward_return``.
    method
        ``"rank"`` (Spearman) or ``"normal"`` (Pearson) — selects which
        becomes the headline ``ic_mean`` / ``ic_std`` / ``ir``. The
        ``rank_ic_mean`` / ``rank_ic_std`` / ``rank_ir`` fields are
        *always* Spearman regardless of ``method``, so callers can rely
        on them as a separate signal in fitness or downstream filters.

        Note: with ``method="rank"``, ``ic_mean == rank_ic_mean`` (both
        are Spearman). The miner uses ``"normal"`` so the fitness terms
        ``w_ic·|ic_mean|`` and ``w_rankic·|rank_ic_mean|`` are
        independent (Pearson + Spearman) rather than a redundant
        ``(w_ic + w_rankic)·|rank|``.
    universe_mask
        Optional boolean date × ticker frame of universe membership. When
        supplied, ``coverage`` is computed members-only (denominator =
        member cells), which is what survivorship-corrected PIT panels
        need — see ``_coverage``. When None (synthetic / dense panels),
        coverage falls back to the all-cells fraction (legacy behaviour).
    """
    walked = evaluate_expression(expr, panel)
    if not isinstance(walked, pd.DataFrame):
        raise TypeError(
            "Expression evaluation did not produce a DataFrame; "
            "root expression must produce a cross-sectional factor "
            f"(got {type(walked).__name__})"
        )
    factor_values = walked

    # Align forward_return to factor_values' index/columns so per-day
    # joins are clean.
    fwd = forward_return.reindex_like(factor_values)

    ic_pearson = _ic_per_day(factor_values, fwd, method="normal")
    ic_rank = _ic_per_day(factor_values, fwd, method="rank")

    rank_mean, rank_std = float(ic_rank.mean()), float(ic_rank.std())
    if method == "rank":
        headline_mean, headline_std = rank_mean, rank_std
    else:
        headline_mean, headline_std = float(ic_pearson.mean()), float(ic_pearson.std())

    return EvaluationResult(
        factor_values=factor_values,
        ic_mean=headline_mean,
        ic_std=headline_std,
        ir=_ir(headline_mean, headline_std),
        rank_ic_mean=rank_mean,
        rank_ic_std=rank_std,
        rank_ir=_ir(rank_mean, rank_std),
        turnover_daily=_turnover_daily(factor_values),
        coverage=_coverage(factor_values, universe_mask),
        n_obs_per_day_min=_n_obs_per_day_min(factor_values, fwd),
    )
