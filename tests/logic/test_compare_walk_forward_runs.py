"""Tests for ``scripts.compare_walk_forward_runs``."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.compare_walk_forward_runs import main  # noqa: E402


class CompareWalkForwardRunsTests(unittest.TestCase):
    def _write_aggregate(self, run_dir: Path, folds: list[dict]) -> None:
        (run_dir / "walk_forward_report.json").write_text(
            json.dumps(
                {
                    "generated_at": "2026-05-08T00:00:00",
                    "num_folds": len(folds),
                    "folds": folds,
                    "aggregate_metrics": {},
                }
            ),
            encoding="utf-8",
        )

    def test_baseline_only_period_does_not_read_unrelated_treatment_fold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_text:
            tmp = Path(tmp_text)
            baseline = tmp / "baseline"
            treatment = tmp / "treatment"
            baseline.mkdir()
            treatment.mkdir()
            self._write_aggregate(
                baseline,
                [
                    {
                        "fold_index": 0,
                        "test_period": "2025-01-01 ~ 2025-03-31",
                        "ic_1d": 0.1,
                        "annualized_return": 0.2,
                        "information_ratio": 1.0,
                    }
                ],
            )
            self._write_aggregate(
                treatment,
                [
                    {
                        "fold_index": 9,
                        "test_period": "2025-04-01 ~ 2025-06-30",
                        "ic_1d": 0.2,
                        "annualized_return": 0.3,
                        "information_ratio": 2.0,
                    }
                ],
            )
            (treatment / "fold_00_report.json").write_text(
                json.dumps(
                    {
                        "ensemble": {
                            "n_models": 99,
                            "contributing_folds": [123],
                        }
                    }
                ),
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with patch.object(sys, "argv", [
                "compare_walk_forward_runs.py",
                str(baseline),
                str(treatment),
            ]), redirect_stdout(stdout):
                self.assertEqual(main(), 0)

        text = stdout.getvalue()
        self.assertNotIn("99  [123]", text)


if __name__ == "__main__":
    unittest.main()
