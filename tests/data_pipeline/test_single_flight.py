"""Single-flight lock tests (阶段5 PR-P). No real fetch/build — pure fs + pid probe.

The OS guarantees the mutual exclusion (``O_EXCL`` + an atomic ``os.replace`` claim of
a stale lock); these tests pin the policy on TOP of it: a live holder is refused, a
confirmed-dead holder is reclaimed (and exactly one of two reclaimers wins), an unknown
/ unreadable holder is treated as held (fail-closed), the lock is always released, and
the CLI maps a refusal to EXIT_ALREADY_RUNNING without running any stage.
"""

import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data_pipeline import single_flight as sf  # noqa: E402
from src.data_pipeline.daily_update import EXIT_ALREADY_RUNNING  # noqa: E402
from src.data_pipeline.single_flight import (  # noqa: E402
    AlreadyRunningError,
    _pid_is_alive,
    lock_path_for,
    single_flight,
)

_PROBE = "src.data_pipeline.single_flight._pid_is_alive"


class LockPathTests(unittest.TestCase):
    def test_lock_is_a_sibling_not_a_child(self) -> None:
        # The swap renames the provider dir whole; a lock INSIDE it would be renamed
        # away mid-run, so it must be a sibling.
        prov = Path("/data/my_cn_data_pit")
        lock = lock_path_for(prov)
        self.assertEqual(lock.parent, prov.parent)
        self.assertNotIn(prov.name, [p.name for p in lock.parents])
        self.assertTrue(lock.name.startswith(prov.name))


