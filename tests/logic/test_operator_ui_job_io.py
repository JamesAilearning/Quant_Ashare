"""Tests for the operator UI job-listing helpers (sort, date range, errors)."""

from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from web.operator_ui.job_io import (
    SORT_OPTIONS,
    JobSummary,
    _apply_filters,
    _apply_sort,
    _parse_date_or_raise,
    jobs_eligible_for_cleanup,
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


class ListAllJobsPaginationTests(unittest.TestCase):
    """UI review P1-10: ``list_all_jobs`` returns a real offset slice
    instead of the cumulative ``sorted_items[: page * page_size]`` form
    the load-more UX relied on. Pin the new contract so the cumulative
    pattern can't quietly come back."""

    def _fake_filtered_items(self, count: int) -> list[object]:
        # We don't need real JobSummary fixtures — the pagination test
        # cares about list slicing, so patch ``_apply_sort`` to return
        # a sentinel list of plain integers ordered as the function
        # would have sorted them.
        return list(range(count))

    def _list_with_fake_items(
        self, items: list[object], **kwargs: object
    ) -> tuple[list[object], int, int]:
        from web.operator_ui import job_io

        with patch.object(job_io, "_load_ui_jobs", return_value=[]), \
             patch.object(job_io, "_load_cli_entries", return_value=[]), \
             patch.object(job_io, "_apply_sort", return_value=items):
            return job_io.list_all_jobs(**kwargs)  # type: ignore[arg-type]

    def test_page_one_returns_first_page_size_items(self) -> None:
        items = self._fake_filtered_items(50)
        page, total, _ = self._list_with_fake_items(items, page=1, page_size=25)
        self.assertEqual(total, 50)
        self.assertEqual(page, list(range(0, 25)))

    def test_page_two_returns_next_window_not_cumulative(self) -> None:
        items = self._fake_filtered_items(50)
        page, total, _ = self._list_with_fake_items(items, page=2, page_size=25)
        self.assertEqual(total, 50)
        # Crucially: items 25..49 ONLY — NOT 0..49. The cumulative
        # variant returned the full prefix on each click; the real
        # pagination returns the window for this page only.
        self.assertEqual(page, list(range(25, 50)))

    def test_last_partial_page_returns_remainder(self) -> None:
        items = self._fake_filtered_items(53)
        page, total, _ = self._list_with_fake_items(items, page=3, page_size=25)
        self.assertEqual(total, 53)
        self.assertEqual(page, list(range(50, 53)))

    def test_request_past_end_returns_empty_with_intact_total(self) -> None:
        items = self._fake_filtered_items(10)
        page, total, _ = self._list_with_fake_items(items, page=99, page_size=25)
        # Total preserves so the UI can show "past last page" or snap
        # back to a valid page without re-querying.
        self.assertEqual(total, 10)
        self.assertEqual(page, [])

    def test_single_page_dataset_returns_everything(self) -> None:
        items = self._fake_filtered_items(7)
        page, total, _ = self._list_with_fake_items(items, page=1, page_size=25)
        self.assertEqual(total, 7)
        self.assertEqual(page, list(range(7)))

    def test_empty_dataset_returns_empty_page_with_total_zero(self) -> None:
        items = self._fake_filtered_items(0)
        page, total, _ = self._list_with_fake_items(items, page=1, page_size=25)
        self.assertEqual(total, 0)
        self.assertEqual(page, [])

    def test_invalid_page_value_raises(self) -> None:
        from web.operator_ui.job_io import list_all_jobs

        with self.assertRaises(ValueError):
            list_all_jobs(page=0)
        with self.assertRaises(ValueError):
            list_all_jobs(page=-5)

    def test_invalid_page_size_raises(self) -> None:
        from web.operator_ui.job_io import list_all_jobs

        with self.assertRaises(ValueError):
            list_all_jobs(page_size=0)


class ListAllJobsRunningCountTests(unittest.TestCase):
    """``list_all_jobs`` MUST return ``running_count`` over the FULL
    filtered set, not just the current page window — otherwise the
    jobs page's auto-refresh control disappears when the operator
    paginates away from running rows (Codex P2 on PR #197)."""

    def _build_summaries(
        self,
        *,
        running_indices: set[int],
        total_count: int,
    ) -> list[JobSummary]:
        summaries: list[JobSummary] = []
        for i in range(total_count):
            status = "running" if i in running_indices else "completed"
            summaries.append(
                _make(run_id=f"r{i:03d}", status=status, created_at="")
            )
        return summaries

    def test_running_count_spans_full_filtered_set_not_current_page(self) -> None:
        from web.operator_ui import job_io

        # 30 jobs, the running one sits at index 0 (= page 1).
        items = self._build_summaries(running_indices={0}, total_count=30)

        with patch.object(job_io, "_load_ui_jobs", return_value=[]), \
             patch.object(job_io, "_load_cli_entries", return_value=[]), \
             patch.object(job_io, "_apply_filters", return_value=items), \
             patch.object(job_io, "_apply_sort", return_value=items):
            page_items, total, running = job_io.list_all_jobs(
                page=2, page_size=25,
            )

        self.assertEqual(total, 30)
        # Page 2 holds items 25..29; none of them are running.
        self.assertEqual(len(page_items), 5)
        self.assertFalse(any(p.status == "running" for p in page_items))
        # But running_count still surfaces the page-1 running job —
        # this is the whole point of the fix.
        self.assertEqual(running, 1)

    def test_running_count_zero_when_no_running_in_full_set(self) -> None:
        from web.operator_ui import job_io

        items = self._build_summaries(running_indices=set(), total_count=10)
        with patch.object(job_io, "_load_ui_jobs", return_value=[]), \
             patch.object(job_io, "_load_cli_entries", return_value=[]), \
             patch.object(job_io, "_apply_filters", return_value=items), \
             patch.object(job_io, "_apply_sort", return_value=items):
            _, _, running = job_io.list_all_jobs(page=1, page_size=25)
        self.assertEqual(running, 0)

    def test_running_count_independent_of_page_window(self) -> None:
        """Same data, same filter — running_count MUST stay constant
        across every page the operator navigates to."""

        from web.operator_ui import job_io

        items = self._build_summaries(
            running_indices={0, 1, 2}, total_count=60,
        )
        with patch.object(job_io, "_load_ui_jobs", return_value=[]), \
             patch.object(job_io, "_load_cli_entries", return_value=[]), \
             patch.object(job_io, "_apply_filters", return_value=items), \
             patch.object(job_io, "_apply_sort", return_value=items):
            _, _, running_p1 = job_io.list_all_jobs(page=1, page_size=25)
            _, _, running_p2 = job_io.list_all_jobs(page=2, page_size=25)
            _, _, running_p3 = job_io.list_all_jobs(page=3, page_size=25)
        self.assertEqual(running_p1, 3)
        self.assertEqual(running_p2, 3)
        self.assertEqual(running_p3, 3)


class JobsPagePaginationUiTests(unittest.TestCase):
    """Source-level pin for the jobs page's prev/next pagination UI
    (UI review P1-10)."""

    def setUp(self) -> None:
        self.source = Path("web/operator_ui/pages/jobs.py").read_text(
            encoding="utf-8"
        )

    def test_prev_next_buttons_replace_cumulative_load_more(self) -> None:
        # Real prev/next buttons with stable keys.
        self.assertIn('"← 上一页"', self.source)
        self.assertIn('"下一页 →"', self.source)
        self.assertIn('key="jobs_pg_prev"', self.source)
        self.assertIn('key="jobs_pg_next"', self.source)
        # Cumulative "加载更多" pattern + its session key MUST be gone.
        self.assertNotIn("加载更多", self.source)
        self.assertNotIn("jobs_load_more", self.source)

    def test_page_indicator_shows_current_total_and_count(self) -> None:
        # Indicator references the (already-clamped) ``_page`` /
        # total pages / total rows so the operator always knows where
        # they are.
        self.assertIn("第 {_page} / {_total_pages} 页", self.source)
        self.assertIn("共 {total} 条", self.source)

    def test_buttons_disable_at_edges(self) -> None:
        # Prev disabled on page 1, Next disabled on last page so click
        # noise doesn't fire ineffective reruns.
        self.assertIn("disabled=_page <= 1", self.source)
        self.assertIn("disabled=_page >= _total_pages", self.source)

    def test_stale_page_is_clamped_and_requery_before_render(self) -> None:
        """When the stored ``jobs_page`` lands past the result set's
        last page (filter narrowed mid-session, jobs were pruned, URL
        points to a now-stale page), the page MUST be clamped AND the
        query re-issued so the indicator and dataframe agree. The
        earlier draft of this PR only clamped the indicator after the
        first ``list_all_jobs`` call ran with the stale page, so the
        UI rendered ``第 N / N 页`` while showing zero rows
        (Codex P2 on PR #197)."""

        # The local helper exists and is the *only* call site for
        # ``list_all_jobs`` on the page (so the re-query path can't
        # bypass it).
        self.assertIn("def _query_page(page_value: int)", self.source)
        # Re-query path runs when ``_page > _total_pages_pre`` after
        # the initial load.
        self.assertIn("_total_pages_pre", self.source)
        self.assertIn("if _page > _total_pages_pre:", self.source)
        # Re-query happens with the clamped ``_page`` value AND
        # writes that value back into session_state so a subsequent
        # rerun also reads the corrected page.
        self.assertIn('st.session_state["jobs_page"] = str(_page)', self.source)
        self.assertIn(
            "items, total, running_count = _query_page(_page)", self.source
        )

    def test_clamp_also_writes_page_back_to_url(self) -> None:
        """The earlier ``_qp_write("page", ...)`` block ran BEFORE the
        clamp using the stale value, so without an additional URL
        write the address bar reads ``?page=99`` while the page
        rendered N. Sharing / reloading would re-trigger the clamp
        loop instead of landing on the right page (Codex P3 on
        PR #197). Pin the inline ``_qp_write`` call inside the clamp
        branch."""

        # Find the clamp branch and check the URL mirror happens
        # inside it, before the re-query.
        clamp_idx = self.source.index("if _page > _total_pages_pre:")
        requery_idx = self.source.index(
            "items, total, running_count = _query_page(_page)", clamp_idx,
        )
        clamp_body = self.source[clamp_idx:requery_idx]
        self.assertIn(
            '_qp_write("page", str(_page))',
            clamp_body,
            "Clamped page must also be mirrored back to URL inside the clamp branch",
        )

    def test_running_count_comes_from_list_all_jobs_not_from_page_items(self) -> None:
        """``running_count`` MUST be unpacked from the
        ``list_all_jobs`` return tuple (3rd element) — NOT recomputed
        from the page slice. Otherwise the auto-refresh control
        disappears when the operator paginates past page 1 even
        while a job on page 1 is still running (Codex P2 on PR #197)."""

        # The 3-tuple unpack happens (and only happens) inside the
        # ``_query_page`` call site we already pin above.
        self.assertIn(
            "items, total, running_count = _query_page(_page)", self.source,
        )
        # The page MUST NOT recompute running_count from the page
        # slice (the bug the upstream fix closes).
        self.assertNotIn(
            'running_count = sum(1 for i in items if i.status == "running")',
            self.source,
        )


class JobsEligibleForCleanupTests(unittest.TestCase):
    """UI review P2-11: ``jobs_eligible_for_cleanup`` selects UI-launched,
    terminal-status jobs older than N days for one-click bulk delete."""

    _TODAY = date(2026, 6, 1)

    def test_selects_old_completed_ui_jobs(self) -> None:
        jobs = [
            _make(run_id="old", status="completed",
                  created_at="2026-04-01T00:00:00+00:00"),
            _make(run_id="recent", status="completed",
                  created_at="2026-05-30T00:00:00+00:00"),
        ]
        out = jobs_eligible_for_cleanup(jobs, older_than_days=30, today=self._TODAY)
        self.assertEqual(out, ["old"])

    def test_excludes_running_and_pending(self) -> None:
        jobs = [
            _make(run_id="run", status="running",
                  created_at="2026-01-01T00:00:00+00:00"),
            _make(run_id="pend", status="pending",
                  created_at="2026-01-01T00:00:00+00:00"),
            _make(run_id="done", status="failed",
                  created_at="2026-01-01T00:00:00+00:00"),
        ]
        out = jobs_eligible_for_cleanup(jobs, older_than_days=30, today=self._TODAY)
        self.assertEqual(out, ["done"])

    def test_excludes_cli_jobs(self) -> None:
        jobs = [
            _make(run_id="ui", status="completed", source="ui",
                  created_at="2026-01-01T00:00:00+00:00"),
            _make(run_id="cli", status="completed", source="cli",
                  created_at="2026-01-01T00:00:00+00:00"),
        ]
        out = jobs_eligible_for_cleanup(jobs, older_than_days=30, today=self._TODAY)
        self.assertEqual(out, ["ui"])

    def test_excludes_undated_jobs(self) -> None:
        jobs = [_make(run_id="nodate", status="completed", created_at="")]
        out = jobs_eligible_for_cleanup(jobs, older_than_days=30, today=self._TODAY)
        self.assertEqual(out, [])

    def test_boundary_is_strictly_older(self) -> None:
        # Cutoff = today - 30 = 2026-05-02. A job exactly on the cutoff
        # is NOT older-than (strict <), one day before IS.
        jobs = [
            _make(run_id="on_cutoff", status="completed",
                  created_at="2026-05-02T00:00:00+00:00"),
            _make(run_id="before_cutoff", status="completed",
                  created_at="2026-05-01T00:00:00+00:00"),
        ]
        out = jobs_eligible_for_cleanup(jobs, older_than_days=30, today=self._TODAY)
        self.assertEqual(out, ["before_cutoff"])

    def test_negative_days_raises(self) -> None:
        with self.assertRaises(ValueError):
            jobs_eligible_for_cleanup([], older_than_days=-1, today=self._TODAY)


if __name__ == "__main__":
    unittest.main()
