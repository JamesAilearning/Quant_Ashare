"""Per-operator edge-case tests for the Phase 1 CPU operator library.

Covers: normal inputs, NaN, zero, negative, constant, single-row,
empty, AND a PIT-gap input (a series with a NaN hole that MUST NOT
be bridged by ``min_periods=window`` rolling ops). Per
``docs/factor_mining/factor_mining_claude_code_design.md`` §5.3 and
``factor_mining_phase1_preflight.md`` §4.1.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.factor_mining.operators import (
    abs_,
    add,
    cs_demean,
    cs_rank,
    cs_winsorize,
    cs_zscore,
    div_safe,
    log_safe,
    mul,
    neg,
    sign,
    sqrt_safe,
    sub,
    ts_argmax,
    ts_argmin,
    ts_corr,
    ts_decay_linear,
    ts_delta,
    ts_kurt,
    ts_max,
    ts_mean,
    ts_min,
    ts_pctchange,
    ts_rank,
    ts_skew,
    ts_std,
    ts_sum,
    where,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _gap_series() -> pd.Series:
    """``[1, 2, 3, NaN, NaN, NaN, 7, 8, 9, 10]`` — a PIT-gap fixture."""
    return pd.Series([1.0, 2.0, 3.0, np.nan, np.nan, np.nan, 7.0, 8.0, 9.0, 10.0])


def _cs_panel() -> pd.DataFrame:
    """3 dates × 4 tickers small cross-sectional panel."""
    return pd.DataFrame(
        {
            "A": [1.0, 5.0, np.nan],
            "B": [2.0, 5.0, 6.0],
            "C": [3.0, 5.0, 7.0],
            "D": [4.0, 5.0, 8.0],
        }
    )


# ===========================================================================
# Arithmetic
# ===========================================================================


def test_add_normal():
    a = pd.Series([1.0, 2.0, 3.0])
    b = pd.Series([10.0, 20.0, 30.0])
    pd.testing.assert_series_equal(add(a, b), pd.Series([11.0, 22.0, 33.0]))


def test_add_preserves_nan():
    a = pd.Series([1.0, np.nan, 3.0])
    b = pd.Series([10.0, 20.0, np.nan])
    result = add(a, b)
    assert np.isnan(result.iloc[1])
    assert np.isnan(result.iloc[2])
    assert result.iloc[0] == 11.0


def test_sub_noncommutative():
    a = pd.Series([1.0, 2.0])
    b = pd.Series([3.0, 4.0])
    assert (sub(a, b) != sub(b, a)).any()


def test_mul_with_zero():
    a = pd.Series([1.0, 2.0, np.nan])
    b = pd.Series([0.0, 5.0, 0.0])
    result = mul(a, b)
    assert result.iloc[0] == 0.0
    assert result.iloc[1] == 10.0
    assert np.isnan(result.iloc[2])


def test_div_safe_zero_denominator_returns_nan():
    a = pd.Series([1.0, 2.0, 3.0, 4.0])
    b = pd.Series([2.0, 0.0, -0.0, 1e-15])
    result = div_safe(a, b)
    assert result.iloc[0] == 0.5
    assert np.isnan(result.iloc[1])  # zero
    assert np.isnan(result.iloc[2])  # negative zero
    assert np.isnan(result.iloc[3])  # near-zero (< eps)


def test_div_safe_no_infinity():
    a = pd.Series([1.0, 1.0, 1.0])
    b = pd.Series([1e-20, 1e-15, 0.0])
    result = div_safe(a, b)
    assert not np.isinf(result).any()
    assert np.isnan(result).all()


def test_div_safe_normal():
    a = pd.Series([10.0, 20.0, 30.0])
    b = pd.Series([2.0, 4.0, 5.0])
    result = div_safe(a, b)
    pd.testing.assert_series_equal(result, pd.Series([5.0, 5.0, 6.0]))


def test_div_safe_empty():
    a = pd.Series([], dtype=float)
    b = pd.Series([], dtype=float)
    result = div_safe(a, b)
    assert len(result) == 0


# ===========================================================================
# Unary
# ===========================================================================


def test_neg_preserves_nan():
    x = pd.Series([1.0, -2.0, np.nan])
    result = neg(x)
    assert result.iloc[0] == -1.0
    assert result.iloc[1] == 2.0
    assert np.isnan(result.iloc[2])


def test_abs_preserves_nan():
    x = pd.Series([-1.0, 2.0, np.nan, 0.0])
    result = abs_(x)
    pd.testing.assert_series_equal(
        result.fillna(-999),
        pd.Series([1.0, 2.0, -999.0, 0.0]),
    )
    assert np.isnan(result.iloc[2])


def test_sign_zero_and_nan():
    x = pd.Series([-3.0, 0.0, 5.0, np.nan])
    result = sign(x)
    assert result.iloc[0] == -1.0
    assert result.iloc[1] == 0.0  # sign(0) == 0
    assert result.iloc[2] == 1.0
    assert np.isnan(result.iloc[3])


def test_log_safe_negative_and_zero_return_nan():
    x = pd.Series([-1.0, 0.0, 1.0, np.e])
    result = log_safe(x)
    assert np.isnan(result.iloc[0])  # negative → NaN
    assert np.isnan(result.iloc[1])  # zero → NaN (log(0) undefined)
    assert np.isclose(result.iloc[2], 0.0)
    assert np.isclose(result.iloc[3], 1.0)


def test_sqrt_safe_negative_returns_nan_zero_returns_zero():
    x = pd.Series([-1.0, 0.0, 4.0, 9.0])
    result = sqrt_safe(x)
    assert np.isnan(result.iloc[0])
    assert result.iloc[1] == 0.0  # sqrt(0) = 0
    assert result.iloc[2] == 2.0
    assert result.iloc[3] == 3.0


# ===========================================================================
# Time-series: min_periods=window AND PIT-gap no-bridge
# ===========================================================================


def test_ts_mean_normal():
    x = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    result = ts_mean(x, 3)
    assert np.isnan(result.iloc[0])
    assert np.isnan(result.iloc[1])
    assert result.iloc[2] == 2.0
    assert result.iloc[3] == 3.0
    assert result.iloc[4] == 4.0


def test_ts_mean_pit_gap_no_bridge():
    """A NaN hole MUST NOT be bridged. Window cells covering the hole
    AND ``window - 1`` cells trailing the hole MUST be NaN."""
    x = _gap_series()  # [1,2,3,NaN,NaN,NaN,7,8,9,10]
    result = ts_mean(x, 3)
    assert result.iloc[2] == 2.0  # last clean window before hole
    # hole + trailing window-1 cells all NaN
    for i in [3, 4, 5, 6, 7]:
        assert np.isnan(result.iloc[i]), f"position {i} should be NaN"
    assert result.iloc[8] == 8.0
    assert result.iloc[9] == 9.0


def test_ts_std_min_periods_window():
    x = pd.Series([1.0, 2.0, 3.0, 4.0])
    result = ts_std(x, 4)
    # Only one full window at the end
    assert np.isnan(result.iloc[0])
    assert np.isnan(result.iloc[2])
    assert not np.isnan(result.iloc[3])


def test_ts_max_min_pit_gap():
    x = _gap_series()
    rmax = ts_max(x, 3)
    rmin = ts_min(x, 3)
    for i in [3, 4, 5, 6, 7]:
        assert np.isnan(rmax.iloc[i])
        assert np.isnan(rmin.iloc[i])
    assert rmax.iloc[8] == 9.0
    assert rmin.iloc[8] == 7.0


def test_ts_sum_pit_gap():
    x = _gap_series()
    result = ts_sum(x, 3)
    for i in [3, 4, 5, 6, 7]:
        assert np.isnan(result.iloc[i])
    assert result.iloc[2] == 6.0
    assert result.iloc[8] == 24.0


def test_ts_delta_normal():
    x = pd.Series([1.0, 2.0, 4.0, 7.0])
    result = ts_delta(x, 1)
    assert np.isnan(result.iloc[0])
    assert result.iloc[1] == 1.0
    assert result.iloc[2] == 2.0
    assert result.iloc[3] == 3.0


def test_ts_pctchange_zero_denominator():
    x = pd.Series([1.0, 0.0, 2.0])
    result = ts_pctchange(x, 1)
    # position 2: x=2, prev=0 → 2/0 → div_safe NaN → NaN - 1 = NaN
    assert np.isnan(result.iloc[0])
    assert np.isnan(result.iloc[2])  # zero denom in div_safe


def test_ts_rank_constant_window_returns_half():
    x = pd.Series([5.0, 5.0, 5.0, 5.0, 5.0])
    result = ts_rank(x, 3)
    assert result.iloc[2] == 0.5
    assert result.iloc[3] == 0.5
    assert result.iloc[4] == 0.5


def test_ts_rank_increasing():
    x = pd.Series([1.0, 2.0, 3.0, 4.0])
    result = ts_rank(x, 3)
    # Each full window [a, b, c] with c the max → rank=1.0
    assert result.iloc[2] == 1.0
    assert result.iloc[3] == 1.0


def test_ts_rank_pit_gap():
    x = _gap_series()
    result = ts_rank(x, 3)
    for i in [3, 4, 5, 6, 7]:
        assert np.isnan(result.iloc[i])


def test_ts_argmax_argmin():
    x = pd.Series([3.0, 1.0, 5.0, 2.0, 4.0])
    rmax = ts_argmax(x, 3)
    rmin = ts_argmin(x, 3)
    # window [3,1,5] → argmax=2, argmin=1
    assert rmax.iloc[2] == 2.0
    assert rmin.iloc[2] == 1.0
    # window [1,5,2] → argmax=1, argmin=0
    assert rmax.iloc[3] == 1.0
    assert rmin.iloc[3] == 0.0


def test_ts_corr_normal_and_constant():
    x = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    y = pd.Series([2.0, 4.0, 6.0, 8.0, 10.0])  # perfectly correlated
    result = ts_corr(x, y, 3)
    assert np.isclose(result.iloc[2], 1.0)
    # Constant window in either → NaN (std=0)
    z = pd.Series([5.0, 5.0, 5.0, 5.0, 5.0])
    result_const = ts_corr(x, z, 3)
    # std(z) = 0 in every window → corr undefined (NaN or replaced)
    for i in [2, 3, 4]:
        assert np.isnan(result_const.iloc[i])


def test_ts_corr_no_infinity():
    x = pd.Series([1.0, 2.0, 3.0])
    y = pd.Series([1.0, 1.0, 1.0])
    result = ts_corr(x, y, 3)
    assert not np.isinf(result).any()


def test_ts_corr_pit_gap():
    x = _gap_series()
    y = pd.Series([2.0, 4.0, 6.0, np.nan, np.nan, np.nan, 14.0, 16.0, 18.0, 20.0])
    result = ts_corr(x, y, 3)
    for i in [3, 4, 5, 6, 7]:
        assert np.isnan(result.iloc[i])


def test_ts_skew_kurt_min_periods():
    x = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    rskew = ts_skew(x, 5)
    rkurt = ts_kurt(x, 5)
    assert np.isnan(rskew.iloc[3])  # not enough points
    assert not np.isnan(rskew.iloc[4])
    assert np.isnan(rkurt.iloc[3])
    assert not np.isnan(rkurt.iloc[4])


def test_ts_decay_linear_simple():
    # Linear weights: w = [1, 2, 3] / 6 → result at position 2 is
    # (1*x0 + 2*x1 + 3*x2) / 6
    x = pd.Series([1.0, 2.0, 3.0, 4.0])
    result = ts_decay_linear(x, 3)
    expected_at_2 = (1 * 1 + 2 * 2 + 3 * 3) / 6
    expected_at_3 = (1 * 2 + 2 * 3 + 3 * 4) / 6
    assert np.isclose(result.iloc[2], expected_at_2)
    assert np.isclose(result.iloc[3], expected_at_3)


def test_ts_decay_linear_pit_gap():
    x = _gap_series()
    result = ts_decay_linear(x, 3)
    for i in [3, 4, 5, 6, 7]:
        assert np.isnan(result.iloc[i])


# ===========================================================================
# Cross-sectional
# ===========================================================================


def test_cs_rank_panel_shape():
    df = _cs_panel()
    result = cs_rank(df)
    # Each row's ranks should be in [-0.5, 0.5]
    assert (result.min(axis=1) >= -0.5).all()
    assert (result.max(axis=1) <= 0.5).all()
    # Row 0: 1<2<3<4 → ranks normalized are 0.25, 0.5, 0.75, 1.0 ; minus 0.5 → -0.25, 0, 0.25, 0.5
    expected_row0 = pd.Series([-0.25, 0.0, 0.25, 0.5], index=["A", "B", "C", "D"])
    pd.testing.assert_series_equal(
        result.iloc[0], expected_row0, check_names=False
    )


def test_cs_rank_rejects_series():
    with pytest.raises(ValueError):
        cs_rank(pd.Series([1.0, 2.0, 3.0]))


def test_cs_zscore_constant_row_returns_zero():
    df = _cs_panel()
    result = cs_zscore(df)
    # Row 1 is constant [5, 5, 5, 5] → all zeros (NOT NaN)
    assert (result.iloc[1] == 0).all()


def test_cs_zscore_preserves_nan():
    df = _cs_panel()
    result = cs_zscore(df)
    # Row 2 has NaN in column "A" → result at [2, A] must be NaN
    assert np.isnan(result.iloc[2, 0])
    # Other cells of row 2 are finite z-scores
    assert not np.isnan(result.iloc[2, 1])
    assert not np.isnan(result.iloc[2, 2])
    assert not np.isnan(result.iloc[2, 3])


def test_cs_zscore_normal_row():
    df = _cs_panel()
    result = cs_zscore(df)
    # Row 0: [1, 2, 3, 4], mean=2.5, std (ddof=1) = sqrt(5/3)
    std = np.sqrt(5 / 3)
    expected = pd.Series(
        [(1 - 2.5) / std, (2 - 2.5) / std, (3 - 2.5) / std, (4 - 2.5) / std],
        index=["A", "B", "C", "D"],
    )
    pd.testing.assert_series_equal(result.iloc[0], expected, check_names=False)


def test_cs_demean_normal():
    df = _cs_panel()
    result = cs_demean(df)
    # Row 0 mean = 2.5
    assert np.isclose(result.iloc[0, 0], -1.5)
    assert np.isclose(result.iloc[0, 3], 1.5)
    # Row 1 constant → all zeros
    assert (result.iloc[1] == 0).all()


def test_cs_winsorize_clamps_extremes():
    df = pd.DataFrame(
        {f"T{i}": [float(i)] for i in range(20)}
    )  # one row, 20 tickers 0..19
    result = cs_winsorize(df, lower=0.05, upper=0.95)
    # Quantile 0.05 ≈ 0.95, quantile 0.95 ≈ 18.05; values clipped
    assert result.iloc[0].min() >= df.iloc[0].quantile(0.05) - 1e-9
    assert result.iloc[0].max() <= df.iloc[0].quantile(0.95) + 1e-9


# ===========================================================================
# Conditional
# ===========================================================================


def test_where_routes_branches():
    cond = pd.Series([1.0, -1.0, 0.5, -0.5])
    a = pd.Series([10.0, 20.0, 30.0, 40.0])
    b = pd.Series([100.0, 200.0, 300.0, 400.0])
    result = where(cond, a, b)
    assert result.iloc[0] == 10.0  # cond > 0 → a
    assert result.iloc[1] == 200.0  # cond <= 0 → b
    assert result.iloc[2] == 30.0
    assert result.iloc[3] == 400.0


def test_where_nan_cond_propagates():
    cond = pd.Series([1.0, np.nan, -1.0])
    a = pd.Series([1.0, 2.0, 3.0])
    b = pd.Series([10.0, 20.0, 30.0])
    result = where(cond, a, b)
    assert result.iloc[0] == 1.0
    assert np.isnan(result.iloc[1])
    assert result.iloc[2] == 30.0


# ===========================================================================
# Empty & single-row edge cases
# ===========================================================================


def test_ts_mean_empty():
    x = pd.Series([], dtype=float)
    result = ts_mean(x, 3)
    assert len(result) == 0


def test_ts_mean_single_row_window_too_large():
    x = pd.Series([1.0])
    result = ts_mean(x, 3)
    assert np.isnan(result.iloc[0])


def test_cs_rank_single_row():
    df = pd.DataFrame({"A": [1.0], "B": [2.0], "C": [3.0]})
    result = cs_rank(df)
    assert result.iloc[0, 0] == pytest.approx(-1 / 6, abs=1e-9)  # rank 1/3 - 0.5
    assert result.iloc[0, 2] == 0.5
