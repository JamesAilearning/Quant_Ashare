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
from datetime import datetime, timedelta, timezone
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
        # (r3 起判据抽成纯函数 await_window_expired,主脚本与片段共用同一
        # 个判据——两处各写一份不等式正是它们会分叉的方式。)
        self.assertIn("await_window_expired(", self.src)
        self.assertIn(
            "not await_window_expired(\n        _awaiting_since, _read_at\n    )",
            self.src,
        )
        # The rerun is what makes the marker take effect this interaction.
        self.assertIn("st.rerun()", self.src)

    def test_await_deadline_is_enforced_inside_the_fragment(self) -> None:
        # codex #442 r3: 片段计时**只重跑片段**,主脚本不再执行——主脚本里
        # 算出的窗口判断在片段注册之后永远不会被重新求值。子进程若在写出
        # running 记录前就死掉(如撞单飞锁秒退 exit 17),签名永不变化,五分钟
        # 的「有界」窗口形同虚设,会一直轮询下去。
        self.assertIn("_await_deadline", self.src)
        fragment_at = self.src.index("def _watch_update_completion()")
        body = self.src[fragment_at : fragment_at + 1400]
        self.assertIn("await_window_expired", body)
        self.assertIn("st.rerun(scope=\"app\")", body)
        # 签名分支必须 return,否则到期判断会在同一次 tick 里重复触发。
        self.assertIn("return", body)

    def test_baseline_signature_is_captured_at_full_render(self) -> None:
        # codex #442 r2: 在片段里对两侧各算一次分类是**无效**的——跨过
        # 6 小时线时,片段用同一个 now 分类新读到的记录和旧的 _status
        # 对象,两边同时变 stale,元组照样相等,永不 rerun。基线必须在
        # 整页渲染时刻定格并被闭包捕获。
        self.assertIn(
            "_baseline_signature = _status_signature(_status, _status_class)",
            self.src,
        )
        baseline_at = self.src.index("_baseline_signature = _status_signature")
        watch_at = self.src.index("if _watching:")
        self.assertLess(baseline_at, watch_at, "基线必须在片段注册之前算定")
        # 片段内只算「新读到的那一侧」,另一侧用整页渲染时刻捕获的基线。
        self.assertIn(
            "_status_signature(_latest, classify_running(_latest))",
            self.src,
        )

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
        sig_at = self.src.index("def _status_signature")
        body = self.src[sig_at : sig_at + 1400]
        self.assertIn("tuple[str, str, str, str]", body)
        self.assertIn("classification or", body)
        # codex #442 r4: 分类由调用方传入，函数内部**不得**重算——两侧各自
        # 重算时，跨线时刻会同时翻面而元组照样相等，闸门永久锁死。
        self.assertNotIn("classify_running(status)", body)

    def test_render_time_classification_is_computed_once(self) -> None:
        # codex #442 r4: 闸门 / 展示 / 基线三处必须复用**同一次**分类。
        # 各调一次的话，记录恰在这几行之间跨过 6 小时线时，闸门说「新鲜」
        # 而基线已「陈旧」，此后片段读到的也都是陈旧、恒等于基线。
        self.assertIn("_status_class = classify_running(_status)", self.src)
        self.assertIn("and _status_class == RUNNING_FRESH", self.src)
        self.assertIn("_cls = _status_class", self.src)
        # 主脚本作用域里只允许出现这一次对 _status 的分类调用。
        self.assertEqual(self.src.count("classify_running(_status)"), 1)

    def test_failure_output_prefers_stdout_where_the_reason_lives(self) -> None:
        # The repo logger writes refusals to STDOUT (StreamHandler on
        # sys.stdout, propagate=False); stderr mostly carries import-time
        # environment noise. A page that prefers stderr would show the
        # noise instead of the reason — pin the preference order.
        self.assertIn("_result.stdout_tail or _result.stderr_tail", self.src)


class AwaitWindowBehaviorTests(unittest.TestCase):
    """启动等待窗的**行为**覆盖(codex #442 r3 要求)。

    判据抽成纯函数并注入 ``now``,所以「状态一直不变时窗口会到期」这件事
    可以被真正执行一遍,而不是只钉源码里出现过某个符号。
    """

    def setUp(self) -> None:
        from web.operator_ui.pages.run_center import (
            _AWAIT_LAUNCH_WINDOW,
            await_window_expired,
        )

        self.window = _AWAIT_LAUNCH_WINDOW
        self.expired = await_window_expired
        self.t0 = datetime(2026, 8, 18, 10, 0, tzinfo=timezone(timedelta(hours=8)))

    def test_not_expired_at_launch(self) -> None:
        self.assertFalse(self.expired(self.t0, self.t0))

    def test_not_expired_just_before_the_deadline(self) -> None:
        self.assertFalse(
            self.expired(self.t0, self.t0 + self.window - timedelta(seconds=1))
        )

    def test_expired_exactly_at_the_deadline(self) -> None:
        # 边界取 >=:恰好到点就该停,否则「五分钟」在实现上是开区间。
        self.assertTrue(self.expired(self.t0, self.t0 + self.window))

    def test_expired_well_past_the_deadline(self) -> None:
        self.assertTrue(self.expired(self.t0, self.t0 + self.window * 3))

    def test_window_is_bounded_and_short(self) -> None:
        # 窗口是给「子进程还没来得及写记录」留的余量,不是一个长轮询开关。
        self.assertLessEqual(self.window, timedelta(minutes=15))
        self.assertGreater(self.window, timedelta(0))


class RegistrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = _APP.read_text(encoding="utf-8")

    def test_page_is_registered_in_navigation(self) -> None:
        self.assertIn('run_center.py"), title="运行中心"', self.app)

    def test_page_has_a_nav_icon(self) -> None:
        self.assertIn('"运行中心": ', self.app)


if __name__ == "__main__":
    unittest.main()
