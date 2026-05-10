"""Shared IC (Information Coefficient) calculation helpers.

Both :mod:`signal_analyzer` and :mod:`factor_analyzer` compute
cross-sectional IC in the same way; this module avoids the duplication.

The top-level helpers are intentionally thin — just the rank/normal IC
logic — so callers own the groupby and result aggregation.
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
