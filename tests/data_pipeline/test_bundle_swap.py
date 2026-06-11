"""Crash-state tests for the atomic bundle swap (P3-6a red lines).

Every test constructs an on-disk crash state in a temp dir and asserts the next
startup detects + repairs it (or leaves healthy state alone). The OLD bundle is
never destroyed by anything short of a completed, validated swap.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data_pipeline.bundle_swap import (  # noqa: E402
    BundleSwapError,
    bak_dir,
    check_and_repair,
    new_dir,
    swap,
)


def _mk_bundle(path: Path, marker: str) -> None:
    """A minimal 'bundle': a dir with one marker file identifying its build."""
    path.mkdir(parents=True)
    (path / "calendars.txt").write_text(marker, encoding="utf-8")


def _marker(path: Path) -> str:
    return (path / "calendars.txt").read_text(encoding="utf-8")


class SwapTests(unittest.TestCase):

    def test_swap_promotes_new_and_keeps_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            provider = Path(tmp) / "provider"
            _mk_bundle(provider, "OLD")
            _mk_bundle(new_dir(provider), "NEW")
            swap(provider)
            self.assertEqual(_marker(provider), "NEW")
            self.assertEqual(_marker(bak_dir(provider)), "OLD")  # rollback kept
            self.assertFalse(new_dir(provider).exists())

    def test_first_deploy_without_existing_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            provider = Path(tmp) / "provider"
            _mk_bundle(new_dir(provider), "NEW")
            swap(provider)
            self.assertEqual(_marker(provider), "NEW")
            self.assertFalse(bak_dir(provider).exists())

    def test_swap_clears_previous_backup_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            provider = Path(tmp) / "provider"
            _mk_bundle(provider, "OLD")
            _mk_bundle(new_dir(provider), "NEW")
            _mk_bundle(bak_dir(provider), "ANCIENT")
            swap(provider)
            self.assertEqual(_marker(provider), "NEW")
            self.assertEqual(_marker(bak_dir(provider)), "OLD")

    def test_swap_without_staged_bundle_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            provider = Path(tmp) / "provider"
            _mk_bundle(provider, "OLD")
            with self.assertRaisesRegex(BundleSwapError, "does not exist"):
                swap(provider)
            self.assertEqual(_marker(provider), "OLD")  # untouched


class CrashStateTests(unittest.TestCase):
    """RED LINE: the three crash-injection states, each detected + resolved."""

    def test_crash_after_build_before_swap_removes_stale_new(self) -> None:
        # Crash state 1: build finished (or validate failed) and the run died —
        # live bundle + stale .new. Repair removes the unproven .new; the live
        # bundle is byte-identical untouched.
        with tempfile.TemporaryDirectory() as tmp:
            provider = Path(tmp) / "provider"
            _mk_bundle(provider, "OLD")
            _mk_bundle(new_dir(provider), "UNPROVEN")
            self.assertEqual(check_and_repair(provider), "removed-stale-new")
            self.assertEqual(_marker(provider), "OLD")
            self.assertFalse(new_dir(provider).exists())

    def test_crash_between_stage1_and_stage2_completes_swap(self) -> None:
        # Crash state 2: stage 1 ran (live -> .bak) then death — no live bundle,
        # .bak + .new present. Stage 1 having run PROVES validation passed, so
        # repair completes stage 2; the backup is preserved.
        with tempfile.TemporaryDirectory() as tmp:
            provider = Path(tmp) / "provider"
            _mk_bundle(bak_dir(provider), "OLD")
            _mk_bundle(new_dir(provider), "NEW")
            self.assertEqual(
                check_and_repair(provider), "completed-interrupted-swap",
            )
            self.assertEqual(_marker(provider), "NEW")
            self.assertEqual(_marker(bak_dir(provider)), "OLD")

    def test_crash_after_stage2_is_healthy(self) -> None:
        # Crash state 3: both renames completed before death — live + .bak is
        # the normal post-swap state; repair must NOT touch anything.
        with tempfile.TemporaryDirectory() as tmp:
            provider = Path(tmp) / "provider"
            _mk_bundle(provider, "NEW")
            _mk_bundle(bak_dir(provider), "OLD")
            self.assertEqual(check_and_repair(provider), "healthy")
            self.assertEqual(_marker(provider), "NEW")
            self.assertEqual(_marker(bak_dir(provider)), "OLD")

    def test_mid_swap_crash_injection_then_repair(self) -> None:
        # True crash injection: stage 1 succeeds, stage 2 raises (process dies).
        # The next startup's check_and_repair completes the swap.
        with tempfile.TemporaryDirectory() as tmp:
            provider = Path(tmp) / "provider"
            _mk_bundle(provider, "OLD")
            _mk_bundle(new_dir(provider), "NEW")
            real_rename = Path.rename
            calls = {"n": 0}

            def dying_rename(self: Path, target):  # type: ignore[no-untyped-def]
                calls["n"] += 1
                if calls["n"] == 2:  # stage 2
                    raise OSError("simulated crash between the two renames")
                return real_rename(self, target)

            with patch.object(Path, "rename", dying_rename):
                with self.assertRaises(OSError):
                    swap(provider)
            # Mid-swap state on disk: no live bundle, .bak + .new present.
            self.assertFalse(provider.exists())
            self.assertEqual(_marker(bak_dir(provider)), "OLD")
            self.assertEqual(_marker(new_dir(provider)), "NEW")
            # Next startup repairs it.
            self.assertEqual(
                check_and_repair(provider), "completed-interrupted-swap",
            )
            self.assertEqual(_marker(provider), "NEW")

    def test_backup_only_state_restores_old_bundle(self) -> None:
        # Degenerate state: only .bak survives. Repair restores it so the
        # system has a working bundle again.
        with tempfile.TemporaryDirectory() as tmp:
            provider = Path(tmp) / "provider"
            _mk_bundle(bak_dir(provider), "OLD")
            self.assertEqual(check_and_repair(provider), "restored-from-backup")
            self.assertEqual(_marker(provider), "OLD")

    def test_orphan_new_without_any_bundle_is_removed(self) -> None:
        # First-ever build died before its swap: .new only, and stage 1 never
        # ran so validation CANNOT be proven — remove, never auto-promote.
        with tempfile.TemporaryDirectory() as tmp:
            provider = Path(tmp) / "provider"
            _mk_bundle(new_dir(provider), "UNPROVEN")
            self.assertEqual(check_and_repair(provider), "removed-stale-new")
            self.assertFalse(provider.exists())
            self.assertFalse(new_dir(provider).exists())

    def test_dry_run_reports_but_never_mutates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            provider = Path(tmp) / "provider"
            _mk_bundle(bak_dir(provider), "OLD")
            _mk_bundle(new_dir(provider), "NEW")
            self.assertEqual(
                check_and_repair(provider, dry_run=True),
                "completed-interrupted-swap",
            )
            # Nothing moved.
            self.assertFalse(provider.exists())
            self.assertTrue(bak_dir(provider).exists())
            self.assertTrue(new_dir(provider).exists())


if __name__ == "__main__":
    unittest.main()
