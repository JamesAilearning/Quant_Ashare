"""Recommend-runner tests (openspec 2026-08-16-ui-run-center W2/W4).

The runner executes the SAME command the cockpit prints (ensemble form),
synchronously. These tests fake ``subprocess.run`` — they pin the argv
(five same-source flags, and NONE of the flags the serving-config binding
chain owns), the UTF-8 text-mode kwargs, every outcome branch, and the
single-target source pin.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from web.operator_ui.recommend_runner import (  # noqa: E402
    DEFAULT_TIMEOUT_S,
    RECOMMEND_SCRIPT,
    RecommendRunResult,
    build_recommend_argv,
    run_daily_recommend,
)

_PARAMS = dict(
    ensemble_manifest="D:/stock/phase_b_artifacts/manifest.json",
    provider_uri="D:/qlib_data/my_cn_data_pit",
    delisted_registry="D:/qlib_data/tushare_raw/delisted_registry.parquet",
    name_source="D:/qlib_data/tushare_raw/active_stocks.parquet",
    bundle_max_age_days=14,
)

# The binding chain owns these: universe/cadence/topk resolve inside the
# CLI from config/serving/csi800_n5_production.yaml, and model/fit-window
# flags are refused outright in ensemble mode.
_FORBIDDEN_FLAGS = (
    "--model",
    "--fit-start",
    "--fit-end",
    "--topk",
    "--instruments",
    "--rebalance-cadence-days",
)


class CommandShapeTests(unittest.TestCase):
    def test_argv_carries_exactly_the_cockpit_flags(self) -> None:
        captured: dict = {}

        def _run(cmd: list[str], **kwargs: object) -> SimpleNamespace:
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return SimpleNamespace(returncode=0, stdout="banner", stderr="")

        with mock.patch(
            "web.operator_ui.recommend_runner.subprocess.run", _run
        ):
            result = run_daily_recommend(**_PARAMS)

        self.assertEqual(result.kind, "ok")
        cmd = captured["cmd"]
        # FULL-LIST equality, not per-flag membership: a smuggled extra
        # flag (--allow-holey-recommend would bypass the fetch-integrity
        # gate, --as-of would rewrite the decision day) passes any
        # membership-style pin. Exactly the cockpit's five flags or red.
        self.assertEqual(
            cmd,
            [
                sys.executable,
                str(RECOMMEND_SCRIPT),
                "--ensemble-manifest",
                _PARAMS["ensemble_manifest"],
                "--provider-uri",
                _PARAMS["provider_uri"],
                "--delisted-registry",
                _PARAMS["delisted_registry"],
                "--name-source",
                _PARAMS["name_source"],
                "--bundle-max-age-days",
                "14",
            ],
        )
        # Redundant with the equality above, kept as documentation of
        # WHY the list is closed: these belong to the CLI-side binding
        # chain and the ensemble-mode mutual-exclusion gate.
        for flag in _FORBIDDEN_FLAGS:
            self.assertNotIn(flag, cmd)

    def test_utf8_text_mode_kwargs_are_pinned(self) -> None:
        captured: dict = {}

        def _run(cmd: list[str], **kwargs: object) -> SimpleNamespace:
            captured["kwargs"] = kwargs
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with mock.patch(
            "web.operator_ui.recommend_runner.subprocess.run", _run
        ):
            run_daily_recommend(**_PARAMS)
        kwargs = captured["kwargs"]
        self.assertTrue(kwargs["capture_output"])
        self.assertTrue(kwargs["text"])
        self.assertEqual(kwargs["encoding"], "utf-8")
        self.assertEqual(kwargs["errors"], "replace")
        self.assertEqual(kwargs["timeout"], DEFAULT_TIMEOUT_S)
        # cwd = repo root so the CLI's relative out_dir lands in
        # output/daily_recommend/ exactly like a terminal run.
        self.assertEqual(kwargs["cwd"], str(PROJECT_ROOT))
        # Both pipe ends pinned: the child encodes UTF-8 too.
        self.assertEqual(kwargs["env"]["PYTHONIOENCODING"], "utf-8")

    def test_python_override_is_honored(self) -> None:
        cmd = build_recommend_argv(
            python=r"D:\_canonical_venv\Scripts\python.exe", **_PARAMS
        )
        self.assertEqual(cmd[0], r"D:\_canonical_venv\Scripts\python.exe")

    def test_recommend_script_exists_in_the_repo(self) -> None:
        # Drift guard: a rename of scripts/daily_recommend.py must fail
        # loudly HERE, not at an operator's button click.
        self.assertTrue(RECOMMEND_SCRIPT.exists(), RECOMMEND_SCRIPT)


class OutcomeBranchTests(unittest.TestCase):
    def _run_with(self, fake: object) -> RecommendRunResult:
        with mock.patch(
            "web.operator_ui.recommend_runner.subprocess.run", fake
        ):
            return run_daily_recommend(**_PARAMS)

    def test_exit_zero_is_ok_with_stdout_banner(self) -> None:
        def _run(cmd: list[str], **kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(
                returncode=0, stdout="entry_date=2026-08-18\n", stderr=""
            )

        result = self._run_with(_run)
        self.assertEqual(result.kind, "ok")
        self.assertEqual(result.exit_code, 0)
        self.assertIn("entry_date=2026-08-18", result.stdout_tail)
        self.assertGreaterEqual(result.elapsed_s, 0.0)

    def test_nonzero_exit_is_failed_with_stderr_reason(self) -> None:
        # Every refusal in this CLI is loud on stderr (stale bundle,
        # binding mismatch, …) — the page shows it verbatim.
        def _run(cmd: list[str], **kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(
                returncode=1, stdout="", stderr="bundle is stale: 20 > 14"
            )

        result = self._run_with(_run)
        self.assertEqual(result.kind, "failed")
        self.assertEqual(result.exit_code, 1)
        self.assertIn("bundle is stale", result.stderr_tail)
        self.assertIn("退出码 1", result.error)

    def test_timeout_is_distinct_and_loud(self) -> None:
        def _run(cmd: list[str], **kwargs: object) -> SimpleNamespace:
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=900)

        result = self._run_with(_run)
        self.assertEqual(result.kind, "timeout")
        self.assertIn("已终止", result.error)

    def test_launch_failure_is_distinct_and_loud(self) -> None:
        def _run(cmd: list[str], **kwargs: object) -> SimpleNamespace:
            raise OSError("permission denied")

        result = self._run_with(_run)
        self.assertEqual(result.kind, "launch_failed")
        self.assertIn("无法启动解释器", result.error)

    def test_missing_script_is_run_failed_without_spawning(self) -> None:
        def _run(cmd: list[str], **kwargs: object) -> SimpleNamespace:
            raise AssertionError("must not spawn when the script is gone")

        with mock.patch(
            "web.operator_ui.recommend_runner.RECOMMEND_SCRIPT",
            Path("/no/such/daily_recommend_script.py"),
        ), mock.patch(
            "web.operator_ui.recommend_runner.subprocess.run", _run
        ):
            result = run_daily_recommend(**_PARAMS)
        self.assertEqual(result.kind, "run_failed")


class SingleTargetSourcePinTests(unittest.TestCase):
    """Each audited runner points at exactly one CLI (sibling of the
    ``pit_validation_runner`` governance pin)."""

    def test_recommend_runner_targets_only_the_recommend_cli(self) -> None:
        src = (
            PROJECT_ROOT / "web" / "operator_ui" / "recommend_runner.py"
        ).read_text(encoding="utf-8")
        self.assertIn("daily_recommend.py", src)
        self.assertNotIn("daily_update.py", src)
        self.assertNotIn("06_validate_pit_data", src)

    def test_recommend_runner_never_imports_inference_code(self) -> None:
        # Import-level on purpose: the docstring legitimately NAMES the
        # boundary ("never imports src.inference"), so a raw substring
        # scan would bite the sentence stating the rule.
        import ast

        tree = ast.parse(
            (
                PROJECT_ROOT / "web" / "operator_ui" / "recommend_runner.py"
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
            "recommend_runner 不得 import src.* —— 与推理代码的唯一耦合是"
            " CLI 进程边界",
        )


if __name__ == "__main__":
    unittest.main()
