"""Unit tests for the CN stamp-tax schedule + helper.

Covers:

* ``StampTaxScheduleEntry`` construction validation
* ``CanonicalExchangeCostModel.stamp_tax_schedule`` validation
* ``compute_effective_stamp_tax_bps`` for single-segment,
  cross-one-transition, cross-two-transition, and pre-schedule
  cases
* ``resolve_stamp_tax_schedule`` (YAML → typed tuple coercion)
* ``stamp_tax_schedule_migration_snippet`` shape

Audit P0-4 / openspec/changes/add-stamp-tax-schedule.
"""

from __future__ import annotations

import sys
import unittest
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.canonical_backtest_contract import (  # noqa: E402
    CN_STAMP_TAX_SCHEDULE_DEFAULT,
    STAMP_TAX_BPS_MAX,
    CanonicalBacktestContractError,
    CanonicalExchangeCostModel,
    EffectiveStampTaxBps,
    StampTaxScheduleEntry,
    compute_effective_stamp_tax_bps,
    resolve_stamp_tax_schedule,
    stamp_tax_schedule_migration_snippet,
)


class StampTaxScheduleEntryTests(unittest.TestCase):
    def test_constructs_with_valid_inputs(self) -> None:
        e = StampTaxScheduleEntry(effective_from=date(2023, 8, 28), bps=5.0)
        self.assertEqual(e.effective_from, date(2023, 8, 28))
        self.assertEqual(e.bps, 5.0)

    def test_rejects_non_date_effective_from(self) -> None:
        with self.assertRaisesRegex(CanonicalBacktestContractError, "effective_from"):
            StampTaxScheduleEntry(
                effective_from="2023-08-28",  # type: ignore[arg-type]
                bps=5.0,
            )

    def test_rejects_bool_bps(self) -> None:
        with self.assertRaisesRegex(CanonicalBacktestContractError, "bps must be a real number"):
            StampTaxScheduleEntry(effective_from=date(2023, 8, 28), bps=True)  # type: ignore[arg-type]

    def test_rejects_negative_bps(self) -> None:
        with self.assertRaisesRegex(CanonicalBacktestContractError, "must be in"):
            StampTaxScheduleEntry(effective_from=date(2023, 8, 28), bps=-1.0)

    def test_rejects_bps_above_cap(self) -> None:
        with self.assertRaisesRegex(CanonicalBacktestContractError, "must be in"):
            StampTaxScheduleEntry(
                effective_from=date(2023, 8, 28),
                bps=STAMP_TAX_BPS_MAX + 1,
            )

    def test_rejects_datetime_effective_from(self) -> None:
        """Codex P2 follow-up on PR #178.

        ``datetime.datetime`` is a subclass of ``datetime.date``, so a
        bare ``isinstance(..., date)`` check would accept it and
        defer the failure to a confusing TypeError deep in
        ``compute_effective_stamp_tax_bps``. The contract must
        reject datetimes at construction with a message naming the
        field and pointing at the fix."""
        with self.assertRaisesRegex(
            CanonicalBacktestContractError,
            "must be a datetime.date \\(not datetime.datetime\\)",
        ):
            StampTaxScheduleEntry(
                effective_from=datetime(2023, 8, 28, 0, 0, 0),  # type: ignore[arg-type]
                bps=5.0,
            )

    def test_rejects_datetime_effective_from_with_time_component(self) -> None:
        """Variant that uses a non-zero time component — same
        rejection. Real-world YAML hazard: ``2023-08-28T12:00:00``
        unintentionally loaded as datetime."""
        with self.assertRaisesRegex(
            CanonicalBacktestContractError, "datetime.date"
        ):
            StampTaxScheduleEntry(
                effective_from=datetime(2023, 8, 28, 12, 30, 45),  # type: ignore[arg-type]
                bps=5.0,
            )


class DefaultScheduleTests(unittest.TestCase):
    def test_default_has_2023_reform(self) -> None:
        """Spec scenario: default schedule has the 2023-08-28 reform.

        Required by the OpenSpec spec under
        ``v2-canonical-backtest-contract``.
        """
        dates = [e.effective_from for e in CN_STAMP_TAX_SCHEDULE_DEFAULT]
        self.assertIn(date(2023, 8, 28), dates)
        reform_entry = next(
            e for e in CN_STAMP_TAX_SCHEDULE_DEFAULT
            if e.effective_from == date(2023, 8, 28)
        )
        self.assertEqual(reform_entry.bps, 5.0)

    def test_default_has_an_earlier_entry_at_10_bps(self) -> None:
        earlier = [
            e for e in CN_STAMP_TAX_SCHEDULE_DEFAULT
            if e.effective_from < date(2023, 8, 28)
        ]
        self.assertTrue(earlier)
        self.assertTrue(any(e.bps == 10.0 for e in earlier))


