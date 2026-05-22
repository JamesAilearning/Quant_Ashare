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


if __name__ == "__main__":
    unittest.main()
