"""受控取消手动更新（2026-08-26-manual-update-controlled-cancel）。

三件事：取消只走活句柄（绝不按 pid 杀）、结果与日志标记如实、平台差异
按实测行为各自成立（Windows=硬杀完备因七阶段 in-process；POSIX=SIGINT
先礼后兵，礼貌路径由编排器 BaseException 终录承接）。
"""

from __future__ import annotations

import inspect
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from web.operator_ui.update_runner import (
    UpdateCancel,
    UpdateLaunch,
    cancel_update,
    cancelled_run_matches,
)

_ROOT = Path(__file__).resolve().parents[2]

_SLEEPER = (
    "import time\n"
    "for _ in range(600):\n"
    "    time.sleep(0.1)\n"
)


def _spawn(code: str) -> subprocess.Popen[bytes]:
    """按生产 spawn 的同款平台旗标起一个小子进程。"""
    if sys.platform == "win32":
        return subprocess.Popen(
            [sys.executable, "-c", code],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP
                           | subprocess.CREATE_NO_WINDOW))
    return subprocess.Popen(
        [sys.executable, "-c", code],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT, start_new_session=True)


class CancelOutcomesAreHonest(unittest.TestCase):
    def test_an_already_finished_process_is_a_noop(self) -> None:
        # 误点取消时进程其实已经跑完——什么都不杀、如实说、不落取消标记
        # （成败由状态工件/台账自述，取消无权改写历史）。
        proc = _spawn("pass")
        proc.wait(timeout=30)
        with tempfile.TemporaryDirectory() as t:
            log = Path(t) / "daily_update.log"
            got = cancel_update(proc, log, grace_seconds=2)
            self.assertEqual("already_finished", got.kind)
            self.assertFalse(log.exists(), "no-op 取消不该落任何日志标记")

    def test_a_live_process_is_cancelled_and_logged(self) -> None:
        proc = _spawn(_SLEEPER)
        try:
            time.sleep(0.5)
            with tempfile.TemporaryDirectory() as t:
                log = Path(t) / "daily_update.log"
                got = cancel_update(proc, log, grace_seconds=3)
                self.assertEqual("cancelled", got.kind)
                self.assertIsNotNone(proc.poll(), "进程没死")
                text = log.read_text(encoding="utf-8")
                self.assertIn("cancel requested", text)
                self.assertIn("cancel outcome", text)
                self.assertIn("[run_center]", text, "标记要走既有归属惯例")
        finally:
            if proc.poll() is None:
                proc.kill()

    @unittest.skipUnless(sys.platform == "win32", "Windows 硬杀路径")
    def test_windows_cancel_is_honestly_forcible(self) -> None:
        # 实测在档：CTRL_BREAK 对 NO_WINDOW 子进程送达不了（控制台隔离），
        # 且 Python 把 CTRL_BREAK 映射为 SIGBREAK 直接终止不产生
        # KeyboardInterrupt——Windows 路径**如实是硬杀**，graceful 必为
        # False，绝不谎称礼貌退出。
        proc = _spawn(_SLEEPER)
        try:
            time.sleep(0.5)
            with tempfile.TemporaryDirectory() as t:
                got = cancel_update(
                    proc, Path(t) / "log.log", grace_seconds=3)
            self.assertEqual("cancelled", got.kind)
            self.assertFalse(got.graceful, "Windows 谎称了礼貌退出")
        finally:
            if proc.poll() is None:
                proc.kill()

    @unittest.skipIf(sys.platform == "win32", "POSIX 礼貌信号路径")
    def test_posix_sigint_is_tried_before_the_hammer(self) -> None:
        # POSIX：子进程是会话组长，SIGINT → KeyboardInterrupt——编排器的
        # BaseException 终录路径正是接这个（#465 第三十二轮）。用带
        # handler 的小子进程作证：礼貌信号真的先到、且被判 graceful。
        with tempfile.TemporaryDirectory() as t:
            marker = Path(t) / "caught.txt"
            code = (
                "import time, pathlib, sys\n"
                "try:\n"
                "    for _ in range(600):\n"
                "        time.sleep(0.1)\n"
                "except KeyboardInterrupt:\n"
                f"    pathlib.Path({str(marker)!r}).write_text('sigint')\n"
                "    sys.exit(1)\n"
            )
            proc = _spawn(code)
            try:
                time.sleep(0.8)
                got = cancel_update(
                    proc, Path(t) / "log.log", grace_seconds=8)
                self.assertEqual("cancelled", got.kind)
                self.assertTrue(got.graceful, "礼貌信号没被判 graceful")
                self.assertEqual("sigint",
                                 marker.read_text(encoding="utf-8"),
                                 "子进程没收到 KeyboardInterrupt")
                # 及时退出 ≠ 终态已写（codex 第十轮 P2）：这个小子进程
                # 根本不写状态工件——核实不到就必须是 False。
                self.assertFalse(got.terminal_recorded,
                                 "没有终态记录却声称核实到了")
            finally:
                if proc.poll() is None:
                    proc.kill()


