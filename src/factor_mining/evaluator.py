"""Factor evaluator: recursive walker + IC / IR / RankIC / turnover.

Phase 2 module. Takes a Phase 1 ``Expression`` and a panel loaded by
the Phase 2 ``FactorMiningDataView`` and produces an
``EvaluationResult`` carrying the metrics fitness consumes.

No qlib import, no ``src.pit`` import. The IC primitive is reused
from ``src.core._ic_utils.compute_ic_for_group`` per
``inventory.md`` §B.3 recommendation.
"""

from __future__ import annotations

from collections.abc import Mapping
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
]

PanelLike = Mapping[str, pd.DataFrame]
WalkResult = pd.DataFrame | int


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


def _coverage(factor_values: pd.DataFrame) -> float:
    """Fraction of cells in ``factor_values`` that are non-NaN."""
    if factor_values.empty:
        return 0.0
    arr = factor_values.to_numpy()
    finite = np.isfinite(arr).sum()
    return float(finite) / float(arr.size) if arr.size > 0 else 0.0


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
        coverage=_coverage(factor_values),
        n_obs_per_day_min=_n_obs_per_day_min(factor_values, fwd),
    )
