"""Single-flight lock tests (阶段5 PR-P). No real fetch/build — pure fs + OS lock.

The lock is an OS advisory lock (``fcntl.flock`` / ``msvcrt.locking``): the kernel owns
it and releases it on process exit, so there is no stale-lock / reclaim / pid logic to
test. These pin the policy: a second acquirer for the same provider is refused, distinct
providers do not contend, the lock is released after the body (even on a raise), and the
CLI maps a refusal to EXIT_ALREADY_RUNNING without running any stage.
"""

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data_pipeline import single_flight as sf  # noqa: E402
from src.data_pipeline.daily_update import (  # noqa: E402
    EXIT_ALREADY_RUNNING,
    EXIT_CONFIG,
)
from src.data_pipeline.single_flight import (  # noqa: E402
    AlreadyRunningError,
    lock_path_for,
    single_flight,
)


class LockPathTests(unittest.TestCase):
    def test_lock_is_a_sibling_not_a_child(self) -> None:
        # The swap renames the provider dir whole; a lock INSIDE it would be renamed
        # away mid-run, so it must be a sibling.
        prov = Path("/data/my_cn_data_pit")
        lock = lock_path_for(prov)
        self.assertEqual(lock.parent, prov.parent)
        self.assertNotIn(prov.name, [p.name for p in lock.parents])
        self.assertTrue(lock.name.startswith(prov.name))


class LockPrimitiveTests(unittest.TestCase):
    def test_exclusive_lock_excludes_a_second_handle_then_releases(self) -> None:
        # Pin the OS primitive directly: two handles to the same lock file, only one can
        # hold the exclusive lock; releasing it lets the other take it.
        with tempfile.TemporaryDirectory() as t:
            path = Path(t) / "x.lock"
            fd1 = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o644)
            fd2 = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o644)
            try:
                self.assertTrue(sf._try_lock_exclusive(fd1))
                self.assertFalse(sf._try_lock_exclusive(fd2))  # held by fd1
                sf._unlock(fd1)
                self.assertTrue(sf._try_lock_exclusive(fd2))   # released -> fd2 gets it
                sf._unlock(fd2)
            finally:
                os.close(fd1)
                os.close(fd2)