class HardCancelEvidenceIsDurable(unittest.TestCase):
    def test_the_match_is_exact_stamp_identity(self) -> None:
        # 证据按状态戳**精确相等**绑定（与边界归属同款纪律）：任一侧空
        # 不覆盖、错戳不覆盖——绝不把别人的运行说成已取消。
        self.assertTrue(cancelled_run_matches(
            "2026-08-26T21:00:00+08:00", "2026-08-26T21:00:00+08:00"))
        for status, evidence in (
            (None, "2026-08-26T21:00:00+08:00"),
            ("", "2026-08-26T21:00:00+08:00"),
            ("2026-08-26T21:00:00+08:00", None),
            ("2026-08-26T21:00:00+08:00", ""),
            ("2026-08-26T21:00:00+08:00", "2026-08-26T22:00:00+08:00"),
        ):
            with self.subTest(status=status, evidence=evidence):
                self.assertFalse(cancelled_run_matches(status, evidence))

    def test_a_swap_window_hit_is_reported_loudly(self) -> None:
        # 取消恰好落在两段 rename 之间：canonical 目录缺位——不许再说
        # 「在线数据不受影响」，要响亮报 swap_interrupted 并落标记
        # （codex #470 P1）。
        proc = _spawn(_SLEEPER)
        try:
            time.sleep(0.5)
            with tempfile.TemporaryDirectory() as t:
                log = Path(t) / "log.log"
                missing = Path(t) / "provider_gone"
                # crash 态签名：canonical 缺 **且** .bak 在（rename1 已做、
                # rename2 未做）——裸缺位不算（bootstrap 见下条用例）。
                (Path(t) / "provider_gone.bak").mkdir()
                got = cancel_update(
                    proc, log, provider_dir=missing, grace_seconds=3)
                self.assertEqual("cancelled", got.kind)
                self.assertTrue(got.swap_interrupted,
                                "canonical 缺+.bak 在没被判切换窗命中")
                self.assertIn("SWAP WINDOW",
                              log.read_text(encoding="utf-8"))
        finally:
            if proc.poll() is None:
                proc.kill()

    def test_bootstrap_absence_is_not_a_swap_hit(self) -> None:
        # 首次 bootstrap 本来就没有 live bundle——取消后 canonical 不存在
        # 但也没有 .bak：不许被误诊成切换窗命中（codex 第二轮 P2）。
        proc = _spawn(_SLEEPER)
        try:
            time.sleep(0.5)
            with tempfile.TemporaryDirectory() as t:
                got = cancel_update(
                    proc, Path(t) / "log.log",
                    provider_dir=Path(t) / "never_existed",
                    grace_seconds=3)
                self.assertEqual("cancelled", got.kind)
                self.assertFalse(got.swap_interrupted,
                                 "bootstrap 缺位被误诊成切换窗")
        finally:
            if proc.poll() is None:
                proc.kill()

    def test_an_intact_provider_is_not_a_swap_hit(self) -> None:
        proc = _spawn(_SLEEPER)
        try:
            time.sleep(0.5)
            with tempfile.TemporaryDirectory() as t:
                intact = Path(t) / "provider"
                intact.mkdir()
                got = cancel_update(
                    proc, Path(t) / "log.log",
                    provider_dir=intact, grace_seconds=3)
                self.assertEqual("cancelled", got.kind)
                self.assertFalse(got.swap_interrupted, "完好目录被误报")
        finally:
            if proc.poll() is None:
                proc.kill()

    def test_the_page_keeps_evidence_across_reruns(self) -> None:
        # 多次 rerun 的语义由纯 helper + 页面接线共同承担：证据键持久
        # （不 pop 掉除非状态被接替）、覆盖用精确相等 helper、running
        # 分支有专属「已取消」措辞、启动闸被解锁。
        page = (_ROOT / "web" / "operator_ui" / "pages" / "run_center.py"
                ).read_text(encoding="utf-8")
        for needle, why in (
            ("_CANCELLED_EVIDENCE_KEY", "持久证据键"),
            ("cancelled_run_matches(", "覆盖走精确相等 helper"),
            ("_cancel_evidence.get(\"started_at\")", "证据字段取用"),
            ("_running_fresh = False", "启动闸解锁"),
            ("已被本会话", "running 分支的已取消措辞"),
            ("st.session_state.pop(_CANCELLED_EVIDENCE_KEY, None)",
             "状态被接替时证据退役"),
        ):
            self.assertIn(needle, page, f"页面缺 {why}")

    def test_the_launch_gate_honours_the_cancelled_stamp(self) -> None:
        # 页面解锁了按钮、launch 内部的状态闸却仍按 fresh running 拒绝 =
        # 假解锁——切换窗命中后被指引的「立即重跑」会一直 already_running
        # 到六小时线（codex 第二轮 P1）。闸只放行**戳完全相等**的那一条。
        import json

        from datetime import datetime, timedelta, timezone

        from web.operator_ui.update_runner import _blocking_run_status
        from web.operator_ui.update_status import status_path_for_provider
        with tempfile.TemporaryDirectory() as t:
            provider = Path(t) / "prov"
            provider.mkdir()
            # 相对当前时刻取戳——硬编码墙钟戳会在写下六小时后越过陈旧线,
            # fresh running 变 stale 不再拦,用例静默失效（2026-08-27 实爆
            # 的定时炸弹,与被测逻辑无关）。
            _now = datetime.now(tz=timezone(timedelta(hours=8)))
            stamp = _now.isoformat()
            import os as _os
            record = {
                "schema_version": 1,
                "state": "running",
                "provider_dir": _os.path.normcase(str(provider.resolve())),
                "run_date": "2026-08-26",
                "started_at": stamp,
            }
            sp = status_path_for_provider(provider)
            sp.write_text(json.dumps(record), encoding="utf-8")
            # 无证据：fresh running 照常拦。
            self.assertIsNotNone(_blocking_run_status(provider))
            # 精确戳：放行（单飞锁仍是真仲裁）。
            self.assertIsNone(_blocking_run_status(
                provider, cancelled_started_at=stamp))
            # 错戳：不放行——绝不覆盖别的运行。
            self.assertIsNotNone(_blocking_run_status(
                provider,
                cancelled_started_at=(_now - timedelta(hours=1)).isoformat()))

    def test_the_evidence_stamp_is_reread_after_the_kill(self) -> None:
        # 子进程可能在页首读取之后才写下它的 running 记录——拿页首快照
        # 存证据会存到旧戳,下一轮精确匹配落空、证据被退役,孤儿照样锁页
        # （codex 第二轮 P1）。钉页面在终止后重读并验属主。
        page = (_ROOT / "web" / "operator_ui" / "pages" / "run_center.py"
                ).read_text(encoding="utf-8")
        self.assertIn("_fresh_status = read_update_status(_status_path)",
                      page, "证据戳没有在终止后重读")
        self.assertIn('_fresh_status.kind == "running"', page,
                      "重读后没验 running 才落证据")
        self.assertIn("_fresh_status, _provider_path", page,
                      "重读后没验属主")
        self.assertIn("cancelled_started_at=(", page,
                      "launch 没把证据递进状态闸")

    def test_a_live_session_run_blocks_a_second_launch(self) -> None:
        # 子进程写 running 记录之前的窗口里按钮仍可点——第二次启动会用新
        # 句柄顶掉第一个（第二个通常 exit 17 即退、句柄退役），原运行失去
        # 唯一取消凭据（codex 第三轮 P2）。钉两道闸：按钮 disabled 纳入
        # 会话在飞布尔 + 点击时兜底拒绝。
        page = (_ROOT / "web" / "operator_ui" / "pages" / "run_center.py"
                ).read_text(encoding="utf-8")
        self.assertIn("_session_run_alive", page)
        self.assertIn("disabled=(_running_fresh or _session_run_alive", page,
                      "按钮闸没纳入会话在飞")
        self.assertIn("if _launch_clicked and _session_run_alive:", page,
                      "缺点击时兜底")
        self.assertIn("先取消它或等它结束", page)

    def test_a_graceful_cancel_does_not_overclaim_on_a_swap_hit(self) -> None:
        # POSIX 礼貌信号同样可能落在切换窗内——graceful 成功文案不许无条件
        # 说「在线数据未受影响」，与硬杀分支同样以 swap_interrupted 为条件
        # （codex 第三轮 P2）。
        page = (_ROOT / "web" / "operator_ui" / "pages" / "run_center.py"
                ).read_text(encoding="utf-8")
        self.assertIn('if _last_cancel.get("swap_interrupted")', page)
        # 锚串随第十轮更新（graceful 前多了 _mode 字符串,裸词首现挪了
        # 位）：切在核实版成功文案上,断言意图一字未动。
        graceful_block = page.split("已取消（礼貌信号生效）")[1][:400]
        self.assertIn("swap_interrupted", graceful_block,
                      "graceful 文案没有以切换窗为条件")

    def test_marker_write_failure_is_reported_not_swallowed(self) -> None:
        # 日志在启动后变得不可写（权限/磁盘满）时，取消照常执行但审计线
        # 索缺失必须透出（codex 第四轮 P2）——静默吞掉=操作不可审计。
        proc = _spawn(_SLEEPER)
        try:
            time.sleep(0.5)
            with tempfile.TemporaryDirectory() as t:
                blocker = Path(t) / "blocker"
                blocker.write_text("file", encoding="utf-8")
                unwritable = blocker / "log.log"   # 父是文件——open 必败
                got = cancel_update(proc, unwritable, grace_seconds=3)
                self.assertEqual("cancelled", got.kind, "取消本身不该失败")
                self.assertFalse(got.markers_written,
                                 "标记写失败被静默吞掉")
        finally:
            if proc.poll() is None:
                proc.kill()
        # 可写时旗标为 True（防旗标恒 False 的假阳）。
        proc = _spawn(_SLEEPER)
        try:
            time.sleep(0.5)
            with tempfile.TemporaryDirectory() as t:
                got = cancel_update(
                    proc, Path(t) / "log.log", grace_seconds=3)
                self.assertTrue(got.markers_written)
        finally:
            if proc.poll() is None:
                proc.kill()

    def test_the_hard_cancel_message_follows_the_reread(self) -> None:
        # 硬杀消息只有在重读真的找到并存下匹配 running 记录时才许声称
        # 「将持续标注/已解锁」——launch 到状态落盘的窗口里杀掉的进程根
        # 本没写记录，无孤儿可更正（codex 第四轮 P2）。
        page = (_ROOT / "web" / "operator_ui" / "pages" / "run_center.py"
                ).read_text(encoding="utf-8")
        self.assertIn('"evidence_stored": False,', page)
        self.assertIn('_lc["evidence_stored"] = True', page)
        self.assertIn('elif _last_cancel.get("evidence_stored"):', page,
                      "持续标注的声称没有以证据落盘为条件")
        self.assertIn("更正标注", page, "无记录情形缺如实分支")
        self.assertIn("日志标记写入失败", page, "标记失败没有页面警告")

    def test_evidence_binds_only_inside_the_kill_window(self) -> None:
        # 单飞锁随进程消亡即释放——调度器可在「杀死后、重读前」起跑写下
        # 自己的 running；按 provider+kind 收养会把活运行标成已取消并解
        # 锁双闸（codex 第五轮 P1）。时间绑定：launch ≤ started ≤ killed；
        # 身份绑定另测（见下条）——这里全程用同一 pid，隔离时间维度。
        from web.operator_ui.update_runner import evidence_binds_to_killed_run
        launch = "2026-08-27T09:00:00+08:00"
        killed = "2026-08-27T09:05:00+08:00"
        self.assertTrue(evidence_binds_to_killed_run(
            "2026-08-27T09:00:30+08:00", launch, killed,
            record_pid=4242, killed_pid=4242))
        for label, record in (
            ("调度器接替（杀后起跑）", "2026-08-27T09:05:01+08:00"),
            ("launch 之前的旧记录", "2026-08-27T08:59:59+08:00"),
            ("解析不动", "not-a-stamp"),
            ("缺时区", "2026-08-27T09:00:30"),
            ("空", None),
        ):
            with self.subTest(label=label):
                self.assertFalse(evidence_binds_to_killed_run(
                    record, launch, killed,
                    record_pid=4242, killed_pid=4242))

    def test_evidence_requires_the_killed_pid_identity(self) -> None:
        # 光有时间窗不够（codex 第九轮 P2c）：调度器可在 launch 之后、UI
        # 子进程拿锁**之前**起跑并夺锁——它的 started_at 恰好落在窗内，
        # 被杀的 UI 子进程只是 exit-17 的输家；按时间收养会把**活着的**
        # 调度器运行标成已取消。身份硬条件：记录里的写者 pid == 被杀句柄
        # 的 pid（产出器落 os.getpid()，launch 直接 spawn 编排器无 shell
        # 壳，两者同一进程）。旧记录无 pid → None → fail-closed 不绑定；
        # bool 要挡（True == 1）。
        from web.operator_ui.update_runner import evidence_binds_to_killed_run
        launch = "2026-08-27T09:00:00+08:00"
        killed = "2026-08-27T09:05:00+08:00"
        in_window = "2026-08-27T09:00:30+08:00"
        for label, record_pid, killed_pid in (
            ("夺锁调度器（窗内但不同 pid）", 5000, 4242),
            ("旧产出器记录（无 pid）", None, 4242),
            ("句柄侧缺 pid", 4242, None),
            ("bool 冒充 pid（True == 1）", True, 1),
            ("双侧皆空", None, None),
        ):
            with self.subTest(label=label):
                self.assertFalse(evidence_binds_to_killed_run(
                    in_window, launch, killed,
                    record_pid=record_pid, killed_pid=killed_pid))

    def test_failed_cancels_also_report_audit_loss(self) -> None:
        # 两个 cancel_failed 返回此前落在标记聚合之外——不可写日志让失败
        # 的取消静默无审计（codex 第五轮 P2）。用 kill 即抛的存根走失败
        # 路径 + 父为文件的不可写日志。
        class _KillRaises:
            pid = 999999
            returncode = None
            def poll(self):
                return None
            def kill(self):
                raise OSError("denied")
            def wait(self, timeout=None):
                raise AssertionError("不应走到 wait")
        with tempfile.TemporaryDirectory() as t:
            blocker = Path(t) / "blocker"
            blocker.write_text("file", encoding="utf-8")
            got = cancel_update(
                _KillRaises(), blocker / "log.log",  # type: ignore[arg-type]
                grace_seconds=0.5)
        self.assertEqual("cancel_failed", got.kind)
        self.assertFalse(got.markers_written, "失败结局的审计缺失被吞")
        page = (_ROOT / "web" / "operator_ui" / "pages" / "run_center.py"
                ).read_text(encoding="utf-8")
        nl = chr(10)
        self.assertIn(
            nl + '    if (_last_cancel.get("kind") in '
            '("cancelled", "cancel_failed")', page,
            "标记警告没在共同作用域（困在 cancelled 分支里时 "
            "cancel_failed 永远渲染不到——codex 第七轮 P2）")

    def test_an_uncheckable_swap_state_is_unknown_not_healthy(self) -> None:
        # 检查自身抛 OSError（卷不可用/权限）时不许当健康——unknown 是第
        # 三态，graceful 文案不许在未核实时声称数据无恙（codex 第五轮
        # P2）。
        class _RaisingDir:
            # 严格探测走 stat（exists 会把 OSError 吞成 False——正是第七
            # 轮修掉的坑）；PermissionError 是「探测失败」不是「确证不在」。
            name = "prov"
            def stat(self):
                raise PermissionError("volume gone")
            def with_name(self, name):
                return self
        proc = _spawn(_SLEEPER)
        try:
            time.sleep(0.5)
            with tempfile.TemporaryDirectory() as t:
                got = cancel_update(
                    proc, Path(t) / "log.log",
                    provider_dir=_RaisingDir(),  # type: ignore[arg-type]
                    grace_seconds=3)
            self.assertEqual("cancelled", got.kind)
            self.assertFalse(got.swap_interrupted)
            self.assertTrue(got.swap_state_unknown,
                            "检查失败被静默当成健康")
        finally:
            if proc.poll() is None:
                proc.kill()
        page = (_ROOT / "web" / "operator_ui" / "pages" / "run_center.py"
                ).read_text(encoding="utf-8")
        self.assertIn("swap_state_unknown", page)
        self.assertIn("无法核实", page, "unknown 缺页面警告")
        # FileNotFoundError 仍是「确证不在」：bootstrap 缺位照旧不误诊
        # （由 test_bootstrap_absence... 用真目录覆盖，此处钉探测语义）。
        src = (_ROOT / "web" / "operator_ui" / "update_runner.py"
               ).read_text(encoding="utf-8")
        self.assertIn("except FileNotFoundError:", src,
                      "严格探测没有区分「不在」与「探测失败」")
        self.assertIn("path.stat()", src, "探测没走 stat")

    def test_the_exit_bound_comes_from_the_cancel_boundary(self) -> None:
        # 上界必须在**确认死亡当刻**采样并由取消边界返回——死亡确认后
        # cancel_update 还要写标记/查文件系统,调度器可在那段拿到已释放的
        # 锁写接替 running,页面晚采会把它框进窗（codex 第六轮 P1）。
        from datetime import datetime
        proc = _spawn(_SLEEPER)
        try:
            time.sleep(0.5)
            with tempfile.TemporaryDirectory() as t:
                before = datetime.now().astimezone()
                got = cancel_update(
                    proc, Path(t) / "log.log", grace_seconds=3)
                after = datetime.now().astimezone()
            self.assertEqual("cancelled", got.kind)
            self.assertIsNotNone(got.exited_at, "取消边界没返回死亡时刻")
            stamp = datetime.fromisoformat(got.exited_at or "")
            self.assertTrue(before <= stamp <= after,
                            "exited_at 不在取消执行区间内")
        finally:
            if proc.poll() is None:
                proc.kill()
        # 页面接线：上界用取消边界返回值；下界在 spawn 之前采样（源码序
        # 钉：前采行必须先于 launch 调用出现）。
        page = (_ROOT / "web" / "operator_ui" / "pages" / "run_center.py"
                ).read_text(encoding="utf-8")
        self.assertIn("_killed_at = _outcome.exited_at", page,
                      "上界没改用取消边界的死亡时刻")
        pre = page.index("_pre_launch_at = datetime.now")
        launch = page.index("_launch = launch_daily_update(")
        self.assertLess(pre, launch, "下界没有在 spawn 之前采样")
        self.assertIn('"launched_at": _pre_launch_at', page,
                      "会话下界没用前采时刻")

    def test_a_late_death_after_failed_cancel_still_settles(self) -> None:
        # cancel_failed 返回后进程才迟到死亡——退役块若当自然完成丢句柄，
        # 孤儿 running 无证据锁页六小时（codex 第八轮 P2）。钉：失败时留
        # 未决上下文（上界=请求时刻，比死亡观测更紧——被杀那次的
        # started_at 必早于请求、接替者必晚于真实死亡>请求）；退役块凭它
        # 补结算证据。
        page = (_ROOT / "web" / "operator_ui" / "pages" / "run_center.py"
                ).read_text(encoding="utf-8")
        self.assertIn('_live_run["cancel_pending_at"] = _cancel_requested_at',
                      page, "失败结局没留未决上下文")
        self.assertIn("_cancel_requested_at = datetime.now", page)
        # 锚取补结算块自己的首行注释（confirm 分支里「迟到死亡补结算的
        # 时间上界」是另一处提及，不能用裸词切）。
        settle = page.split("迟到死亡补结算（codex")[1].split("句柄退役")[0]
        # 断言意图随第十二轮**再次刻意改判**：第八轮请求时刻上界拒真孤
        # 儿（子进程可在请求后才写记录）;第十一轮死亡观测上界收回收 pid
        # 的接替者（观测可晚于真实死亡最长一个轮询周期）。终态取法=生存
        # 期内观察到的**精确戳候选**（活→读→活,pid 在两次 poll 间被子
        # 进程持续持有不可能回收）,补结算只收养精确相等那条 + 直接 pid
        # 相等;无候选 fail-closed。
        self.assertIn("cancel_pending_own_started_at", settle,
                      "补结算没用生存期内观察的身份候选")
        self.assertIn("cancelled_run_matches(_late_status.started_at, "
                      "_own_stamp)", settle,
                      "候选没按精确相等收养")
        self.assertIn("_late_status.pid == _live_proc.pid", settle,
                      "补结算收养缺直接 pid 相等")
        self.assertNotIn("_pending_at,", settle,
                         "请求时刻仍被当绑定上界（第十一轮已改判）")
        self.assertNotIn("evidence_binds_to_killed_run(", settle,
                         "补结算仍用时间窗绑定（第十二轮已改为生存期内"
                         "候选——观测上界收回收 pid 的接替者）")

    def test_a_record_written_after_the_request_still_binds(self) -> None:
        # 窗语义回归（第十一轮引入;第十二轮后该窗只服务**当场**路径,
        # 迟到路径改用生存期内候选）：请求 09:01,子进程 09:01:30 才写出
        # 记录,09:02 确认死亡——窗上界必须是死亡时刻而非请求时刻,否则
        # 这条**真孤儿**被拒之窗外（第八轮错法的实证对照保留在下）。
        from web.operator_ui.update_runner import evidence_binds_to_killed_run
        launched = "2026-08-27T09:00:00+08:00"
        request = "2026-08-27T09:01:00+08:00"
        record = "2026-08-27T09:01:30+08:00"
        observed_exit = "2026-08-27T09:02:00+08:00"
        self.assertTrue(evidence_binds_to_killed_run(
            record, launched, observed_exit,
            record_pid=4242, killed_pid=4242),
            "请求后才写出的真孤儿没被死亡观测上界收进窗")
        self.assertFalse(evidence_binds_to_killed_run(
            record, launched, request,
            record_pid=4242, killed_pid=4242),
            "（对照）请求时刻上界确实会拒掉这条真孤儿——第八轮错法的实证")

    def test_own_record_is_observed_only_within_the_lifetime(self) -> None:
        # 迟到收养的身份候选只能在进程**可证活着**时取（codex 第十二轮
        # P2）：活→读→活,pid 在两次 poll 间被子进程持续持有不可能回收;
        # 死后观测到的记录可能是回收 pid 的接替者。真值：活+记录是它的→
        # 返回戳;进程已死→None;pid 不同→None;别的 provider→None。
        import json

        from web.operator_ui.update_runner import observe_own_running_record
        from web.operator_ui.update_status import status_path_for_provider
        import os as _os
        proc = _spawn(_SLEEPER)
        try:
            time.sleep(0.5)
            with tempfile.TemporaryDirectory() as t:
                provider = Path(t) / "prov"
                provider.mkdir()
                stamp = "2026-08-27T09:00:00+08:00"
                record = {
                    "schema_version": 1, "state": "running",
                    "provider_dir": _os.path.normcase(
                        str(provider.resolve())),
                    "run_date": "2026-08-27", "started_at": stamp,
                    "pid": proc.pid,
                }
                sp = status_path_for_provider(provider)
                sp.write_text(json.dumps(record), encoding="utf-8")
                self.assertEqual(
                    stamp, observe_own_running_record(proc, provider),
                    "活着且记录是它的——候选没取到")
                # pid 不同（别的运行的记录）：不认。
                sp.write_text(json.dumps({**record, "pid": proc.pid + 1}),
                              encoding="utf-8")
                self.assertIsNone(observe_own_running_record(proc, provider))
                # 属别的 provider：不认。
                sp.write_text(json.dumps({
                    **record, "provider_dir": _os.path.normcase(
                        str((Path(t) / "other").resolve()))}),
                    encoding="utf-8")
                self.assertIsNone(observe_own_running_record(proc, provider))
                # 进程已死：即便记录严丝合缝也不认——死后无法证明 pid
                # 没被回收。
                sp.write_text(json.dumps(record), encoding="utf-8")
                proc.kill()
                proc.wait(timeout=30)
                self.assertIsNone(observe_own_running_record(proc, provider))
        finally:
            if proc.poll() is None:
                proc.kill()
        # 页面接线：confirm 当刻 + watcher 片段两处都在生存期内刷新候选。
        page = (_ROOT / "web" / "operator_ui" / "pages" / "run_center.py"
                ).read_text(encoding="utf-8")
        self.assertEqual(2, page.count("observe_own_running_record("),
                         "候选刷新不是恰好两处（confirm + watcher）")

    def test_a_late_settlement_owes_the_full_cancel_epilogue(self) -> None:
        # 迟到死亡的收尾义务与当场确认死亡**完全同款**（codex 第九轮
        # P2b）：此前补结算只重读状态、存证据——迟到死亡同样可能恰好落
        # 在两段 rename 之间,不查就把「canonical 缺位需立即修复」静默标
        # 成干净取消。钉：补结算走共享边界 settle_late_cancel、结局进
        # _LAST_CANCEL_KEY（swap/unknown/标记三警告的消费处）、当场
        # rerun 不带旧横幅渲染到底。
        page = (_ROOT / "web" / "operator_ui" / "pages" / "run_center.py"
                ).read_text(encoding="utf-8")
        settle = page.split("迟到死亡补结算（codex")[1].split("句柄退役")[0]
        self.assertIn("settle_late_cancel(", settle,
                      "补结算没走共享收尾边界")
        self.assertIn('"swap_interrupted": _late_outcome.swap_interrupted',
                      settle, "补结算结局没带切换窗判定")
        self.assertIn('"swap_state_unknown": _late_outcome.swap_state_unknown',
                      settle, "补结算结局没带 unknown 三态")
        self.assertIn('"evidence_stored": _late_evidence', settle)
        self.assertIn("st.rerun()", settle, "补结算后没有当场重绘")

    def test_settle_late_cancel_shares_the_death_epilogue(self) -> None:
        # 行为侧：已死进程的补结算 = 结局标记 + 严格 swap 检查（与
        # cancel_update 同一实现——分抄两份正是第九轮 P2b 抓到的分叉）。
        from web.operator_ui.update_runner import settle_late_cancel
        proc = _spawn("pass")
        proc.wait(timeout=30)
        with tempfile.TemporaryDirectory() as t:
            log = Path(t) / "log.log"
            missing = Path(t) / "provider_gone"
            (Path(t) / "provider_gone.bak").mkdir()
            got = settle_late_cancel(proc, log, provider_dir=missing)
            self.assertEqual("cancelled", got.kind)
            self.assertFalse(got.graceful, "迟到死亡不许谎称礼貌退出")
            self.assertTrue(got.swap_interrupted,
                            "迟到补结算漏了切换窗检查（第九轮 P2b 原样）")
            text = log.read_text(encoding="utf-8")
            self.assertIn("exited late", text, "迟到结局标记缺失")
            self.assertIn("SWAP WINDOW", text)
        # 完好目录不误报；活进程 fail-loud 拒绝（死亡是单调的,活进程到
        # 这儿=调用方编程错误,不许静默装作结算过）。
        proc2 = _spawn("pass")
        proc2.wait(timeout=30)
        with tempfile.TemporaryDirectory() as t:
            intact = Path(t) / "prov"
            intact.mkdir()
            got = settle_late_cancel(
                proc2, Path(t) / "log.log", provider_dir=intact)
            self.assertEqual("cancelled", got.kind)
            self.assertFalse(got.swap_interrupted)
        live = _spawn(_SLEEPER)
        try:
            time.sleep(0.5)
            with self.assertRaises(ValueError):
                settle_late_cancel(live, None)
        finally:
            if live.poll() is None:
                live.kill()

    def test_settlement_runs_before_the_watcher_registers(self) -> None:
        # 执行顺序（codex 第十三轮 P2）：fragment 在每次整页执行时也内联
        # 运行,死句柄支路的 st.rerun 会在走到它之后的任何代码之前中止本
        # 轮——补结算若在片段之后,下一轮又先撞片段,无限 rerun、补结算
        # 永不执行、句柄永不退役。钉源码序:退役/补结算块 < 片段注册 <
        # 取消控件区;且补结算入口全页恰好一处（旧位置不得残留第二份）。
        page = (_ROOT / "web" / "operator_ui" / "pages" / "run_center.py"
                ).read_text(encoding="utf-8")
        # 锚唯一性先钉死——若旧位置残留同名注释,index 找到的可能不是真
        # 块,顺序断言会假绿。
        self.assertEqual(1, page.count("迟到死亡补结算（codex"),
                         "补结算块锚不唯一,顺序断言不可信")
        settle_at = page.index("迟到死亡补结算（codex")
        fragment_at = page.index("def _watch_update_completion()")
        cancel_ui_at = page.index('key="run_center::cancel_request"')
        self.assertLess(settle_at, fragment_at,
                        "补结算块没在片段注册之前——死句柄 rerun 循环")
        self.assertLess(fragment_at, cancel_ui_at)
        self.assertEqual(1, page.count("settle_late_cancel("),
                         "补结算入口不是恰好一处")
        # 取消控件区对「本轮途中才死」的句柄只收控件、不做结算——结算
        # 义务全在顶部块。
        self.assertIn("死亡发生在顶部退役/补结算块之后", page)

    def test_a_retry_racing_the_death_still_settles(self) -> None:
        # 竞态（codex 第十四轮 P2）：先前 cancel_failed 已发 kill、操作人
        # 重试,进程恰在「顶部结算检查之后、cancel_update 初检之前」死掉
        # ——cancel_update 返回 already_finished,按 no-op 丢弃句柄会把
        # 迟到收尾（结局标记/swap 诊断/孤儿证据）整个跳过。钉:confirm
        # 分支拦截 already_finished × 未决上下文,改走共享补结算函数;
        # 共享函数恰好两个调用点（顶部块 + 拦截）,不许长出第三份实现。
        page = (_ROOT / "web" / "operator_ui" / "pages" / "run_center.py"
                ).read_text(encoding="utf-8")
        self.assertEqual(
            2, page.count("_settle_late_pending(_live_run, _live_proc)"),
            "共享补结算调用点不是恰好两处（顶部块 + already_finished 拦截）")
        confirm = page.split("launched_at=(_live_run or {})")[1]
        intercept = confirm.split("st.session_state[_LAST_CANCEL_KEY]")[0]
        self.assertIn('_outcome.kind == "already_finished"', intercept,
                      "拦截没在结局落盘之前")
        self.assertIn('_live_run.get("cancel_pending_at")', intercept,
                      "拦截没验未决上下文")
        self.assertIn("_settle_late_pending(_live_run, _live_proc)",
                      intercept, "拦截没走共享补结算")

    def test_the_watcher_notices_a_pending_late_death(self) -> None:
        # 硬杀后状态签名与日志进度都可能全程冻结——watcher 只比那两样,
        # 补结算块要等操作人手动交互才被重新执行,死进程的取消控件与孤儿
        # running 一直挂着（codex 第九轮 P2a）。钉：watching 纳入未决取消
        # 句柄;片段内句柄一死就整页 rerun。
        page = (_ROOT / "web" / "operator_ui" / "pages" / "run_center.py"
                ).read_text(encoding="utf-8")
        self.assertIn("_pending_cancel_proc is not None", page,
                      "watching 条件没纳入未决取消句柄")
        fragment_at = page.index("def _watch_update_completion()")
        body = page[fragment_at:fragment_at + 2400]
        self.assertIn("_pending_cancel_proc.poll() is not None", body,
                      "片段没盯未决句柄的死亡")

    def test_evidence_adoption_is_pid_bound_at_both_sites(self) -> None:
        # 两处收养都必须带 pid 身份——漏一处,该处就退回纯时间/纯戳,第
        # 九轮 P2c 的夺锁调度器场景原样复活。当场 confirm 分支走
        # evidence_binds_to_killed_run（record_pid/killed_pid）;迟到补结
        # 算第十二轮改为「生存期内候选精确戳 + 直接 pid 相等」（时间窗
        # 上界已被证伪,见 test_a_late_death_...）。
        page = (_ROOT / "web" / "operator_ui" / "pages" / "run_center.py"
                ).read_text(encoding="utf-8")
        self.assertEqual(
            1, page.count("record_pid="),
            "当场收养的 pid 身份参数不是恰好一处（confirm）")
        self.assertIn("record_pid=_fresh_status.pid", page)
        self.assertEqual(1, page.count("killed_pid=_live_proc.pid"),
                         "当场收养的句柄侧 pid 缺失")
        self.assertIn("_late_status.pid == _live_proc.pid", page,
                      "补结算收养缺 pid 身份")

    def test_late_settlement_needs_an_actually_issued_kill(self) -> None:
        # cancel_failed 的两种失败对迟到死亡语义相反（codex 第十轮 P2）：
        # kill() 抛了=进程没被碰过,之后自然跑完就是自然完成,补结算成
        # 「已强制取消」是撒谎;kill 已发只是宽限窗没等到=之后的死亡是取
        # 消导致的。真值：OSError 路径 kill_issued=False,超时路径=True。
        class _KillRaises:
            pid = 999999
            returncode = None
            def poll(self):
                return None
            def kill(self):
                raise OSError("denied")
            def wait(self, timeout=None):
                raise AssertionError("不应走到 wait")
        class _Survivor:
            pid = 999998
            returncode = None
            def poll(self):
                return None
            def kill(self):
                pass  # kill 发出成功
            def wait(self, timeout=None):
                raise subprocess.TimeoutExpired(cmd="x", timeout=timeout)
        with tempfile.TemporaryDirectory() as t:
            log = Path(t) / "log.log"
            raised = cancel_update(
                _KillRaises(), log,  # type: ignore[arg-type]
                grace_seconds=0.5)
            self.assertEqual("cancel_failed", raised.kind)
            self.assertFalse(raised.kill_issued,
                             "kill 没发出去却声称已发")
            survived = cancel_update(
                _Survivor(), log,  # type: ignore[arg-type]
                grace_seconds=0.5)
            self.assertEqual("cancel_failed", survived.kind)
            self.assertTrue(survived.kill_issued,
                            "kill 已发的超时失败没标 kill_issued")
        # 页面接线：未决上下文只在 kill_issued 时留。
        page = (_ROOT / "web" / "operator_ui" / "pages" / "run_center.py"
                ).read_text(encoding="utf-8")
        self.assertIn("and _outcome.kill_issued", page,
                      "未决上下文没有以 kill 已发为条件")

    def test_late_settlement_carries_the_audit_failure_state(self) -> None:
        # 原失败尝试的请求/失败标记没落盘,日志之后恢复可写、迟到结局标
        # 记写成了——审计链仍缺头两条,从乐观缺省重来会谎报审计完整
        # （codex 第十轮 P2）。种子聚合而非重置。
        from web.operator_ui.update_runner import settle_late_cancel
        proc = _spawn("pass")
        proc.wait(timeout=30)
        with tempfile.TemporaryDirectory() as t:
            log = Path(t) / "log.log"
            got = settle_late_cancel(proc, log, markers_written=False)
            self.assertEqual("cancelled", got.kind)
            self.assertIn("exited late", log.read_text(encoding="utf-8"),
                          "迟到结局标记应照常写")
            self.assertFalse(got.markers_written,
                             "原失败的审计缺口被乐观缺省洗掉了")
        # 页面接线：种子从未决上下文带回,缺键 fail-closed。
        page = (_ROOT / "web" / "operator_ui" / "pages" / "run_center.py"
                ).read_text(encoding="utf-8")
        self.assertIn('"cancel_pending_markers_written"] = (', page,
                      "失败时没存标记状态")
        self.assertIn('get("cancel_pending_markers_written", False)', page,
                      "补结算没带回标记种子（或缺键不是 fail-closed）")
        self.assertIn("markers_written=_late_markers", page,
                      "种子没递进补结算边界")

    def test_a_graceful_claim_needs_a_verified_terminal_record(self) -> None:
        # 「编排器自己写下了终态记录」不许从**及时退出**推断（codex 第十
        # 轮 P2）：SIGINT 可落在 import/解析配置/拿锁阶段,终录路径尚未
        # 就位。核实=finished 且写者 pid == 被杀句柄 pid。
        import json

        from web.operator_ui.update_runner import (
            terminal_record_confirms_the_run,
        )
        from web.operator_ui.update_status import status_path_for_provider
        import os as _os
        with tempfile.TemporaryDirectory() as t:
            provider = Path(t) / "prov"
            provider.mkdir()
            base = {
                "schema_version": 1, "state": "finished",
                "provider_dir": _os.path.normcase(str(provider.resolve())),
                "run_date": "2026-08-27",
                "started_at": "2026-08-27T09:00:00+08:00",
                "finished_at": "2026-08-27T09:01:00+08:00",
                "exit_code": 1, "failed_stage": "exception",
                "detail": "KeyboardInterrupt",
            }
            sp = status_path_for_provider(provider)
            # 本次运行的时间窗（身份+时间合取——codex 第十一轮 P2：纯
            # pid 会把复用同 pid 的陈年 finished 工件核实成本次终态）。
            win = {"launched_at": "2026-08-27T08:59:00+08:00",
                   "exited_at": "2026-08-27T09:02:00+08:00"}
            # 工件缺失：证不出来。
            self.assertFalse(
                terminal_record_confirms_the_run(provider, 77, **win))
            # finished、pid 属本次、戳落窗内：核实成立。
            sp.write_text(json.dumps({**base, "pid": 77}), encoding="utf-8")
            self.assertTrue(
                terminal_record_confirms_the_run(provider, 77, **win))
            # 同一工件,本次 launch 晚于它的 started_at（= 陈年工件恰好
            # 复用同 pid）：时间窗拒。
            self.assertFalse(terminal_record_confirms_the_run(
                provider, 77,
                launched_at="2026-08-27T09:30:00+08:00",
                exited_at="2026-08-27T09:31:00+08:00"))
            # 窗界缺失/finished 晚于观测退出：fail-closed。
            self.assertFalse(terminal_record_confirms_the_run(
                provider, 77, launched_at=None,
                exited_at=win["exited_at"]))
            self.assertFalse(terminal_record_confirms_the_run(
                provider, 77, launched_at=win["launched_at"],
                exited_at="2026-08-27T09:00:30+08:00"))
            # pid 不同（上一次运行的终态）/旧记录无 pid/还是 running：全拒。
            sp.write_text(json.dumps({**base, "pid": 78}), encoding="utf-8")
            self.assertFalse(
                terminal_record_confirms_the_run(provider, 77, **win))
            sp.write_text(json.dumps(base), encoding="utf-8")
            self.assertFalse(
                terminal_record_confirms_the_run(provider, 77, **win))
            # 记录戳无时区：解析不进比较,拒。
            sp.write_text(json.dumps({
                **base, "pid": 77,
                "started_at": "2026-08-27T09:00:00",
            }), encoding="utf-8")
            self.assertFalse(
                terminal_record_confirms_the_run(provider, 77, **win))
            running = {k: v for k, v in base.items()
                       if k not in ("finished_at", "exit_code",
                                    "failed_stage", "detail")}
            sp.write_text(json.dumps({**running, "state": "running",
                                      "pid": 77}), encoding="utf-8")
            self.assertFalse(
                terminal_record_confirms_the_run(provider, 77, **win))
        self.assertFalse(terminal_record_confirms_the_run(
            None, 77, **win))
        # 页面接线：核实版声称以 terminal_recorded 为条件;未核实的
        # graceful 与硬杀同走孤儿收养（收养条件不再看 graceful）。
        page = (_ROOT / "web" / "operator_ui" / "pages" / "run_center.py"
                ).read_text(encoding="utf-8")
        self.assertIn('and _last_cancel.get("terminal_recorded")', page,
                      "graceful 声称没有以核实为条件")
        self.assertIn("not _outcome.terminal_recorded", page,
                      "孤儿收养条件没改为「未核实终态」")
        self.assertNotIn("and not _outcome.graceful:", page,
                         "收养仍按 graceful 分流——graceful 无终态的孤儿"
                         "会漏收养")
        # 时间窗下界要从会话透传进两条取消边界（codex 第十一轮 P2）。
        self.assertIn('launched_at=(_live_run or {}).get("launched_at")',
                      page, "cancel_update 没拿到时间窗下界")
        self.assertIn('launched_at=_live_run.get("launched_at")', page,
                      "补结算没拿到时间窗下界")

    def test_a_failed_cancel_keeps_the_handle(self) -> None:
        # cancel_failed 时进程可能还活着——句柄是唯一合法取消凭据，
        # 丢了就只剩任务管理器（codex #470 P2）。
        page = (_ROOT / "web" / "operator_ui" / "pages" / "run_center.py"
                ).read_text(encoding="utf-8")
        self.assertIn(
            'if _outcome.kind in ("cancelled", "already_finished"):', page,
            "句柄的交出没有以确认终局为条件")


