"""Shared IC (Information Coefficient) calculation helpers.

Both :mod:`signal_analyzer` and :mod:`factor_analyzer` compute
cross-sectional IC in the same way; this module avoids the duplication.

Two levels: :func:`compute_ic_for_group` is the per-date primitive, and
:func:`daily_ic_series` runs the shared date-groupby over a merged frame.
Callers still own the result aggregation (dropna / mean / std / naming),
which genuinely differs per call site.
"""

from __future__ import annotations

from typing import Any

# Minimum merged observations per lag for a meaningful IC summary.
# Shared between factor_analyzer and signal_analyzer.
MIN_IC_OBSERVATIONS_PER_LAG = 10


def compute_ic_for_group(group: Any, method: str) -> float:
    """Compute cross-sectional IC for a single date group.

    Parameters
    ----------
    group : pd.DataFrame
        DataFrame with columns ``"pred"``/``"factor"`` (signal) and ``"ret"``
        (forward return) for one date.  Must have at least 3 rows.
    method : str
        ``"rank"`` for Spearman IC, ``"normal"`` for Pearson IC.

    Returns
    -------
    float
        IC value, or ``nan`` when the group is too small.
    """
    import numpy as np

    if len(group) < 3:
        return np.nan

    # Support both "pred" (signal_analyzer) and "factor" (factor_analyzer)
    # column naming so one helper serves both callers.
    signal_col = "pred" if "pred" in group.columns else "factor"

    if method == "rank":
        return float(group[signal_col].rank().corr(group["ret"].rank()))
    return float(group[signal_col].corr(group["ret"]))


def daily_ic_series(merged: Any, method: str) -> Any:
    """Per-date cross-sectional IC series for a merged signal/return frame.

    ``merged`` carries a ``datetime`` index level plus a signal column
    (``pred`` or ``factor``) and a ``ret`` column. Groups by date and
    applies :func:`compute_ic_for_group`, returning one IC per date. The
    caller owns any aggregation (dropna / mean / std / naming).
    """
    return merged.groupby(level="datetime").apply(
        lambda g: compute_ic_for_group(g, method),
        include_groups=False,
    )
