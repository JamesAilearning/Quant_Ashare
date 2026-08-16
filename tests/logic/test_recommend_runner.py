"""Recommend-runner tests (openspec 2026-08-16-ui-run-center W2/W4).

The runner executes the SAME command the cockpit prints (ensemble form),
synchronously in its OWN process group (codex #440 r5: joblib
grandchildren hold the captured pipes, so a timeout must kill the whole
tree or the drain blocks forever while the provider lock is held),
pointing the CLI at a per-run STAGING dir and publishing finished files
via per-file atomic ``os.replace`` with a rollback ledger (codex #440
r1/r3/r4). These tests fake ``subprocess.Popen`` — they pin the argv
(the five same-source flags plus the staging ``--out-dir``, and NONE of
the flags the serving-config binding chain owns), the UTF-8/pipe/group
kwargs, the lock gate, the staging/publish/rollback life cycle, every
outcome branch, and the single-target source pin.
"""

from __future__ import annotations

import contextlib
import os
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


class _FakeProc:
    """Popen stand-in. Materializes staging artifacts at construction —
    the way the real CLI writes them while running — then hands the
    outcome to ``communicate()``. ``hang_first`` makes the first
    ``communicate`` raise TimeoutExpired (the r5 timeout path)."""

    def __init__(
        self,
        cmd: list[str],
        files: dict[str, str] | None,
        returncode: int,
        stdout: str,
        stderr: str,
        hang_first: bool = False,
    ) -> None:
        self.pid = 4321
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self._hang_first = hang_first
        self._cmd = cmd
        self.communicate_timeouts: list[float | None] = []
        if files is not None:
            staging = _staging_of(cmd)
            staging.mkdir(parents=True, exist_ok=True)
            for name, content in files.items():
                (staging / name).write_text(content, encoding="utf-8")

    def communicate(
        self, timeout: float | None = None
    ) -> tuple[str, str]:
        self.communicate_timeouts.append(timeout)
        if self._hang_first:
            self._hang_first = False
            raise subprocess.TimeoutExpired(
                cmd=self._cmd, timeout=timeout or 0
            )
        return self._stdout, self._stderr

    def poll(self) -> int:
        return self.returncode


def _fake_popen(
    files: dict[str, str] | None,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
    hang_first: bool = False,
):
    """A ``subprocess.Popen`` stand-in factory; records created procs."""
    made: list[_FakeProc] = []

    def _popen(cmd: list[str], **kwargs: object) -> _FakeProc:
        proc = _FakeProc(cmd, files, returncode, stdout, stderr, hang_first)
        made.append(proc)
        return proc

    _popen.made = made  # type: ignore[attr-defined]
    return _popen


def _no_leftover_staging(out_dir: Path) -> bool:
    return not any(out_dir.glob(".staging-*"))


@contextlib.contextmanager
def _stub_lock(held: bool):
    yield held


def _patched_lock(held: bool = True):
    """Replace the runner's authoritative lock with a stub.

    Every generic test MUST patch it: the real ``hold_update_lock``
    derives the lock file from ``provider_uri`` — with the production
    default in ``_PARAMS`` that is the REAL provider sibling on the
    operator box (and a nonexistent drive on CI).
    """
    return mock.patch(
        "web.operator_ui.recommend_runner.hold_update_lock",
        lambda provider: _stub_lock(held),
    )


