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
