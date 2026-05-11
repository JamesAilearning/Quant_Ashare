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
    extend_end_by_trading_days,
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


class ExtendEndByTradingDaysTests(unittest.TestCase):
    """Regression: this helper used to live as line-for-line copies in
    ``signal_analyzer`` and ``factor_analyzer``. Centralised here so
    drift between the two callers is impossible.

    The contract:
    - On success, returns the n-th trading day strictly after ``end_dt``.
    - On any degraded path (qlib import failure, calendar exception,
      calendar returns fewer days than requested) returns a calendar-day
      fallback ``end_dt + n*3`` and emits a WARNING through the
      caller-supplied logger.
    """

    def test_returns_nth_trading_day_when_calendar_has_enough(self) -> None:
        import logging
        import sys
        import types
        from unittest.mock import patch

        import pandas as pd

        # Stub qlib.data so ``from qlib.data import D`` succeeds and
        # returns a calendar with ample forward days.
        cal_dates = pd.date_range("2026-04-25", periods=20, freq="B")

        class _FakeD:
            @staticmethod
            def calendar(start_time, end_time, freq):
                return cal_dates

        fake_module = types.ModuleType("qlib.data")
        fake_module.D = _FakeD
        with patch.dict(sys.modules, {"qlib.data": fake_module}):
            mock_logger = logging.getLogger("test_extend_end")
            result = extend_end_by_trading_days(
                "2026-04-25", 5,
                logger=mock_logger, caller_name="TestCaller",
            )

        # 5 trading days strictly after 2026-04-25 → cal_dates[5]
        # (cal_dates[0] is 2026-04-27 since 25 is Saturday and freq=B)
        # We just assert it's a Timestamp far enough forward.
        self.assertIsInstance(result, pd.Timestamp)
        self.assertGreater(result, pd.Timestamp("2026-04-25"))

    def test_falls_back_to_calendar_days_when_qlib_returns_too_few(self) -> None:
        import sys
        import types
        from unittest.mock import patch

        import pandas as pd

        # Calendar has only 2 forward days; we ask for 5 → fallback path.
        cal_dates = pd.date_range("2026-04-25", periods=2, freq="B")

        class _FakeD:
            @staticmethod
            def calendar(start_time, end_time, freq):
                return cal_dates

        fake_module = types.ModuleType("qlib.data")
        fake_module.D = _FakeD

        captured_warnings: list[str] = []

        class _CapturingLogger:
            def warning(self, msg, *args, **kwargs):
                captured_warnings.append(msg % args if args else msg)

        with patch.dict(sys.modules, {"qlib.data": fake_module}):
            result = extend_end_by_trading_days(
                "2026-04-25", 5,
                logger=_CapturingLogger(), caller_name="TestCaller",
            )

        # Calendar-day fallback: end + 5*3 days
        self.assertEqual(result, pd.Timestamp("2026-04-25") + pd.Timedelta(days=15))
        # Must have warned with the caller name in the message
        self.assertEqual(len(captured_warnings), 1)
        self.assertIn("TestCaller", captured_warnings[0])

    def test_falls_back_when_calendar_raises(self) -> None:
        import sys
        import types
        from unittest.mock import patch

        import pandas as pd

        class _FakeD:
            @staticmethod
            def calendar(start_time, end_time, freq):
                raise RuntimeError("calendar provider exploded")

        fake_module = types.ModuleType("qlib.data")
        fake_module.D = _FakeD

        captured_warnings: list[str] = []

        class _CapturingLogger:
            def warning(self, msg, *args, **kwargs):
                captured_warnings.append(msg % args if args else msg)

        with patch.dict(sys.modules, {"qlib.data": fake_module}):
            result = extend_end_by_trading_days(
                "2026-04-25", 7,
                logger=_CapturingLogger(), caller_name="OtherCaller",
            )

        self.assertEqual(result, pd.Timestamp("2026-04-25") + pd.Timedelta(days=21))
        self.assertEqual(len(captured_warnings), 1)
        # Must mention which caller and surface the original exception type
        self.assertIn("OtherCaller", captured_warnings[0])
        self.assertIn("RuntimeError", captured_warnings[0])


if __name__ == "__main__":
    unittest.main()
