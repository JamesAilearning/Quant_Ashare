"""Walk-forward embargo-gap fold generation (Phase C1).

Proves ``WalkForwardEngine._generate_windows`` inserts a
``>= LABEL_LOOKAHEAD_DAYS`` trading-day embargo gap between adjacent
segments WITHOUT weakening the guard:

* every generated fold is accepted by the guard's OWN
  ``validate_segment_embargo`` (the guard is the oracle — if generator and
  guard ever disagree, this test fails), on BOTH boundaries; and
* the Alpha158 label-lookahead window of the last train row never reaches
  into the valid segment (the red-line no-leak assertion).

Pure unit test — synthetic continuous-business-day calendar, no qlib, no
training.
"""

from __future__ import annotations

import unittest
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from dateutil.relativedelta import relativedelta

import src.data._segment_embargo as _embargo
from src.core.walk_forward.engine import WalkForwardEngine
from src.data._segment_embargo import (
    LABEL_LOOKAHEAD_DAYS,
    trading_days_between,
    validate_segment_embargo,
)


def _business_days(start: date, end: date) -> list[date]:
    """Continuous Mon-Fri calendar (no holidays needed — the gap logic
    depends only on the trading-day *sequence*, not which days are holidays)."""
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


_CAL = _business_days(date(2017, 1, 2), date(2026, 12, 31))


