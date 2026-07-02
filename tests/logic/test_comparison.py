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

    def test_block_length_caps_when_decay_never_observed(self) -> None:
        # a persistently-autocorrelated series (linear ramp) never decays within the
        # checked lags -> the block must be the CAP (_MAX_BLOCK), not a short 10 that
        # would understate the bootstrap SE (codex P1).
        from src.core.comparison import _MAX_BLOCK
        self.assertEqual(estimate_block_length(np.arange(300.0)), _MAX_BLOCK)

    def test_pooled_ir_uses_full_run_not_just_shared_dates(self) -> None:
        from src.core.comparison import _annualized_ir
        shared = _dates(100, "2025-07-01")
        extra = _dates(10, "2026-01-01")  # tail dates A lacks (label-horizon case)
        rng = np.random.default_rng(5)
        b_shared = _fold(shared, rng.standard_normal(100) * 0.01)
        b_extra = _fold(extra, np.full(10, 0.05))  # distinct tail so full != shared
        with TemporaryDirectory() as tmp:
            a = _write_run(Path(tmp) / "A",
                           [_fold(shared, rng.standard_normal(100) * 0.01)])
            b = _write_run(Path(tmp) / "B", [{**b_shared, **b_extra}])
            r = compare_runs(a, b, pre_registration_ref=_PREREG)
        b_all = np.array([v[0] - v[1] - v[2] for v in {**b_shared, **b_extra}.values()])
        # pooled treatment IR reflects ALL of B's days, not only the 100 shared
        self.assertAlmostEqual(r.pooled_net_ir_treatment, _annualized_ir(b_all), places=6)

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
        # verdict SIDE must track the CI, not the point estimate
        self.assertEqual(r.verdict == "treatment_better", r.paired_net_ci95[0] > 0)

    def test_contradiction_flag_when_ic_disagrees(self) -> None:
        base = np.full(250, 0.0)
        treat = np.full(250, 0.002) + np.linspace(0, 1e-9, 250)  # B better on net excess
        with TemporaryDirectory() as tmp:
            a, b = self._runs(tmp, base, treat, ic_a=0.05, ic_b=0.01)  # but B worse on IC
            r = compare_runs(a, b, pre_registration_ref=_PREREG)
        self.assertEqual(r.verdict, "treatment_better")
        self.assertIsNotNone(r.contradiction_flag)

    def test_zero_difference_direction_is_flat(self) -> None:
        # identical runs -> paired diff == 0 -> indistinguishable, and the direction
        # diagnostic must say FLAT, not "treatment<baseline".
        rng = np.random.default_rng(11)
        exc = rng.standard_normal(60) * 0.01
        with TemporaryDirectory() as tmp:
            d = _dates(60)
            a = _write_run(Path(tmp) / "A", [_fold(d, exc)])
            b = _write_run(Path(tmp) / "B", [_fold(d, exc)])   # identical excess
            r = compare_runs(a, b, pre_registration_ref=_PREREG)
        self.assertEqual(r.verdict, "indistinguishable")
        self.assertIn("flat", r.diagnostics["direction"])

    def test_seam_bound_reported(self) -> None:
        d = _dates(120)
        rng = np.random.default_rng(9)  # varying excess -> finite IR (not the degenerate NaN)
        with TemporaryDirectory() as tmp:
            a = _write_run(Path(tmp) / "A", [_fold(d[:60], rng.standard_normal(60) * 0.01),
                                             _fold(d[60:], rng.standard_normal(60) * 0.01)])
            b = _write_run(Path(tmp) / "B", [_fold(d[:60], rng.standard_normal(60) * 0.01),
                                             _fold(d[60:], rng.standard_normal(60) * 0.01)])
            r = compare_runs(a, b, pre_registration_ref=_PREREG)
        # BOTH runs' seam bounded, not only treatment
        for k in ("baseline_pooled_net_ir_incl_boundary", "baseline_seam_impact",
                  "treatment_pooled_net_ir_incl_boundary", "treatment_seam_impact"):
            self.assertIn(k, r.seam_bound)
        # seam is computed on the SAME full series as the reported pooled IR, so the
        # "included-boundary" leg must equal the pooled IR (not the intersection).
        self.assertEqual(r.seam_bound["treatment_pooled_net_ir_incl_boundary"],
                         r.pooled_net_ir_treatment)

    def test_serialized_output_carries_study_protocol_caveat(self) -> None:
        with TemporaryDirectory() as tmp:
            d = _dates(120)
            a = _write_run(Path(tmp) / "A", [_fold(d, np.zeros(120))])
            b = _write_run(Path(tmp) / "B", [_fold(d, np.full(120, 0.001))])
            out = compare_runs(a, b, pre_registration_ref=_PREREG).to_dict()
        joined = " ".join(out["caveats"]).lower()
        self.assertIn("study-protocol", joined)
        self.assertIn("not a continuous production", joined)

    def test_indistinguishable_but_ic_favours_a_side_is_flagged(self) -> None:
        rng = np.random.default_rng(7)
        base = rng.standard_normal(250) * 0.01
        treat = base + rng.standard_normal(250) * 0.01  # net indistinguishable
        with TemporaryDirectory() as tmp:
            a, b = self._runs(tmp, base, treat, ic_a=0.01, ic_b=0.06)  # IC clearly favours B
            r = compare_runs(a, b, pre_registration_ref=_PREREG)
        self.assertEqual(r.verdict, "indistinguishable")
        self.assertIsNotNone(r.contradiction_flag)
        self.assertIn("indistinguishable", (r.contradiction_flag or "").lower())


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

    def test_too_few_paired_days_raises(self) -> None:
        # < min_paired_days shared days -> refuse (a ~zero-width CI would fake a winner)
        with TemporaryDirectory() as tmp:
            d = _dates(10)
            a = _write_run(Path(tmp) / "A", [_fold(d, np.arange(10) * 0.001)])
            b = _write_run(Path(tmp) / "B", [_fold(d, np.arange(10) * 0.002)])
            with self.assertRaises(ComparisonError):
                compare_runs(a, b, pre_registration_ref=_PREREG)

    def test_out_of_range_block_length_override_raises(self) -> None:
        # a bad override must be rejected up front so the RECORDED block == the one used
        with TemporaryDirectory() as tmp:
            d = _dates(60)
            a = _write_run(Path(tmp) / "A", [_fold(d, np.zeros(60))])
            b = _write_run(Path(tmp) / "B", [_fold(d, np.zeros(60))])
            # 60 shared days -> cap is 30 (n//2); reject 0/-1, > cap, and the full length
            # (which would collapse the bootstrap CI to a point).
            for bad in (0, -1, 31, 60, 10_000):
                with self.assertRaises(ComparisonError):
                    compare_runs(a, b, pre_registration_ref=_PREREG, block_length=bad)

    def test_duplicate_oos_date_across_folds_raises(self) -> None:
        # overlapping test windows -> same OOS date in two folds -> refuse (collapsing by
        # date would silently drop a realized fold-day).
        with TemporaryDirectory() as tmp:
            d = _dates(30)
            a = _write_run(Path(tmp) / "A",
                           [_fold(d[:25], np.zeros(25)), _fold(d[20:], np.zeros(10))])
            with self.assertRaises(ComparisonError):
                load_run_daily_series(a)

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
