"""Tests for the operator UI job-listing helpers (sort, date range, errors)."""

from __future__ import annotations

import unittest

from web.operator_ui.job_io import (
    SORT_OPTIONS,
    JobSummary,
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


class NormaliseJobIdLengthTests(unittest.TestCase):
    """Regression: prior to PR5 the ``_normalise_*`` helpers truncated
    ``run_id`` to 40 chars for display, but jobs.py uses ``run_id`` as
    the canonical routing key when handing off to results.py /
    walk_forward.py. Truncation broke exact-match selectbox lookup for
    any id longer than 40 chars. The full id MUST survive normalisation.
    """

    def test_ui_job_keeps_full_run_id_even_when_longer_than_40_chars(self) -> None:
        from web.operator_ui.job_io import _normalise_ui_job

        long_id = "pipeline_" + "x" * 64  # 73 chars, well past the old ceiling
        raw = {
            "job_id": long_id,
            "mode": "pipeline",
            "status": "completed",
            "created_at": "2026-05-22T12:00:00+00:00",
        }
        result = _normalise_ui_job(raw)
        self.assertEqual(result.run_id, long_id)

    def test_cli_entry_keeps_full_run_id_even_when_longer_than_40_chars(self) -> None:
        from web.operator_ui.job_io import _normalise_cli_entry

        long_id = "walk_forward_" + "y" * 64
        raw = {
            "run_id": long_id,
            "engine": "walk_forward",
            "status": "completed",
            "completed_at": "2026-05-22T12:00:00+00:00",
        }
        result = _normalise_cli_entry(raw)
        self.assertEqual(result.run_id, long_id)


class ExtractFailureDetailTests(unittest.TestCase):
    """Regression tests for ``_extract_failure_detail`` — the helper that
    surfaces the real error line in the Jobs table when a job fails.
    Operators need this to triage without opening stderr.log by hand.
    """

    def _job_dir(self, tmp: str, stderr_text: str | None):
        from pathlib import Path as _P

        job_dir = _P(tmp) / "job"
        job_dir.mkdir()
        if stderr_text is not None:
            (job_dir / "stderr.log").write_text(stderr_text, encoding="utf-8")
        return job_dir

    def test_returns_empty_when_stderr_log_missing(self) -> None:
        import tempfile

        from web.operator_ui.job_io import _extract_failure_detail

        with tempfile.TemporaryDirectory() as tmp:
            job_dir = self._job_dir(tmp, stderr_text=None)
            self.assertEqual(_extract_failure_detail(job_dir), "")

    def test_returns_empty_for_empty_stderr(self) -> None:
        import tempfile

        from web.operator_ui.job_io import _extract_failure_detail

        with tempfile.TemporaryDirectory() as tmp:
            job_dir = self._job_dir(tmp, stderr_text="")
            self.assertEqual(_extract_failure_detail(job_dir), "")

    def test_prefers_last_line_with_error_token(self) -> None:
        import tempfile

        from web.operator_ui.job_io import _extract_failure_detail

        log = (
            "INFO  starting up\n"
            "INFO  loaded config\n"
            "ValueError: features not exists: /path/instruments/csi800.txt\n"
            "INFO  shutting down\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = self._job_dir(tmp, stderr_text=log)
            self.assertEqual(
                _extract_failure_detail(job_dir),
                "ValueError: features not exists: /path/instruments/csi800.txt",
            )

    def test_falls_back_to_last_nonempty_line_when_no_error_token(self) -> None:
        import tempfile

        from web.operator_ui.job_io import _extract_failure_detail

        log = "step 1\nstep 2\n  \nstep 3 final\n"
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = self._job_dir(tmp, stderr_text=log)
            self.assertEqual(_extract_failure_detail(job_dir), "step 3 final")

    def test_truncates_overlong_line_to_max_chars(self) -> None:
        import tempfile

        from web.operator_ui.job_io import _extract_failure_detail

        long_msg = "ValueError: " + ("x" * 500)
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = self._job_dir(tmp, stderr_text=long_msg + "\n")
            result = _extract_failure_detail(job_dir, max_chars=120)
            self.assertEqual(len(result), 120)
            self.assertTrue(result.startswith("ValueError:"))

    def test_reads_only_tail_of_huge_log(self) -> None:
        """A multi-megabyte stderr.log MUST NOT be fully loaded — the
        helper only peeks at the trailing 8 KiB."""
        import tempfile

        from web.operator_ui.job_io import _extract_failure_detail

        # 1 MiB of innocuous prelude + a real error at the very end.
        prelude = ("INFO  noise line\n" * 70_000)
        log = prelude + "ValueError: tail error\n"
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = self._job_dir(tmp, stderr_text=log)
            self.assertEqual(
                _extract_failure_detail(job_dir),
                "ValueError: tail error",
            )


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
