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

    def test_old_run_history_redirect_file_is_removed(self) -> None:
        """``pages/run_history.py`` was a 6-line ``st.switch_page`` stub
        that wasn't registered in ``app.py``'s navigation — pure dead
        code that just added grep noise. UI review P1-14 deleted it;
        pin its absence so a revert doesn't bring it back. Operators
        who bookmarked the old URL hit Streamlit's default 404 and can
        navigate to Jobs from the sidebar."""

        self.assertFalse(
            Path("web/operator_ui/pages/run_history.py").exists(),
            "run_history.py should have been deleted as dead code",
        )

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
        self.assertIn("今天", source)
        self.assertIn("最近 7 天", source)
        self.assertIn("最近 30 天", source)
        self.assertIn("本年至今", source)
        self.assertIn("清除日期", source)

    def test_jobs_page_routes_row_click_via_switch_page(self) -> None:
        """Row selection SHALL navigate to results.py or walk_forward.py via
        ``st.switch_page`` and seed the run id in session_state / query_params."""

        source = Path("web/operator_ui/pages/jobs.py").read_text(encoding="utf-8")

        self.assertIn("on_select=", source)
        self.assertIn("selection_mode=", source)
        self.assertIn("st.switch_page", source)
        self.assertIn('pages/results.py', source)
        self.assertIn('pages/walk_forward.py', source)

    def test_jobs_page_uses_canonical_run_id_query_param(self) -> None:
        """Row click SHALL set ``st.query_params["run_id"]`` — the key that
        ``results.py._query_run_id`` and ``walk_forward.py`` consume.

        Regression guard for Codex PR #118 P1: prior implementation set
        ``st.query_params["run"]`` which neither detail page read, so the
        selected run did not survive ``st.switch_page``.
        """

        source = Path("web/operator_ui/pages/jobs.py").read_text(encoding="utf-8")

        self.assertIn('st.query_params["run_id"]', source)
        self.assertNotIn('st.query_params["run"] =', source)

    def test_walk_forward_page_honours_query_param_run_selection(self) -> None:
        """walk_forward.py SHALL pre-select the run named in
        ``st.query_params["run_id"]`` so click-through from the Jobs hub
        lands on the operator's chosen run, not the most recent one."""

        source = Path("web/operator_ui/pages/walk_forward.py").read_text(encoding="utf-8")

        self.assertIn('st.query_params.get("run_id"', source)
        self.assertIn("wf_selected_run", source)
        self.assertIn("_default_index", source)

    def test_jobs_page_offers_active_filter_chips(self) -> None:
        """Each non-default filter SHALL surface as a removable chip
        (TICKET-A "filter chips") and there SHALL be a Clear-all action."""

        source = Path("web/operator_ui/pages/jobs.py").read_text(encoding="utf-8")

        self.assertIn("jobs_chip_clear_", source)
        self.assertIn("清除全部", source)

    def test_jobs_page_offers_running_job_autorefresh(self) -> None:
        """When at least one job is running, the page SHALL surface an
        explicit auto-refresh control (TICKET-A "polling")."""

        source = Path("web/operator_ui/pages/jobs.py").read_text(encoding="utf-8")

        self.assertIn("自动刷新", source)
        self.assertIn("running_count", source)


if __name__ == "__main__":
    unittest.main()
