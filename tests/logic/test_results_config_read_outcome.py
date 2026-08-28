"""归档 config 的读取结果:三种「内容都是空」的情形必须分得开。

零字节的文件、被守卫拒绝的路径、压根不存在的文件——``_read_bytes_artifact``
对三者都给 ``b""``。重跑按钮的禁用判据若拿内容去推，就会把「读失败」讲成
「零字节空文件」:操作人点进配置页看到「源运行的 config.yaml 是空文件」，而
真正的原因（路径在允许的输出根之外 / 没有读权限）已经被丢掉
（codex P2 on #471）。

只看 ``path.is_file()`` 同样不行:那绕开守卫，一份存在但落在输出根之外的
归档会被当成可读。判据必须是**两者都成立**。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from web.operator_ui._path_guard import output_path  # noqa: E402
from web.operator_ui.artifact_reader import ArtifactReadIssue  # noqa: E402
from web.operator_ui.pages._results_helpers import (  # noqa: E402
    _read_bytes_artifact_checked,
    _read_config,
)


class GuardedReadOutcomeTests(unittest.TestCase):
    """守卫只放行允许的输出根之内的路径,所以「读成功」那几格的夹具必须建在
    那里面——建在系统临时目录里会被守卫拒掉,测到的就不是它自称要测的那件事
    （首版夹具就栽在这上面,实测当场看出来）。"""

    def setUp(self) -> None:
        root = output_path("_test_read_outcome")
        root.mkdir(parents=True, exist_ok=True)
        self.root = root
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def test_a_zero_byte_file_reads_successfully(self) -> None:
        path = self.root / "config.yaml"
        path.write_bytes(b"")
        issues: list[ArtifactReadIssue] = []

        data, ok = _read_bytes_artifact_checked(
            path, issues, artifact_name="config.yaml")

        self.assertEqual(data, b"")
        self.assertTrue(ok, "零字节文件是**读成功**的——内容空不等于读失败")
        self.assertEqual(issues, [])

    def test_a_missing_file_is_not_readable(self) -> None:
        issues: list[ArtifactReadIssue] = []

        data, ok = _read_bytes_artifact_checked(
            self.root / "nope.yaml", issues, artifact_name="config.yaml")

        self.assertEqual(data, b"")
        self.assertFalse(ok)

    def test_a_none_path_is_not_readable(self) -> None:
        issues: list[ArtifactReadIssue] = []

        data, ok = _read_bytes_artifact_checked(
            None, issues, artifact_name="config.yaml")

        self.assertEqual(data, b"")
        self.assertFalse(ok)

    def test_a_guard_rejected_path_is_not_readable(self) -> None:
        """存在、可读，但**落在允许的输出根之外**——守卫拒绝。

        这一格是本条的要害:文件确实在，`path.is_file()` 为真，只看它就会把
        这份归档当成可读，随后被讲成「零字节空文件」。
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            path.write_bytes(b"topk: 50\n")
            issues: list[ArtifactReadIssue] = []

            data, ok = _read_bytes_artifact_checked(
                path, issues, artifact_name="config.yaml")

        if not issues:
            self.skipTest(
                "本机的输出根守卫没有拒绝这个临时路径——这一格测不到")
        self.assertFalse(
            ok, "守卫拒绝了，却报成可读——真正的失败原因会被丢掉")
        self.assertEqual(data, b"")

    def test_read_config_reports_the_outcome_not_the_emptiness(self) -> None:
        # `_read_config` 的第四个返回值是**读取结果**,不是「内容非空」。
        path = self.root / "config.yaml"
        path.write_bytes(b"")
        issues: list[ArtifactReadIssue] = []

        config, resolved, data, readable = _read_config(
            {"config_path": str(path)}, issues)

        self.assertEqual(config, {})
        self.assertEqual(data, b"")
        self.assertEqual(resolved, path)
        self.assertTrue(
            readable, "零字节归档必须报成**读成功**——它要走进验证被报出")


if __name__ == "__main__":
    unittest.main()
