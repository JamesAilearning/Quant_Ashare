"""Recommend-runner tests (openspec 2026-08-16-ui-run-center W2/W4).

The runner executes the SAME command the cockpit prints (ensemble form),
synchronously, pointing the CLI at a per-run STAGING dir and publishing
finished files via per-file atomic ``os.replace`` (codex #440 r1: a
timeout kill mid-``write_outputs`` must never tear a published day's
artifact). These tests fake ``subprocess.run`` — they pin the argv (the
five same-source flags plus the staging ``--out-dir``, and NONE of the
flags the serving-config binding chain owns), the UTF-8 text-mode
kwargs, the staging/publish life cycle, every outcome branch, and the
single-target source pin.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
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

_ARTIFACTS = {
    "daily_recommendation_2026-08-14.json": "{}",
    "daily_recommendation_2026-08-14.csv": "rank,code",
    "daily_recommendation_2026-08-14_scored_full.csv": "code,score",
}


def _staging_of(cmd: list[str]) -> Path:
    return Path(cmd[cmd.index("--out-dir") + 1])


def _fake_run(
    files: dict[str, str] | None,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
):
    """A subprocess.run stand-in that materializes artifacts in the
    staging dir the way the real CLI's write_outputs does (or not, when
    files is None)."""

    def _run(cmd: list[str], **kwargs: object) -> SimpleNamespace:
        if files is not None:
            staging = _staging_of(cmd)
            staging.mkdir(parents=True, exist_ok=True)
            for name, content in files.items():
                (staging / name).write_text(content, encoding="utf-8")
        return SimpleNamespace(
            returncode=returncode, stdout=stdout, stderr=stderr
        )

    return _run


def _no_leftover_staging(out_dir: Path) -> bool:
    return not any(out_dir.glob(".staging-*"))


class CommandShapeTests(unittest.TestCase):
    def test_argv_is_exactly_the_cockpit_flags_plus_staging(self) -> None:
        captured: dict = {}

        def _run(cmd: list[str], **kwargs: object) -> SimpleNamespace:
            captured["cmd"] = cmd
            return _fake_run(_ARTIFACTS)(cmd, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch(
                "web.operator_ui.recommend_runner.OUT_DIR", Path(tmp)
            ), mock.patch(
                "web.operator_ui.recommend_runner.subprocess.run", _run
            ):
                result = run_daily_recommend(**_PARAMS)
            self.assertEqual(result.kind, "ok")
            cmd = captured["cmd"]
            # CLOSED-LIST pin: everything except the (per-run) staging
            # value is byte-equal. A smuggled extra flag
            # (--allow-holey-recommend would bypass the fetch-integrity
            # gate, --as-of would rewrite the decision day) makes the
            # prefix comparison red.
            self.assertEqual(
                cmd[:-1],
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
                    "--out-dir",
                ],
            )
            # The one open slot is pinned by shape: a fresh staging dir
            # UNDER the publish dir (same volume → os.replace atomic).
            self.assertTrue(
                cmd[-1].startswith(str(Path(tmp) / ".staging-")), cmd[-1]
            )
            # Redundant with the closed list, kept as documentation of
            # WHY it is closed.
            for flag in _FORBIDDEN_FLAGS:
                self.assertNotIn(flag, cmd)

    def test_utf8_text_mode_kwargs_are_pinned(self) -> None:
        captured: dict = {}

        def _run(cmd: list[str], **kwargs: object) -> SimpleNamespace:
            captured["kwargs"] = kwargs
            return SimpleNamespace(returncode=1, stdout="", stderr="x")

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
        # cwd = repo root, matching a terminal run's anchor.
        self.assertEqual(kwargs["cwd"], str(PROJECT_ROOT))
        # Both pipe ends pinned: the child encodes UTF-8 too.
        self.assertEqual(kwargs["env"]["PYTHONIOENCODING"], "utf-8")

    def test_python_override_is_honored(self) -> None:
        cmd = build_recommend_argv(
            python=r"D:\_canonical_venv\Scripts\python.exe",
            out_dir="D:/x/staging",
            **_PARAMS,
        )
        self.assertEqual(cmd[0], r"D:\_canonical_venv\Scripts\python.exe")

    def test_recommend_script_exists_in_the_repo(self) -> None:
        # Drift guard: a rename of scripts/daily_recommend.py must fail
        # loudly HERE, not at an operator's button click.
        self.assertTrue(RECOMMEND_SCRIPT.exists(), RECOMMEND_SCRIPT)


class StagingPublishTests(unittest.TestCase):
    """The reason staging exists: published artifacts survive every
    non-success outcome, and success publishes complete files only."""

    def test_success_publishes_all_artifacts_and_cleans_staging(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            with mock.patch(
                "web.operator_ui.recommend_runner.OUT_DIR", out
            ), mock.patch(
                "web.operator_ui.recommend_runner.subprocess.run",
                _fake_run(_ARTIFACTS, stdout="entry_date=2026-08-14"),
            ):
                result = run_daily_recommend(**_PARAMS)
            self.assertEqual(result.kind, "ok")
            self.assertEqual(
                sorted(result.published), sorted(_ARTIFACTS)
            )
            for name, content in _ARTIFACTS.items():
                self.assertEqual(
                    (out / name).read_text(encoding="utf-8"), content
                )
            self.assertTrue(_no_leftover_staging(out))

    def test_success_replaces_only_its_own_day(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            out.mkdir(exist_ok=True)
            prior = out / "daily_recommendation_2026-01-01.json"
            prior.write_text("OLD", encoding="utf-8")
            with mock.patch(
                "web.operator_ui.recommend_runner.OUT_DIR", out
            ), mock.patch(
                "web.operator_ui.recommend_runner.subprocess.run",
                _fake_run(_ARTIFACTS),
            ):
                result = run_daily_recommend(**_PARAMS)
            self.assertEqual(result.kind, "ok")
            self.assertEqual(prior.read_text(encoding="utf-8"), "OLD")

    def test_timeout_cleans_staging_and_preserves_published(self) -> None:
        # Simulate a kill mid-write_outputs: partial file in staging,
        # then the TimeoutExpired surfaces.
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            out.mkdir(exist_ok=True)
            prior = out / "daily_recommendation_2026-08-13.json"
            prior.write_text("VALID-PRIOR-DAY", encoding="utf-8")

            def _run(cmd: list[str], **kwargs: object) -> SimpleNamespace:
                staging = _staging_of(cmd)
                staging.mkdir(parents=True, exist_ok=True)
                (staging / "daily_recommendation_2026-08-13.json").write_text(
                    '{"torn":', encoding="utf-8"
                )
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=900)

            with mock.patch(
                "web.operator_ui.recommend_runner.OUT_DIR", out
            ), mock.patch(
                "web.operator_ui.recommend_runner.subprocess.run", _run
            ):
                result = run_daily_recommend(**_PARAMS)
            self.assertEqual(result.kind, "timeout")
            self.assertIn("已终止", result.error)
            self.assertEqual(
                prior.read_text(encoding="utf-8"), "VALID-PRIOR-DAY"
            )
            self.assertTrue(_no_leftover_staging(out))

    def test_nonzero_exit_cleans_staging(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            with mock.patch(
                "web.operator_ui.recommend_runner.OUT_DIR", out
            ), mock.patch(
                "web.operator_ui.recommend_runner.subprocess.run",
                _fake_run(
                    {"daily_recommendation_2026-08-14.json": "half"},
                    returncode=1,
                    stderr="bundle is stale: 20 > 14",
                ),
            ):
                result = run_daily_recommend(**_PARAMS)
            self.assertEqual(result.kind, "failed")
            self.assertTrue(_no_leftover_staging(out))
            self.assertEqual(list(out.iterdir()), [])

    def test_zero_exit_without_artifacts_is_a_contract_breach(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch(
                "web.operator_ui.recommend_runner.OUT_DIR", Path(tmp)
            ), mock.patch(
                "web.operator_ui.recommend_runner.subprocess.run",
                _fake_run({}),  # creates the staging dir, writes nothing
            ):
                result = run_daily_recommend(**_PARAMS)
            self.assertEqual(result.kind, "run_failed")
            self.assertIn("无产物", result.error)

    def test_publish_interruption_keeps_staging_for_manual_disposal(
        self,
    ) -> None:
        # Deleting staging on a publish error would destroy the only
        # complete copy of the run's artifacts.
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            with mock.patch(
                "web.operator_ui.recommend_runner.OUT_DIR", out
            ), mock.patch(
                "web.operator_ui.recommend_runner.subprocess.run",
                _fake_run(_ARTIFACTS),
            ), mock.patch(
                "web.operator_ui.recommend_runner.os.replace",
                side_effect=OSError("cross-device or locked"),
            ):
                result = run_daily_recommend(**_PARAMS)
            self.assertEqual(result.kind, "run_failed")
            self.assertIn("保留", result.error)
            staging_dirs = list(out.glob(".staging-*"))
            self.assertEqual(len(staging_dirs), 1)
            self.assertEqual(
                sorted(p.name for p in staging_dirs[0].iterdir()),
                sorted(_ARTIFACTS),
            )


class OutcomeBranchTests(unittest.TestCase):
    def _run_with(self, fake: object) -> RecommendRunResult:
        with mock.patch(
            "web.operator_ui.recommend_runner.subprocess.run", fake
        ):
            return run_daily_recommend(**_PARAMS)

    def test_exit_zero_is_ok_with_stdout_banner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch(
                "web.operator_ui.recommend_runner.OUT_DIR", Path(tmp)
            ):
                result = self._run_with(
                    _fake_run(
                        _ARTIFACTS, stdout="entry_date=2026-08-18\n"
                    )
                )
        self.assertEqual(result.kind, "ok")
        self.assertEqual(result.exit_code, 0)
        self.assertIn("entry_date=2026-08-18", result.stdout_tail)
        self.assertGreaterEqual(result.elapsed_s, 0.0)

    def test_nonzero_exit_is_failed_with_reason_tails(self) -> None:
        # The repo logger writes refusals to STDOUT; the runner hands
        # both tails to the page, which prefers stdout.
        result = self._run_with(
            _fake_run(
                None,
                returncode=1,
                stdout="ERROR daily_recommend: bundle is stale: 20 > 14",
                stderr="import-time noise",
            )
        )
        self.assertEqual(result.kind, "failed")
        self.assertEqual(result.exit_code, 1)
        self.assertIn("bundle is stale", result.stdout_tail)
        self.assertIn("import-time noise", result.stderr_tail)
        self.assertIn("退出码 1", result.error)

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
