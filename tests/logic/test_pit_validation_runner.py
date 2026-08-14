"""PIT validation subprocess-runner tests (2026-08-14-data-inspect-pit-subprocess).

The runner is the page's process-isolation seam: the 06 CLI runs in a
subprocess (qlib is a per-process singleton) and the page renders the parsed
``--report-json`` output. These tests fake ``subprocess.run`` so they need no
qlib, no bundle, and no network — they pin the command shape, the UTF-8
text-mode convention, and every outcome branch (including the subtle one:
process exit code 2 WITH a parseable report is a RESULT, not a runner error).
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from web.operator_ui.pit_validation_runner import (  # noqa: E402
    DEFAULT_TIMEOUT_S,
    PITRunResult,
    VALIDATOR_SCRIPT,
    run_pit_validation,
)

_VALID_REPORT = {
    "provider_dir": "/data/my_cn_data_pit",
    "exit_code": 0,
    "checks": [
        {
            "name": "Survivorship spot-check", "code": "A", "passed": True,
            "warnings": [], "errors": [], "details": {},
        },
        {
            "name": "Delist boundary sweep", "code": "B", "passed": True,
            "warnings": ["early suspension near delist"], "errors": [],
            "details": {},
        },
    ],
}


def _fake_run(payload: object | None, returncode: int = 0, stderr: str = ""):
    """A subprocess.run stand-in that materializes the --report-json file the
    way the real CLI does (or not, when payload is None)."""

    def _run(cmd, **kwargs):
        if payload is not None:
            report_path = Path(cmd[cmd.index("--report-json") + 1])
            if isinstance(payload, str):  # raw text — e.g. deliberately invalid
                report_path.write_text(payload, encoding="utf-8")
            else:
                report_path.write_text(
                    json.dumps(payload), encoding="utf-8"
                )
        return SimpleNamespace(returncode=returncode, stdout="", stderr=stderr)

    return _run


class CommandShapeTests(unittest.TestCase):
    def test_command_targets_the_06_cli_with_pinned_utf8_text_mode(self) -> None:
        captured: dict = {}

        def _run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            report_path = Path(cmd[cmd.index("--report-json") + 1])
            report_path.write_text(json.dumps(_VALID_REPORT), encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with mock.patch(
            "web.operator_ui.pit_validation_runner.subprocess.run", _run
        ):
            result = run_pit_validation(Path("/data/prov"), Path("/data/reg.parquet"))

        self.assertEqual(result.kind, "ok")
        cmd, kwargs = captured["cmd"], captured["kwargs"]
        self.assertEqual(cmd[0], sys.executable)
        self.assertEqual(cmd[1], str(VALIDATOR_SCRIPT))
        for flag in ("--provider-dir", "--delisted-registry", "--report-json"):
            self.assertIn(flag, cmd)
        # Convention pinned by e7504f6: text-mode subprocess calls in this repo
        # carry explicit UTF-8 so the 06 CLI's Chinese log lines never decode
        # through the locale codec.
        self.assertTrue(kwargs["text"])
        self.assertEqual(kwargs["encoding"], "utf-8")
        self.assertEqual(kwargs["errors"], "replace")
        self.assertTrue(kwargs["capture_output"])
        self.assertEqual(kwargs["timeout"], DEFAULT_TIMEOUT_S)

    def test_python_override_is_honored(self) -> None:
        captured: dict = {}

        def _run(cmd, **kwargs):
            captured["cmd"] = cmd
            report_path = Path(cmd[cmd.index("--report-json") + 1])
            report_path.write_text(json.dumps(_VALID_REPORT), encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with mock.patch(
            "web.operator_ui.pit_validation_runner.subprocess.run", _run
        ):
            run_pit_validation(
                Path("/data/prov"), Path("/data/reg.parquet"),
                python=r"D:\_canonical_venv\Scripts\python.exe",
            )
        self.assertEqual(captured["cmd"][0], r"D:\_canonical_venv\Scripts\python.exe")

    def test_validator_script_path_exists_in_the_repo(self) -> None:
        # Drift guard: the runner derives the CLI path from the repo layout;
        # a rename of scripts/data_pipeline/06_validate_pit_data.py must fail
        # loudly HERE, not at an operator's button click.
        self.assertTrue(VALIDATOR_SCRIPT.exists(), VALIDATOR_SCRIPT)


class OutcomeBranchTests(unittest.TestCase):
    def _run_with(self, fake) -> PITRunResult:
        with mock.patch(
            "web.operator_ui.pit_validation_runner.subprocess.run", fake
        ):
            return run_pit_validation(Path("/data/prov"), Path("/data/reg.parquet"))

    def test_clean_run_parses_every_check(self) -> None:
        result = self._run_with(_fake_run(_VALID_REPORT))
        self.assertEqual(result.kind, "ok")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(len(result.checks), 2)
        self.assertEqual(result.checks[1]["warnings"], ["early suspension near delist"])
        self.assertGreaterEqual(result.elapsed_s, 0.0)

    def test_validation_failures_are_a_result_not_a_runner_error(self) -> None:
        # The 06 CLI returns exit code 2 when checks FAIL — and still writes
        # the report. The page must render those failures, not an error blob.
        failed_report = dict(_VALID_REPORT, exit_code=2)
        failed_report["checks"] = [
            dict(_VALID_REPORT["checks"][0], passed=False, errors=["look-ahead"]),
        ]
        result = self._run_with(_fake_run(failed_report, returncode=2))
        self.assertEqual(result.kind, "ok")
        self.assertEqual(result.exit_code, 2)
        self.assertFalse(result.checks[0]["passed"])
        self.assertEqual(result.checks[0]["errors"], ["look-ahead"])

    def test_setup_failure_without_report_is_run_failed_with_stderr(self) -> None:
        result = self._run_with(
            _fake_run(None, returncode=2, stderr="PITValidatorError: registry unreadable")
        )
        self.assertEqual(result.kind, "run_failed")
        self.assertIn("registry unreadable", result.error)
        self.assertIn("退出码 2", result.error)

    def test_zero_exit_without_report_is_a_contract_breach(self) -> None:
        result = self._run_with(_fake_run(None, returncode=0))
        self.assertEqual(result.kind, "corrupt_report")
        self.assertIn("退出码 0 但报告文件不存在", result.error)

    def test_unparseable_report_is_corrupt_not_defaulted(self) -> None:
        result = self._run_with(_fake_run("{not json", returncode=0))
        self.assertEqual(result.kind, "corrupt_report")
        self.assertIn("不是合法 JSON", result.error)

    def test_shape_violating_report_is_corrupt_not_defaulted(self) -> None:
        bad = dict(_VALID_REPORT, checks=[{"name": 1, "code": "A"}])
        result = self._run_with(_fake_run(bad, returncode=0))
        self.assertEqual(result.kind, "corrupt_report")
        self.assertIn("形状违约", result.error)

    def test_timeout_is_distinct_and_loud(self) -> None:
        def _run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs["timeout"])

        result = self._run_with(_run)
        self.assertEqual(result.kind, "timeout")
        self.assertIn("已终止", result.error)

    def test_launch_failure_is_distinct_and_loud(self) -> None:
        def _run(cmd, **kwargs):
            raise OSError("permission denied")

        result = self._run_with(_run)
        self.assertEqual(result.kind, "launch_failed")
        self.assertIn("无法启动解释器", result.error)

    def test_temp_report_dir_is_gone_after_the_run(self) -> None:
        # Read-only boundary: the report JSON lives in a TemporaryDirectory
        # that must not leak onto disk.
        seen: list[Path] = []

        def _run(cmd, **kwargs):
            report_path = Path(cmd[cmd.index("--report-json") + 1])
            seen.append(report_path.parent)
            report_path.write_text(json.dumps(_VALID_REPORT), encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with mock.patch(
            "web.operator_ui.pit_validation_runner.subprocess.run", _run
        ):
            run_pit_validation(Path("/data/prov"), Path("/data/reg.parquet"))
        self.assertEqual(len(seen), 1)
        self.assertFalse(seen[0].exists(), f"temp dir leaked: {seen[0]}")


if __name__ == "__main__":
    unittest.main()
