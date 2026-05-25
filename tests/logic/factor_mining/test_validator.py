"""Tests for the Phase 6 IS/OOS validator."""

from __future__ import annotations

import inspect

import numpy as np
import pandas as pd
import pytest

from src.factor_mining.factor_pool import FactorPool, PoolEntry
from src.factor_mining.validator import (
    ValidationCriteria,
    filter_correlated,
    validate_pool,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_panel_basic(n_tickers=10, n_dates=120, seed=0):
    """Synthetic OHLCV panel — n_dates dates × n_tickers tickers."""
    rng = np.random.default_rng(seed)
    tickers = [f"T{i:03d}" for i in range(n_tickers)]
    dates = pd.date_range("2024-01-01", periods=n_dates, freq="D")
    panel = {}
    for f in ["$open", "$high", "$low", "$close", "$volume", "$money"]:
        data = rng.normal(100, 5, size=(n_dates, n_tickers))
        panel[f] = pd.DataFrame(
            data,
            index=pd.Index(dates, name="datetime"),
            columns=pd.Index(tickers, name="instrument"),
        )
    fwd = pd.DataFrame(
        rng.normal(0, 0.02, size=(n_dates, n_tickers)),
        index=pd.Index(dates, name="datetime"),
        columns=pd.Index(tickers, name="instrument"),
    )
    return panel, fwd


def _make_overfit_panel(n_tickers=10, n_dates=120, seed=42):
    """Engineered overfit panel.

    `$volume` on IS dates EQUALS the forward return (rank-perfect
    correlation per day → IS rank-IC ≈ 1.0). On OOS dates `$volume`
    is unrelated noise → OOS rank-IC ≈ 0.

    Other fields are random.
    """
    rng = np.random.default_rng(seed)
    tickers = [f"T{i:03d}" for i in range(n_tickers)]
    dates = pd.date_range("2024-01-01", periods=n_dates, freq="D")
    split_idx = int(0.5 * n_dates)
    fwd_arr = rng.normal(0, 0.02, size=(n_dates, n_tickers))
    fwd = pd.DataFrame(
        fwd_arr,
        index=pd.Index(dates, name="datetime"),
        columns=pd.Index(tickers, name="instrument"),
    )
    # $volume on IS dates is fwd; on OOS dates is fresh noise.
    volume_arr = np.empty_like(fwd_arr)
    volume_arr[:split_idx] = fwd_arr[:split_idx]
    volume_arr[split_idx:] = rng.normal(0, 1, size=(n_dates - split_idx, n_tickers))
    panel = {
        "$open": pd.DataFrame(
            rng.normal(100, 5, size=(n_dates, n_tickers)),
            index=pd.Index(dates, name="datetime"),
            columns=pd.Index(tickers, name="instrument"),
        ),
        "$high": pd.DataFrame(
            rng.normal(100, 5, size=(n_dates, n_tickers)),
            index=pd.Index(dates, name="datetime"),
            columns=pd.Index(tickers, name="instrument"),
        ),
        "$low": pd.DataFrame(
            rng.normal(100, 5, size=(n_dates, n_tickers)),
            index=pd.Index(dates, name="datetime"),
            columns=pd.Index(tickers, name="instrument"),
        ),
        "$close": pd.DataFrame(
            rng.normal(100, 5, size=(n_dates, n_tickers)),
            index=pd.Index(dates, name="datetime"),
            columns=pd.Index(tickers, name="instrument"),
        ),
        "$volume": pd.DataFrame(
            volume_arr,
            index=pd.Index(dates, name="datetime"),
            columns=pd.Index(tickers, name="instrument"),
        ),
        "$money": pd.DataFrame(
            rng.normal(1e6, 1e4, size=(n_dates, n_tickers)),
            index=pd.Index(dates, name="datetime"),
            columns=pd.Index(tickers, name="instrument"),
        ),
    }
    return panel, fwd, dates[split_idx]


def _make_stable_panel(n_tickers=10, n_dates=120, seed=1):
    """`$volume` is forward return on BOTH IS and OOS dates.

    A factor cs_rank($volume) will rank-perfectly track fwd everywhere
    → high IS AND OOS rank-IC → validator should pass it.
    """
    rng = np.random.default_rng(seed)
    tickers = [f"T{i:03d}" for i in range(n_tickers)]
    dates = pd.date_range("2024-01-01", periods=n_dates, freq="D")
    fwd_arr = rng.normal(0, 0.02, size=(n_dates, n_tickers))
    fwd = pd.DataFrame(
        fwd_arr,
        index=pd.Index(dates, name="datetime"),
        columns=pd.Index(tickers, name="instrument"),
    )
    panel = {
        "$open": pd.DataFrame(
            rng.normal(100, 5, size=(n_dates, n_tickers)),
            index=pd.Index(dates, name="datetime"),
            columns=pd.Index(tickers, name="instrument"),
        ),
        "$high": pd.DataFrame(
            rng.normal(100, 5, size=(n_dates, n_tickers)),
            index=pd.Index(dates, name="datetime"),
            columns=pd.Index(tickers, name="instrument"),
        ),
        "$low": pd.DataFrame(
            rng.normal(100, 5, size=(n_dates, n_tickers)),
            index=pd.Index(dates, name="datetime"),
            columns=pd.Index(tickers, name="instrument"),
        ),
        "$close": pd.DataFrame(
            rng.normal(100, 5, size=(n_dates, n_tickers)),
            index=pd.Index(dates, name="datetime"),
            columns=pd.Index(tickers, name="instrument"),
        ),
        # $volume is the engineered signal — equal to fwd on all dates
        "$volume": fwd.copy(),
        "$money": pd.DataFrame(
            rng.normal(1e6, 1e4, size=(n_dates, n_tickers)),
            index=pd.Index(dates, name="datetime"),
            columns=pd.Index(tickers, name="instrument"),
        ),
    }
    split = dates[int(0.5 * n_dates)]
    return panel, fwd, split


def _make_pool_one_factor(expr_str: str, fitness: float = 1.0) -> FactorPool:
    from src.factor_mining.expression import parse_expression

    expr = parse_expression(expr_str)
    pool = FactorPool()
    pool.add(
        PoolEntry(
            expr=expr,
            fitness=fitness,
            ic_mean=0.05, ic_std=0.10, ir=0.5,
            rank_ic_mean=0.04, rank_ic_std=0.08, rank_ir=0.5,
            turnover_daily=0.10, coverage=0.95, n_obs_per_day_min=20,
            expr_size=2, expr_hash=hash(expr),
        )
    )
    return pool


# ---------------------------------------------------------------------------
# ValidationCriteria defaults
# ---------------------------------------------------------------------------


def test_criteria_defaults_match_d4():
    c = ValidationCriteria(is_oos_split_date="2024-06-01")
    assert c.min_oos_ir == 0.3
    assert c.min_oos_rank_ic_mean == 0.02
    assert c.max_pool_correlation == 0.6
    assert c.min_obs_per_segment == 30


# ---------------------------------------------------------------------------
# Segment length checks
# ---------------------------------------------------------------------------


def test_too_short_oos_segment_rejects():
    panel, fwd = _make_panel_basic(n_dates=50, n_tickers=8)
    # Split very late → only ~5 OOS dates → fails min_obs_per_segment
    split = panel["$close"].index[45].strftime("%Y-%m-%d")
    pool = _make_pool_one_factor("cs_rank($volume)")
    crit = ValidationCriteria(is_oos_split_date=split, min_obs_per_segment=200)
    results = validate_pool(pool, panel, fwd, crit)
    assert len(results) == 1
    assert not results[0].passes
    assert "oos_segment_too_short" in results[0].reasons


def test_too_short_is_segment_rejects():
    panel, fwd = _make_panel_basic(n_dates=50, n_tickers=8)
    split = panel["$close"].index[3].strftime("%Y-%m-%d")
    pool = _make_pool_one_factor("cs_rank($volume)")
    crit = ValidationCriteria(is_oos_split_date=split, min_obs_per_segment=200)
    results = validate_pool(pool, panel, fwd, crit)
    assert not results[0].passes
    assert "is_segment_too_short" in results[0].reasons


# ---------------------------------------------------------------------------
# Stable factor passes
# ---------------------------------------------------------------------------


def test_validate_pool_passes_stable_factor():
    panel, fwd, split = _make_stable_panel(n_dates=120, n_tickers=10)
    pool = _make_pool_one_factor("cs_rank($volume)")
    crit = ValidationCriteria(
        is_oos_split_date=split.strftime("%Y-%m-%d"),
        min_oos_ir=0.3,
        min_oos_rank_ic_mean=0.02,
        min_obs_per_segment=10,
    )
    results = validate_pool(pool, panel, fwd, crit)
    assert len(results) == 1
    # rank-IC ≈ 1.0 every day → OOS rank-IC mean is high
    assert results[0].passes, f"reasons: {results[0].reasons}"
    # The factor's OOS rank-IC mean is large (close to 1.0)
    assert abs(results[0].oos_rank_ic_mean) >= 0.5


# ---------------------------------------------------------------------------
# Overfit factor rejected — the Phase 6 acceptance test
# ---------------------------------------------------------------------------


def test_validate_pool_rejects_overfit_factor():
    panel, fwd, split = _make_overfit_panel(n_dates=120, n_tickers=10)
    pool = _make_pool_one_factor("cs_rank($volume)")
    crit = ValidationCriteria(
        is_oos_split_date=split.strftime("%Y-%m-%d"),
        min_oos_ir=0.3,
        min_oos_rank_ic_mean=0.02,
        min_obs_per_segment=10,
    )
    results = validate_pool(pool, panel, fwd, crit)
    assert len(results) == 1
    r = results[0]
    # OOS rank-IC should be near zero (volume is uncorrelated noise on OOS)
    assert abs(r.oos_rank_ic_mean) < 0.2
    # Validator must reject — exactly per the design doc Phase 6 acceptance
    assert not r.passes
    # At least one of the OOS-threshold reasons fired
    failed = set(r.reasons)
    assert failed & {"oos_ir_below_threshold", "oos_rank_ic_below_threshold"}, (
        f"expected an OOS threshold failure; got reasons={r.reasons}"
    )


# ---------------------------------------------------------------------------
# filter_correlated
# ---------------------------------------------------------------------------


def test_filter_correlated_drops_highly_correlated():
    panel, fwd, split = _make_stable_panel(n_dates=120, n_tickers=10)
    # Two factors that should be highly correlated on the panel:
    #   cs_rank($volume) and cs_rank($volume) — same expression, but a
    #   FactorPool dedups by hash so we can't add two of the same.
    # Use cs_rank($volume) and cs_zscore($volume) which produce highly
    # correlated cross-sectional rankings (both rank by $volume).
    from src.factor_mining.expression import parse_expression

    pool = FactorPool()
    pool.add(PoolEntry(
        expr=parse_expression("cs_rank($volume)"),
        fitness=2.0,
        ic_mean=0.1, ic_std=0.2, ir=0.5,
        rank_ic_mean=0.5, rank_ic_std=0.1, rank_ir=5.0,
        turnover_daily=0.1, coverage=1.0, n_obs_per_day_min=10,
        expr_size=2, expr_hash=hash(parse_expression("cs_rank($volume)")),
    ))
    pool.add(PoolEntry(
        expr=parse_expression("cs_zscore($volume)"),
        fitness=1.0,
        ic_mean=0.1, ic_std=0.2, ir=0.5,
        rank_ic_mean=0.5, rank_ic_std=0.1, rank_ir=5.0,
        turnover_daily=0.1, coverage=1.0, n_obs_per_day_min=10,
        expr_size=2, expr_hash=hash(parse_expression("cs_zscore($volume)")),
    ))
    crit = ValidationCriteria(
        is_oos_split_date=split.strftime("%Y-%m-%d"),
        min_oos_ir=0.0,
        min_oos_rank_ic_mean=0.0,
        max_pool_correlation=0.6,
        min_obs_per_segment=10,
    )
    results = validate_pool(pool, panel, fwd, crit)
    # Both should pass per-factor (thresholds set to 0)
    assert all(r.passes for r in results)
    filtered = filter_correlated(results, panel, crit, pool)
    # The lower-fitness one should now fail with correlated-with-higher-fitness
    by_hash = {r.expr_hash: r for r in filtered}
    rank_hash = hash(parse_expression("cs_rank($volume)"))
    zscore_hash = hash(parse_expression("cs_zscore($volume)"))
    # cs_rank has the higher fitness (2.0 > 1.0) so it stays
    assert by_hash[rank_hash].passes
    # cs_zscore has the lower fitness and is highly correlated → drops
    assert not by_hash[zscore_hash].passes
    assert "correlated_with_higher_fitness" in by_hash[zscore_hash].reasons


def test_filter_correlated_preserves_uncorrelated():
    panel, fwd = _make_panel_basic(n_dates=120, n_tickers=10)
    # Two uncorrelated factors: cs_rank($volume) and cs_rank($money) —
    # because volume and money are independent random panels in the basic
    # synthetic, their ranks should be largely uncorrelated.
    from src.factor_mining.expression import parse_expression

    pool = FactorPool()
    for name, fit in [("cs_rank($volume)", 1.0), ("cs_rank($money)", 0.8)]:
        e = parse_expression(name)
        pool.add(PoolEntry(
            expr=e,
            fitness=fit,
            ic_mean=0.1, ic_std=0.2, ir=0.5,
            rank_ic_mean=0.1, rank_ic_std=0.1, rank_ir=1.0,
            turnover_daily=0.1, coverage=1.0, n_obs_per_day_min=10,
            expr_size=2, expr_hash=hash(e),
        ))
    split = panel["$close"].index[60].strftime("%Y-%m-%d")
    crit = ValidationCriteria(
        is_oos_split_date=split,
        min_oos_ir=0.0,
        min_oos_rank_ic_mean=0.0,
        max_pool_correlation=0.6,
        min_obs_per_segment=10,
    )
    results = validate_pool(pool, panel, fwd, crit)
    filtered = filter_correlated(results, panel, crit, pool)
    # Both uncorrelated factors should remain passing
    assert all(r.passes for r in filtered)


# ---------------------------------------------------------------------------
# warmup_days — rolling-window factors get valid OOS values from day 1
# ---------------------------------------------------------------------------


def test_split_panel_warmup_zero_is_legacy_behavior():
    """``warmup_days=0`` must preserve the pre-PR3 slicing exactly."""
    from src.factor_mining.validator import _split_panel

    panel, fwd = _make_panel_basic(n_dates=100, n_tickers=5)
    split = panel["$close"].index[50]
    is_p, is_f, oos_p, oos_f = _split_panel(panel, fwd, split, warmup_days=0)
    assert (is_p["$close"].index < split).all()
    assert (oos_p["$close"].index >= split).all()
    assert (is_f.index < split).all()
    assert (oos_f.index >= split).all()
    # No NaN injection in fwd
    assert not oos_f.isna().all(axis=1).any()


def test_split_panel_warmup_extends_oos_panel_and_masks_fwd():
    """``warmup_days > 0`` extends the OOS panel back by ``warmup_days``
    rows and masks those warmup rows' fwd values to NaN."""
    from src.factor_mining.validator import _split_panel

    panel, fwd = _make_panel_basic(n_dates=100, n_tickers=5)
    split = panel["$close"].index[50]
    is_p, is_f, oos_p, oos_f = _split_panel(panel, fwd, split, warmup_days=10)
    # IS unchanged
    assert (is_p["$close"].index < split).all()
    assert (is_f.index < split).all()
    # OOS panel starts 10 rows before split → date index[40]
    expected_oos_start = panel["$close"].index[40]
    assert oos_p["$close"].index.min() == expected_oos_start
    # OOS fwd: warmup rows are NaN, post-split rows are not
    warmup_rows = oos_f.loc[oos_f.index < split]
    assert warmup_rows.isna().all().all(), "warmup fwd must be all NaN"
    post_split = oos_f.loc[oos_f.index >= split]
    assert not post_split.isna().all().any()


def test_warmup_lets_rolling_factor_score_oos_from_day_one():
    """A factor using a long rolling window (``ts_mean($close, 20)``)
    on OOS should produce far more non-NaN values when ``warmup_days``
    >= the window than when ``warmup_days == 0``."""
    panel, fwd, split = _make_stable_panel(n_dates=120, n_tickers=10)
    pool = _make_pool_one_factor("cs_rank(ts_mean($volume, 20))")

    crit_no_warmup = ValidationCriteria(
        is_oos_split_date=split.strftime("%Y-%m-%d"),
        min_oos_ir=0.0,
        min_oos_rank_ic_mean=0.0,
        min_obs_per_segment=10,
        warmup_days=0,
    )
    crit_warmup = ValidationCriteria(
        is_oos_split_date=split.strftime("%Y-%m-%d"),
        min_oos_ir=0.0,
        min_oos_rank_ic_mean=0.0,
        min_obs_per_segment=10,
        warmup_days=20,
    )

    r_no_warmup = validate_pool(pool, panel, fwd, crit_no_warmup)[0]
    r_warmup = validate_pool(pool, panel, fwd, crit_warmup)[0]

    # Warmup must materially expand the OOS observation count.
    assert r_warmup.oos_n_obs > r_no_warmup.oos_n_obs, (
        f"warmup OOS n_obs ({r_warmup.oos_n_obs}) should exceed "
        f"no-warmup OOS n_obs ({r_no_warmup.oos_n_obs})"
    )


def test_warmup_rejects_negative_value():
    """Defensive: ``warmup_days < 0`` is nonsense — raise rather than
    silently treat as zero."""
    from src.factor_mining.validator import _split_panel

    panel, fwd = _make_panel_basic(n_dates=50)
    split = panel["$close"].index[25]
    with pytest.raises(ValueError, match="warmup_days must be"):
        _split_panel(panel, fwd, split, warmup_days=-1)


# ---------------------------------------------------------------------------
# Legacy method-tag warning (uses PR2's LEGACY_METHOD_TAG)
# ---------------------------------------------------------------------------


def test_validate_pool_warns_on_legacy_method_entries(monkeypatch):
    """When the pool contains entries loaded from a pre-PR2 parquet
    (method == LEGACY_METHOD_TAG), the validator must log a warning
    so downstream callers know ``ic_mean`` semantics are ambiguous."""
    import src.factor_mining.validator as v_mod
    from src.factor_mining.expression import parse_expression
    from src.factor_mining.factor_pool import LEGACY_METHOD_TAG

    captured: list[str] = []
    real_warning = v_mod._log.warning

    def fake_warning(msg, *args, **kwargs):
        captured.append(msg % args if args else msg)
        return real_warning(msg, *args, **kwargs)

    monkeypatch.setattr(v_mod._log, "warning", fake_warning)

    panel, fwd, split = _make_stable_panel(n_dates=120, n_tickers=10)
    expr = parse_expression("cs_rank($volume)")
    pool = FactorPool()
    pool.add(
        PoolEntry(
            expr=expr,
            fitness=1.0,
            ic_mean=0.05, ic_std=0.1, ir=0.5,
            rank_ic_mean=0.04, rank_ic_std=0.1, rank_ir=0.4,
            turnover_daily=0.1, coverage=0.95, n_obs_per_day_min=20,
            expr_size=2, expr_hash=hash(expr),
            method=LEGACY_METHOD_TAG,
        )
    )
    crit = ValidationCriteria(
        is_oos_split_date=split.strftime("%Y-%m-%d"),
        min_oos_ir=0.0,
        min_oos_rank_ic_mean=0.0,
        min_obs_per_segment=10,
    )
    validate_pool(pool, panel, fwd, crit)
    assert any("lack a method tag" in msg for msg in captured), (
        f"expected legacy-tag warning; got {captured!r}"
    )


def test_validate_pool_no_warning_when_all_entries_tagged(monkeypatch):
    """No legacy-tag warning when every entry carries an explicit
    method (normal or rank)."""
    import src.factor_mining.validator as v_mod
    from src.factor_mining.expression import parse_expression

    captured: list[str] = []
    monkeypatch.setattr(
        v_mod._log, "warning",
        lambda msg, *a, **kw: captured.append(msg % a if a else msg),
    )

    panel, fwd, split = _make_stable_panel(n_dates=120, n_tickers=10)
    expr = parse_expression("cs_rank($volume)")
    pool = FactorPool()
    pool.add(
        PoolEntry(
            expr=expr,
            fitness=1.0,
            ic_mean=0.05, ic_std=0.1, ir=0.5,
            rank_ic_mean=0.04, rank_ic_std=0.1, rank_ir=0.4,
            turnover_daily=0.1, coverage=0.95, n_obs_per_day_min=20,
            expr_size=2, expr_hash=hash(expr),
            method="normal",
        )
    )
    crit = ValidationCriteria(
        is_oos_split_date=split.strftime("%Y-%m-%d"),
        min_oos_ir=0.0,
        min_oos_rank_ic_mean=0.0,
        min_obs_per_segment=10,
    )
    validate_pool(pool, panel, fwd, crit)
    assert not any("lack a method tag" in msg for msg in captured), (
        f"unexpected legacy-tag warning fired; messages={captured!r}"
    )


# ---------------------------------------------------------------------------
# D5 strict gate
# ---------------------------------------------------------------------------


def test_validator_does_not_import_qlib_or_pit():
    import src.factor_mining.validator as mod

    src = inspect.getsource(mod)
    assert "from qlib" not in src
    assert "qlib.data" not in src
    assert "qlib.init" not in src
    assert "from src.pit" not in src
    assert "import src.pit" not in src
