"""Regression tests for operator UI display formatting helpers."""

from __future__ import annotations

import math
import unittest
from datetime import datetime, timezone


class OperatorUiFormattingTests(unittest.TestCase):
    def test_format_percent_handles_signed_values_and_missing(self) -> None:
        from web.operator_ui.formatting import format_percent

        self.assertEqual(format_percent(0.1834), "+18.34%")
        self.assertEqual(format_percent(-0.0245), "-2.45%")
        self.assertEqual(format_percent(None), "unavailable")
        self.assertEqual(format_percent(float("nan")), "unavailable")
        self.assertEqual(format_percent(float("inf")), "unavailable")

    def test_format_percent_without_plus_still_preserves_negative_sign(self) -> None:
        from web.operator_ui.formatting import format_percent

        self.assertEqual(format_percent(0.1834, signed=False), "18.34%")
        self.assertEqual(format_percent(-0.0245, signed=False), "-2.45%")
        self.assertEqual(
            format_percent(-0.0245, signed=False, parens_negative=True),
            "(2.45%)",
        )

    def test_format_number_handles_grouping_abbreviation_and_missing(self) -> None:
        from web.operator_ui.formatting import format_number

        self.assertEqual(format_number(1_234_567), "1,234,567.00")
        self.assertEqual(format_number(1_234_567, abbreviate=True), "1.23M")
        self.assertEqual(format_number(1.83456, decimals=4), "1.8346")
        self.assertEqual(format_number(True), "unavailable")
        self.assertEqual(format_number(math.nan), "unavailable")

    def test_format_money_and_duration_are_stable(self) -> None:
        from web.operator_ui.formatting import format_duration, format_money

        self.assertEqual(format_money(1_234_567.5), "CNY 1,234,567.50")
        self.assertEqual(format_money(-500), "-CNY 500.00")
        self.assertEqual(format_duration(0.5), "< 1s")
        self.assertEqual(format_duration(90), "1m 30s")
        self.assertEqual(format_duration(3725), "1h 2m")
        self.assertEqual(format_duration(-1), "unavailable")

    def test_relative_and_absolute_dates_are_formatted(self) -> None:
        from web.operator_ui.formatting import format_date_absolute, format_relative_time

        now = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)

        self.assertEqual(
            format_relative_time("2026-05-21T11:55:00+00:00", now=now),
            "5m ago",
        )
        self.assertEqual(
            format_relative_time("2026-05-20T12:00:00+00:00", now=now),
            "Yesterday",
        )
        self.assertEqual(format_relative_time("not-a-date", now=now), "unavailable")
        self.assertEqual(
            format_date_absolute("2026-05-21T10:30:00+00:00", style="date"),
            "2026-05-21",
        )
        self.assertEqual(
            format_date_absolute("2026-05-21T10:30:00+00:00", style="datetime"),
            "2026-05-21 10:30",
        )

    def test_legacy_fmt_metric_stays_available(self) -> None:
        from web.operator_ui.formatting import fmt_metric

        self.assertEqual(fmt_metric(1.234567), "1.2346")
        self.assertEqual(fmt_metric(None), "unavailable")


if __name__ == "__main__":
    unittest.main()
