"""Tests for ``scripts/cleanup_output.py``.

The script does filesystem inventory + optional recursive delete.
Tests use ``tmp_path`` so they're hermetic; we never touch the real
``output/``.
"""

from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# scripts/ doesn't import normally — load it as a module.
from scripts.cleanup_output import (  # noqa: E402
    _human_size,
    discover_run_dirs,
    select_candidates,
)


def _seed_run(tmp_path: Path, name: str, *, files: dict[str, int],
              mtime_offset_days: float = 0.0) -> Path:
    """Create ``tmp_path/name/<filenames>`` with given sizes and set
    each file's mtime to ``now - mtime_offset_days``."""
    run_dir = tmp_path / name
    run_dir.mkdir()
    now = time.time()
    for filename, size in files.items():
        path = run_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\0" * size)
        target_mtime = now - mtime_offset_days * 86400
        os.utime(path, (target_mtime, target_mtime))
    return run_dir


# ---------------------------------------------------------------------------
# _human_size
# ---------------------------------------------------------------------------


class HumanSizeTests(unittest.TestCase):
    def test_bytes(self):
        self.assertEqual(_human_size(0), "0 B")
        self.assertEqual(_human_size(512), "512 B")

    def test_kb(self):
        self.assertEqual(_human_size(2048), "2.0 KB")

    def test_mb(self):
        self.assertEqual(_human_size(5 * 1024 * 1024), "5.0 MB")

    def test_gb(self):
        self.assertEqual(_human_size(int(2.5 * 1024**3)), "2.5 GB")

    def test_negative_returns_zero(self):
        self.assertEqual(_human_size(-1), "0 B")


# ---------------------------------------------------------------------------
# discover_run_dirs
# ---------------------------------------------------------------------------


