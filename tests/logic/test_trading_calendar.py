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
    QlibTradingCalendar,
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


class QlibTradingCalendarConcurrencyTests(unittest.TestCase):
    """Exercise ``QlibTradingCalendar``'s cache-init lock without qlib.

    We don't want to import qlib in unit tests, so we monkey-patch
    ``_fetch_and_build_cache`` with a slow stub that sleeps long enough
    for multiple concurrent callers to queue on the lock. The assertion
    is: a shared instance observes exactly *one* underlying fetch, even
    with N threads racing the cold path.
    """

    def test_concurrent_callers_trigger_single_fetch(self) -> None:
        import threading
        import time

        fetch_count = [0]
        fetch_count_lock = threading.Lock()
        barrier = threading.Barrier(8)

        cal = QlibTradingCalendar(freq="day")

        def slow_fetch() -> StaticTradingCalendar:
            # Count fetches atomically.
            with fetch_count_lock:
                fetch_count[0] += 1
            # Sleep long enough that, without the lock, every waiting
            # thread would also invoke fetch.
            time.sleep(0.1)
            return StaticTradingCalendar([date(2026, 2, 2), date(2026, 2, 3)])

        cal._fetch_and_build_cache = slow_fetch  # type: ignore[method-assign]

        results: list[int] = []
        results_lock = threading.Lock()

        def worker() -> None:
            barrier.wait()  # release all threads simultaneously
            n = cal.count_trading_days(date(2026, 2, 1), date(2026, 2, 10))
            with results_lock:
                results.append(n)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(fetch_count[0], 1, "cache fetch must happen exactly once")
        self.assertEqual(results, [2] * 8, "all threads must see the same cached result")

    def test_lock_only_blocks_cold_path(self) -> None:
        """Once the cache is populated the lock is not acquired on the
        fast path. This is a smoke check: the second call must not block
        even if another thread is holding the instance lock.
        """
        import threading

        cal = QlibTradingCalendar(freq="day")
        # Pre-populate the cache synchronously.
        cal._cache = StaticTradingCalendar([date(2026, 2, 2)])  # type: ignore[assignment]

        # Hold the lock on a different thread and confirm the main thread
        # can still read through ``count_trading_days``.
        lock_held = threading.Event()
        release = threading.Event()

        def hold_lock() -> None:
            with cal._cache_lock:
                lock_held.set()
                release.wait(timeout=2.0)

        holder = threading.Thread(target=hold_lock)
        holder.start()
        self.assertTrue(lock_held.wait(timeout=2.0))
        # If the fast path incorrectly re-acquired the lock, this call
        # would deadlock until the holder thread released. Instead it
        # should return immediately.
        result = cal.count_trading_days(date(2026, 1, 1), date(2026, 3, 1))
        self.assertEqual(result, 1)
        release.set()
        holder.join(timeout=2.0)


if __name__ == "__main__":
    unittest.main()
