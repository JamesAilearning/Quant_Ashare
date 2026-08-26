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

        from web.operator_ui.update_runner import _blocking_run_status
        from web.operator_ui.update_status import status_path_for_provider
        with tempfile.TemporaryDirectory() as t:
            provider = Path(t) / "prov"
            provider.mkdir()
            stamp = "2026-08-26T21:00:00+08:00"
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
                cancelled_started_at="2026-08-26T20:00:00+08:00"))

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