class DiscoverRunDirsTests(unittest.TestCase):
    def test_missing_root_returns_empty(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            ghost = Path(td) / "does_not_exist"
            self.assertEqual(discover_run_dirs(ghost), [])

    def test_lists_subdirs_with_aggregated_size_and_mtime(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            root = Path(td)
            _seed_run(root, "walk_forward", files={
                "fold_00_report.json": 1024,
                "fold_00_predictions.pkl": 10 * 1024 * 1024,
            })
            _seed_run(root, "walk_forward_mined", files={
                "fold_00_report.json": 2048,
            })
            (root / "stray_file.txt").write_text("hi")  # not a dir

            dirs = discover_run_dirs(root)
            self.assertEqual(
                sorted(d.path.name for d in dirs),
                ["walk_forward", "walk_forward_mined"],
            )
            by_name = {d.path.name: d for d in dirs}
            self.assertEqual(
                by_name["walk_forward"].size_bytes,
                1024 + 10 * 1024 * 1024,
            )
            self.assertEqual(by_name["walk_forward_mined"].size_bytes, 2048)

    def test_include_glob_filters(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            root = Path(td)
            _seed_run(root, "walk_forward", files={"a.json": 1})
            _seed_run(root, "operator_ui", files={"b.json": 1})
            dirs = discover_run_dirs(root, include="walk_forward*")
            self.assertEqual([d.path.name for d in dirs], ["walk_forward"])

    def test_empty_subdir_listed_with_zero_size(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            root = Path(td)
            (root / "empty_run").mkdir()
            dirs = discover_run_dirs(root)
            self.assertEqual(len(dirs), 1)
            self.assertEqual(dirs[0].size_bytes, 0)

    def test_symlinked_run_dir_skipped(self):
        """Codex P2 on PR #168: directory symlinks satisfy
        ``Path.is_dir()`` but crash ``shutil.rmtree`` mid-cleanup.
        Skipping symlinks entirely keeps the inventory + cleanup
        loop safe — the operator can ``rm`` the link manually
        if they want to."""
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as td:
            root = Path(td)
            real = _seed_run(root, "real_run", files={"a.json": 1024})
            link = root / "linked_run"
            try:
                link.symlink_to(real, target_is_directory=True)
            except (OSError, NotImplementedError):
                # Windows without symlink privilege — skip rather
                # than fail; the regression is verified on platforms
                # where symlinks are creatable.
                self.skipTest(
                    "symlinks not creatable on this platform/privilege "
                    "level"
                )
            dirs = discover_run_dirs(root)
            self.assertEqual([d.path.name for d in dirs], ["real_run"])


# ---------------------------------------------------------------------------
# select_candidates
# ---------------------------------------------------------------------------


class SelectCandidatesTests(unittest.TestCase):
    def _seed_three(self, root: Path):
        # Build three runs spaced 5 / 20 / 60 days back, each with
        # a single 1KB file so size != 0.
        _seed_run(root, "recent", files={"a.json": 1024}, mtime_offset_days=5)
        _seed_run(root, "older", files={"b.json": 1024}, mtime_offset_days=20)
        _seed_run(root, "ancient", files={"c.json": 1024}, mtime_offset_days=60)
        return discover_run_dirs(root)

    def test_no_filters_all_candidates(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            dirs = self._seed_three(Path(td))
            cands = select_candidates(dirs)
            self.assertEqual(
                sorted(c.path.name for c in cands),
                ["ancient", "older", "recent"],
            )

    def test_older_than_30_days_picks_ancient_only(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            dirs = self._seed_three(Path(td))
            cands = select_candidates(dirs, older_than_days=30)
            self.assertEqual([c.path.name for c in cands], ["ancient"])

    def test_keep_last_2_keeps_recent_and_older(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            dirs = self._seed_three(Path(td))
            cands = select_candidates(dirs, keep_last=2)
            # recent + older are kept; ancient is candidate.
            self.assertEqual([c.path.name for c in cands], ["ancient"])

    def test_filters_combine_via_and(self):
        """``older_than=15`` flags ``older`` + ``ancient``;
        ``keep_last=2`` keeps the two newest (``recent`` + ``older``).
        Intersection: just ``ancient``."""
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            dirs = self._seed_three(Path(td))
            cands = select_candidates(dirs, older_than_days=15, keep_last=2)
            self.assertEqual([c.path.name for c in cands], ["ancient"])

    def test_keep_last_zero_keeps_nothing(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            dirs = self._seed_three(Path(td))
            cands = select_candidates(dirs, keep_last=0)
            self.assertEqual(len(cands), 3)

    def test_keep_last_larger_than_count_keeps_all(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            dirs = self._seed_three(Path(td))
            cands = select_candidates(dirs, keep_last=99)
            self.assertEqual(cands, [])


# ---------------------------------------------------------------------------
# main() — dry-run + execute
# ---------------------------------------------------------------------------


class MainCliTests(unittest.TestCase):
    def test_dry_run_does_not_delete(self):
        from tempfile import TemporaryDirectory

        from scripts.cleanup_output import main

        with TemporaryDirectory() as td:
            root = Path(td)
            run = _seed_run(
                root, "old_run", files={"a.pkl": 1024}, mtime_offset_days=90,
            )
            # Default: no --execute → dry-run.
            rc = main([
                "--root", str(root),
                "--older-than", "30",
            ])
            self.assertEqual(rc, 0)
            self.assertTrue(
                run.is_dir(), "dry-run should NOT delete the directory",
            )

    def test_execute_deletes_candidates(self):
        from tempfile import TemporaryDirectory

        from scripts.cleanup_output import main

        with TemporaryDirectory() as td:
            root = Path(td)
            old = _seed_run(
                root, "old_run", files={"a.pkl": 1024}, mtime_offset_days=90,
            )
            recent = _seed_run(
                root, "recent_run", files={"b.pkl": 1024}, mtime_offset_days=1,
            )
            rc = main([
                "--root", str(root),
                "--older-than", "30",
                "--execute",
            ])
            self.assertEqual(rc, 0)
            self.assertFalse(old.is_dir(), "old_run should be deleted")
            self.assertTrue(recent.is_dir(), "recent_run must survive")

    def test_missing_root_returns_zero_quietly(self):
        from scripts.cleanup_output import main

        # Missing --root is a benign no-op, not an error.
        rc = main(["--root", "/this/path/does/not/exist"])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
