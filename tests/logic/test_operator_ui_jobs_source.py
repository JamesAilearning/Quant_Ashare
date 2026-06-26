"""Source-level guards for operator UI Jobs page security invariants."""

from __future__ import annotations

import unittest
from pathlib import Path

_JOBS_SOURCE = Path("web/operator_ui/pages/jobs.py")


class JobsPageXssGuardTests(unittest.TestCase):
    """The Jobs page copy-run-id button injects HTML into the DOM via
    ``st.html(unsafe_allow_javascript=True)``. ``selected.run_id`` reaches
    that button from ``JobSummary`` instances built by
    ``_normalise_ui_job`` (UI-generated job id, controlled) AND
    ``_normalise_cli_entry`` (CLI catalog entry from
    ``output/runs/_index.jsonl``, NOT URL-guarded). A crafted CLI entry
    containing ``">`` would break out of the input ``value`` attribute
    and execute arbitrary JS in ``window.parent``.

    These tests pin the escape in place at the source level so a future
    refactor does not silently re-introduce the XSS vector.
    """

    def setUp(self) -> None:
        self.source = _JOBS_SOURCE.read_text(encoding="utf-8")

    def test_jobs_page_imports_html_module(self) -> None:
        self.assertIn("import html", self.source)

    def test_jobs_page_escapes_selected_run_id_before_value_attr(self) -> None:
        # The raw, vulnerable f-string MUST NOT survive in source.
        self.assertNotIn('value="{selected.run_id}"', self.source)
        # And the escape must use ``quote=True`` so ``"`` becomes
        # ``&quot;`` — without ``quote=True`` the default escape leaves
        # ``"`` intact and the value attribute still breaks.
        self.assertIn(
            "html.escape(selected.run_id, quote=True)", self.source
        )

    def test_escaped_run_id_is_used_in_rendered_value_attribute(self) -> None:
        # Defense in depth: regardless of the variable name, the value
        # attribute must be sourced from an html.escape(..) result and
        # not from selected.run_id directly.
        self.assertIn('value="{escaped_run_id}"', self.source)


class JobsPageStopActionTests(unittest.TestCase):
    """The Jobs page MUST expose the spec-required Stop action and wire it to
    ``JobManager.stop()`` (openspec v2-operator-ui-console: "Operator UI SHALL
    support stopping a running job"). ``JobManager.stop()`` owns the status
    transition (writes 'stopped' only on a successful kill, 'stop_failed'
    otherwise), so the page only calls it, handles the typed error, and reruns.
    """

    def setUp(self) -> None:
        self.source = _JOBS_SOURCE.read_text(encoding="utf-8")

    def test_stop_button_calls_job_manager_stop(self) -> None:
        self.assertIn("JobManager.stop(selected.run_id)", self.source)

    def test_stop_action_gated_on_running_or_stop_failed(self) -> None:
        self.assertIn(
            'selected.status in ("running", "stop_failed")', self.source
        )

    def test_stop_button_handles_typed_error(self) -> None:
        self.assertIn("except JobManagerError", self.source)


class JobsPageStatusVocabularyTests(unittest.TestCase):
    """The status filter must match the statuses the system actually produces:
    'cancelled' is never written by the runner/JobManager, while the stop
    lifecycle (stopped / stop_failed) and partial / pending were unreachable.
    """

    def setUp(self) -> None:
        self.source = _JOBS_SOURCE.read_text(encoding="utf-8")

    def test_filter_drops_never_produced_cancelled_option(self) -> None:
        self.assertNotIn(
            '["all", "queued", "running", "completed", "failed", "cancelled"]',
            self.source,
        )

    def test_filter_offers_real_terminal_states(self) -> None:
        for status in ('"pending"', '"partial"', '"stopped"', '"stop_failed"'):
            self.assertIn(status, self.source)

    def test_status_icons_cover_stop_lifecycle(self) -> None:
        for icon_key in (
            '"stopped":', '"stop_failed":', '"partial":', '"pending":',
        ):
            self.assertIn(icon_key, self.source)


_APP_SOURCE = Path("web/operator_ui/app.py")


class SidebarStatusIndicatorTests(unittest.TestCase):
    """The sidebar global indicator reads RAW ``list_jobs()`` statuses, so it
    must count the real vocabulary: ``stop_failed`` as a failure and ``partial``
    as a completion (both were previously invisible). The legacy ``completed`` /
    ``ok`` aliases are KEPT alongside ``success`` — the codebase still treats
    them as valid on-disk terminal statuses (job_io _CLEANUP_TERMINAL_STATUSES),
    so old completed runs must not vanish from the count (codex P2 on #293)."""

    def setUp(self) -> None:
        self.source = _APP_SOURCE.read_text(encoding="utf-8")

    def test_counts_stop_failed_as_failure(self) -> None:
        self.assertIn('("failed", "stop_failed")', self.source)

    def test_completed_count_keeps_legacy_aliases_and_adds_partial(self) -> None:
        self.assertIn('("success", "completed", "ok", "partial")', self.source)


if __name__ == "__main__":
    unittest.main()
