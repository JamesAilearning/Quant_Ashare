"""Source-level regression guards for Jobs page structure."""

from __future__ import annotations

import unittest
from pathlib import Path


class JobsSourceTests(unittest.TestCase):
    def test_jobs_page_imports_list_all_jobs(self) -> None:
        source = Path("web/operator_ui/pages/jobs.py").read_text(encoding="utf-8")

        self.assertIn("list_all_jobs", source)
        self.assertIn("render_badge", source)
        self.assertIn("format_relative_time", source)
        self.assertIn("format_duration", source)

    def test_old_run_history_redirects_to_jobs(self) -> None:
        source = Path("web/operator_ui/pages/run_history.py").read_text(encoding="utf-8")

        self.assertIn('pages/jobs.py', source)
        self.assertIn('st.switch_page', source)

    def test_app_nav_includes_jobs_not_run_history(self) -> None:
        source = Path("web/operator_ui/app.py").read_text(encoding="utf-8")

        self.assertIn('jobs.py', source)
        self.assertNotIn('"Run History"', source)

    def test_jobs_page_syncs_state_to_url(self) -> None:
        """Filters / sort / page SHALL be mirrored into st.query_params so
        reload preserves state and the URL is shareable (TICKET-A)."""

        source = Path("web/operator_ui/pages/jobs.py").read_text(encoding="utf-8")

        self.assertIn("st.query_params", source)
        self.assertIn("_qp_write", source)
        self.assertIn("_seed_session_from_url", source)

    def test_jobs_page_supports_date_range_and_sort(self) -> None:
        """The Jobs hub SHALL pass date_from/date_to and sort_by/sort_dir
        through to list_all_jobs (TICKET-A new contract)."""

        source = Path("web/operator_ui/pages/jobs.py").read_text(encoding="utf-8")

        self.assertIn("date_from", source)
        self.assertIn("date_to", source)
        self.assertIn("sort_by", source)
        self.assertIn("sort_dir", source)
        # The five quick-range buttons.
        self.assertIn("Today", source)
        self.assertIn("Last 7d", source)
        self.assertIn("Last 30d", source)
        self.assertIn("This year", source)
        self.assertIn("Clear dates", source)

    def test_jobs_page_routes_row_click_via_switch_page(self) -> None:
        """Row selection SHALL navigate to results.py or walk_forward.py via
        ``st.switch_page`` and seed the run id in session_state / query_params."""

        source = Path("web/operator_ui/pages/jobs.py").read_text(encoding="utf-8")

        self.assertIn("on_select=", source)
        self.assertIn("selection_mode=", source)
        self.assertIn("st.switch_page", source)
        self.assertIn('pages/results.py', source)
        self.assertIn('pages/walk_forward.py', source)

    def test_jobs_page_offers_active_filter_chips(self) -> None:
        """Each non-default filter SHALL surface as a removable chip
        (TICKET-A "filter chips") and there SHALL be a Clear-all action."""

        source = Path("web/operator_ui/pages/jobs.py").read_text(encoding="utf-8")

        self.assertIn("jobs_chip_clear_", source)
        self.assertIn("Clear all", source)

    def test_jobs_page_offers_running_job_autorefresh(self) -> None:
        """When at least one job is running, the page SHALL surface an
        explicit auto-refresh control (TICKET-A "polling")."""

        source = Path("web/operator_ui/pages/jobs.py").read_text(encoding="utf-8")

        self.assertIn("Auto-refresh", source)
        self.assertIn("running_count", source)


if __name__ == "__main__":
    unittest.main()