class TheCancelChannelIsTheHandleNotThePid(unittest.TestCase):
    def test_cancel_takes_a_popen_handle_only(self) -> None:
        # 绝不按 pid 杀：pid 会被系统回收复用，按数字杀可能命中无关进程。
        # 签名钉住第一参数是 Popen；模块内不许长出按 pid 的取消入口。
        params = list(inspect.signature(cancel_update).parameters)
        self.assertEqual("process", params[0])
        src = (_ROOT / "web" / "operator_ui" / "update_runner.py"
               ).read_text(encoding="utf-8")
        self.assertNotIn("def cancel_update_by_pid", src)
        # Windows 分支只允许 process.kill()（句柄级）；os.kill(pid 形态的
        # 取消入口是被禁的（POSIX killpg 是组长信号，另当别论且平台门内）。
        self.assertNotIn("os.kill(", src.replace("os.killpg(", ""))

    def test_launch_carries_the_live_handle(self) -> None:
        # 取消凭据在 UpdateLaunch.process 里随 launch 返回——页面只从
        # 会话里取它，不从 pid 重建。
        self.assertIn("process", UpdateLaunch.__dataclass_fields__)
        src = (_ROOT / "web" / "operator_ui" / "update_runner.py"
               ).read_text(encoding="utf-8")
        self.assertIn("process=proc)", src, "launched 分支没带活句柄")

    def test_the_page_flow_is_two_step_and_session_scoped(self) -> None:
        page = (_ROOT / "web" / "operator_ui" / "pages" / "run_center.py"
                ).read_text(encoding="utf-8")
        # 两步确认：请求键武装 → 确认/保留分离；取消只吃会话句柄。
        for needle, why in (
            ("_CANCEL_ARM_KEY", "两步确认的武装键"),
            ('key="run_center::cancel_confirm"', "确认按钮"),
            ('key="run_center::cancel_abort"', "保留按钮"),
            ("cancel_update(\n                    _live_proc",
             "取消只吃会话里的活句柄"),
            ("_live_proc.poll() is not None", "已结束句柄退役"),
            ("状态工件仍标 running", "硬杀后的如实标注"),
        ):
            self.assertIn(needle, page, f"页面缺 {why}")

    def test_the_outcome_type_speaks_all_kinds(self) -> None:
        # 页面按 kind 措辞——三种结局都得有对应分支，缺一种就退回笼统话。
        page = (_ROOT / "web" / "operator_ui" / "pages" / "run_center.py"
                ).read_text(encoding="utf-8")
        for kind in ("already_finished", "cancelled", "graceful"):
            self.assertIn(kind, page, f"页面没消费 {kind}")
        self.assertIn("kind", UpdateCancel.__dataclass_fields__)


if __name__ == "__main__":
    unittest.main()