def _cfg(**over: object) -> SimpleNamespace:
    # _generate_windows only reads these fields; a namespace keeps the test
    # free of the full WalkForwardConfig's unrelated required fields.
    base: dict[str, object] = dict(
        overall_start="2018-01-01", overall_end="2025-12-31",
        train_months=24, valid_months=3, test_months=3, step_months=3,
        # H=1 keeps the gap sourced from LABEL_LOOKAHEAD_DAYS (this suite
        # patches the constant and relies on the call-time read).
        label_horizon_days=1,
        # Read by the tail execution-headroom guard (fold-22 class); _CAL
        # extends a year past overall_end, so headroom never binds here.
        signal_to_execution_lag=1,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _folds() -> list[tuple[date, ...]]:
    raw = WalkForwardEngine._generate_windows(_cfg(), calendar=_CAL)
    return [tuple(date.fromisoformat(x) for x in w) for w in raw]


class WalkForwardEmbargoGapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folds = _folds()

    def test_generates_folds(self) -> None:
        self.assertGreater(len(self.folds), 0)

    def test_every_fold_accepted_by_guard_on_both_boundaries(self) -> None:
        """Guard's own validator returns [] for every fold — i.e. BOTH
        train->valid AND valid->test have >= LABEL_LOOKAHEAD_DAYS trading
        days. Also assert each boundary explicitly."""
        for tr_s, tr_e, va_s, va_e, te_s, _te_e in self.folds:
            errs = validate_segment_embargo(
                train_end=tr_e, valid_start=va_s, valid_end=va_e,
                test_start=te_s, calendar=_CAL,
            )
            self.assertEqual(errs, [], f"guard rejected fold @{tr_s}: {errs}")
            self.assertGreaterEqual(
                trading_days_between(tr_e, va_s, _CAL), LABEL_LOOKAHEAD_DAYS,
                "train->valid gap too small")
            self.assertGreaterEqual(
                trading_days_between(va_e, te_s, _CAL), LABEL_LOOKAHEAD_DAYS,
                "valid->test gap too small")

    def test_no_label_lookahead_leak_into_valid(self) -> None:
        """RED LINE: the last train row (train_end) has an Alpha158 label
        that reads close at +1..+LABEL_LOOKAHEAD_DAYS trading days. Those
        days MUST fall in the discarded gap, never inside the valid segment
        — otherwise train labels peek at valid data (look-ahead leak)."""
        for _tr_s, tr_e, va_s, va_e, _te_s, _te_e in self.folds:
            i = _CAL.index(tr_e)
            label_reads = _CAL[i + 1: i + 1 + LABEL_LOOKAHEAD_DAYS]
            self.assertEqual(len(label_reads), LABEL_LOOKAHEAD_DAYS)
            self.assertLess(
                max(label_reads), va_s,
                f"train_end {tr_e} label reads {label_reads} reach valid_start {va_s}")
            self.assertTrue(
                all(not (va_s <= d <= va_e) for d in label_reads),
                "a train label read lands inside [valid_start, valid_end]")

    def test_same_no_leak_for_valid_into_test(self) -> None:
        """Same red line for the valid->test boundary: valid's last-row
        label must not peek into the test segment."""
        for _tr_s, _tr_e, _va_s, va_e, te_s, te_e in self.folds:
            i = _CAL.index(va_e)
            label_reads = _CAL[i + 1: i + 1 + LABEL_LOOKAHEAD_DAYS]
            self.assertEqual(len(label_reads), LABEL_LOOKAHEAD_DAYS)
            self.assertLess(max(label_reads), te_s)
            self.assertTrue(all(not (te_s <= d <= te_e) for d in label_reads))

    def test_start_anchors_stay_month_aligned(self) -> None:
        """valid_s/test_s unchanged vs the month-aligned nominal (only the
        segment *ends* moved) — keeps the quarter grid (codex #211 P2)."""
        for tr_s, _tr_e, va_s, _va_e, te_s, _te_e in self.folds:
            self.assertEqual(va_s, tr_s + relativedelta(months=24))
            self.assertEqual(te_s, va_s + relativedelta(months=3))

    def test_first_fold_quarter_aligned(self) -> None:
        """2018-01-01 anchor → first test window is the documented
        2020-04-01 quarter (matches empirical_results_b_std.md)."""
        self.assertEqual(self.folds[0][4], date(2020, 4, 1))  # test_s

    def test_inserted_gap_equals_guard_constant(self) -> None:
        """The gap is EXACTLY LABEL_LOOKAHEAD_DAYS — proves it is derived
        from the guard's constant, not a hardcoded literal. If someone
        hardcodes a different number, this drifts and fails."""
        for _tr_s, tr_e, va_s, va_e, te_s, _te_e in self.folds:
            self.assertEqual(trading_days_between(tr_e, va_s, _CAL), LABEL_LOOKAHEAD_DAYS)
            self.assertEqual(trading_days_between(va_e, te_s, _CAL), LABEL_LOOKAHEAD_DAYS)

    def test_h5_widens_gap_to_six(self) -> None:
        """label_horizon_days=5 -> the fold gap is 6 trading days (H+1) on
        both boundaries — the engine consumer of the horizon-driven embargo
        (add-label-horizon-config scenario 'a longer horizon widens the
        required gap')."""
        folds = [
            tuple(date.fromisoformat(x) for x in f)
            for f in WalkForwardEngine._generate_windows(
                _cfg(label_horizon_days=5), calendar=_CAL,
            )
        ]
        self.assertGreater(len(folds), 0)
        for _tr_s, tr_e, va_s, va_e, te_s, _te_e in folds:
            self.assertEqual(trading_days_between(tr_e, va_s, _CAL), 6)
            self.assertEqual(trading_days_between(va_e, te_s, _CAL), 6)

    def test_gap_zero_reduces_to_adjacent(self) -> None:
        """gap == 0 (a future zero-lookahead handler) reduces to adjacent
        boundaries: 0 trading days strictly between the segments — i.e. the
        gap really is read from the guard constant (here patched to 0), not
        a hardcoded 2."""
        with patch.object(_embargo, "LABEL_LOOKAHEAD_DAYS", 0):
            folds = [
                tuple(date.fromisoformat(x) for x in w)
                for w in WalkForwardEngine._generate_windows(_cfg(), calendar=_CAL)
            ]
        self.assertGreater(len(folds), 0)
        for _tr_s, tr_e, va_s, va_e, te_s, _te_e in folds:
            self.assertEqual(trading_days_between(tr_e, va_s, _CAL), 0)
            self.assertEqual(trading_days_between(va_e, te_s, _CAL), 0)

    def test_anchor_beyond_calendar_is_skipped(self) -> None:
        """A fold whose valid/test START anchor falls after the calendar's
        last day (future / truncated bundle) is SKIPPED — not emitted with a
        tail calendar date that would pass the embargo check while
        valid_start/test_start point outside coverage. Every emitted fold's
        start anchors stay within the calendar."""
        short_cal = _business_days(date(2017, 1, 2), date(2023, 12, 31))
        folds = [
            tuple(date.fromisoformat(x) for x in w)
            for w in WalkForwardEngine._generate_windows(_cfg(), calendar=short_cal)
        ]
        last = short_cal[-1]
        self.assertGreater(len(folds), 0)  # early folds still inside coverage
        for _tr_s, _tr_e, va_s, _va_e, te_s, _te_e in folds:
            self.assertLessEqual(va_s, last, "valid_start beyond calendar coverage")
            self.assertLessEqual(te_s, last, "test_start beyond calendar coverage")


if __name__ == "__main__":
    unittest.main()
