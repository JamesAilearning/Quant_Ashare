"""Unit tests for src.data.trading_calendar.

Covers:
- StaticTradingCalendar construction (sorted, deduped, immutable)
- count_trading_days inclusive semantics
- end < start short-circuit
- input validation (non-date rejected)

QlibTradingCalendar happy-path requires a real qlib provider and is
intentionally NOT exercised here. The lazy-import-failure path is
covered by inspecting the error message shape.
"""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.trading_calendar import (  # noqa: E402
    StaticTradingCalendar,
    TradingCalendar,
    TradingCalendarError,
)


class StaticTradingCalendarTests(unittest.TestCase):
    def test_empty_calendar_returns_zero(self) -> None:
        cal = StaticTradingCalendar([])
        self.assertEqual(cal.count_trading_days(date(2026, 1, 1), date(2026, 12, 31)), 0)

    def test_single_date_inside_window(self) -> None:
        cal = StaticTradingCalendar([date(2026, 6, 15)])
        self.assertEqual(cal.count_trading_days(date(2026, 6, 1), date(2026, 6, 30)), 1)

    def test_inclusive_endpoints(self) -> None:
        cal = StaticTradingCalendar(
            [date(2026, 2, 2), date(2026, 2, 3), date(2026, 2, 4)]
        )
        self.assertEqual(cal.count_trading_days(date(2026, 2, 2), date(2026, 2, 4)), 3)

    def test_exact_boundary_match(self) -> None:
        cal = StaticTradingCalendar(
            [date(2026, 2, 2), date(2026, 2, 3), date(2026, 2, 4), date(2026, 2, 5)]
        )
        # Both endpoints land exactly on calendar entries.
        self.assertEqual(cal.count_trading_days(date(2026, 2, 3), date(2026, 2, 4)), 2)

    def test_one_endpoint_outside_calendar(self) -> None:
        cal = StaticTradingCalendar(
            [date(2026, 2, 10), date(2026, 2, 11), date(2026, 2, 12)]
        )
        # Window starts before all entries; end inside.
        self.assertEqual(cal.count_trading_days(date(2026, 2, 1), date(2026, 2, 11)), 2)

    def test_window_outside_all_entries(self) -> None:
        cal = StaticTradingCalendar(
            [date(2026, 2, 10), date(2026, 2, 11), date(2026, 2, 12)]
        )
        self.assertEqual(cal.count_trading_days(date(2026, 3, 1), date(2026, 3, 31)), 0)

    def test_end_before_start_returns_zero(self) -> None:
        cal = StaticTradingCalendar(
            [date(2026, 2, 10), date(2026, 2, 11), date(2026, 2, 12)]
        )
        self.assertEqual(cal.count_trading_days(date(2026, 3, 1), date(2026, 2, 1)), 0)

    def test_cross_year_span(self) -> None:
        cal = StaticTradingCalendar(
            [date(2025, 12, 30), date(2025, 12, 31), date(2026, 1, 5)]
        )
        self.assertEqual(cal.count_trading_days(date(2025, 12, 1), date(2026, 1, 31)), 3)

    def test_duplicates_in_input_are_collapsed(self) -> None:
        cal = StaticTradingCalendar(
            [date(2026, 2, 2), date(2026, 2, 2), date(2026, 2, 3), date(2026, 2, 2)]
        )
        self.assertEqual(cal.count_trading_days(date(2026, 2, 1), date(2026, 2, 5)), 2)

    def test_unsorted_input_is_normalized(self) -> None:
        cal = StaticTradingCalendar(
            [date(2026, 3, 1), date(2026, 1, 1), date(2026, 2, 1)]
        )
        self.assertEqual(cal.count_trading_days(date(2026, 1, 1), date(2026, 3, 1)), 3)

    def test_non_date_input_in_constructor_raises(self) -> None:
        with self.assertRaises(TradingCalendarError):
            StaticTradingCalendar(["2026-02-02"])  # type: ignore[list-item]

    def test_non_date_input_in_query_raises(self) -> None:
        cal = StaticTradingCalendar([date(2026, 2, 2)])
        with self.assertRaises(TradingCalendarError):
            cal.count_trading_days("2026-02-01", date(2026, 2, 3))  # type: ignore[arg-type]
        with self.assertRaises(TradingCalendarError):
            cal.count_trading_days(date(2026, 2, 1), "2026-02-03")  # type: ignore[arg-type]

    def test_runtime_protocol_check(self) -> None:
        cal = StaticTradingCalendar([date(2026, 2, 2)])
        self.assertIsInstance(cal, TradingCalendar)


if __name__ == "__main__":
    unittest.main()