class CommandShapeTests(unittest.TestCase):
    def test_argv_is_exactly_the_cockpit_flags_plus_staging(self) -> None:
        fake = _fake_popen(_ARTIFACTS)
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch(
                "web.operator_ui.recommend_runner.OUT_DIR", Path(tmp)
            ), mock.patch(
                "web.operator_ui.recommend_runner.subprocess.Popen", fake
            ), _patched_lock():
                result = run_daily_recommend(**_PARAMS)
            self.assertEqual(result.kind, "ok")
            cmd = fake.made[0]._cmd  # type: ignore[attr-defined]
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
            # The run ceiling goes to communicate(), not Popen.
            self.assertEqual(
                fake.made[0].communicate_timeouts,  # type: ignore[attr-defined]
                [DEFAULT_TIMEOUT_S],
            )

    def test_utf8_pipe_and_process_group_kwargs_are_pinned(self) -> None:
        captured: dict = {}

        def _popen(cmd: list[str], **kwargs: object) -> _FakeProc:
            captured["kwargs"] = kwargs
            return _FakeProc(cmd, None, 1, "", "x")

        with mock.patch(
            "web.operator_ui.recommend_runner.subprocess.Popen", _popen
        ), _patched_lock():
            run_daily_recommend(**_PARAMS)
        kwargs = captured["kwargs"]
        self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
        self.assertIs(kwargs["stdout"], subprocess.PIPE)
        self.assertIs(kwargs["stderr"], subprocess.PIPE)
        self.assertTrue(kwargs["text"])
        self.assertEqual(kwargs["encoding"], "utf-8")
        self.assertEqual(kwargs["errors"], "replace")
        # cwd = repo root, matching a terminal run's anchor.
        self.assertEqual(kwargs["cwd"], str(PROJECT_ROOT))
        # Both pipe ends pinned: the child encodes UTF-8 too.
        self.assertEqual(kwargs["env"]["PYTHONIOENCODING"], "utf-8")
        # Own process group/session — the r5 tree-kill precondition.
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
                "web.operator_ui.recommend_runner.subprocess.Popen",
                _fake_popen(_ARTIFACTS, stdout="entry_date=2026-08-14"),
            ), _patched_lock():
                result = run_daily_recommend(**_PARAMS)
            self.assertEqual(result.kind, "ok")
            self.assertEqual(sorted(result.published), sorted(_ARTIFACTS))
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
                "web.operator_ui.recommend_runner.subprocess.Popen",
                _fake_popen(_ARTIFACTS),
            ), _patched_lock():
                result = run_daily_recommend(**_PARAMS)
            self.assertEqual(result.kind, "ok")
            self.assertEqual(prior.read_text(encoding="utf-8"), "OLD")

    def test_timeout_kills_the_tree_then_cleans_staging(self) -> None:
        # Simulate a kill mid-write_outputs: partial file in staging,
        # first communicate raises, the tree-kill succeeds, the drain
        # returns. Prior-day artifacts stay untouched.
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            out.mkdir(exist_ok=True)
            prior = out / "daily_recommendation_2026-08-13.json"
            prior.write_text("VALID-PRIOR-DAY", encoding="utf-8")
            fake = _fake_popen(
                {"daily_recommendation_2026-08-13.json": '{"torn":'},
                hang_first=True,
            )
            with mock.patch(
                "web.operator_ui.recommend_runner.OUT_DIR", out
            ), mock.patch(
                "web.operator_ui.recommend_runner.subprocess.Popen", fake
            ), mock.patch(
                "web.operator_ui.recommend_runner._kill_tree",
                return_value=None,
            ) as kill, _patched_lock():
                result = run_daily_recommend(**_PARAMS)
            self.assertEqual(result.kind, "timeout")
            kill.assert_called_once()
            self.assertIn("整棵进程树已终止", result.error)
            self.assertEqual(
                prior.read_text(encoding="utf-8"), "VALID-PRIOR-DAY"
            )
            self.assertTrue(_no_leftover_staging(out))

    def test_incomplete_tree_kill_keeps_staging_and_names_the_pid(
        self,
    ) -> None:
        # codex #440 r5: if descendants survive, deleting staging would
        # race live writers — keep it, name the pid, stay loud.
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            fake = _fake_popen(_ARTIFACTS, hang_first=True)
            with mock.patch(
                "web.operator_ui.recommend_runner.OUT_DIR", out
            ), mock.patch(
                "web.operator_ui.recommend_runner.subprocess.Popen", fake
            ), mock.patch(
                "web.operator_ui.recommend_runner._kill_tree",
                return_value="taskkill 退出码 1:access denied",
            ), _patched_lock():
                result = run_daily_recommend(**_PARAMS)
            self.assertEqual(result.kind, "timeout")
            self.assertIn("终止不完整", result.error)
            self.assertIn("4321", result.error)
            self.assertIn("保留", result.error)
            self.assertEqual(len(list(out.glob(".staging-*"))), 1)

    def test_failed_taskkill_is_incomplete_even_if_top_exited(self) -> None:
        # codex #440 r6: with a dead top pid, taskkill /T cannot walk
        # the tree (rc 128) while orphaned joblib workers may live on —
        # a nonzero taskkill must NEVER be inferred back to "tree dead"
        # from the top-level exit alone.
        from web.operator_ui.recommend_runner import _kill_tree

        proc = _FakeProc([], None, 0, "", "")  # poll() -> 0: top exited

        def _taskkill(cmd: list[str], **kwargs: object) -> SimpleNamespace:
            self.assertEqual(cmd[:3], ["taskkill", "/F", "/T"])
            return SimpleNamespace(
                returncode=128, stdout="", stderr="not found"
            )

        with mock.patch(
            "web.operator_ui.recommend_runner.subprocess.run", _taskkill
        ), mock.patch(
            "web.operator_ui.recommend_runner.sys.platform", "win32"
        ):
            note = _kill_tree(proc)  # type: ignore[arg-type]
        self.assertIsNotNone(note)
        assert note is not None
        self.assertIn("128", note)

    def test_nonzero_exit_cleans_staging(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            with mock.patch(
                "web.operator_ui.recommend_runner.OUT_DIR", out
            ), mock.patch(
                "web.operator_ui.recommend_runner.subprocess.Popen",
                _fake_popen(
                    {"daily_recommendation_2026-08-14.json": "half"},
                    returncode=1,
                    stderr="bundle is stale: 20 > 14",
                ),
            ), _patched_lock():
                result = run_daily_recommend(**_PARAMS)
            self.assertEqual(result.kind, "failed")
            self.assertTrue(_no_leftover_staging(out))
            self.assertEqual(list(out.iterdir()), [])

    def test_zero_exit_without_artifacts_is_a_contract_breach(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch(
                "web.operator_ui.recommend_runner.OUT_DIR", Path(tmp)
            ), mock.patch(
                "web.operator_ui.recommend_runner.subprocess.Popen",
                _fake_popen({}),  # creates the staging dir, writes nothing
            ), _patched_lock():
                result = run_daily_recommend(**_PARAMS)
            self.assertEqual(result.kind, "run_failed")
            self.assertIn("无产物", result.error)

    def test_mid_publish_failure_rolls_back_the_prior_set(self) -> None:
        # codex #440 r3: with a sequential publish, a failure at the
        # SECOND replace used to leave file #1 from the new run next to
        # file #2/#3 from the old run — and staging was no longer a
        # complete copy. The rollback ledger must restore the old set
        # exactly and return every new file to staging.
        real_replace = os.replace
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            out.mkdir(exist_ok=True)
            for name in _ARTIFACTS:
                (out / name).write_text("OLD-" + name, encoding="utf-8")

            def _flaky(src: str | Path, dst: str | Path) -> None:
                # Fail publishing the .json (second in sorted order) —
                # the .csv has already replaced its prior by then.
                if (
                    Path(dst).name.endswith(".json")
                    and Path(src).parent.name.startswith(".staging-")
                ):
                    raise OSError("destination locked")
                real_replace(src, dst)

            with mock.patch(
                "web.operator_ui.recommend_runner.OUT_DIR", out
            ), mock.patch(
                "web.operator_ui.recommend_runner.subprocess.Popen",
                _fake_popen(_ARTIFACTS),
            ), mock.patch(
                "web.operator_ui.recommend_runner.os.replace", _flaky
            ), _patched_lock():
                result = run_daily_recommend(**_PARAMS)

            self.assertEqual(result.kind, "run_failed")
            self.assertIn("已整体回滚", result.error)
            # The published set is the OLD run again, byte for byte.
            for name in _ARTIFACTS:
                self.assertEqual(
                    (out / name).read_text(encoding="utf-8"),
                    "OLD-" + name,
                )
            # Staging holds the complete NEW set (and no rollback dir).
            staging_dirs = list(out.glob(".staging-*"))
            self.assertEqual(len(staging_dirs), 1)
            self.assertEqual(
                sorted(p.name for p in staging_dirs[0].iterdir()),
                sorted(_ARTIFACTS),
            )

    def test_ledger_move_failure_rolls_back_instead_of_overwriting(
        self,
    ) -> None:
        # codex #440 r4: an exists() gate would fold a transient stat
        # error into "no prior" and overwrite the old artifact without
        # a ledger entry. The ledger move is attempted directly: a
        # non-FileNotFoundError failure there must trigger the full
        # rollback with the pair untouched — never a silent overwrite.
        real_replace = os.replace
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            out.mkdir(exist_ok=True)
            for name in _ARTIFACTS:
                (out / name).write_text("OLD-" + name, encoding="utf-8")

            def _flaky(src: str | Path, dst: str | Path) -> None:
                # The .json's LEDGER move (dst inside .prior) hits a
                # transient access error — not absence.
                if (
                    Path(dst).parent.name == ".prior"
                    and Path(dst).name.endswith(".json")
                ):
                    raise PermissionError("transient stat/access error")
                real_replace(src, dst)

            with mock.patch(
                "web.operator_ui.recommend_runner.OUT_DIR", out
            ), mock.patch(
                "web.operator_ui.recommend_runner.subprocess.Popen",
                _fake_popen(_ARTIFACTS),
            ), mock.patch(
                "web.operator_ui.recommend_runner.os.replace", _flaky
            ), _patched_lock():
                result = run_daily_recommend(**_PARAMS)

            self.assertEqual(result.kind, "run_failed")
            self.assertIn("已整体回滚", result.error)
            # Every published name is the OLD run again — including the
            # .json, whose prior was never silently overwritten.
            for name in _ARTIFACTS:
                self.assertEqual(
                    (out / name).read_text(encoding="utf-8"),
                    "OLD-" + name,
                )
            staging_dirs = list(out.glob(".staging-*"))
            self.assertEqual(len(staging_dirs), 1)
            self.assertEqual(
                sorted(p.name for p in staging_dirs[0].iterdir()),
                sorted(_ARTIFACTS),
            )

    def test_incomplete_rollback_reports_residuals_loudly(self) -> None:
        # If the rollback itself also fails, the torn state must be
        # named residual by residual — never summarized away.
        real_replace = os.replace
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            out.mkdir(exist_ok=True)
            for name in _ARTIFACTS:
                (out / name).write_text("OLD-" + name, encoding="utf-8")

            def _flaky(src: str | Path, dst: str | Path) -> None:
                src_p, dst_p = Path(src), Path(dst)
                publishing = src_p.parent.name.startswith(".staging-")
                unpublishing = dst_p.parent.name.startswith(".staging-")
                if publishing and dst_p.name.endswith(".json"):
                    raise OSError("destination locked")
                if unpublishing and dst_p.name.endswith(".csv"):
                    raise OSError("still locked")
                real_replace(src, dst)

            with mock.patch(
                "web.operator_ui.recommend_runner.OUT_DIR", out
            ), mock.patch(
                "web.operator_ui.recommend_runner.subprocess.Popen",
                _fake_popen(_ARTIFACTS),
            ), mock.patch(
                "web.operator_ui.recommend_runner.os.replace", _flaky
            ), _patched_lock():
                result = run_daily_recommend(**_PARAMS)

            self.assertEqual(result.kind, "run_failed")
            self.assertIn("残留", result.error)
            self.assertIn("(新文件仍在发布目录)", result.error)
            # The stuck name's prior must NOT clobber the new file (the
            # only copy left) — it stays in the ledger dir, said so.
            self.assertIn("(旧版本滞留在回滚目录)", result.error)
            self.assertEqual(
                (out / "daily_recommendation_2026-08-14.csv").read_text(
                    encoding="utf-8"
                ),
                _ARTIFACTS["daily_recommendation_2026-08-14.csv"],
            )
            # Staging (with the rollback dir) is preserved as evidence.
            self.assertEqual(len(list(out.glob(".staging-*"))), 1)

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
                "web.operator_ui.recommend_runner.subprocess.Popen",
                _fake_popen(_ARTIFACTS),
            ), mock.patch(
                "web.operator_ui.recommend_runner.os.replace",
                side_effect=OSError("cross-device or locked"),
            ), _patched_lock():
                result = run_daily_recommend(**_PARAMS)
            self.assertEqual(result.kind, "run_failed")
            self.assertIn("保留", result.error)
            staging_dirs = list(out.glob(".staging-*"))
            self.assertEqual(len(staging_dirs), 1)
            self.assertEqual(
                sorted(
                    p.name for p in staging_dirs[0].iterdir() if p.is_file()
                ),
                sorted(_ARTIFACTS),
            )


class OutcomeBranchTests(unittest.TestCase):
    def _run_with(self, fake: object) -> RecommendRunResult:
        with mock.patch(
            "web.operator_ui.recommend_runner.subprocess.Popen", fake
        ), _patched_lock():
            return run_daily_recommend(**_PARAMS)

    def test_exit_zero_is_ok_with_stdout_banner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch(
                "web.operator_ui.recommend_runner.OUT_DIR", Path(tmp)
            ):
                result = self._run_with(
                    _fake_popen(
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
            _fake_popen(
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
        def _popen(cmd: list[str], **kwargs: object) -> _FakeProc:
            raise OSError("permission denied")

        result = self._run_with(_popen)
        self.assertEqual(result.kind, "launch_failed")
        self.assertIn("无法启动解释器", result.error)

    def test_missing_script_is_run_failed_without_spawning(self) -> None:
        def _popen(cmd: list[str], **kwargs: object) -> _FakeProc:
            raise AssertionError("must not spawn when the script is gone")

        with mock.patch(
            "web.operator_ui.recommend_runner.RECOMMEND_SCRIPT",
            Path("/no/such/daily_recommend_script.py"),
        ), mock.patch(
            "web.operator_ui.recommend_runner.subprocess.Popen", _popen
        ):
            result = run_daily_recommend(**_PARAMS)
        self.assertEqual(result.kind, "run_failed")


class UpdateLockGateTests(unittest.TestCase):
    """codex #440 r2: the authoritative serialization is the updater's
    own provider lock — the status artifact is advisory only. The lock
    mirror's correctness is proven BIDIRECTIONALLY against the real
    ``src`` implementation (codex #440 r5)."""

    def test_busy_lock_refuses_without_spawning(self) -> None:
        def _popen(cmd: list[str], **kwargs: object) -> _FakeProc:
            raise AssertionError("must not spawn while the lock is held")

        with mock.patch(
            "web.operator_ui.recommend_runner.subprocess.Popen", _popen
        ), _patched_lock(held=False):
            result = run_daily_recommend(**_PARAMS)
        self.assertEqual(result.kind, "blocked_by_update")
        self.assertIn("单飞锁", result.error)

    def test_real_updater_lock_blocks_end_to_end(self) -> None:
        # No stubs on the lock here: the REAL src single-flight lock is
        # held (as the updater holds it), provider_uri points at a temp
        # provider, and the runner must refuse before spawning.
        from src.data_pipeline.single_flight import single_flight

        def _popen(cmd: list[str], **kwargs: object) -> _FakeProc:
            raise AssertionError("must not spawn while the updater runs")

        with tempfile.TemporaryDirectory() as tmp:
            provider = Path(tmp) / "prov"
            provider.mkdir()
            params = dict(_PARAMS, provider_uri=str(provider))
            with single_flight(provider), mock.patch(
                "web.operator_ui.recommend_runner.subprocess.Popen", _popen
            ):
                result = run_daily_recommend(**params)
        self.assertEqual(result.kind, "blocked_by_update")

    def test_web_holder_blocks_the_real_updater(self) -> None:
        # The REVERSE direction (codex #440 r5): while the web mirror
        # holds the lock, the real updater must refuse with its normal
        # AlreadyRunningError — and acquire cleanly after release.
        from src.data_pipeline.single_flight import (
            AlreadyRunningError,
            single_flight,
        )
        from web.operator_ui.provider_lock import hold_update_lock

        with tempfile.TemporaryDirectory() as tmp:
            provider = Path(tmp) / "prov"
            provider.mkdir()
            with hold_update_lock(provider) as held:
                self.assertTrue(held)
                with self.assertRaises(AlreadyRunningError):
                    with single_flight(provider):
                        pass
            # Released on exit — the updater acquires cleanly now.
            with single_flight(provider):
                pass


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