class CostModelScheduleValidationTests(unittest.TestCase):
    def _build(self, schedule):
        return CanonicalExchangeCostModel(
            commission_rate=0.0005,
            stamp_tax_schedule=schedule,
            slippage_bps=5.0,
            min_cost=5.0,
        )

    def test_accepts_default_schedule(self) -> None:
        cm = self._build(CN_STAMP_TAX_SCHEDULE_DEFAULT)
        self.assertEqual(cm.stamp_tax_schedule, CN_STAMP_TAX_SCHEDULE_DEFAULT)

    def test_rejects_empty_schedule(self) -> None:
        with self.assertRaisesRegex(CanonicalBacktestContractError, "non-empty"):
            self._build(())

    def test_rejects_list_not_tuple(self) -> None:
        with self.assertRaisesRegex(CanonicalBacktestContractError, "must be a tuple"):
            self._build([StampTaxScheduleEntry(date(2020, 1, 1), 10.0)])  # type: ignore[arg-type]

    def test_rejects_non_entry_element(self) -> None:
        with self.assertRaisesRegex(
            CanonicalBacktestContractError, "StampTaxScheduleEntry"
        ):
            self._build((("not", "an entry"),))  # type: ignore[arg-type]

    def test_rejects_descending_dates(self) -> None:
        bad = (
            StampTaxScheduleEntry(date(2023, 8, 28), 5.0),
            StampTaxScheduleEntry(date(2008, 9, 19), 10.0),
        )
        with self.assertRaisesRegex(
            CanonicalBacktestContractError, "strictly ascending"
        ):
            self._build(bad)

    def test_rejects_duplicate_dates(self) -> None:
        bad = (
            StampTaxScheduleEntry(date(2023, 8, 28), 5.0),
            StampTaxScheduleEntry(date(2023, 8, 28), 7.5),
        )
        with self.assertRaisesRegex(
            CanonicalBacktestContractError, "strictly ascending"
        ):
            self._build(bad)


