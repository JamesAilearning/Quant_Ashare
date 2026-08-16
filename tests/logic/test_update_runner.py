"""Update-launcher tests (openspec 2026-08-16-ui-run-center W1/W4).

The launcher is the run-center page's ONLY road to ``daily_update``: a
detached subprocess whose argv mirrors the scheduler wrapper. These tests
fake ``subprocess.Popen`` so they need no tushare, no bundle and no
2-hour run — they pin the argv shape, the detach/log/env kwargs, every
refusal branch, and the runner's single-target source pin (the sibling
of the ``pit_validation_runner`` governance pin: each audited runner
points at exactly one CLI).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from web.operator_ui.update_runner import (  # noqa: E402
    REFERENCE_CASES,
    START_DATE,
    TOKEN_ENV_VAR,
    UPDATE_SCRIPT,
    UpdateLaunch,
    build_update_argv,
    default_log_path,
    launch_daily_update,
    log_tail,
)

_CN_TZ = timezone(timedelta(hours=8))
_ENV_OK = {TOKEN_ENV_VAR: "test-token", "PATH": os.environ.get("PATH", "")}


def _flag_value(cmd: list[str], flag: str) -> str:
    return cmd[cmd.index(flag) + 1]


class _Sandbox:
    """A provider dir + optional status artifact in a temp tree."""

    def __init__(self, tmp: str) -> None:
        self.root = Path(tmp)
        self.provider = self.root / "my_cn_data_pit"
        self.provider.mkdir()
        self.tushare = self.root / "tushare_raw"
        self.registry = self.tushare / "delisted_registry.parquet"

    def write_status(
        self,
        *,
        state: str = "running",
        started_at: str | None = None,
        provider_dir: str | None = None,
    ) -> Path:
        record = {
            "schema_version": 1,
            "state": state,
            "provider_dir": (
                provider_dir
                if provider_dir is not None
                else os.path.normcase(str(self.provider.resolve()))
            ),
            "run_date": "2026-08-16",
            "started_at": (
                started_at
                if started_at is not None
                else datetime.now(tz=_CN_TZ).isoformat()
            ),
        }
        path = self.provider.with_name(
            f"{self.provider.name}.daily_update_status.json"
        )
        path.write_text(json.dumps(record), encoding="utf-8")
        return path


class CommandShapeTests(unittest.TestCase):
    def _launch(self, box: _Sandbox, captured: dict) -> UpdateLaunch:
        def _popen(cmd: list[str], **kwargs: object) -> SimpleNamespace:
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return SimpleNamespace(pid=4321)

        with mock.patch(
            "web.operator_ui.update_runner.subprocess.Popen", _popen
        ):
            return launch_daily_update(
                box.provider, box.tushare, box.registry, env=_ENV_OK
            )

    def test_argv_mirrors_the_scheduler_wrapper(self) -> None:
        captured: dict = {}
        with tempfile.TemporaryDirectory() as tmp:
            box = _Sandbox(tmp)
            result = self._launch(box, captured)
            self.assertEqual(result.kind, "launched")
            self.assertEqual(result.pid, 4321)
            cmd = captured["cmd"]
            self.assertEqual(cmd[0], sys.executable)
            self.assertEqual(cmd[1], str(UPDATE_SCRIPT))
            self.assertEqual(
                _flag_value(cmd, "--provider-dir"), str(box.provider)
            )
            self.assertEqual(
                _flag_value(cmd, "--tushare-dir"), str(box.tushare)
            )
            self.assertEqual(
                _flag_value(cmd, "--delisted-registry"), str(box.registry)
            )
            self.assertEqual(
                _flag_value(cmd, "--reference-cases"), str(REFERENCE_CASES)
            )
            self.assertEqual(_flag_value(cmd, "--start-date"), START_DATE)
            # The launcher must never smuggle in flags the scheduler does
            # not pass — --dry-run would silently skip the lock AND the
            # status artifact, --status-path would retarget #434's file.
            self.assertNotIn("--dry-run", cmd)
            self.assertNotIn("--status-path", cmd)
            self.assertNotIn("--allow-holey-fetch", cmd)

    def test_detach_log_and_env_kwargs(self) -> None:
        captured: dict = {}
        with tempfile.TemporaryDirectory() as tmp:
            box = _Sandbox(tmp)
            result = self._launch(box, captured)
            kwargs = captured["kwargs"]
            self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
            self.assertIs(kwargs["stderr"], subprocess.STDOUT)
            self.assertEqual(kwargs["cwd"], str(PROJECT_ROOT))
            # Both ends of the encoding are pinned: the child encodes
            # UTF-8 (utf8_child_env) into the shared log.
            self.assertEqual(kwargs["env"]["PYTHONIOENCODING"], "utf-8")
            self.assertEqual(kwargs["env"][TOKEN_ENV_VAR], "test-token")
            if sys.platform == "win32":
                self.assertEqual(
                    kwargs["creationflags"],
                    subprocess.CREATE_NEW_PROCESS_GROUP
                    | subprocess.CREATE_NO_WINDOW,
                )
                self.assertNotIn("start_new_session", kwargs)
            else:
                self.assertTrue(kwargs["start_new_session"])
                self.assertNotIn("creationflags", kwargs)
            # stdout is the scheduler's log stream, append mode, and OUR
            # handle is closed after launch (the child holds its own).
            log_handle = kwargs["stdout"]
            self.assertTrue(log_handle.closed)
            self.assertEqual(
                Path(log_handle.name), default_log_path(box.provider)
            )
            self.assertEqual(result.log_path, default_log_path(box.provider))

    def test_dated_marker_line_lands_in_the_log(self) -> None:
        # The shared log's own lines carry only HH:MM:SS; the launcher's
        # marker is what lets an operator attribute the next block.
        captured: dict = {}
        with tempfile.TemporaryDirectory() as tmp:
            box = _Sandbox(tmp)
            self._launch(box, captured)
            log_path = default_log_path(box.provider)
            text = log_path.read_text(encoding="utf-8")
            self.assertIn("[run_center]", text)
            self.assertIn(str(datetime.now(tz=_CN_TZ).year), text)
            self.assertEqual(log_path.parent.name, "logs")

    def test_python_override_is_honored(self) -> None:
        captured: dict = {}
        with tempfile.TemporaryDirectory() as tmp:
            box = _Sandbox(tmp)

            def _popen(cmd: list[str], **kwargs: object) -> SimpleNamespace:
                captured["cmd"] = cmd
                return SimpleNamespace(pid=1)

            with mock.patch(
                "web.operator_ui.update_runner.subprocess.Popen", _popen
            ):
                launch_daily_update(
                    box.provider,
                    box.tushare,
                    box.registry,
                    python=r"D:\_canonical_venv\Scripts\python.exe",
                    env=_ENV_OK,
                )
        self.assertEqual(
            captured["cmd"][0], r"D:\_canonical_venv\Scripts\python.exe"
        )

    def test_script_and_reference_cases_exist_in_the_repo(self) -> None:
        # Drift guards: renames must fail loudly HERE, not at an
        # operator's button click.
        self.assertTrue(UPDATE_SCRIPT.exists(), UPDATE_SCRIPT)
        self.assertTrue(REFERENCE_CASES.exists(), REFERENCE_CASES)

    def test_build_argv_is_pure_and_ordered(self) -> None:
        cmd = build_update_argv(
            Path("/data/prov"), Path("/data/tu"), Path("/data/reg.parquet")
        )
        self.assertEqual(cmd[0], sys.executable)
        self.assertEqual(cmd[1], str(UPDATE_SCRIPT))
        self.assertEqual(cmd[-2:], ["--start-date", START_DATE])


class RefusalBranchTests(unittest.TestCase):
    def _launch_expect_no_popen(
        self, box: _Sandbox, env: dict[str, str]
    ) -> UpdateLaunch:
        def _popen(cmd: list[str], **kwargs: object) -> SimpleNamespace:
            raise AssertionError("Popen must not be called on a refusal")

        with mock.patch(
            "web.operator_ui.update_runner.subprocess.Popen", _popen
        ):
            return launch_daily_update(
                box.provider, box.tushare, box.registry, env=env
            )

    def test_missing_token_refuses_before_any_launch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            box = _Sandbox(tmp)
            result = self._launch_expect_no_popen(box, {"PATH": "x"})
            self.assertEqual(result.kind, "no_token")
            self.assertIn(TOKEN_ENV_VAR, result.error)

    def test_whitespace_token_is_still_no_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            box = _Sandbox(tmp)
            result = self._launch_expect_no_popen(
                box, {TOKEN_ENV_VAR: "   "}
            )
            self.assertEqual(result.kind, "no_token")

    def test_fresh_running_record_refuses_duplicate_launch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            box = _Sandbox(tmp)
            box.write_status()
            result = self._launch_expect_no_popen(box, dict(_ENV_OK))
            self.assertEqual(result.kind, "already_running")
            self.assertIn("exit 17", result.error)

    def test_stale_running_record_does_not_block(self) -> None:
        # A >6h running record may be a crashed run — the single-flight
        # lock, not this advisory check, is the arbiter then.
        with tempfile.TemporaryDirectory() as tmp:
            box = _Sandbox(tmp)
            stale = datetime.now(tz=_CN_TZ) - timedelta(hours=7)
            box.write_status(started_at=stale.isoformat())

            def _popen(cmd: list[str], **kwargs: object) -> SimpleNamespace:
                return SimpleNamespace(pid=7)

            with mock.patch(
                "web.operator_ui.update_runner.subprocess.Popen", _popen
            ):
                result = launch_daily_update(
                    box.provider, box.tushare, box.registry, env=_ENV_OK
                )
            self.assertEqual(result.kind, "launched")

    def test_foreign_provider_record_does_not_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            box = _Sandbox(tmp)
            box.write_status(provider_dir=os.path.normcase("/some/other"))

            def _popen(cmd: list[str], **kwargs: object) -> SimpleNamespace:
                return SimpleNamespace(pid=7)

            with mock.patch(
                "web.operator_ui.update_runner.subprocess.Popen", _popen
            ):
                result = launch_daily_update(
                    box.provider, box.tushare, box.registry, env=_ENV_OK
                )
            self.assertEqual(result.kind, "launched")

    def test_finished_record_does_not_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            box = _Sandbox(tmp)
            # A finished record (whatever its exit code) never blocks —
            # write a running-shaped record flipped to finished fields.
            path = box.write_status(state="finished")
            record = json.loads(path.read_text(encoding="utf-8"))
            record.update(
                finished_at=datetime.now(tz=_CN_TZ).isoformat(),
                exit_code=12,
                failed_stage="fetch",
                detail="holes",
            )
            path.write_text(json.dumps(record), encoding="utf-8")

            def _popen(cmd: list[str], **kwargs: object) -> SimpleNamespace:
                return SimpleNamespace(pid=7)

            with mock.patch(
                "web.operator_ui.update_runner.subprocess.Popen", _popen
            ):
                result = launch_daily_update(
                    box.provider, box.tushare, box.registry, env=_ENV_OK
                )
            self.assertEqual(result.kind, "launched")

    def test_popen_oserror_is_launch_failed_and_closes_the_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            box = _Sandbox(tmp)

            def _popen(cmd: list[str], **kwargs: object) -> SimpleNamespace:
                raise OSError("no such interpreter")

            with mock.patch(
                "web.operator_ui.update_runner.subprocess.Popen", _popen
            ):
                result = launch_daily_update(
                    box.provider, box.tushare, box.registry, env=_ENV_OK
                )
            self.assertEqual(result.kind, "launch_failed")
            self.assertIn("no such interpreter", result.error)

    def test_missing_script_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            box = _Sandbox(tmp)
            with mock.patch(
                "web.operator_ui.update_runner.UPDATE_SCRIPT",
                Path(tmp) / "gone.py",
            ):
                result = self._launch_expect_no_popen(box, dict(_ENV_OK))
            self.assertEqual(result.kind, "script_missing")


class LogTailTests(unittest.TestCase):
    def test_missing_log_is_an_empty_state_not_an_error(self) -> None:
        self.assertEqual(log_tail(Path("/no/such/dir/x.log")), "")

    def test_tail_returns_the_end_of_the_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "daily_update.log"
            log.write_text("头部\n" + "x" * 5000 + "\n结尾标记", encoding="utf-8")
            tail = log_tail(log, chars=100)
            self.assertLessEqual(len(tail), 100)
            self.assertIn("结尾标记", tail)


class SingleTargetSourcePinTests(unittest.TestCase):
    """The audited-runner rule: each runner points at exactly one CLI.

    Sibling of the governance pin on ``pit_validation_runner`` (which is
    itself pinned to NOT contain ``daily_update.py`` — that is why this
    runner had to be a new module).
    """

    def test_update_runner_targets_only_the_update_cli(self) -> None:
        src = (
            PROJECT_ROOT / "web" / "operator_ui" / "update_runner.py"
        ).read_text(encoding="utf-8")
        self.assertIn("daily_update.py", src)
        self.assertNotIn("06_validate_pit_data", src)
        self.assertNotIn("daily_recommend.py", src)

    def test_update_runner_never_imports_the_orchestrator(self) -> None:
        # Import-level on purpose: the module's docstring legitimately
        # NAMES the boundary it keeps ("never imports src.data_pipeline"),
        # so a raw substring scan would bite the very sentence that states
        # the rule. What must hold is the actual import graph.
        import ast

        tree = ast.parse(
            (
                PROJECT_ROOT / "web" / "operator_ui" / "update_runner.py"
            ).read_text(encoding="utf-8")
        )
        roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".")[0])
        self.assertNotIn(
            "src",
            roots,
            "update_runner 不得 import src.* —— 与编排器的唯一耦合是 CLI"
            " 进程边界",
        )


if __name__ == "__main__":
    unittest.main()