class SingleFlightTests(unittest.TestCase):
    def test_acquire_writes_pid_then_releases(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            prov = Path(t) / "prov"
            lock = lock_path_for(prov)
            with single_flight(prov):
                self.assertTrue(lock.exists())
                self.assertEqual(int(lock.read_text(encoding="utf-8")), os.getpid())
            self.assertFalse(lock.exists())  # released on exit

    def test_live_holder_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            prov = Path(t) / "prov"
            lock_path_for(prov).write_text(str(os.getpid()), encoding="utf-8")  # alive
            with self.assertRaises(AlreadyRunningError):
                with single_flight(prov):
                    self.fail("must not enter while a live run holds the lock")
            self.assertTrue(lock_path_for(prov).exists())  # never stolen

    def test_stale_holder_is_reclaimed(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            prov = Path(t) / "prov"
            lock_path_for(prov).write_text("424242", encoding="utf-8")
            with mock.patch(_PROBE, return_value=False):  # confirmed dead
                with single_flight(prov):
                    self.assertEqual(
                        int(lock_path_for(prov).read_text(encoding="utf-8")),
                        os.getpid(),  # reclaimed: now records OUR pid
                    )
            self.assertFalse(lock_path_for(prov).exists())

    def test_reclaim_loser_is_refused(self) -> None:
        # Two reclaimers race a dead-pid lock: the loser's os.replace finds the source
        # already gone. Simulate by having os.replace raise AND leave a LIVE-pid lock
        # in place (the winner's fresh lock). The loser must refuse, never enter.
        with tempfile.TemporaryDirectory() as t:
            prov = Path(t) / "prov"
            path = lock_path_for(prov)
            path.write_text("424242", encoding="utf-8")

            def winner_took_it(src: str, dst: str) -> None:
                Path(src).write_text(str(os.getpid()), encoding="utf-8")  # winner's pid
                raise FileNotFoundError("source already reclaimed by another run")

            with mock.patch(_PROBE, side_effect=lambda pid: pid == os.getpid()), \
                    mock.patch.object(sf.os, "replace", side_effect=winner_took_it):
                with self.assertRaises(AlreadyRunningError):
                    with single_flight(prov):
                        self.fail("reclaim loser must not enter the body")

    def test_unknown_liveness_is_treated_as_held(self) -> None:
        # Probe failure (tasklist timeout / OSError) -> None -> fail-closed: NEVER
        # steal a lock we cannot prove is abandoned.
        with tempfile.TemporaryDirectory() as t:
            prov = Path(t) / "prov"
            lock_path_for(prov).write_text("424242", encoding="utf-8")
            with mock.patch(_PROBE, return_value=None):
                with self.assertRaises(AlreadyRunningError):
                    with single_flight(prov):
                        self.fail("unknown liveness must not be reclaimed")
            self.assertTrue(lock_path_for(prov).exists())  # left intact

    def test_garbage_lock_is_treated_as_held(self) -> None:
        # An unreadable / non-integer pid -> holder None -> fail-closed (held).
        with tempfile.TemporaryDirectory() as t:
            prov = Path(t) / "prov"
            lock_path_for(prov).write_text("not-a-pid", encoding="utf-8")
            with self.assertRaises(AlreadyRunningError):
                with single_flight(prov):
                    self.fail("garbage lock must not be reclaimed")

    def test_empty_lock_is_treated_as_held(self) -> None:
        # The crash-window artifact: O_EXCL created the file but the pid was not yet
        # written. A concurrent run sees holder None -> fail-closed.
        with tempfile.TemporaryDirectory() as t:
            prov = Path(t) / "prov"
            lock_path_for(prov).write_text("", encoding="utf-8")
            with self.assertRaises(AlreadyRunningError):
                with single_flight(prov):
                    self.fail("empty (mid-acquire) lock must not be reclaimed")
            self.assertTrue(lock_path_for(prov).exists())

    def test_distinct_providers_do_not_contend(self) -> None:
        # Single-flight is per-provider (a normative SHALL): different providers never
        # block each other.
        with tempfile.TemporaryDirectory() as t:
            prov_a, prov_b = Path(t) / "a", Path(t) / "b"
            with single_flight(prov_a):
                with single_flight(prov_b):  # must NOT raise
                    self.assertTrue(lock_path_for(prov_a).exists())
                    self.assertTrue(lock_path_for(prov_b).exists())
                    self.assertNotEqual(lock_path_for(prov_a), lock_path_for(prov_b))

    def test_parent_dir_is_created_on_fresh_machine(self) -> None:
        # Fresh-machine bootstrap: the provider dir's parent may not exist yet. The
        # lock acquire must create it, not crash with a bare OSError.
        with tempfile.TemporaryDirectory() as t:
            prov = Path(t) / "deep" / "nested" / "prov"  # parent dirs absent
            self.assertFalse(prov.parent.exists())
            with single_flight(prov):
                self.assertTrue(lock_path_for(prov).exists())

    def test_releases_even_when_body_raises(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            prov = Path(t) / "prov"
            with self.assertRaises(RuntimeError):
                with single_flight(prov):
                    raise RuntimeError("boom")
            self.assertFalse(lock_path_for(prov).exists())  # finally released it

    def test_release_leaves_a_foreign_pid_lock_intact(self) -> None:
        # Defensive: if the lock somehow records a foreign pid on exit (should not
        # happen while we hold it), do NOT delete it — never clobber another run's lock.
        with tempfile.TemporaryDirectory() as t:
            prov = Path(t) / "prov"
            with single_flight(prov):
                lock_path_for(prov).write_text("999999", encoding="utf-8")  # foreign
            self.assertTrue(lock_path_for(prov).exists())  # left intact
            self.assertEqual(lock_path_for(prov).read_text(encoding="utf-8"), "999999")


class PidLivenessTests(unittest.TestCase):
    """Direct tests of the local _pid_is_alive probe (every other test mocks it out)."""

    def test_nonpositive_pid_is_confirmed_dead(self) -> None:
        self.assertIs(_pid_is_alive(0), False)
        self.assertIs(_pid_is_alive(-1), False)

    def _win(self, returncode: int, stdout: str):
        return types.SimpleNamespace(returncode=returncode, stdout=stdout)

    def test_windows_tasklist_match_is_alive(self) -> None:
        out = '"python.exe","123","Console","1","14,660 K"\r\n'
        with mock.patch.object(sf.platform, "system", return_value="Windows"), \
                mock.patch.object(sf.subprocess, "run", return_value=self._win(0, out)):
            self.assertIs(_pid_is_alive(123), True)

    def test_windows_tasklist_no_match_is_dead(self) -> None:
        out = 'INFO: No tasks are running which match the specified criteria.\r\n'
        with mock.patch.object(sf.platform, "system", return_value="Windows"), \
                mock.patch.object(sf.subprocess, "run", return_value=self._win(0, out)):
            self.assertIs(_pid_is_alive(123), False)

    def test_windows_tasklist_bad_rc_is_unknown(self) -> None:
        with mock.patch.object(sf.platform, "system", return_value="Windows"), \
                mock.patch.object(sf.subprocess, "run", return_value=self._win(1, "")):
            self.assertIsNone(_pid_is_alive(123))

    def test_windows_tasklist_probe_error_is_unknown(self) -> None:
        with mock.patch.object(sf.platform, "system", return_value="Windows"), \
                mock.patch.object(sf.subprocess, "run",
                                  side_effect=subprocess.TimeoutExpired("tasklist", 15)):
            self.assertIsNone(_pid_is_alive(123))

    def test_posix_kill_success_is_alive(self) -> None:
        with mock.patch.object(sf.platform, "system", return_value="Linux"), \
                mock.patch.object(sf.os, "kill", return_value=None):
            self.assertIs(_pid_is_alive(123), True)

    def test_posix_no_such_process_is_dead(self) -> None:
        with mock.patch.object(sf.platform, "system", return_value="Linux"), \
                mock.patch.object(sf.os, "kill", side_effect=ProcessLookupError):
            self.assertIs(_pid_is_alive(123), False)

    def test_posix_permission_error_is_alive(self) -> None:
        with mock.patch.object(sf.platform, "system", return_value="Linux"), \
                mock.patch.object(sf.os, "kill", side_effect=PermissionError):
            self.assertIs(_pid_is_alive(123), True)


class CliSingleFlightTests(unittest.TestCase):
    def test_cli_returns_already_running_and_runs_no_stage(self) -> None:
        from scripts.daily_update import main
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            prov = tmp / "prov"
            lock_path_for(prov).write_text(str(os.getpid()), encoding="utf-8")  # live
            argv = [
                "--tushare-dir", str(tmp / "raw"),
                "--provider-dir", str(prov),
                "--delisted-registry", str(tmp / "raw" / "reg.parquet"),
                "--reference-cases", str(tmp / "ref.yaml"),
            ]
            with mock.patch("scripts.daily_update.run_daily_update") as run:
                rc = main(argv)
            self.assertEqual(rc, EXIT_ALREADY_RUNNING)
            run.assert_not_called()

    def test_cli_dry_run_is_exempt_and_leaves_the_lock_untouched(self) -> None:
        from scripts.daily_update import main
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            prov = tmp / "prov"
            lock_path_for(prov).write_text(str(os.getpid()), encoding="utf-8")  # live
            argv = [
                "--tushare-dir", str(tmp / "raw"),
                "--provider-dir", str(prov),
                "--delisted-registry", str(tmp / "raw" / "reg.parquet"),
                "--reference-cases", str(tmp / "ref.yaml"),
                "--dry-run",
            ]
            with mock.patch("scripts.daily_update.run_daily_update",
                            return_value=0) as run:
                rc = main(argv)
            self.assertEqual(rc, 0)  # dry-run mutates nothing -> not blocked
            run.assert_called_once()
            # the held lock is untouched (dry-run never acquired/released it)
            self.assertEqual(
                lock_path_for(prov).read_text(encoding="utf-8"), str(os.getpid()),
            )


if __name__ == "__main__":
    unittest.main()
