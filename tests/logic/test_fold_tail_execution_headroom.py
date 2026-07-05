"""Tail execution-headroom guard on fold generation (the fold-22 class).

Phase C1 §6: a fold whose test window ends at the PIT calendar's last day
crashed ``BacktestRunner`` with an index-out-of-bounds at the final bar (the
fill needs a T+``signal_to_execution_lag`` execution bar), and per-fold error
isolation swallowed the crash into a silent NaN placeholder fold — the run
reported "22/23 valid" without naming why. ``_generate_windows`` now refuses
to EMIT a fold lacking the execution headroom, with a loud named cause; the
fold re-appears naturally once the bundle rolls forward.
"""
from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.walk_forward.engine import WalkForwardEngine  # noqa: E402


def _business_days(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _cfg(**over: object) -> SimpleNamespace:
    base: dict[str, object] = dict(
        overall_start="2018-01-01", overall_end="2025-12-31",
        train_months=24, valid_months=3, test_months=3, step_months=3,
        label_horizon_days=1,
        signal_to_execution_lag=1,
    )
    base.update(over)
    return SimpleNamespace(**base)


class FoldTailExecutionHeadroomTests(unittest.TestCase):
    def test_ample_headroom_keeps_all_folds(self) -> None:
        # Calendar extends far past overall_end -> the documented 23 folds.
        cal = _business_days(date(2017, 1, 2), date(2026, 12, 31))
        windows = WalkForwardEngine._generate_windows(_cfg(), calendar=cal)
        self.assertEqual(len(windows), 23)
        self.assertEqual(windows[-1][4], "2025-10-01")  # fold 22 present

    def test_calendar_ending_at_test_end_drops_the_tail_fold(self) -> None:
        # THE C1 scenario: calendar's last trading day == fold 22's test_end
        # (2025-12-31 is a Wednesday). The fill for the last tradable day
        # needs T+1, which does not exist -> the fold must not be emitted.
        cal = _business_days(date(2017, 1, 2), date(2025, 12, 31))
        with self.assertLogs("src.core.walk_forward.engine", level="WARNING") as logs:
            windows = WalkForwardEngine._generate_windows(_cfg(), calendar=cal)
        self.assertEqual(len(windows), 22)  # fold 22 skipped, 0..21 intact
        self.assertEqual(windows[-1][4], "2025-07-01")
        joined = "\n".join(logs.output)
        self.assertIn("tail execution", joined)
        self.assertIn("2025-10-01", joined)  # the skipped fold is NAMED
        self.assertIn("fold-22 class", joined)

    def test_one_extra_bar_restores_the_tail_fold(self) -> None:
        # Exactly one trading day after test_end == the T+1 bar exists.
        cal = _business_days(date(2017, 1, 2), date(2026, 1, 1))
        windows = WalkForwardEngine._generate_windows(_cfg(), calendar=cal)
        self.assertEqual(len(windows), 23)

    def test_headroom_scales_with_execution_lag(self) -> None:
        # lag=3 needs three bars after the last tradable day: a calendar
        # with only two extra bars drops the tail fold; three keeps it.
        cal_two_extra = _business_days(date(2017, 1, 2), date(2026, 1, 2))
        windows = WalkForwardEngine._generate_windows(
            _cfg(signal_to_execution_lag=3), calendar=cal_two_extra,
        )
        self.assertEqual(len(windows), 22)
        cal_three_extra = _business_days(date(2017, 1, 2), date(2026, 1, 5))
        windows = WalkForwardEngine._generate_windows(
            _cfg(signal_to_execution_lag=3), calendar=cal_three_extra,
        )
        self.assertEqual(len(windows), 23)

    def test_earlier_folds_are_byte_identical_when_tail_drops(self) -> None:
        # Dropping the unrunnable tail must not perturb any earlier window.
        cal_full = _business_days(date(2017, 1, 2), date(2026, 12, 31))
        cal_cut = _business_days(date(2017, 1, 2), date(2025, 12, 31))
        full = WalkForwardEngine._generate_windows(_cfg(), calendar=cal_full)
        cut = WalkForwardEngine._generate_windows(_cfg(), calendar=cal_cut)
        self.assertEqual(cut, full[:-1])


if __name__ == "__main__":
    unittest.main()