class ComputeEffectiveStampTaxBpsTests(unittest.TestCase):
    """The runtime collapses a schedule into a single per-run scalar."""

    def test_single_segment_returns_segment_rate(self) -> None:
        """Spec scenario: period within one schedule entry."""
        result = compute_effective_stamp_tax_bps(
            CN_STAMP_TAX_SCHEDULE_DEFAULT,
            date(2024, 1, 1),
            date(2024, 12, 31),
        )
        self.assertIsInstance(result, EffectiveStampTaxBps)
        self.assertEqual(result.bps, 5.0)
        self.assertEqual(result.transitions, tuple())

    def test_period_entirely_before_reform_returns_10(self) -> None:
        result = compute_effective_stamp_tax_bps(
            CN_STAMP_TAX_SCHEDULE_DEFAULT,
            date(2020, 1, 1),
            date(2023, 8, 27),  # day BEFORE reform
        )
        self.assertEqual(result.bps, 10.0)
        self.assertEqual(result.transitions, tuple())

    def test_period_crosses_2023_reform(self) -> None:
        """Spec scenario: period crosses the 2023-08-28 transition.

        The weighted scalar must be strictly between 5.0 and 10.0,
        and the transitions tuple must contain the 2023-08-28 entry.
        """
        result = compute_effective_stamp_tax_bps(
            CN_STAMP_TAX_SCHEDULE_DEFAULT,
            date(2022, 1, 1),
            date(2024, 12, 31),
        )
        self.assertGreater(result.bps, 5.0)
        self.assertLess(result.bps, 10.0)
        self.assertEqual(len(result.transitions), 1)
        self.assertEqual(result.transitions[0].effective_from, date(2023, 8, 28))
        self.assertEqual(result.transitions[0].bps, 5.0)

    def test_period_starting_exactly_on_transition_is_one_segment(self) -> None:
        """A period that starts on the day a new rate takes effect is
        entirely covered by that new segment — no transition fires
        because no transition was CROSSED."""
        result = compute_effective_stamp_tax_bps(
            CN_STAMP_TAX_SCHEDULE_DEFAULT,
            date(2023, 8, 28),
            date(2024, 12, 31),
        )
        self.assertEqual(result.bps, 5.0)
        self.assertEqual(result.transitions, tuple())

    def test_period_ending_day_before_transition_is_pre_reform(self) -> None:
        """A period that ends the day before a transition is
        entirely pre-reform."""
        result = compute_effective_stamp_tax_bps(
            CN_STAMP_TAX_SCHEDULE_DEFAULT,
            date(2022, 1, 1),
            date(2023, 8, 27),
        )
        self.assertEqual(result.bps, 10.0)
        self.assertEqual(result.transitions, tuple())

    def test_period_before_schedule_start_raises(self) -> None:
        """Spec scenario: period precedes the schedule's first
        ``effective_from``. Hard error — we do NOT extrapolate."""
        with self.assertRaisesRegex(
            CanonicalBacktestContractError, "precedes the schedule"
        ):
            compute_effective_stamp_tax_bps(
                CN_STAMP_TAX_SCHEDULE_DEFAULT,
                date(2005, 1, 1),
                date(2009, 12, 31),
            )

    def test_three_segment_schedule_weighted_average(self) -> None:
        """Synthetic three-segment schedule. Use round dates so the
        weighted average is exactly computable.

        100 days @ 10 bps + 100 days @ 5 bps + 100 days @ 2 bps
        weighted scalar should equal (1000 + 500 + 200) / 300 = 5.667
        """
        schedule = (
            StampTaxScheduleEntry(date(2020, 1, 1), 10.0),
            StampTaxScheduleEntry(date(2020, 4, 10), 5.0),   # +100 days
            StampTaxScheduleEntry(date(2020, 7, 19), 2.0),   # +100 days
        )
        # Period covers exactly the three 100-day segments.
        # period_end is INCLUSIVE so the third segment runs from
        # 2020-07-19 (inclusive) through period_end (inclusive). To
        # get exactly 100 days in segment 3 we need
        # period_end == 2020-07-19 + 99 days == 2020-10-26.
        result = compute_effective_stamp_tax_bps(
            schedule,
            date(2020, 1, 1),
            date(2020, 10, 26),
        )
        self.assertAlmostEqual(result.bps, (1000.0 + 500.0 + 200.0) / 300.0, places=4)
        self.assertEqual(len(result.transitions), 2)

    def test_calendar_weighted_vs_default(self) -> None:
        """With a trading calendar that skips ~30% of calendar days,
        the weighting shifts. Use an artificial calendar to verify
        the helper honours the calendar argument."""
        schedule = (
            StampTaxScheduleEntry(date(2023, 1, 1), 10.0),
            StampTaxScheduleEntry(date(2023, 4, 1), 5.0),  # ~90 cal days later
        )
        period_start = date(2023, 1, 1)
        period_end = date(2023, 6, 30)
        # Calendar that only includes the FIRST month of pre-reform
        # — heavily biases trading-day weighting toward pre-reform.
        calendar = [date(2023, 1, 1) + __import__("datetime").timedelta(days=i)
                    for i in range(31)]
        result_no_cal = compute_effective_stamp_tax_bps(
            schedule, period_start, period_end,
        )
        result_with_cal = compute_effective_stamp_tax_bps(
            schedule, period_start, period_end, calendar=calendar,
        )
        # Calendar version weights pre-reform days more heavily.
        self.assertGreater(result_with_cal.bps, result_no_cal.bps)

    def test_empty_calendar_in_period_raises(self) -> None:
        """Codex P1 follow-up on PR #178.

        When a caller passes an explicit ``calendar`` that has zero
        entries in every schedule segment (e.g. a misconfigured
        bundle that doesn't cover the requested window), the
        helper MUST raise rather than fall back to the first
        segment's rate. Silent fallback would produce a degraded
        official scalar AND swallow the cross-period transitions
        list — exactly the "no silent fallback" anti-pattern this
        codebase forbids.
        """
        # Period spans the 2023-08-28 reform; calendar is supplied
        # but its dates all fall OUTSIDE the period.
        calendar_outside_period = [date(2018, 1, 1), date(2019, 1, 1)]
        with self.assertRaisesRegex(
            CanonicalBacktestContractError,
            "zero trading days",
        ):
            compute_effective_stamp_tax_bps(
                CN_STAMP_TAX_SCHEDULE_DEFAULT,
                date(2022, 1, 1),
                date(2024, 12, 31),
                calendar=calendar_outside_period,
            )

    def test_calendar_none_with_zero_length_segment_does_not_trip_empty_calendar_guard(
        self,
    ) -> None:
        """With ``calendar=None`` the weights are calendar-day
        counts. Segments are pre-filtered to non-zero length, so
        the empty-calendar guard MUST NOT fire here — only when an
        explicit calendar was supplied and was empty. Defensive
        regression."""
        result = compute_effective_stamp_tax_bps(
            CN_STAMP_TAX_SCHEDULE_DEFAULT,
            date(2024, 1, 1),
            date(2024, 12, 31),
            calendar=None,
        )
        # Same result as the single-segment test — no exception.
        self.assertEqual(result.bps, 5.0)

    def test_end_before_start_raises(self) -> None:
        with self.assertRaisesRegex(
            CanonicalBacktestContractError, "period_end .* < period_start"
        ):
            compute_effective_stamp_tax_bps(
                CN_STAMP_TAX_SCHEDULE_DEFAULT,
                date(2024, 12, 31),
                date(2024, 1, 1),
            )

    def test_empty_schedule_raises(self) -> None:
        with self.assertRaisesRegex(
            CanonicalBacktestContractError, "schedule is empty"
        ):
            compute_effective_stamp_tax_bps(
                tuple(),
                date(2024, 1, 1),
                date(2024, 12, 31),
            )


