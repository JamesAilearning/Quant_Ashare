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

    def test_lock_refusal_is_rendered_as_the_authority(self) -> None:
        # codex #440 r2: the status gate above is advisory UX only; the
        # authoritative serialization is the runner holding the
        # updater's provider lock, and its refusal must be rendered.
        self.assertIn("blocked_by_update", self.src)

    def test_polling_survives_a_freshly_launched_update(self) -> None:
        # codex #442 r1: _running_fresh is computed BEFORE the child writes
        # its running record, so a launch from an idle page registered no
        # watcher — the page sat on the pre-launch status until a manual
        # refresh. The launch now stamps a bounded await marker and reruns
        # so the watcher registers in the same interaction.
        self.assertIn("_AWAIT_LAUNCH_KEY", self.src)
        self.assertIn("_AWAIT_LAUNCH_WINDOW", self.src)
        self.assertIn("_watching = _running_fresh or _awaiting_launch", self.src)
        self.assertIn("if _watching:", self.src)
        # The marker must be bounded, or a failed launch polls forever.
        self.assertIn("_read_at - _awaiting_since < _AWAIT_LAUNCH_WINDOW", self.src)
        # The rerun is what makes the marker take effect this interaction.
        self.assertIn("st.rerun()", self.src)

    def test_baseline_signature_is_captured_at_full_render(self) -> None:
        # codex #442 r2: 在片段里对两侧各算一次分类是**无效**的——跨过
        # 6 小时线时,片段用同一个 now 分类新读到的记录和旧的 _status
        # 对象,两边同时变 stale,元组照样相等,永不 rerun。基线必须在
        # 整页渲染时刻定格并被闭包捕获。
        self.assertIn("_baseline_signature = _status_signature(_status)", self.src)
        baseline_at = self.src.index("_baseline_signature = _status_signature")
        watch_at = self.src.index("if _watching:")
        self.assertLess(baseline_at, watch_at, "基线必须在片段注册之前算定")
        # 片段内只算「新读到的那一侧」,另一侧用捕获的基线。
        self.assertIn(
            "if _status_signature(read_update_status(_status_path)) != _baseline_signature:",
            self.src,
        )
        self.assertNotIn("!= _status_signature(\n            _status\n        )", self.src)

    def test_stale_record_does_not_retire_the_launch_marker(self) -> None:
        # codex #442 r2: 恢复性启动(带着一条陈旧 running 记录去补跑)时,
        # 若把这条旧记录当成「新运行已出现」而清掉等待标记,_watching 会
        # 落回 False,新运行直到手动刷新才被看见。只有**新鲜**记录才算数。
        self.assertIn("if _running_fresh:", self.src)
        retire_at = self.src.index("if _running_fresh:")
        block = self.src[retire_at : retire_at + 700]
        self.assertIn("_AWAIT_LAUNCH_KEY", block)
        # 退休条件不得只看 kind == running（那正是 r2 指出的缺陷）。
        self.assertNotIn(
            'if _status.kind == "running" and record_matches_provider(', self.src
        )

    def test_freshness_transition_is_part_of_the_watch_signature(self) -> None:
        # codex #442 r1: kind/started_at/finished_at are byte-identical as a
        # run crosses the 6h staleness line, so a crashed run kept the gates
        # locked and the stale warning hidden. The classification is part of
        # the signature now.
        self.assertIn("classify_running(status)", self.src)
        sig_at = self.src.index("def _status_signature")
        body = self.src[sig_at : sig_at + 1200]
        self.assertIn("classify_running", body)
        self.assertIn("tuple[str, str, str, str]", body)

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