class SingleFlightTests(unittest.TestCase):
    def test_acquire_then_release_lets_a_later_run_acquire(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            prov = Path(t) / "prov"
            with single_flight(prov):
                self.assertTrue(lock_path_for(prov).exists())
            # released -> a later (serial) run takes it cleanly
            with single_flight(prov):
                pass

    def test_same_provider_concurrent_acquire_is_refused(self) -> None:
        # A second acquirer (a new open + non-blocking lock) for the SAME provider while
        # the first is held is refused — the OS lock excludes across open descriptions.
        with tempfile.TemporaryDirectory() as t:
            prov = Path(t) / "prov"
            with single_flight(prov):
                with self.assertRaises(AlreadyRunningError):
                    with single_flight(prov):
                        self.fail("must not enter while the lock is held")

    def test_distinct_providers_do_not_contend(self) -> None:
        # Single-flight is per-provider (a normative SHALL): different providers never
        # block each other.
        with tempfile.TemporaryDirectory() as t:
            prov_a, prov_b = Path(t) / "a", Path(t) / "b"
            with single_flight(prov_a):
                with single_flight(prov_b):  # must NOT raise
                    self.assertNotEqual(lock_path_for(prov_a), lock_path_for(prov_b))

    def test_lock_released_when_body_raises(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            prov = Path(t) / "prov"
            with self.assertRaises(RuntimeError):
                with single_flight(prov):
                    raise RuntimeError("boom")
            # released despite the raise -> a later run acquires
            with single_flight(prov):
                pass

    def test_parent_dir_is_created_on_fresh_machine(self) -> None:
        # Fresh-machine bootstrap: the provider dir's parent may not exist yet. Acquiring
        # the lock must create it, not crash with a bare OSError.
        with tempfile.TemporaryDirectory() as t:
            prov = Path(t) / "deep" / "nested" / "prov"
            self.assertFalse(prov.parent.exists())
            with single_flight(prov):
                self.assertTrue(lock_path_for(prov).exists())

    def test_lock_file_persists_between_runs(self) -> None:
        # Deliberate: the lock file is NOT unlinked (unlinking would break the advisory
        # lock — a deleted-but-open inode no longer excludes a re-created path).
        with tempfile.TemporaryDirectory() as t:
            prov = Path(t) / "prov"
            with single_flight(prov):
                pass
            self.assertTrue(lock_path_for(prov).exists())  # left on disk, reused

    def test_shared_raw_input_serializes_distinct_providers(self) -> None:
        # codex P2: two runs with DIFFERENT providers but a SHARED raw input (tushare
        # dump / registry) would clobber each other's fixed-name temp files — so they
        # must serialize even though their providers differ.
        with tempfile.TemporaryDirectory() as t:
            shared = Path(t) / "tushare"
            with single_flight(Path(t) / "provA", shared):
                with self.assertRaises(AlreadyRunningError):
                    with single_flight(Path(t) / "provB", shared):
                        self.fail("a shared raw input must serialize distinct providers")

    def test_fully_disjoint_runs_do_not_contend(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            with single_flight(Path(t) / "provA", Path(t) / "tushareA"):
                with single_flight(Path(t) / "provB", Path(t) / "tushareB"):
                    pass  # no shared resource -> both run

    def test_partial_acquire_is_released_on_contention(self) -> None:
        # When a later lock in the set is held, the earlier ones we took are released —
        # no leak that would wedge a subsequent run. ("prov" sorts before "tushare", so
        # the prov lock is acquired first, then the shared lock fails.)
        with tempfile.TemporaryDirectory() as t:
            prov, shared = Path(t) / "prov", Path(t) / "tushare"
            with single_flight(shared):  # hold ONLY the shared lock
                with self.assertRaises(AlreadyRunningError):
                    with single_flight(prov, shared):  # contends on the shared lock
                        self.fail()
            # the shared holder released; prov was never wedged -> both acquire now
            with single_flight(prov, shared):
                pass

    def test_requires_at_least_one_resource(self) -> None:
        with self.assertRaises(ValueError):
            with single_flight():
                self.fail("empty resource set must be rejected")


class CliSingleFlightTests(unittest.TestCase):
    def _argv(self, tmp: Path, prov: Path, *extra: str) -> list[str]:
        return [
            "--tushare-dir", str(tmp / "raw"),
            "--provider-dir", str(prov),
            "--delisted-registry", str(tmp / "raw" / "reg.parquet"),
            "--reference-cases", str(tmp / "ref.yaml"),
            *extra,
        ]

    def test_cli_returns_already_running_and_runs_no_stage(self) -> None:
        from scripts.daily_update import main
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            prov = tmp / "prov"
            with single_flight(prov):  # hold the lock for this provider
                with mock.patch("scripts.daily_update.run_daily_update") as run:
                    rc = main(self._argv(tmp, prov))
                self.assertEqual(rc, EXIT_ALREADY_RUNNING)
                run.assert_not_called()  # the lock refusal preempts every stage

    def test_cli_dry_run_is_exempt_from_the_lock(self) -> None:
        from scripts.daily_update import main
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            prov = tmp / "prov"
            with single_flight(prov):  # lock held...
                with mock.patch("scripts.daily_update.run_daily_update",
                                return_value=0) as run:
                    rc = main(self._argv(tmp, prov, "--dry-run"))
                # ...but a dry-run mutates nothing, so it is NOT blocked
                self.assertEqual(rc, 0)
                run.assert_called_once()

    def test_cli_lock_setup_failure_returns_a_defined_exit_code(self) -> None:
        # An unwritable / unreachable lock path is a SETUP error, not contention: the CLI
        # must return a defined code (EXIT_CONFIG), never crash with an undefined exit 1.
        from scripts.daily_update import main
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            prov = tmp / "prov"
            with mock.patch.object(sf.os, "open",
                                   side_effect=PermissionError("read-only fs")):
                rc = main(self._argv(tmp, prov))
            self.assertEqual(rc, EXIT_CONFIG)


_CHILD = (
    "import sys, time\n"
    "from pathlib import Path\n"
    "sys.path.insert(0, sys.argv[3])\n"
    "from src.data_pipeline.single_flight import single_flight\n"
    "with single_flight(Path(sys.argv[1])):\n"
    "    Path(sys.argv[2]).write_text('up')\n"
    "    time.sleep(30)\n"
)


class CrossProcessTests(unittest.TestCase):
    """The true witness: TWO OS processes. A second process is excluded while the first
    holds the lock, and the kernel releases the lock when the holder is KILLED (the
    headline property a pidfile-with-finally would NOT provide)."""

    def test_exclusion_across_processes_and_release_on_kill(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            prov, ready = Path(t) / "prov", Path(t) / "ready"
            child = subprocess.Popen(
                [sys.executable, "-c", _CHILD, str(prov), str(ready), str(PROJECT_ROOT)],
            )
            try:
                for _ in range(100):  # wait up to ~10s for the child to take the lock
                    if ready.exists():
                        break
                    time.sleep(0.1)
                self.assertTrue(ready.exists(), "child never acquired the lock")
                # cross-PROCESS exclusion while the child holds it
                with self.assertRaises(AlreadyRunningError):
                    with single_flight(prov):
                        self.fail("must be excluded across processes")
            finally:
                child.kill()
                child.wait(timeout=10)
            # the kernel released the lock when the child died -> a later acquire wins
            # (poll: Windows may release the handle slightly asynchronously)
            acquired = False
            for _ in range(100):
                try:
                    with single_flight(prov):
                        acquired = True
                    break
                except AlreadyRunningError:
                    time.sleep(0.1)
            self.assertTrue(acquired, "lock not released after the holder was killed")


if __name__ == "__main__":
    unittest.main()
