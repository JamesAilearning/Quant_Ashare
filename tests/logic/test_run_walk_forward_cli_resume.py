"""Tests for the walk-forward CLI's resume-flag parser (PR4).

The `_parse_cli` helper is tested in isolation — no qlib init, no
engine, no model training. We just verify the argparse surface
matches the OpenSpec contract.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.run_walk_forward import _parse_cli  # noqa: E402
from src.core.walk_forward._resume import _ResumeKind  # noqa: E402


class ResumeFlagsTests(unittest.TestCase):
    def test_default_is_auto_resume(self) -> None:
        config, mode, _ds = _parse_cli([])
        self.assertEqual(config, "config_walk.yaml")
        self.assertEqual(mode.kind, _ResumeKind.AUTO)

    def test_positional_config_is_honored(self) -> None:
        config, mode, _ds = _parse_cli(["my.yaml"])
        self.assertEqual(config, "my.yaml")
        self.assertEqual(mode.kind, _ResumeKind.AUTO)

    def test_resume_from_fold_n_produces_bounded_mode(self) -> None:
        config, mode, _ds = _parse_cli(["walk.yaml", "--resume-from-fold", "3"])
        self.assertEqual(config, "walk.yaml")
        self.assertEqual(mode.kind, _ResumeKind.RESUME_FROM_FOLD)
        self.assertEqual(mode.from_fold_index, 3)

    def test_no_resume_produces_force_rerun(self) -> None:
        config, mode, _ds = _parse_cli(["walk.yaml", "--no-resume"])
        self.assertEqual(config, "walk.yaml")
        self.assertEqual(mode.kind, _ResumeKind.FORCE_RERUN)

    def test_both_flags_together_exits_nonzero(self) -> None:
        """argparse's mutually-exclusive group raises SystemExit(2)."""
        with self.assertRaises(SystemExit) as cm:
            _parse_cli(["walk.yaml", "--no-resume", "--resume-from-fold", "1"])
        self.assertEqual(cm.exception.code, 2)

    def test_negative_resume_from_fold_exits_nonzero(self) -> None:
        with self.assertRaises(SystemExit):
            _parse_cli(["walk.yaml", "--resume-from-fold", "-1"])

    def test_non_int_resume_from_fold_exits_nonzero(self) -> None:
        with self.assertRaises(SystemExit):
            _parse_cli(["walk.yaml", "--resume-from-fold", "abc"])

    def test_resume_from_fold_zero_is_legal(self) -> None:
        """N=0 means 'no skips' — re-run everything but per-fold
        manifest writes still happen. Equivalent to AUTO + empty
        discovered set."""
        _config, mode, _ds = _parse_cli(["walk.yaml", "--resume-from-fold", "0"])
        self.assertEqual(mode.kind, _ResumeKind.RESUME_FROM_FOLD)
        self.assertEqual(mode.from_fold_index, 0)


if __name__ == "__main__":
    unittest.main()
