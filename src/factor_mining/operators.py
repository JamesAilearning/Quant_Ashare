"""CPU operator library for factor mining (Phase 1).

Twenty-eight pure-Python operator implementations per
``docs/factor_mining/scale_invariance.md`` §4. No qlib import, no data
fetch, no GPU code. Every operator handles NaN, zero, negative,
constant, empty, and PIT-gap inputs defensively. All ``ts_*``
operators use ``min_periods=window`` so partial windows never bridge
a NaN gap (the unit-level guarantee that mined factors will not cross
entity boundaries once Phase 2 wires PIT data in).

Shape conventions
-----------------

Operators are written to work on pandas Series (single ticker,
datetime index) and pandas DataFrames (date index, columns = tickers)
interchangeably for arithmetic / unary / ``ts_*`` operators. The
``cs_*`` operators are inherently cross-sectional and require a 2-D
DataFrame whose rows are dates and whose columns are tickers; calling
them on a 1-D Series raises ``ValueError``.

These are the Phase 1 reference implementations. Phase 4 may add GPU
equivalents whose outputs must match these within ``1e-5`` for finite
values — CPU stays the correctness reference.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _no_inf(x):
    """Replace ±Inf with NaN while preserving the pandas container type."""
    if isinstance(x, (pd.Series, pd.DataFrame)):
        return x.where(~np.isinf(x), np.nan)
    return np.where(np.isinf(x), np.nan, x)


# ============================================================================
# Arithmetic (T_FLOAT × T_FLOAT → T_FLOAT)
# ============================================================================


def add(a, b):
    """Element-wise addition."""
    return a + b


def sub(a, b):
    """Element-wise subtraction."""
    return a - b


def mul(a, b):
    """Element-wise multiplication."""
    return a * b


def div_safe(a, b):
    """Element-wise division with NaN (not ±Inf) at near-zero denominators.

    Treats ``|b| <= 1e-12`` as zero and returns NaN at those positions.
    Any residual ±Inf (e.g. from arithmetic on Inf inputs) is also
    replaced with NaN.
    """
    eps = 1e-12
    if isinstance(b, (pd.Series, pd.DataFrame)):
        b_safe = b.where(b.abs() > eps)
    else:
        b_arr = np.asarray(b, dtype=float)
        b_safe = np.where(np.abs(b_arr) > eps, b_arr, np.nan)
    return _no_inf(a / b_safe)


# ============================================================================
# Unary (T_FLOAT → T_FLOAT)
# ============================================================================


def neg(x):
    """Negation."""
    return -x


def abs_(x):
    """Absolute value. Exposed as ``abs`` in the operator registry."""
    if isinstance(x, (pd.Series, pd.DataFrame)):
        return x.abs()
    return np.abs(x)


def sign(x):
    """Sign function; preserves NaN; ``sign(0) == 0``."""
    return np.sign(x)


def log_safe(x):
    """Natural log; inputs ``<= 0`` return NaN (no raise)."""
    if isinstance(x, (pd.Series, pd.DataFrame)):
        return np.log(x.where(x > 0))
    arr = np.asarray(x, dtype=float)
    return np.log(np.where(arr > 0, arr, np.nan))


def sqrt_safe(x):
    """Square root; inputs ``< 0`` return NaN (no raise). ``sqrt(0) == 0``."""
    if isinstance(x, (pd.Series, pd.DataFrame)):
        return np.sqrt(x.where(x >= 0))
    arr = np.asarray(x, dtype=float)
    return np.sqrt(np.where(arr >= 0, arr, np.nan))


# ============================================================================
# Time-series (T_FLOAT × T_INT_WINDOW → T_FLOAT), min_periods=window
# ============================================================================
#
# Every ``ts_*`` uses ``min_periods=window`` so a NaN hole of width ≥1
# zero out the rolling result for ``window`` positions covering AND
# trailing the hole. This is the PIT-gap invariant.
# ============================================================================


def ts_mean(x, n):
    """Rolling mean with ``min_periods=n``."""
    return x.rolling(n, min_periods=n).mean()


def ts_std(x, n):
    """Rolling standard deviation with ``min_periods=n``."""
    return x.rolling(n, min_periods=n).std()


def ts_max(x, n):
    """Rolling max with ``min_periods=n``."""
    return x.rolling(n, min_periods=n).max()


def ts_min(x, n):
    """Rolling min with ``min_periods=n``."""
    return x.rolling(n, min_periods=n).min()


def ts_sum(x, n):
    """Rolling sum with ``min_periods=n``."""
    return x.rolling(n, min_periods=n).sum()


def ts_delta(x, n):
    """``x_t - x_{t-n}``."""
    return x - x.shift(n)


def ts_pctchange(x, n):
    """``x_t / x_{t-n} - 1``; zero/near-zero denom → NaN; ±Inf → NaN."""
    return div_safe(x, x.shift(n)) - 1


def _rank_window(arr):
    """Normalised 0..1 rank of the last element within the window.

    Constant window → 0.5 (mid rank). Any NaN in window → NaN.
    """
    if np.isnan(arr).any():
        return np.nan
    if arr.max() == arr.min():
        return 0.5
    rank_zero_based = (arr <= arr[-1]).sum() - 1
    return rank_zero_based / (len(arr) - 1)


def ts_rank(x, n):
    """Rolling rank of the last element, normalised to ``[0, 1]``.

    Constant window returns ``0.5`` (per ``scale_invariance.md`` §4
    note for the ``ts_rank`` defensive case).
    """
    return x.rolling(n, min_periods=n).apply(_rank_window, raw=True)


def _argmax_window(arr):
    if np.isnan(arr).any():
        return np.nan
    return float(np.argmax(arr))


def _argmin_window(arr):
    if np.isnan(arr).any():
        return np.nan
    return float(np.argmin(arr))


def ts_argmax(x, n):
    """Rolling index (0..n-1, within window) of the max."""
    return x.rolling(n, min_periods=n).apply(_argmax_window, raw=True)


def ts_argmin(x, n):
    """Rolling index (0..n-1, within window) of the min."""
    return x.rolling(n, min_periods=n).apply(_argmin_window, raw=True)


def ts_corr(x, y, n):
    """Rolling Pearson correlation; ±Inf → NaN."""
    return _no_inf(x.rolling(n, min_periods=n).corr(y))


def ts_skew(x, n):
    """Rolling skewness with ``min_periods=n``."""
    return x.rolling(n, min_periods=n).skew()


def ts_kurt(x, n):
    """Rolling excess kurtosis with ``min_periods=n``."""
    return x.rolling(n, min_periods=n).kurt()


def ts_decay_linear(x, n):
    """Linear-weighted moving average; weights ``1..n`` normalised."""
    weights = np.arange(1, n + 1, dtype=float)
    weights = weights / weights.sum()

    def _decay(arr):
        if np.isnan(arr).any():
            return np.nan
        return float(np.dot(weights, arr))

    return x.rolling(n, min_periods=n).apply(_decay, raw=True)


# ============================================================================
# Cross-sectional (T_FLOAT → T_CSF) — per-date row-wise reductions
# ============================================================================
#
# These require a 2-D DataFrame (dates × tickers). The grammar's
# scale-invariance gate ensures only PURE inputs reach here.
# ============================================================================


def _require_dataframe(x, op_name):
    if isinstance(x, pd.Series):
        raise ValueError(
            f"{op_name} is cross-sectional; requires a 2-D DataFrame "
            "(date index × ticker columns), not a 1-D Series"
        )


def cs_rank(x):
    """Per-row rank normalised to ``[-0.5, 0.5]`` via ``DataFrame.rank``."""
    _require_dataframe(x, "cs_rank")
    return x.rank(axis=1, pct=True) - 0.5


def cs_zscore(x):
    """Per-row z-score; near-zero std → 0 (not NaN), preserves per-cell NaN."""
    _require_dataframe(x, "cs_zscore")
    mean = x.mean(axis=1)
    std = x.std(axis=1)
    eps = 1e-12
    std_safe = std.where(std.abs() > eps)
    z = x.sub(mean, axis=0).div(std_safe, axis=0)
    # Where std≈0 the division yielded NaN; replace with 0. Then
    # re-introduce NaN at cells where the original x was NaN, so we
    # don't fabricate a z-score for missing data.
    z = z.fillna(0).where(x.notna(), np.nan)
    return z


def cs_demean(x):
    """Per-row demean: ``x - row_mean``."""
    _require_dataframe(x, "cs_demean")
    return x.sub(x.mean(axis=1), axis=0)


def cs_winsorize(x, lower=0.05, upper=0.95):
    """Per-row winsorise to the [``lower``, ``upper``] quantile band."""
    _require_dataframe(x, "cs_winsorize")
    lower_q = x.quantile(lower, axis=1)
    upper_q = x.quantile(upper, axis=1)
    return x.clip(lower=lower_q, upper=upper_q, axis=0)


# ============================================================================
# Conditional (T_FLOAT × T_FLOAT × T_FLOAT → T_FLOAT)
# ============================================================================


def where(cond, a, b):
    """``cond > 0 ? a : b``. Element-wise; NaN in ``cond`` → NaN result."""
    if isinstance(cond, (pd.Series, pd.DataFrame)):
        mask = cond > 0
        result = a.where(mask, b) if isinstance(a, (pd.Series, pd.DataFrame)) else np.where(mask, a, b)
        # NaN in cond should not silently route to either branch
        nan_mask = cond.isna() if hasattr(cond, "isna") else np.isnan(cond)
        if isinstance(result, (pd.Series, pd.DataFrame)):
            return result.where(~nan_mask, np.nan)
        return np.where(nan_mask, np.nan, result)
    arr = np.asarray(cond, dtype=float)
    return np.where(np.isnan(arr), np.nan, np.where(arr > 0, a, b))
