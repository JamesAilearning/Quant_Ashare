"""Unit tests for the Gate-4A IC evaluator's pure functions.

Coverage matrix (>=1 case per dimension):
  fold geometry     — frozen dev chain -> 19 folds (fold_0 2020Q2, fold_18
                      2024Q4); canonical full window -> 23 folds; missing
                      geometry keys fail loud.
  config chain      — child overrides parent via extends.
  size deciles      — as-of ffill; staleness cap drops + counts; too-few
                      names refuses.
  within-decile rank— ranks are per-decile, mapped into (0,1); factor NA
                      excluded.
  forward returns   — plain return; suspended-at-entry dropped + counted;
                      delisted mid-fold truncated to last close + counted;
                      missing execution day fails loud.
  fold IC           — perfect monotone signal -> rank_ic == 1; sliver
                      cross-section refuses.
  monotonicity      — bucket means recover a constructed gradient.
  aggregate         — mean/std/t/positive-fold count on a hand series.
  C1 formula        — value; any-input-NA -> NA; non-positive denominator
                      -> NA (never sign-flipped).
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.research.gate4a_ic_evaluator import (  # noqa: E402
    MAX_MV_STALENESS_DAYS,
    EvaluatorError,
    aggregate,
    compute_c1_gpa,
    dev_fold_windows,
    fold_ic,
    forward_returns,
    load_config_chain,
    masked_ts_codes_on,
    monotonicity,
    rebalance_stamps,
    size_deciles_asof,
    st_ts_codes_on,
    within_decile_rank,
)
from src.core.microstructure_mask import MicrostructureMaskResult  # noqa: E402

_GEOMETRY = {
    "overall_start": "2018-01-01",
    "train_months": 24, "valid_months": 3, "test_months": 3, "step_months": 3,
}


# ---------------------------------------------------------------------------
# fold geometry
# ---------------------------------------------------------------------------

def test_dev_fold_windows_match_frozen_19_fold_geometry():
    folds = dev_fold_windows({**_GEOMETRY, "overall_end": "2024-12-31"})
    assert len(folds) == 19
    assert folds[0].test_start == date(2020, 4, 1)
    assert folds[0].test_end == date(2020, 6, 30)
    assert folds[-1].test_start == date(2024, 10, 1)
    assert folds[-1].test_end == date(2024, 12, 31)


def test_full_canonical_window_yields_23_folds():
    folds = dev_fold_windows({**_GEOMETRY, "overall_end": "2025-12-31"})
    assert len(folds) == 23
    assert folds[-1].test_end == date(2025, 12, 31)


def test_missing_geometry_key_fails_loud():
    with pytest.raises(EvaluatorError, match="fold geometry"):
        dev_fold_windows({"overall_start": "2018-01-01",
                          "overall_end": "2024-12-31"})


def test_load_config_chain_child_overrides_parent(tmp_path):
    (tmp_path / "parent.yaml").write_text(
        "overall_end: '2025-12-31'\ntopk: 50\n", encoding="utf-8")
    (tmp_path / "child.yaml").write_text(
        "extends: parent.yaml\noverall_end: '2024-12-31'\n", encoding="utf-8")
    merged = load_config_chain(tmp_path / "child.yaml")
    assert merged["overall_end"] == "2024-12-31"
    assert merged["topk"] == 50


# ---------------------------------------------------------------------------
# size deciles (as-of + staleness)
# ---------------------------------------------------------------------------

def _calendar(n: int, start: date = date(2024, 1, 1)) -> list[date]:
    return [start + timedelta(days=i) for i in range(n)]


def test_size_deciles_asof_ffills_and_caps_staleness():
    cal = _calendar(40)
    codes = [f"s{i:02d}.SZ" for i in range(21)]
    mv = pd.DataFrame(index=cal, columns=codes, dtype=float)
    day = cal[35]
    for i, c in enumerate(codes[:20]):
        mv.loc[cal[34], c] = float((i + 1) * 100)  # fresh (1 day old)
    # s20: only value is OLDER than the staleness cap -> must be dropped
    mv.loc[cal[35 - MAX_MV_STALENESS_DAYS - 1], "s20.SZ"] = 999.0
    deciles, counts = size_deciles_asof(mv, day, codes, cal)
    assert counts["size_dropped_stale_or_missing"] == 1
    assert "s20.SZ" not in deciles.index
    assert deciles["s00.SZ"] == 0 and deciles["s19.SZ"] == 9
    assert sorted(deciles.unique()) == list(range(10))


def test_size_deciles_refuses_sliver_cross_section():
    cal = _calendar(5)
    codes = ["a.SZ", "b.SZ"]
    mv = pd.DataFrame({c: [1.0] * 5 for c in codes}, index=cal)
    with pytest.raises(EvaluatorError, match="too few"):
        size_deciles_asof(mv, cal[-1], codes, cal)


def test_within_decile_rank_is_per_decile_and_unit_interval():
    factor = pd.Series({"a": 10.0, "b": 20.0, "c": 1.0, "d": 2.0,
                        "e": float("nan")})
    deciles = pd.Series({"a": 0, "b": 0, "c": 9, "d": 9, "e": 9})
    sig = within_decile_rank(factor, deciles)
    assert "e" not in sig.index          # factor NA excluded
    assert sig["a"] < sig["b"]           # ranked within decile 0
    assert sig["c"] < sig["d"]           # ranked within decile 9
    assert sig["b"] == sig["d"]          # top-of-decile aligns across deciles
    assert ((sig > 0) & (sig < 1)).all()


# ---------------------------------------------------------------------------
# forward returns
# ---------------------------------------------------------------------------

def _close_frame() -> tuple[pd.DataFrame, list[date]]:
    cal = _calendar(10)
    close = pd.DataFrame(index=cal, dtype=float,
                         columns=["up.SZ", "susp.SZ", "dead.SZ"])
    close["up.SZ"] = [100 + i for i in range(10)]
    close["susp.SZ"] = [50.0] * 10
    close.loc[cal[1], "susp.SZ"] = float("nan")   # suspended at execution
    close["dead.SZ"] = [10.0] * 5 + [float("nan")] * 5  # delists mid-fold
    close.loc[cal[4], "dead.SZ"] = 8.0
    return close, cal


def test_forward_returns_plain_truncated_and_dropped():
    close, cal = _close_frame()
    ret, counts = forward_returns(close, cal[1], cal[9],
                                  ["up.SZ", "susp.SZ", "dead.SZ"])
    assert ret["up.SZ"] == pytest.approx((109 / 101) - 1)
    assert "susp.SZ" not in ret.index
    assert counts["return_dropped_no_entry_close"] == 1
    # dead.SZ: last available close (day 4 = 8.0) vs entry 10.0
    assert ret["dead.SZ"] == pytest.approx((8.0 / 10.0) - 1)
    assert counts["return_truncated_last_close"] == 1


def test_forward_returns_missing_execution_day_fails_loud():
    close, cal = _close_frame()
    with pytest.raises(EvaluatorError, match="execution day"):
        forward_returns(close, date(1999, 1, 1), cal[9], ["up.SZ"])


def test_forward_returns_no_post_entry_close_marks_flat_and_counts():
    # entry close exists, ZERO closes afterwards -> exit = entry close
    # (the last available close <= fold end), return 0.0, counted —
    # never silently dropped (codex #354 r1 P2).
    cal = _calendar(6)
    close = pd.DataFrame(index=cal, columns=["halt.SZ"], dtype=float)
    close.loc[cal[1], "halt.SZ"] = 42.0
    ret, counts = forward_returns(close, cal[1], cal[5], ["halt.SZ"])
    assert ret["halt.SZ"] == pytest.approx(0.0)
    assert counts["return_flat_no_post_entry_close"] == 1
    assert counts["return_truncated_last_close"] == 0


def test_rebalance_stamps_mirror_canonical_schedule():
    # 63-day quarter: schedule positions {0} -> one primary stamp holding
    # to the fold's last day.
    days63 = _calendar(63)
    stamps, n_zero = rebalance_stamps(days63, 63, 0)
    assert n_zero == 0 and len(stamps) == 1
    t, ex, end = stamps[0]
    assert (t, ex, end) == (days63[0], days63[1], days63[62])
    # 64-day quarter: position 63 IS the last in-window day -> excluded by
    # the fillable rule (lag-1 exec would leave the window) -> one stamp.
    days64 = _calendar(64)
    stamps, n_zero = rebalance_stamps(days64, 63, 0)
    assert n_zero == 0 and len(stamps) == 1
    # 65-day quarter: position 63 executes on the LAST day -> zero-length
    # horizon -> dropped + counted; primary survives.
    days65 = _calendar(65)
    stamps, n_zero = rebalance_stamps(days65, 63, 0)
    assert n_zero == 1 and len(stamps) == 1
    # 66-day quarter: tail stamp gets a real 1-day horizon -> kept.
    days66 = _calendar(66)
    stamps, n_zero = rebalance_stamps(days66, 63, 0)
    assert n_zero == 0 and len(stamps) == 2
    t2, ex2, end2 = stamps[1]
    assert (t2, ex2, end2) == (days66[63], days66[64], days66[65])
    # primary horizon ends at the TAIL's execution day (position turnover)
    assert stamps[0][2] == days66[64]


def test_rebalance_stamps_refuses_tiny_fold():
    with pytest.raises(EvaluatorError, match="trading days"):
        rebalance_stamps(_calendar(2), 63, 0)


def test_st_ts_codes_on_flags_st_names_on_execution_day():
    from src.data.st_history import build_st_lookup
    nc = pd.DataFrame([
        {"ts_code": "000001.SZ", "name": "ST平安", "start_date": "20200110",
         "end_date": None, "ann_date": "20200110", "change_reason": ""},
        {"ts_code": "000001.SZ", "name": "平安银行", "start_date": "20210110",
         "end_date": None, "ann_date": "20210110", "change_reason": ""},
        {"ts_code": "600000.SH", "name": "浦发银行", "start_date": "20000101",
         "end_date": None, "ann_date": "20000101", "change_reason": ""},
    ])
    lookup = build_st_lookup(nc)
    universe = ["000001.SZ", "600000.SH"]
    assert st_ts_codes_on(lookup, universe, date(2020, 6, 1)) == \
        frozenset({"000001.SZ"})          # ST in effect on that day
    assert st_ts_codes_on(lookup, universe, date(2021, 6, 1)) == frozenset()
    # 摘帽 later must NOT rewrite history (PIT no-look-ahead)
    assert st_ts_codes_on(lookup, universe, date(2020, 12, 31)) == \
        frozenset({"000001.SZ"})


def test_masked_ts_codes_on_filters_by_day_and_converts_codes():
    mask = MicrostructureMaskResult(
        masked=frozenset({("2024-01-02", "SH600000"),
                          ("2024-01-02", "SZ000001"),
                          ("2024-01-03", "SH600004")}),
        n_suspended=2, n_one_price_days=1)
    got = masked_ts_codes_on(mask, date(2024, 1, 2))
    assert got == frozenset({"600000.SH", "000001.SZ"})
    assert masked_ts_codes_on(mask, date(2024, 1, 4)) == frozenset()


# ---------------------------------------------------------------------------
# IC / monotonicity / aggregate
# ---------------------------------------------------------------------------

def test_fold_ic_perfect_monotone_signal():
    n = 40
    sig = pd.Series({f"s{i}": i / n for i in range(n)})
    ret = pd.Series({f"s{i}": i * 0.001 for i in range(n)})
    out = fold_ic(sig, ret)
    assert out["n"] == n
    assert out["rank_ic"] == pytest.approx(1.0)
    assert out["ic"] == pytest.approx(1.0)


def test_fold_ic_refuses_sliver():
    sig = pd.Series({f"s{i}": float(i) for i in range(10)})
    with pytest.raises(EvaluatorError, match="sliver"):
        fold_ic(sig, sig * 2)


def test_fold_ic_refuses_non_finite_correlation():
    # constant forward-return vector -> NaN correlation: must abort the
    # run, never vanish from the aggregate (codex #354 r3 P2).
    n = 40
    sig = pd.Series({f"s{i}": i / n for i in range(n)})
    flat = pd.Series({f"s{i}": 0.0 for i in range(n)})
    with pytest.raises(EvaluatorError, match="non-finite IC"):
        fold_ic(sig, flat)


def test_monotonicity_recovers_gradient():
    n = 100
    sig = pd.Series({f"s{i}": i / n for i in range(n)})
    ret = pd.Series({f"s{i}": i * 0.01 for i in range(n)})
    means = monotonicity(sig, ret, n_buckets=5)
    assert len(means) == 5
    assert means == sorted(means)


def test_aggregate_stats_on_hand_series():
    rows = [{"rank_ic": 0.05, "ic": 0.04}, {"rank_ic": 0.03, "ic": 0.02},
            {"rank_ic": -0.01, "ic": -0.02}, {"rank_ic": 0.05, "ic": 0.04}]
    agg = aggregate(rows)
    assert agg["n_folds"] == 4
    assert agg["rank_ic_mean"] == pytest.approx(0.03)
    assert agg["rank_ic_positive_folds"] == 3
    series = pd.Series([0.05, 0.03, -0.01, 0.05])
    expected_t = series.mean() / (series.std(ddof=1) / 2)
    assert agg["rank_ic_t"] == pytest.approx(float(expected_t))


# ---------------------------------------------------------------------------
# C1 formula
# ---------------------------------------------------------------------------

def test_compute_c1_gpa_value_na_and_nonpositive_denominator():
    frame = pd.DataFrame({
        "revenue": [100.0, 100.0, 100.0, float("nan")],
        "oper_cost": [60.0, 60.0, 60.0, 60.0],
        "total_assets": [200.0, 0.0, -5.0, 200.0],
    }, index=["ok", "zero_ta", "neg_ta", "na_rev"])
    c1 = compute_c1_gpa(frame)
    assert c1["ok"] == pytest.approx(0.2)
    assert pd.isna(c1["zero_ta"])   # never divide by zero
    assert pd.isna(c1["neg_ta"])    # never a sign-flipped denominator
    assert pd.isna(c1["na_rev"])    # any input NA -> factor NA


def test_compute_c1_gpa_missing_field_fails_loud():
    with pytest.raises(EvaluatorError, match="lacks field"):
        compute_c1_gpa(pd.DataFrame({"revenue": [1.0]}))
