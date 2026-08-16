"""Run-center page source pins (openspec 2026-08-16-ui-run-center W3/W4).

The page's contract is "trigger through the two audited runners, touch
nothing yourself": no direct spawn machinery, no write APIs, no
orchestrator imports. String-level scans on the page source, mirroring
the per-page pins of ``test_ops_cockpit_page_source`` /
``test_daily_decision_page_source`` (each page carries its OWN contract —
the cockpit's read-only pin list must NOT be copied here wholesale,
because this page legitimately renders buttons).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_PAGE = PROJECT_ROOT / "web" / "operator_ui" / "pages" / "run_center.py"
_APP = PROJECT_ROOT / "web" / "operator_ui" / "app.py"

# The page itself must never reach for spawn or write machinery — spawns
# live in the two runners, and the page renders results only.
_FORBIDDEN_IN_PAGE = (
    "subprocess",
    "Popen",
    "os.system",
    "os.spawn",
    "open(",
    "write_text",
    "write_bytes",
    "to_parquet",
    "to_csv",
    "mkdir",
    "shutil",
    "JobManager",
    "job_runner",
    "import qlib",
    "src.data_pipeline",
    "src.inference",
    "bundle_swap",
)

# The page's ONLY execution roads, plus the shared page furniture.
_REQUIRED_IN_PAGE = (
    "render_page_header",
    "update_runner",
    "recommend_runner",
    "launch_daily_update",
    "run_daily_recommend",
    "morning_command",
    "is_ensemble",
)


class PageSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.src = _PAGE.read_text(encoding="utf-8")

    def test_page_never_spawns_or_writes_directly(self) -> None:
        for needle in _FORBIDDEN_IN_PAGE:
            with self.subTest(forbidden=needle):
                self.assertNotIn(
                    needle,
                    self.src,
                    f"run_center.py 不得出现 {needle!r} —— 派生/写入只许"
                    "发生在两个 audited runner 里",
                )

    def test_page_reaches_execution_only_through_the_runners(self) -> None:
        for needle in _REQUIRED_IN_PAGE:
            with self.subTest(required=needle):
                self.assertIn(needle, self.src)

    def test_launch_is_gated_on_the_fresh_running_precheck(self) -> None:
        # The launch button must consume the reader's freshness
        # classification — a page that drops the guard would invite
        # double launches that only the single-flight lock then stops.
        self.assertIn("RUNNING_FRESH", self.src)
        self.assertIn("disabled=_running_fresh", self.src)

    def test_recommend_is_gated_on_the_ensemble_incumbent(self) -> None:
        # Ensemble-only by spec: the legacy single-model path stays a
        # terminal affair.
        self.assertIn("is_ensemble", self.src)
        self.assertIn('startswith("python ")', self.src)

    def test_recommend_is_serialized_against_a_running_update(self) -> None:
        # codex #440 r1: bundle_swap's two-rename window is not
        # reader-concurrent — while an update is running and fresh, the
        # page must not offer the recommend button either. Pin the gate
        # into the runnable expression.
        self.assertIn("and not _running_fresh", self.src)

    def test_failure_output_prefers_stdout_where_the_reason_lives(self) -> None:
        # The repo logger writes refusals to STDOUT (StreamHandler on
        # sys.stdout, propagate=False); stderr mostly carries import-time
        # environment noise. A page that prefers stderr would show the
        # noise instead of the reason — pin the preference order.
        self.assertIn("_result.stdout_tail or _result.stderr_tail", self.src)


class RegistrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = _APP.read_text(encoding="utf-8")

    def test_page_is_registered_in_navigation(self) -> None:
        self.assertIn('run_center.py"), title="运行中心"', self.app)

    def test_page_has_a_nav_icon(self) -> None:
        self.assertIn('"运行中心": ', self.app)


if __name__ == "__main__":
    unittest.main()
