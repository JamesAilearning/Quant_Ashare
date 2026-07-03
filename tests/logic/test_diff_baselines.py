"""Tests for scripts/regen/diff_baselines.py — the re-sign acceptance gate
(audit P2, operator decision 2: rules committed before the numbers are seen)."""
from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd


def _load_cli():
    path = PROJECT_ROOT / "scripts" / "regen" / "diff_baselines.py"
    spec = importlib.util.spec_from_file_location("_diff_baselines_under_test", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _baseline(folds: list[dict]) -> dict:
    return {"_status": "x", "_provenance": "x", "aggregate_metrics": {}, "per_fold": folds}


def _fold(i: int, test_period: str, ic_1d: float, ic_5d: float = 0.05,
          ret: float = 0.1, dd: float = -0.05, ir: float = 0.5) -> dict:
    return {"fold_index": i, "test_period": test_period, "ic_1d": ic_1d,
            "ic_5d": ic_5d, "annualized_return": ret, "max_drawdown": dd,
            "information_ratio": ir}


class DiffBaselinesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cli = _load_cli()
        self.td = TemporaryDirectory()
        root = Path(self.td.name)
        # registry: one instrument delisted inside fold-1's window
        pd.DataFrame({
            "ticker": ["SH600068"], "delist_date": ["2021-08-15"],
        }).to_parquet(root / "reg.parquet")
        self.reg = str(root / "reg.parquet")
        self.root = root

    def tearDown(self) -> None:
        self.td.cleanup()

    def _run(self, old_folds: list[dict], new_folds: list[dict]) -> int:
        old_p = self.root / "old.json"
        new_p = self.root / "new.json"
        old_p.write_text(json.dumps(_baseline(old_folds)), encoding="utf-8")
        new_p.write_text(json.dumps(_baseline(new_folds)), encoding="utf-8")
        return self.cli.main([
            "--old", str(old_p), "--new", str(new_p),
            "--registry", self.reg,
            "--out-md", str(self.root / "diff.md"),
        ])

    # fold 0: no hit (2021-01..2021-03); fold 1: hit (2021-07..2021-09)
    _F0 = "2021-01-01..2021-03-31"
    _F1 = "2021-07-01..2021-09-30"

    def test_identical_baselines_pass(self) -> None:
        folds = [_fold(0, self._F0, 0.01), _fold(1, self._F1, 0.02)]
        self.assertEqual(self._run(folds, [dict(f) for f in folds]), 0)

    def test_attributed_ic_change_passes_with_table(self) -> None:
        old = [_fold(0, self._F0, 0.01), _fold(1, self._F1, 0.02)]
        new = [_fold(0, self._F0, 0.01), _fold(1, self._F1, 0.021)]  # hit fold moved
        self.assertEqual(self._run(old, new), 0)
        md = (self.root / "diff.md").read_text(encoding="utf-8")
        self.assertIn("SH600068", md)  # attribution named in the table

    def test_unattributed_ic_change_fails_r3(self) -> None:
        old = [_fold(0, self._F0, 0.01), _fold(1, self._F1, 0.02)]
        new = [_fold(0, self._F0, 0.011), _fold(1, self._F1, 0.02)]  # NO-hit fold moved
        self.assertEqual(self._run(old, new), 1)
        md = (self.root / "diff.md").read_text(encoding="utf-8")
        self.assertIn("R3 VIOLATION", md)

    def test_backtest_drift_fails_r2_even_on_hit_fold(self) -> None:
        old = [_fold(0, self._F0, 0.01), _fold(1, self._F1, 0.02)]
        new = [_fold(0, self._F0, 0.01), _fold(1, self._F1, 0.02, ret=0.11)]
        self.assertEqual(self._run(old, new), 1)
        md = (self.root / "diff.md").read_text(encoding="utf-8")
        self.assertIn("R2 VIOLATION", md)

    def test_fold_set_mismatch_fails(self) -> None:
        old = [_fold(0, self._F0, 0.01)]
        new = [_fold(0, self._F0, 0.01), _fold(1, self._F1, 0.02)]
        self.assertEqual(self._run(old, new), 1)


if __name__ == "__main__":
    unittest.main()
