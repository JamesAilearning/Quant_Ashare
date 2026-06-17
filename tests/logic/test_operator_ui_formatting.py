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
        self.assertEqual(format_percent(None), "—")
        self.assertEqual(format_percent(float("nan")), "—")
        self.assertEqual(format_percent(float("inf")), "—")

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
        self.assertEqual(format_number(True), "—")
        self.assertEqual(format_number(math.nan), "—")

    def test_format_money_and_duration_are_stable(self) -> None:
        from web.operator_ui.formatting import format_duration, format_money

        self.assertEqual(format_money(1_234_567.5), "CNY 1,234,567.50")
        self.assertEqual(format_money(-500), "-CNY 500.00")
        self.assertEqual(format_duration(0.5), "<1 秒")
        self.assertEqual(format_duration(90), "1分 30秒")
        self.assertEqual(format_duration(3725), "1小时 2分")
        self.assertEqual(format_duration(-1), "—")

    def test_relative_and_absolute_dates_are_formatted(self) -> None:
        from web.operator_ui.formatting import format_date_absolute, format_relative_time

        now = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)

        self.assertEqual(
            format_relative_time("2026-05-21T11:55:00+00:00", now=now),
            "5 分钟前",
        )
        self.assertEqual(
            format_relative_time("2026-05-20T12:00:00+00:00", now=now),
            "昨天",
        )
        self.assertEqual(format_relative_time("not-a-date", now=now), "—")
        self.assertEqual(
            format_date_absolute("2026-05-21T10:30:00+00:00", style="date"),
            "2026-05-21",
        )
        # UTC 10:30 → CN local (UTC+8) 18:30 (audit G: stored UTC shown in CN).
        self.assertEqual(
            format_date_absolute("2026-05-21T10:30:00+00:00", style="datetime"),
            "2026-05-21 18:30",
        )

    def test_to_cn_date_buckets_utc_near_midnight_into_cn_day(self) -> None:
        from web.operator_ui.formatting import to_cn_date

        # 22:00Z → CN 06:00 NEXT day → buckets under the CN date the operator
        # sees, keeping the jobs date-filter consistent with the display.
        self.assertEqual(to_cn_date("2026-06-16T22:00:00+00:00"), "2026-06-17")
        self.assertEqual(to_cn_date("2026-06-16T10:00:00+00:00"), "2026-06-16")
        # naive / empty / unparseable fall back to the leading 10 chars / "".
        self.assertEqual(to_cn_date("2026-06-16T22:00:00"), "2026-06-16")
        self.assertEqual(to_cn_date(""), "")
        self.assertEqual(to_cn_date("garbage"), "garbage"[:10])

    def test_absolute_datetime_converts_utc_to_cn_local(self) -> None:
        from web.operator_ui.formatting import format_date_absolute

        # A UTC time near midnight rolls to the NEXT CN day (22:00Z + 8h = 06:00).
        self.assertEqual(
            format_date_absolute("2026-06-16T22:00:00+00:00", style="datetime"),
            "2026-06-17 06:00",
        )
        self.assertEqual(
            format_date_absolute("2026-06-16T22:00:00+00:00", style="date"),
            "2026-06-17",
        )
        # iso style stays canonical (preserves the original UTC offset).
        self.assertEqual(
            format_date_absolute("2026-06-16T22:00:00+00:00", style="iso"),
            "2026-06-16T22:00:00+00:00",
        )
        # naive datetimes (no tzinfo) are shown as-is, not shifted.
        self.assertEqual(
            format_date_absolute("2026-06-16T22:00:00", style="datetime"),
            "2026-06-16 22:00",
        )

    def test_format_percent_arrow_uses_symbols(self) -> None:
        from web.operator_ui.formatting import format_percent

        self.assertEqual(format_percent(0.1834, arrow=True), "+18.34% \u2197")
        self.assertEqual(format_percent(-0.0245, arrow=True), "-2.45% \u2198")
        self.assertEqual(format_percent(0, arrow=True), "+0.00% \u2197")
        self.assertEqual(format_percent(None, arrow=True), "—")

    def test_format_duration_drops_seconds_when_total_exceeds_10_minutes(self) -> None:
        from web.operator_ui.formatting import format_duration

        self.assertEqual(format_duration(630), "10分")
        self.assertEqual(format_duration(90), "1分 30秒")
        self.assertEqual(format_duration(600), "10分")
        self.assertEqual(format_duration(599), "9分 59秒")

    def test_format_percent_handles_zero_and_negative_zero_as_positive(self) -> None:
        from web.operator_ui.formatting import format_percent

        self.assertEqual(format_percent(0.0), "+0.00%")
        self.assertEqual(format_percent(float("-0.0")), "+0.00%")

    def test_format_number_abbreviate_respects_thresholds(self) -> None:
        from web.operator_ui.formatting import format_number

        self.assertEqual(format_number(999, abbreviate=True), "999.00")
        self.assertEqual(format_number(1_000, abbreviate=True), "1.00k")
        self.assertEqual(format_number(1_234_567, abbreviate=True), "1.23M")

    def test_legacy_fmt_metric_stays_available(self) -> None:
        from web.operator_ui.formatting import fmt_metric

        self.assertEqual(fmt_metric(1.234567), "1.2346")
        self.assertEqual(fmt_metric(None), "—")


if __name__ == "__main__":
    unittest.main()
