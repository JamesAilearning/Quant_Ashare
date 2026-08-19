"""Source contracts for the read-only Today Workbench workflow."""

from __future__ import annotations

import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_APP = _ROOT / "web" / "operator_ui" / "app.py"
_PAGE = _ROOT / "web" / "operator_ui" / "pages" / "today_workbench.py"
_RUN_CENTER = _ROOT / "web" / "operator_ui" / "pages" / "run_center.py"
_DECISION = _ROOT / "web" / "operator_ui" / "pages" / "daily_decision.py"


class TodayWorkbenchSourceTests(unittest.TestCase):
    def test_navigation_is_grouped_by_operator_tasks(self) -> None:
        source = _APP.read_text(encoding="utf-8")
        for group in ("日常决策", "研究与验证", "生产治理"):
            with self.subTest(group=group):
                self.assertIn(f'"{group}"', source)
        self.assertIn('today_workbench.py"), title="今日工作台"', source)
        self.assertIn('daily_decision.py"), title="日度信号与人工决策"', source)

    def test_workbench_only_summarises_existing_artifacts(self) -> None:
        source = _PAGE.read_text(encoding="utf-8")
        forbidden = (
            "subprocess",
            "JobManager",
            "Pipeline",
            "WalkForwardEngine",
            "run_daily_recommend",
            "st.button(",
        )
        for needle in forbidden:
            with self.subTest(forbidden=needle):
                self.assertNotIn(needle, source)
        for required in ("summarise_daily_signal", "summarise_operations", "st.page_link"):
            with self.subTest(required=required):
                self.assertIn(required, source)

    def test_workbench_uses_the_non_mutating_job_reader(self) -> None:
        source = _PAGE.read_text(encoding="utf-8")
        self.assertIn(
            "from web.operator_ui.job_io import load_all_jobs_read_only", source
        )
        self.assertIn("all_jobs = tuple(load_all_jobs_read_only())", source)
        self.assertIn("summarise_operations(all_jobs)", source)
        self.assertNotIn("from web.operator_ui.job_io import load_all_jobs\n", source)

    def test_success_handoff_requires_a_published_dated_artifact(self) -> None:
        source = _RUN_CENTER.read_text(encoding="utf-8")
        self.assertIn("remember_run_center_published_date", source)
        self.assertIn("run_center_published_date", source)
        self.assertIn("DAILY_DECISION_REQUESTED_DATE_KEY", source)
        self.assertIn('st.switch_page("pages/daily_decision.py")', source)

    def test_review_action_is_rendered_outside_the_one_shot_run_branch(self) -> None:
        source = _RUN_CENTER.read_text(encoding="utf-8")
        result_branch = source.index('elif st.button(\n    "📝 跑今日出单')
        review_action = source.index('key="run_center::view_published_daily_signal"')
        dashboard = source.index("# ③ 看板入口")
        self.assertGreater(review_action, result_branch)
        self.assertLess(review_action, dashboard)
        self.assertIn(
            "This stays outside the one-shot ``run_recommend`` branch", source
        )

    def test_decision_page_consumes_the_one_shot_handoff_before_selectbox(self) -> None:
        source = _DECISION.read_text(encoding="utf-8")
        prepare_at = source.rindex("prepare_daily_decision_selection")
        selectbox_at = source.index('st.selectbox("交易日(as_of)"')
        self.assertLess(prepare_at, selectbox_at)
        self.assertIn('st.query_params.get("as_of")', source)
        self.assertIn('del st.query_params["as_of"]', source)

    def test_decision_page_does_not_present_signals_as_orders(self) -> None:
        source = _DECISION.read_text(encoding="utf-8")
        header_at = source.index("render_page_header(")
        header = source[header_at : header_at + 360]
        self.assertIn("日度信号与人工决策", header)
        self.assertIn("本页不重跑推断", header)


if __name__ == "__main__":
    unittest.main()
