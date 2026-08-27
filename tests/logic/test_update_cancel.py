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
        # 锚串随第十八轮扩展（自然竞态收场的 already_finished 也写标记,
        # 其缺失同样要警）,断言意图不变：警告在共同作用域。
        self.assertIn(
            nl + '    if (_last_cancel.get("kind") in '
            '("cancelled", "cancel_failed",', page,
            "标记警告没在共同作用域（困在 cancelled 分支里时 "
            "cancel_failed 永远渲染不到——codex 第七轮 P2）")
        self.assertIn('"already_finished")', page,
                      "标记警告没覆盖自然竞态收场（第十八轮）")

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
        # 锚串随第二十四轮更新（收养主判据升级为 launch nonce,精确候选
        # 降为 legacy 无 nonce 记录的回退——断言意图:候选链仍在且仍按
        # 精确相等）。
        self.assertIn("cancelled_run_matches(", settle,
                      "候选没按精确相等收养")
        self.assertIn("_own_stamp)", settle, "候选回退链缺失")
        self.assertIn("record_bears_launch_nonce(", settle,
                      "补结算收养缺 nonce 主判据")
        self.assertIn("_late_status.launch_nonce is None", settle,
                      "候选回退没限定在无 nonce 的 legacy 记录")
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
        import os as _os

        from web.operator_ui.update_runner import observe_own_running_record
        from web.operator_ui.update_status import status_path_for_provider
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
        # 页面接线：取消前 + confirm 失败后 + watcher 片段,三处都在生存
        # 期内取候选（第十七轮 P2:取消调用可耗满宽限窗,kill 恰在返回后
        # 生效——只有确认后那一次会撞死进程,须有取消前观察兜底）。
        page = (_ROOT / "web" / "operator_ui" / "pages" / "run_center.py"
                ).read_text(encoding="utf-8")
        self.assertEqual(3, page.count("observe_own_running_record("),
                         "候选观察不是恰好三处（取消前/confirm 失败后/"
                         "watcher）")
        _before_at = page.index("_own_before = observe_own_running_record(")
        _cancel_at = page.index("_outcome = cancel_update(")
        self.assertLess(_before_at, _cancel_at,
                        "取消前观察没在取消调用之前")
        self.assertIn("or _own_before", page,
                      "失败分支没用取消前观察兜底")

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

    def test_audit_failures_survive_cancellation_retries(self) -> None:
        # 跨重试聚合（codex 第十五轮 P2）：首次 kill 超时且标记写失败,
        # 日志恢复可写、重试成功终止——只报本次 True 会把先前那次活取消
        # 的审计缺口静默洗掉;再次超时也不得用本次 True 覆盖存量 False。
        # 聚合在取消边界单点做,警告消费的是**完整审计链**的状态。
        proc = _spawn(_SLEEPER)
        try:
            time.sleep(0.5)
            with tempfile.TemporaryDirectory() as t:
                log = Path(t) / "log.log"
                got = cancel_update(proc, log, grace_seconds=3,
                                    prior_markers_written=False)
                self.assertEqual("cancelled", got.kind)
                self.assertIn("cancel outcome",
                              log.read_text(encoding="utf-8"),
                              "本次标记确实写成了——聚合前提")
                self.assertFalse(got.markers_written,
                                 "先前的审计缺口被本次重试洗掉了")
        finally:
            if proc.poll() is None:
                proc.kill()
        # 无先前缺口（None/True）不误伤本次结果。
        for prior in (None, True):
            proc2 = _spawn(_SLEEPER)
            try:
                time.sleep(0.5)
                with tempfile.TemporaryDirectory() as t:
                    got = cancel_update(
                        proc2, Path(t) / "log.log", grace_seconds=3,
                        prior_markers_written=prior)
                    self.assertTrue(got.markers_written,
                                    f"prior={prior} 误伤了本次聚合")
            finally:
                if proc2.poll() is None:
                    proc2.kill()
        # 再次超时同样不得洗白存量 False。
        class _Survivor:
            pid = 999998
            returncode = None
            def poll(self):
                return None
            def kill(self):
                pass
            def wait(self, timeout=None):
                raise subprocess.TimeoutExpired(cmd="x", timeout=timeout)
        with tempfile.TemporaryDirectory() as t:
            got = cancel_update(
                _Survivor(), Path(t) / "log.log",  # type: ignore[arg-type]
                grace_seconds=0.5, prior_markers_written=False)
            self.assertEqual("cancel_failed", got.kind)
            self.assertFalse(got.markers_written,
                             "再超时把存量 False 覆盖成 True 了")
        # 页面接线：confirm 分支把未决上下文里的标记状态递进取消边界。
        page = (_ROOT / "web" / "operator_ui" / "pages" / "run_center.py"
                ).read_text(encoding="utf-8")
        self.assertIn('prior_markers_written=(_live_run or {}).get(', page,
                      "重试没带上一次的标记状态")

    @unittest.skipIf(sys.platform == "win32", "POSIX killpg 路径")
    def test_a_successful_group_kill_counts_as_issued(self) -> None:
        # POSIX 竞态（codex 第十七轮 P2）：killpg(SIGKILL) 成功返回、子进
        # 程尚未收割,后备 process.kill() 恰与退出竞态抛 OSError——组信号
        # 的成功返回是「已发出」的证明（内核已受理投递）,硬编码 False 会
        # 让先前 SIGKILL 导致的迟到死亡被当自然完成,整套迟到收尾跳过。
        from unittest import mock

        import web.operator_ui.update_runner as _runner
        class _KillRacesExit:
            pid = 999996
            returncode = None
            def poll(self):
                return None
            def kill(self):
                raise OSError("No such process")
            def wait(self, timeout=None):
                raise AssertionError("不应走到 wait")
        with tempfile.TemporaryDirectory() as t:
            log = Path(t) / "log.log"
            with mock.patch.object(_runner.os, "killpg",
                                   lambda pid, sig: None):
                got = cancel_update(
                    _KillRacesExit(), log,  # type: ignore[arg-type]
                    grace_seconds=0.3)
            self.assertEqual("cancel_failed", got.kind)
            self.assertTrue(got.kill_issued,
                            "组信号成功发出却被记成没发——迟到死亡将被"
                            "当自然完成")
            # 对照：所有信号调用都抛 → 才算没发出去。
            def _raise(pid: int, sig: int) -> None:
                raise OSError("EPERM")
            with mock.patch.object(_runner.os, "killpg", _raise):
                got2 = cancel_update(
                    _KillRacesExit(), log,  # type: ignore[arg-type]
                    grace_seconds=0.3)
            self.assertEqual("cancel_failed", got2.kind)
            self.assertFalse(got2.kill_issued,
                             "全部信号调用都抛了还声称已发出")

    def test_an_unsignalled_death_is_not_reported_as_cancelled(self) -> None:
        # 分类闸（codex 第十八轮 P2）：初检时还活着,但没有任何信号成功
        # 送达它就死了——POSIX 是「初检后自然完成 + SIGINT 抛错」竞态
        # （宽限窗里的死亡会被误判 graceful）,Windows 是「初检到 kill()
        # 之间死亡」的毫秒窗。报成 cancelled 会套上取消专属的 swap/审计
        # 收尾与 graceful 文案——它是自然完成,按 already_finished 收场,
        # 留结局标记收口这次尝试。
        from unittest import mock

        import web.operator_ui.update_runner as _runner
        class _DiesUnderfoot:
            pid = 999995
            returncode = 0
            def __init__(self) -> None:
                self._polls = 0
            def poll(self):
                self._polls += 1
                return None if self._polls == 1 else 0
            def kill(self):
                raise AssertionError("不该对已死进程调用 kill")
            def wait(self, timeout=None):
                raise AssertionError("不应走到 wait")
        with tempfile.TemporaryDirectory() as t:
            log = Path(t) / "log.log"
            proc = _DiesUnderfoot()
            if sys.platform != "win32":
                def _gone(pid: int, sig: int) -> None:
                    raise ProcessLookupError("group gone")
                with mock.patch.object(_runner.os, "killpg", _gone):
                    got = cancel_update(
                        proc, log,  # type: ignore[arg-type]
                        grace_seconds=0.3)
            else:
                got = cancel_update(
                    proc, log,  # type: ignore[arg-type]
                    grace_seconds=0.3)
            self.assertEqual("already_finished", got.kind,
                             "无信号送达的死亡被报成了取消")
            self.assertFalse(got.kill_issued)
            self.assertFalse(got.graceful, "自然完成被判 graceful")
            text = log.read_text(encoding="utf-8")
            self.assertIn("cancel requested", text,
                          "初检活着,请求标记应已落")
            self.assertIn("before any signal was issued", text,
                          "自然竞态缺结局标记收口")

    def test_a_kill_raising_on_a_corpse_is_reclassified(self) -> None:
        # poll 到 kill 之间的窄竞态（codex 第十九轮 P2）：进程恰在预检后
        # 退出,kill() 对已终结句柄抛 OSError——立即返回 cancel_failed 会
        # 留死句柄、把自然完成报成取消失败,还绕过分类闸。复检已死后:
        # 无信号=自然完成(already_finished);有信号=取消导致的确认死亡
        # (cancelled)。
        from unittest import mock

        import web.operator_ui.update_runner as _runner
        class _DiesUnderKill:
            pid = 999994
            returncode = 1
            def __init__(self) -> None:
                self._killed = False
            def poll(self):
                return 1 if self._killed else None
            def kill(self):
                self._killed = True
                raise OSError("process terminated during kill")
            def wait(self, timeout=None):
                raise AssertionError("kill 抛错+已死不应再等")
        # 无信号送达（Windows 原生;POSIX mock killpg 全抛）→ 自然完成。
        with tempfile.TemporaryDirectory() as t:
            log = Path(t) / "log.log"
            if sys.platform != "win32":
                def _gone(pid: int, sig: int) -> None:
                    raise ProcessLookupError("group gone")
                with mock.patch.object(_runner.os, "killpg", _gone):
                    got = cancel_update(
                        _DiesUnderKill(), log,  # type: ignore[arg-type]
                        grace_seconds=0.3)
            else:
                got = cancel_update(
                    _DiesUnderKill(), log,  # type: ignore[arg-type]
                    grace_seconds=0.3)
            self.assertEqual("already_finished", got.kind,
                             "kill 抛错+已死+无信号被报成了取消失败")
            self.assertFalse(got.kill_issued)
            text = log.read_text(encoding="utf-8")
            self.assertIn("before any signal was issued", text)
            self.assertNotIn("cancel FAILED: kill raised", text,
                             "对尸体抛错不是失败,不该落 FAILED 标记")
        # POSIX：组信号已成功发出 → 死亡是取消导致的,确认死亡收尾。
        if sys.platform != "win32":
            with tempfile.TemporaryDirectory() as t:
                log = Path(t) / "log.log"
                with mock.patch.object(_runner.os, "killpg",
                                       lambda pid, sig: None):
                    got = cancel_update(
                        _DiesUnderKill(), log,  # type: ignore[arg-type]
                        grace_seconds=0.3)
                self.assertEqual("cancelled", got.kind,
                                 "组信号已发+kill 撞尸体没按确认死亡收尾")
                self.assertIn("cancel outcome: process exited",
                              log.read_text(encoding="utf-8"))

    def test_a_quiet_kill_return_is_not_delivery_evidence(self) -> None:
        # CPython Popen.kill()→send_signal() 内部先 poll,进程恰在预检与
        # 它之间自然终结时**什么都不发、静默正常返回**（codex 第二十轮
        # P2——第十九轮的存根假设抛错,与真实 Popen 不符）。复检裁决:
        # 返回后已死+无信号在先 → 终态 oracle:本次运行自己的终态记录在
        # =自然完成(already_finished);不在=按已发信号的确认死亡走。
        import json
        import os as _os
        from datetime import datetime, timedelta, timezone
        from unittest import mock

        import web.operator_ui.update_runner as _runner
        from web.operator_ui.update_status import status_path_for_provider

        class _DiesQuietly:
            pid = 999993
            returncode = 0
            def __init__(self) -> None:
                self._killed = False
            def poll(self):
                return 0 if self._killed else None
            def kill(self):
                # 真实 send_signal 行为:内部 poll 见已终结→不发不抛。
                self._killed = True
            def wait(self, timeout=None):
                raise AssertionError("已死不应再等")

        def _run(provider, launched):
            proc = _DiesQuietly()
            with tempfile.TemporaryDirectory() as t2:
                log = Path(t2) / "log.log"
                if sys.platform != "win32":
                    def _gone(pid: int, sig: int) -> None:
                        raise ProcessLookupError("group gone")
                    with mock.patch.object(_runner.os, "killpg", _gone):
                        got = cancel_update(
                            proc, log,  # type: ignore[arg-type]
                            provider_dir=provider, grace_seconds=0.3,
                            launched_at=launched)
                else:
                    got = cancel_update(
                        proc, log,  # type: ignore[arg-type]
                        provider_dir=provider, grace_seconds=0.3,
                        launched_at=launched)
                return got, (log.read_text(encoding="utf-8")
                             if log.exists() else "")

        # 戳一律相对当前时刻构造（硬编码墙钟戳=定时炸弹,launch 闸用例
        # 已爆过一次）:launch ≤ started ≤ finished ≤ exit(=cancel 内采样
        # 的 now) 恒成立。
        _now = datetime.now(tz=timezone(timedelta(hours=8)))
        launched = (_now - timedelta(hours=2)).isoformat()
        with tempfile.TemporaryDirectory() as t:
            provider = Path(t) / "prov"
            provider.mkdir()
            # 变体A:本次运行自己的终态记录在(pid+窗内)→ 自然完成。
            status_path_for_provider(provider).write_text(json.dumps({
                "schema_version": 1, "state": "finished",
                "provider_dir": _os.path.normcase(str(provider.resolve())),
                "run_date": _now.date().isoformat(),
                "started_at": (_now - timedelta(hours=1)).isoformat(),
                "finished_at": (_now - timedelta(minutes=1)).isoformat(),
                "exit_code": 0, "failed_stage": None, "detail": "complete",
                "pid": _DiesQuietly.pid,
            }), encoding="utf-8")
            got, text = _run(provider, launched)
            self.assertEqual("already_finished", got.kind,
                             "静默返回被当成送达证据,自然完成被报成取消")
            self.assertFalse(got.kill_issued)
            self.assertIn("own terminal record", text)
        with tempfile.TemporaryDirectory() as t:
            # 变体B:无终态记录 → 按已发信号的确认死亡走(取消收尾)。
            provider = Path(t) / "prov"
            provider.mkdir()
            got, text = _run(provider, launched)
            self.assertEqual("cancelled", got.kind,
                             "无终录的歧义死亡没走确认死亡收尾")
            self.assertIn("cancel outcome: process exited", text)

    def test_a_killless_retry_still_persists_its_audit_loss(self) -> None:
        # 未决在场 + 重试的 kill() 自身抛且标记写失败（codex 第十六轮
        # P2）：kill_issued=False 的守卫会拦住未决上下文更新——存量
        # True 不动,进程随后死于**先前**的 kill,迟到结算从陈旧 True 起
        # 步、报完整审计链,本次缺失的请求/失败标记被抹掉。真值：prior
        # True + 本次标记失败 → 聚合 False;页面在非 kill_issued 的
        # cancel_failed 分支也要把聚合值写回未决上下文。
        class _KillRaises:
            pid = 999997
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
                grace_seconds=0.5, prior_markers_written=True)
            self.assertEqual("cancel_failed", got.kind)
            self.assertFalse(got.kill_issued)
            self.assertFalse(got.markers_written,
                             "prior=True 也挡不住本次标记失败的聚合")
        # 页面接线：kill 未发的 cancel_failed 分支,未决在场时写回聚合值
        # （且不动 cancel_pending_at——未决身份仍属先前那次）。
        page = (_ROOT / "web" / "operator_ui" / "pages" / "run_center.py"
                ).read_text(encoding="utf-8")
        _anchor = page.index("kill 调用没发出去的重试")
        # 前探 400 字符盖住 elif 条件行,后探 900 盖住分支体。
        killless = page[max(0, _anchor - 400):_anchor + 900]
        self.assertIn('_live_run.get("cancel_pending_at")', killless,
                      "非 kill_issued 分支没验未决在场")
        self.assertIn('_live_run["cancel_pending_markers_written"] = (',
                      killless, "聚合值没写回未决上下文")
        self.assertNotIn('_live_run["cancel_pending_at"] =', killless,
                         "kill 未发的重试不该新立未决身份")

    def test_a_graceful_claim_needs_a_verified_terminal_record(self) -> None:
        # 「编排器自己写下了终态记录」不许从**及时退出**推断（codex 第十
        # 轮 P2）：SIGINT 可落在 import/解析配置/拿锁阶段,终录路径尚未
        # 就位。核实=finished 且写者 pid == 被杀句柄 pid。
        import json
        import os as _os

        from web.operator_ui.update_runner import (
            terminal_record_confirms_the_run,
        )
        from web.operator_ui.update_status import status_path_for_provider
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
            # 身份一票裁决（第二十七轮 P2）:会话有 nonce 时,pid 复用+冻
            # 结时钟可让陈年工件过 pid+窗——记录必须带同一 nonce;窗不
            # 再是判据（nonce 相等即本次运行写的,冻结钟同戳也认）。
            nn = "ab" * 16
            sp.write_text(json.dumps({**base, "pid": 77, "launch_nonce": nn}),
                          encoding="utf-8")
            self.assertTrue(terminal_record_confirms_the_run(
                provider, 77, **win, launch_nonce=nn))
            self.assertTrue(
                terminal_record_confirms_the_run(
                    provider, 77,
                    launched_at="2026-08-27T09:30:00+08:00",
                    exited_at="2026-08-27T09:31:00+08:00",
                    launch_nonce=nn),
                "nonce 相等却因窗外被拒——窗对 nonce 身份不再是判据")
            # 陈年/接替:同 pid 同窗但异 nonce 或无 nonce → 拒。
            sp.write_text(json.dumps({**base, "pid": 77,
                                      "launch_nonce": "cd" * 16}),
                          encoding="utf-8")
            self.assertFalse(terminal_record_confirms_the_run(
                provider, 77, **win, launch_nonce=nn),
                "异 nonce 的陈年工件被核实成本次终录")
            sp.write_text(json.dumps({**base, "pid": 77}), encoding="utf-8")
            self.assertFalse(terminal_record_confirms_the_run(
                provider, 77, **win, launch_nonce=nn),
                "无 nonce 的陈年工件被核实成本次终录")
            # 反向:记录带 nonce、会话没有（legacy 会话）→ 拒。
            sp.write_text(json.dumps({**base, "pid": 77, "launch_nonce": nn}),
                          encoding="utf-8")
            self.assertFalse(terminal_record_confirms_the_run(
                provider, 77, **win))
        self.assertFalse(terminal_record_confirms_the_run(
            None, 77, **win))
        # 接线:页面 cancel_update 调用带 launch_nonce。
        page = (_ROOT / "web" / "operator_ui" / "pages" / "run_center.py"
                ).read_text(encoding="utf-8")
        self.assertIn('launch_nonce=(_live_run or {}).get("launch_nonce")',
                      page, "graceful 终态核实没拿到会话 nonce")
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

    def test_a_record_written_after_all_observations_is_still_claimed(
            self) -> None:
        # 合成回归（codex 第二十四轮 P2 的场景:观察→写记录→死亡→返回）
        # ——记录写在边界观察**之后**、死亡在返回**之前**:取消前/边界内/
        # 事后三级观察全部落空,候选=None,观察式路径必拒;launch nonce
        # 随记录本体落盘,无观察窗,仍能认领。
        from web.operator_ui.update_runner import (
            record_bears_launch_nonce,
        )
        nonce = "ab" * 16
        # 观察式候选路径:候选 None → 拒（这正是尾窗的数学极限）。
        self.assertFalse(cancelled_run_matches(
            "2026-08-27T09:01:30+08:00", None))
        # nonce 路径:同一 launch 的记录 → 认领。
        self.assertTrue(record_bears_launch_nonce(nonce, nonce))
        # 别人的 launch / 调度器（无 nonce）/ 会话缺 nonce:全拒。
        self.assertFalse(record_bears_launch_nonce("cd" * 16, nonce))
        self.assertFalse(record_bears_launch_nonce(None, nonce))
        self.assertFalse(record_bears_launch_nonce(nonce, None))
        self.assertFalse(record_bears_launch_nonce("", ""))
        # 接线:launcher 在 spawn 之前注入环境;页面把 nonce 存进会话;
        # 环境变量名与产出器镜像（两模块互不 import）。
        src = (_ROOT / "web" / "operator_ui" / "update_runner.py"
               ).read_text(encoding="utf-8")
        inject_at = src.index("child_env[LAUNCH_NONCE_ENV] = launch_nonce")
        spawn_at = src.index("proc = subprocess.Popen(")
        self.assertLess(inject_at, spawn_at, "nonce 没在 spawn 之前注入")
        from web.operator_ui.update_runner import LAUNCH_NONCE_ENV
        producer = (_ROOT / "src" / "data_pipeline" / "daily_update.py"
                    ).read_text(encoding="utf-8")
        self.assertIn(f'"{LAUNCH_NONCE_ENV}"', producer,
                      "产出器没镜像同名环境变量")
        page = (_ROOT / "web" / "operator_ui" / "pages" / "run_center.py"
                ).read_text(encoding="utf-8")
        self.assertIn('"launch_nonce": _launch.launch_nonce', page,
                      "会话没存 launch nonce")
        # 两处收养仍用 record_bears_launch_nonce;页首覆盖/退役/启动闸
        # 统一走共享谓词 evidence_covers_record（第二十六轮:身份在场
        # 一票裁决,不许各抄一份会分叉的）。
        self.assertEqual(2, page.count("record_bears_launch_nonce("),
                         "收养的 nonce 主判据不是恰好两处")
        self.assertIn("evidence_covers_record(", page,
                      "页首覆盖判定没走共享谓词")

    def test_a_nonce_mismatch_overrides_a_matching_stamp(self) -> None:
        # 身份一票裁决（codex 第二十六轮 P2）：粗粒度/冻结的系统时钟可以
        # 让接替记录与被取消记录**同戳**——证据带 nonce 时,记录必须带同
        # 一个 nonce 才算覆盖;or 语义会把活着的同戳接替（异 nonce 或无
        # nonce 调度器运行）标成已取消并解锁双闸。戳只留给双方都无
        # nonce 的 legacy 对。
        import json
        import os as _os

        from web.operator_ui.update_runner import (
            _blocking_run_status,
            evidence_covers_record,
        )
        from web.operator_ui.update_status import status_path_for_provider
        stamp = "2026-08-27T09:00:00+08:00"
        nonce = "ab" * 16
        # 真值:证据带 nonce → 同戳异/无 nonce 全不覆盖;同 nonce 覆盖。
        self.assertTrue(evidence_covers_record(stamp, nonce, stamp, nonce))
        self.assertTrue(evidence_covers_record("x", nonce, "", nonce),
                        "nonce 同则戳不同也覆盖（nonce-only 证据）")
        self.assertFalse(evidence_covers_record(stamp, "cd" * 16,
                                                stamp, nonce),
                         "同戳异 nonce 的接替被判覆盖")
        self.assertFalse(evidence_covers_record(stamp, None, stamp, nonce),
                         "同戳无 nonce 的接替被判覆盖")
        # 反向:记录带 nonce 而证据无（legacy 证据认不了新产出器记录）。
        self.assertFalse(evidence_covers_record(stamp, nonce, stamp, None))
        # 双方都无 nonce 的 legacy 对:戳精确相等仍是判据。
        self.assertTrue(evidence_covers_record(stamp, None, stamp, None))
        self.assertFalse(evidence_covers_record(stamp, None, "y", None))
        # 启动闸同语义:同戳异 nonce 的接替记录**不放行**。
        with tempfile.TemporaryDirectory() as t:
            provider = Path(t) / "prov"
            provider.mkdir()
            from datetime import datetime, timedelta, timezone
            _now = datetime.now(tz=timezone(timedelta(hours=8)))
            live_stamp = _now.isoformat()
            status_path_for_provider(provider).write_text(json.dumps({
                "schema_version": 1, "state": "running",
                "provider_dir": _os.path.normcase(str(provider.resolve())),
                "run_date": _now.date().isoformat(),
                "started_at": live_stamp,
                "launch_nonce": "cd" * 16,
            }), encoding="utf-8")
            self.assertIsNotNone(
                _blocking_run_status(
                    provider, cancelled_started_at=live_stamp,
                    cancelled_launch_nonce=nonce),
                "同戳异 nonce 的接替被启动闸放行")
        # 收养处 legacy 回退收紧到「双方都无 nonce」:本会话有 kill
        # nonce 时,子进程必然写 nonce——无 nonce 记录不可能是它。
        page = (_ROOT / "web" / "operator_ui" / "pages" / "run_center.py"
                ).read_text(encoding="utf-8")
        self.assertIn("not _kill_nonce", page,
                      "补结算 legacy 回退没被 kill nonce 关掉")
        self.assertIn("not _kn", page,
                      "confirm legacy 回退没被 kill nonce 关掉")

    def test_nonce_evidence_survives_an_inconclusive_settlement_read(
            self) -> None:
        # 恢复路径回归（codex 第二十五轮 P2）：补结算/confirm 的死后读取
        # 撞上 corrupt/missing 时,未决上下文（含 nonce）被退役而证据还没
        # 落——孤儿恢复可读后被当活运行锁页六小时。被杀运行的 nonce 先验
        # 已知,证据落盘不依赖那次读取:两处都在不确凿读取时落 nonce-only
        # 证据;覆盖判定/启动闸凭 nonce 放行。
        import json
        import os as _os

        from web.operator_ui.update_runner import _blocking_run_status
        from web.operator_ui.update_status import status_path_for_provider
        page = (_ROOT / "web" / "operator_ui" / "pages" / "run_center.py"
                ).read_text(encoding="utf-8")
        self.assertIn(
            'elif _kill_nonce and _late_status.kind in '
            '("missing", "corrupt")',
            page, "补结算不确凿读取没落 nonce 证据")
        self.assertIn('elif _kn and _fresh_status.kind in (', page,
                      "confirm 不确凿重读没落 nonce 证据")
        # 两处确凿收养的证据字典也带 nonce（覆盖判定凭它认领）。
        self.assertIn('"launch_nonce": _kill_nonce or ""', page,
                      "补结算确凿证据没带 nonce")
        self.assertIn('"launch_nonce": _kn or ""', page,
                      "confirm 确凿证据没带 nonce")
        # 启动闸 nonce 放行:带同 nonce 的 running 记录=被杀孤儿,放行;
        # 别人的 nonce 不放行。
        nonce = "ab" * 16
        with tempfile.TemporaryDirectory() as t:
            provider = Path(t) / "prov"
            provider.mkdir()
            from datetime import datetime, timedelta, timezone
            _now = datetime.now(tz=timezone(timedelta(hours=8)))
            record = {
                "schema_version": 1, "state": "running",
                "provider_dir": _os.path.normcase(str(provider.resolve())),
                "run_date": _now.date().isoformat(),
                "started_at": _now.isoformat(),
                "launch_nonce": nonce,
            }
            status_path_for_provider(provider).write_text(
                json.dumps(record), encoding="utf-8")
            self.assertIsNotNone(_blocking_run_status(provider),
                                 "无证据 fresh running 应照常拦")
            self.assertIsNone(
                _blocking_run_status(
                    provider, cancelled_launch_nonce=nonce),
                "nonce 证据没放行被杀孤儿")
            self.assertIsNotNone(
                _blocking_run_status(
                    provider, cancelled_launch_nonce="cd" * 16),
                "别人的 nonce 被放行")

    def test_evidence_survives_inconclusive_status_reads(self) -> None:
        # corrupt/missing 是**读取失败**不是接替证明（codex 第二十三轮
        # P2）：瞬时卷/权限失效借它把证据永久清掉,访问恢复后同一条孤儿
        # running 复现,被当活运行锁页六小时。退役只认确凿接替:戳不同的
        # running（新运行顶替）或 finished 终态（孤儿被改写）。
        from web.operator_ui.update_runner import evidence_retires
        ev = "2026-08-27T09:00:00+08:00"
        nonce = "ab" * 16
        self.assertTrue(evidence_retires("finished", None, None, ev, nonce),
                        "终态接替没退役")
        self.assertTrue(evidence_retires(
            "running", "2026-08-27T10:00:00+08:00", "cd" * 16, ev, nonce),
            "两种身份都对不上的 running 接替没退役")
        # 双方都无 nonce 的 legacy 对:戳仍是覆盖判据。
        self.assertFalse(evidence_retires("running", ev, None, ev, None),
                         "legacy 对戳仍覆盖当前记录却被退役")
        # 身份一票裁决（第二十六轮）:证据带 nonce 而记录无 nonce（或不
        # 同 nonce）——即便**同戳**也是确凿接替,必须退役（粗粒度时钟可
        # 造同戳,or 语义会把活着的接替标成已取消）。
        self.assertTrue(evidence_retires("running", ev, None, ev, nonce),
                        "nonce 证据认领了无 nonce 的同戳接替")
        self.assertTrue(evidence_retires(
            "running", ev, "cd" * 16, ev, nonce),
            "nonce 证据认领了异 nonce 的同戳接替")
        # nonce 覆盖优先（第二十五轮）:nonce-only 证据（戳空）撞上带同
        # nonce 的孤儿——戳对不上也不许退役。
        self.assertFalse(evidence_retires(
            "running", "2026-08-27T10:00:00+08:00", nonce, "", nonce),
            "带本次 nonce 的孤儿被误判成接替者退役")
        for kind in ("missing", "corrupt"):
            with self.subTest(kind=kind):
                self.assertFalse(evidence_retires(kind, "", None, ev, nonce),
                                 f"{kind} 读取失败被当接替证明")
        # 页面接线：退役判定走该 helper。
        page = (_ROOT / "web" / "operator_ui" / "pages" / "run_center.py"
                ).read_text(encoding="utf-8")
        self.assertIn("evidence_retires(", page,
                      "页面退役没走确凿接替判定")

    def test_the_boundary_captures_a_candidate_while_provably_alive(
            self) -> None:
        # 记录写在页面取消前观察之后、进程又死在 cancel_update 返回与页
        # 面事后观察之间——两端观察双双落空（codex 第二十三轮 P2）。
        # 边界内的 TimeoutExpired/复检仍活是最后一个可证生存期窗口,在
        # 那里补一次观察并随 UpdateCancel 带回。
        import json
        import os as _os

        from web.operator_ui.update_status import status_path_for_provider

        class _Survivor:
            pid = 999998
            returncode = None
            def poll(self):
                return None
            def kill(self):
                pass
            def wait(self, timeout=None):
                raise subprocess.TimeoutExpired(cmd="x", timeout=timeout)
        with tempfile.TemporaryDirectory() as t:
            provider = Path(t) / "prov"
            provider.mkdir()
            stamp = "2026-08-27T09:00:00+08:00"
            status_path_for_provider(provider).write_text(json.dumps({
                "schema_version": 1, "state": "running",
                "provider_dir": _os.path.normcase(str(provider.resolve())),
                "run_date": "2026-08-27", "started_at": stamp,
                "pid": _Survivor.pid,
            }), encoding="utf-8")
            got = cancel_update(
                _Survivor(), Path(t) / "log.log",  # type: ignore[arg-type]
                provider_dir=provider, grace_seconds=0.5)
            self.assertEqual("cancel_failed", got.kind)
            self.assertEqual(stamp, got.own_running_stamp,
                             "超时失败路径没在边界内捕获候选")
            # 无 provider 时不观察（None）。
            got2 = cancel_update(
                _Survivor(), Path(t) / "log.log",  # type: ignore[arg-type]
                grace_seconds=0.5)
            self.assertIsNone(got2.own_running_stamp)
        # 页面接线：三级兜底链含边界捕获。
        page = (_ROOT / "web" / "operator_ui" / "pages" / "run_center.py"
                ).read_text(encoding="utf-8")
        self.assertIn("or _outcome.own_running_stamp", page,
                      "候选兜底链缺边界捕获")

    def test_the_unlocked_claim_is_conditioned_on_live_evidence(self) -> None:
        # 证据落盘后、rerun 渲染前,调度器接替写下新 running——顶部逻辑
        # 退役证据、恢复 _running_fresh,而历史 evidence_stored=True 不代
        # 表此刻仍在覆盖:照念「将持续标注/已解锁」与同一帧上方的「正在
        # 运行」+禁用按钮自相矛盾（codex 第二十二轮 P2）。钉:成功文案
        # 以本帧 _cancelled_this_run 为条件;退役情形有如实改口分支。
        page = (_ROOT / "web" / "operator_ui" / "pages" / "run_center.py"
                ).read_text(encoding="utf-8")
        self.assertIn(
            'elif _last_cancel.get("evidence_stored") and _cancelled_this_run:',
            page, "「将持续标注/已解锁」没有以本帧证据覆盖为条件")
        self.assertIn('elif _last_cancel.get("evidence_stored"):', page,
                      "缺退役情形的如实改口分支")
        self.assertIn("证据按纪律", page, "退役改口文案缺失")
        # 源码序:条件版分支必须在无条件版之前（elif 链先窄后宽）。
        strict_at = page.index(
            'elif _last_cancel.get("evidence_stored") and _cancelled_this_run:')
        loose_at = page.index('elif _last_cancel.get("evidence_stored"):')
        self.assertLess(strict_at, loose_at,
                        "宽分支在前——条件版永远不可达")

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
        # 锚串随第二十四轮更新（launched 分支多带 launch_nonce）,断言
        # 意图不动:活句柄仍随 launch 返回。
        self.assertIn("process=proc, launch_nonce=launch_nonce)", src,
                      "launched 分支没带活句柄与 nonce")

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