class ResolveStampTaxScheduleTests(unittest.TestCase):
    def test_none_resolves_to_default(self) -> None:
        self.assertEqual(
            resolve_stamp_tax_schedule(None),
            CN_STAMP_TAX_SCHEDULE_DEFAULT,
        )

    def test_already_typed_tuple_passes_through(self) -> None:
        self.assertIs(
            resolve_stamp_tax_schedule(CN_STAMP_TAX_SCHEDULE_DEFAULT),
            CN_STAMP_TAX_SCHEDULE_DEFAULT,
        )

    def test_yaml_shaped_list_of_dicts(self) -> None:
        raw = [
            {"effective_from": "2020-01-01", "bps": 10.0},
            {"effective_from": "2023-08-28", "bps": 5.0},
        ]
        out = resolve_stamp_tax_schedule(raw)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0].effective_from, date(2020, 1, 1))
        self.assertEqual(out[0].bps, 10.0)
        self.assertEqual(out[1].effective_from, date(2023, 8, 28))
        self.assertEqual(out[1].bps, 5.0)

    def test_accepts_date_object_in_dict(self) -> None:
        raw = [{"effective_from": date(2023, 8, 28), "bps": 5.0}]
        out = resolve_stamp_tax_schedule(raw)
        self.assertEqual(out[0].effective_from, date(2023, 8, 28))

    def test_accepts_from_alias_key(self) -> None:
        """``from`` is accepted as a shorthand for ``effective_from``."""
        raw = [{"from": "2023-08-28", "bps": 5.0}]
        out = resolve_stamp_tax_schedule(raw)
        self.assertEqual(out[0].effective_from, date(2023, 8, 28))

    def test_rejects_string_value(self) -> None:
        with self.assertRaisesRegex(
            CanonicalBacktestContractError, "expected a list"
        ):
            resolve_stamp_tax_schedule("cn_default")

    def test_rejects_single_mapping_not_a_list(self) -> None:
        with self.assertRaisesRegex(
            CanonicalBacktestContractError, "expected a list"
        ):
            resolve_stamp_tax_schedule({"effective_from": "2023-08-28", "bps": 5.0})

    def test_rejects_entry_missing_effective_from(self) -> None:
        with self.assertRaisesRegex(
            CanonicalBacktestContractError, "effective_from"
        ):
            resolve_stamp_tax_schedule([{"bps": 5.0}])

    def test_rejects_entry_missing_bps(self) -> None:
        with self.assertRaisesRegex(CanonicalBacktestContractError, "bps"):
            resolve_stamp_tax_schedule([{"effective_from": "2023-08-28"}])

    def test_rejects_malformed_iso_date(self) -> None:
        with self.assertRaisesRegex(
            CanonicalBacktestContractError, "ISO YYYY-MM-DD"
        ):
            resolve_stamp_tax_schedule([{"effective_from": "Aug 28 2023", "bps": 5.0}])

    def test_rejects_empty_list(self) -> None:
        with self.assertRaisesRegex(
            CanonicalBacktestContractError, "empty sequence"
        ):
            resolve_stamp_tax_schedule([])

    def test_rejects_datetime_value_via_yaml_shape(self) -> None:
        """If YAML loads ``effective_from`` as a datetime (e.g. when
        the file says ``2023-08-28 00:00:00``), the resolver must
        propagate the StampTaxScheduleEntry rejection — NOT silently
        truncate to ``.date()``. Silent truncation would discard
        operator intent (they wrote a time, even if the time is
        meaningless for stamp tax). Codex P2 follow-up on PR #178.
        """
        raw = [{
            "effective_from": datetime(2023, 8, 28, 0, 0, 0),
            "bps": 5.0,
        }]
        with self.assertRaisesRegex(
            CanonicalBacktestContractError, "datetime.date"
        ):
            resolve_stamp_tax_schedule(raw)


class MigrationSnippetTests(unittest.TestCase):
    def test_snippet_mentions_both_dates(self) -> None:
        s = stamp_tax_schedule_migration_snippet()
        self.assertIn("2008-09-19", s)
        self.assertIn("2023-08-28", s)
        self.assertIn("10.0", s)
        self.assertIn("5.0", s)
        self.assertIn("stamp_tax_schedule", s)


if __name__ == "__main__":
    unittest.main()
