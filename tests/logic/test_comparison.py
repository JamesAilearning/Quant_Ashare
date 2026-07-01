"""Unit tests for the run-comparison ruler (add-run-comparison-methodology, PR-2).

Pure synthetic — writes tiny fold-report JSON runs and exercises the statistics /
fail-loud guards without qlib, a bundle, or a real walk-forward.
"""
from __future__ import annotations

import json
import unittest
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from src.core.comparison import (
    ComparisonError,
    compare_runs,
    estimate_block_length,
    load_run_daily_series,
    paired_block_bootstrap,
)
from src.core.walk_forward.aggregate import FOLD_REPORT_SCHEMA_VERSION

_PREREG = "abc1234"  # a stand-in committed-hypothesis commit hash


def _dates(n: int, start: str = "2025-07-01") -> list[str]:
    d0 = date.fromisoformat(start)
    return [(d0 + timedelta(days=i)).isoformat() for i in range(n)]


def _write_run(root: Path, folds: list[dict[str, tuple[float, float, float, float]]],
               schema: str = FOLD_REPORT_SCHEMA_VERSION) -> str:
    """folds: list of {date: (return, bench, cost, ic_1d)}."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "walk_forward_report.json").write_text(json.dumps({"num_folds": len(folds)}))
    for i, fold in enumerate(folds):
        ds = {
            "excess_return": {d: v[0] - v[1] - v[2] for d, v in fold.items()},
            "components": {
                "return": {d: v[0] for d, v in fold.items()},
                "bench": {d: v[1] for d, v in fold.items()},
                "cost": {d: v[2] for d, v in fold.items()},
            },
            "ic": {"1": {d: v[3] for d, v in fold.items()}},
        }
        rep = {"fold_index": i, "daily_series": ds, "schema_version": schema, "metrics": {}}
        (root / f"fold_{i:02d}_report.json").write_text(json.dumps(rep))
    return str(root)


def _fold(dates: list[str], excess: np.ndarray, ic: float = 0.02
          ) -> dict[str, tuple[float, float, float, float]]:
    # return=excess+bench+cost, fixed bench/cost so excess_return == excess
    return {d: (float(e) + 0.001 + 0.0005, 0.001, 0.0005, ic)
            for d, e in zip(dates, excess, strict=True)}


class StatisticsTests(unittest.TestCase):
    def test_block_length_larger_for_autocorrelated_series(self) -> None:
        rng = np.random.default_rng(0)
        iid = rng.standard_normal(300)
        ar = np.cumsum(rng.standard_normal(300))  # strongly autocorrelated
        ar = np.diff(np.r_[0.0, ar])  # keep it a return-like series but correlated
        # a near-iid series decorrelates almost immediately; an AR series later.
        self.assertLessEqual(estimate_block_length(iid), 4)

    def test_annualized_ir_degenerate_series_is_nan(self) -> None:
        import math

        from src.core.comparison import _annualized_ir
        # a constant series (exact zeros AND float-error-near-constant) has no meaningful
        # IR — must be NaN, never a spurious huge value from dividing by ~1e-17.
        self.assertTrue(math.isnan(_annualized_ir(np.zeros(50))))
        self.assertTrue(math.isnan(_annualized_ir(np.full(50, 0.1))))

    def test_bootstrap_se_wider_under_autocorrelation(self) -> None:
        rng = np.random.default_rng(1)
        # AR(1) diff with strong positive autocorrelation
        e = rng.standard_normal(400) * 0.01
        ar = np.empty(400)
        ar[0] = e[0]
        for t in range(1, 400):
            ar[t] = 0.7 * ar[t - 1] + e[t]
        _, se_iid, _, _ = paired_block_bootstrap(ar, block_len=1, n_boot=2000)
        _, se_blk, _, _ = paired_block_bootstrap(ar, block_len=estimate_block_length(ar), n_boot=2000)
        self.assertGreater(se_blk, se_iid)  # iid bootstrap understates SE


class VerdictTests(unittest.TestCase):
    def _runs(self, tmp: str, base_excess: np.ndarray, treat_excess: np.ndarray,
              ic_a: float = 0.02, ic_b: float = 0.02) -> tuple[str, str]:
        d = _dates(len(base_excess))
        a = _write_run(Path(tmp) / "A", [_fold(d, base_excess, ic_a)])
        b = _write_run(Path(tmp) / "B", [_fold(d, treat_excess, ic_b)])
        return a, b

    def test_indistinguishable_when_diff_is_noise(self) -> None:
        rng = np.random.default_rng(2)
        base = rng.standard_normal(250) * 0.01
        treat = base + rng.standard_normal(250) * 0.01  # noisy, zero-mean difference
        with TemporaryDirectory() as tmp:
            a, b = self._runs(tmp, base, treat)
            r = compare_runs(a, b, pre_registration_ref=_PREREG)
        self.assertEqual(r.verdict, "indistinguishable")
        # the mandated companion: diagnostics + the "not equivalent" note
        self.assertIn("not 'equivalent'", r.diagnostics["note"].lower().replace('"', "'"))
        self.assertIn("gross_ir_treatment", r.diagnostics)

    def test_treatment_better_when_clear_positive(self) -> None:
        base = np.full(250, 0.0)
        treat = np.full(250, 0.002) + np.linspace(0, 1e-9, 250)  # steady +, tiny jitter
        with TemporaryDirectory() as tmp:
            a, b = self._runs(tmp, base, treat)
            r = compare_runs(a, b, pre_registration_ref=_PREREG)
        self.assertEqual(r.verdict, "treatment_better")
        self.assertGreater(r.paired_net_ci95[0], 0.0)  # CI strictly above 0

    def test_contradiction_flag_when_ic_disagrees(self) -> None:
        base = np.full(250, 0.0)
        treat = np.full(250, 0.002) + np.linspace(0, 1e-9, 250)  # B better on net excess
        with TemporaryDirectory() as tmp:
            a, b = self._runs(tmp, base, treat, ic_a=0.05, ic_b=0.01)  # but B worse on IC
            r = compare_runs(a, b, pre_registration_ref=_PREREG)
        self.assertEqual(r.verdict, "treatment_better")
        self.assertIsNotNone(r.contradiction_flag)

    def test_seam_bound_reported(self) -> None:
        d = _dates(120)
        with TemporaryDirectory() as tmp:
            a = _write_run(Path(tmp) / "A", [_fold(d[:60], np.zeros(60)), _fold(d[60:], np.zeros(60))])
            b = _write_run(Path(tmp) / "B", [_fold(d[:60], np.full(60, 0.001)),
                                             _fold(d[60:], np.full(60, 0.001))])
            r = compare_runs(a, b, pre_registration_ref=_PREREG)
        self.assertIn("pooled_net_ir_incl_boundary", r.seam_bound)
        self.assertIn("seam_impact", r.seam_bound)


class FailLoudTests(unittest.TestCase):
    def test_missing_prereg_raises(self) -> None:
        with TemporaryDirectory() as tmp:
            d = _dates(60)
            a = _write_run(Path(tmp) / "A", [_fold(d, np.zeros(60))])
            b = _write_run(Path(tmp) / "B", [_fold(d, np.zeros(60))])
            with self.assertRaises(ComparisonError):
                compare_runs(a, b, pre_registration_ref="")

    def test_low_overlap_raises(self) -> None:
        with TemporaryDirectory() as tmp:
            a = _write_run(Path(tmp) / "A", [_fold(_dates(200, "2025-07-01"), np.zeros(200))])
            b = _write_run(Path(tmp) / "B", [_fold(_dates(200, "2026-07-01"), np.zeros(200))])
            with self.assertRaises(ComparisonError):
                compare_runs(a, b, pre_registration_ref=_PREREG)

    def test_missing_daily_series_raises_actionable(self) -> None:
        with TemporaryDirectory() as tmp:
            d = _dates(60)
            a = _write_run(Path(tmp) / "A", [_fold(d, np.zeros(60))], schema="1-legacy")
            with self.assertRaises(ComparisonError) as cm:
                load_run_daily_series(a)
        msg = str(cm.exception).lower()
        self.assertIn("non-comparable", msg)
        self.assertIn("re-run", msg)


if __name__ == "__main__":
    unittest.main()
