"""Tests for the operator UI job-listing helpers (sort, date range, errors)."""

from __future__ import annotations

import unittest

from web.operator_ui.job_io import (
    JobSummary,
    SORT_OPTIONS,
    _apply_filters,
    _apply_sort,
    _parse_date_or_raise,
)


def _make(
    *,
    run_id: str = "rid",
    type_: str = "pipeline",
    status: str = "completed",
    source: str = "ui",
    created_at: str = "",
    duration: float | None = None,
) -> JobSummary:
    return JobSummary(
        run_id=run_id,
        type=type_,
        status=status,
        source=source,
        created_at=created_at,
        finished_at=created_at,
        duration_seconds=duration,
    )


class ApplyFiltersDateRangeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.items = [
            _make(run_id="a", created_at="2026-05-10T12:00:00+00:00"),
            _make(run_id="b", created_at="2026-05-15T12:00:00+00:00"),
            _make(run_id="c", created_at="2026-05-20T12:00:00+00:00"),
            _make(run_id="d", created_at=""),  # no timestamp at all
        ]

    def test_date_from_only_drops_earlier_items_and_undated_ones(self) -> None:
        out = _apply_filters(
            self.items, "all", "all", "all", "", date_from="2026-05-15", date_to=""
        )
        self.assertEqual([i.run_id for i in out], ["b", "c"])

    def test_date_to_only_drops_later_items_and_undated_ones(self) -> None:
        out = _apply_filters(
            self.items, "all", "all", "all", "", date_from="", date_to="2026-05-15"
        )
        self.assertEqual([i.run_id for i in out], ["a", "b"])

    def test_date_range_is_inclusive_on_both_ends(self) -> None:
        out = _apply_filters(
            self.items,
            "all",
            "all",
            "all",
            "",
            date_from="2026-05-10",
            date_to="2026-05-20",
        )
        self.assertEqual([i.run_id for i in out], ["a", "b", "c"])

    def test_no_date_filter_keeps_undated_items(self) -> None:
        out = _apply_filters(self.items, "all", "all", "all", "")
        self.assertEqual(len(out), 4)


class ApplySortTests(unittest.TestCase):
    def test_sort_options_covers_documented_keys(self) -> None:
        self.assertEqual(
            set(SORT_OPTIONS),
            {"created_at", "duration", "status", "type", "run_id"},
        )

    def test_sort_by_created_at_desc_is_newest_first(self) -> None:
        items = [
            _make(run_id="old", created_at="2026-05-10T00:00:00+00:00"),
            _make(run_id="new", created_at="2026-05-20T00:00:00+00:00"),
            _make(run_id="mid", created_at="2026-05-15T00:00:00+00:00"),
        ]
        out = _apply_sort(items, "created_at", "desc")
        self.assertEqual([i.run_id for i in out], ["new", "mid", "old"])

    def test_sort_by_duration_pushes_none_to_end_in_both_directions(self) -> None:
        items = [
            _make(run_id="fast", duration=10.0),
            _make(run_id="unknown", duration=None),
            _make(run_id="slow", duration=100.0),
        ]
        desc = _apply_sort(items, "duration", "desc")
        self.assertEqual([i.run_id for i in desc], ["slow", "fast", "unknown"])
        asc = _apply_sort(items, "duration", "asc")
        self.assertEqual([i.run_id for i in asc], ["fast", "slow", "unknown"])

    def test_sort_by_run_id_asc_is_alphabetical(self) -> None:
        items = [_make(run_id=x) for x in ("c", "a", "b")]
        out = _apply_sort(items, "run_id", "asc")
        self.assertEqual([i.run_id for i in out], ["a", "b", "c"])

    def test_sort_by_unknown_key_raises(self) -> None:
        with self.assertRaises(ValueError):
            _apply_sort([_make()], "no_such_key", "asc")


class ParseDateTests(unittest.TestCase):
    def test_empty_value_is_silent_passthrough(self) -> None:
        _parse_date_or_raise("", field="date_from")  # no exception

    def test_valid_iso_date_passes(self) -> None:
        _parse_date_or_raise("2026-05-20", field="date_from")  # no exception

    def test_invalid_iso_date_raises_with_field_name(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            _parse_date_or_raise("not-a-date", field="date_from")
        self.assertIn("date_from", str(ctx.exception))


class ListAllJobsContractTests(unittest.TestCase):
    def test_unknown_sort_by_raises_before_loading_data(self) -> None:
        from web.operator_ui.job_io import list_all_jobs

        with self.assertRaises(ValueError) as ctx:
            list_all_jobs(sort_by="invalid_key")
        self.assertIn("invalid_key", str(ctx.exception))

    def test_unknown_sort_dir_raises(self) -> None:
        from web.operator_ui.job_io import list_all_jobs

        with self.assertRaises(ValueError):
            list_all_jobs(sort_dir="sideways")

    def test_invalid_date_from_raises(self) -> None:
        from web.operator_ui.job_io import list_all_jobs

        with self.assertRaises(ValueError):
            list_all_jobs(date_from="May 20")


if __name__ == "__main__":
    unittest.main()
