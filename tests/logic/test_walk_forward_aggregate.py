"""Tests for ``src.core.walk_forward.aggregate``.

The aggregate module turns a list of `WalkForwardFold` into the
walk-forward report JSON. We cover the headline functions
dimensionally:

- `compute_aggregate`: empty / all-NaN / mixed-NaN / single-fold CI /
  multi-fold CI.
- `compute_test_window_coverage`: continuous / gapped / overlapping /
  mixed / empty.
- `extract_cost_metrics`: well-formed / missing-key / non-dict /
  missing-required-metric.
- `attribution_section_for_fold`: result-present / skipped.
- `build_aggregate_report`: schema shape + coverage section embedded.
"""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.git_provenance import capture_git_provenance  # noqa: E402
from src.core.walk_forward._types import WalkForwardFold  # noqa: E402
from src.core.walk_forward.aggregate import (  # noqa: E402
    attribution_section_for_fold,
    build_aggregate_report,
    compute_aggregate,
    compute_test_window_coverage,
    extract_cost_metrics,
    write_aggregate_report,
)
from src.core.walk_forward.config import WalkForwardConfig, WalkForwardError  # noqa: E402


def _make_fold(idx: int, *, ic_1d=0.05, ic_5d=0.04, ret=0.10,
               dd=-0.08, ir=0.5, train="2022-01-01 ~ 2022-12-31",
               valid="2023-01-01 ~ 2023-03-31",
               test="2023-04-01 ~ 2023-06-30") -> WalkForwardFold:
    return WalkForwardFold(
        fold_index=idx,
        train_period=train, valid_period=valid, test_period=test,
        ic_1d=ic_1d, ic_5d=ic_5d,
        annualized_return=ret, max_drawdown=dd,
        information_ratio=ir,
        prediction_shape=(100, 50),
    )


# ---------------------------------------------------------------------------
# compute_aggregate
# ---------------------------------------------------------------------------


class ComputeAggregateTests(unittest.TestCase):
    def test_empty_folds_returns_empty_dict(self):
        self.assertEqual(compute_aggregate([]), {})

    def test_single_fold_means_match_inputs(self):
        f = _make_fold(0, ic_1d=0.05, ir=0.7)
        agg = compute_aggregate([f])
        self.assertAlmostEqual(agg["mean_ic_1d"], 0.05)
        self.assertAlmostEqual(agg["mean_information_ratio"], 0.7)
        self.assertEqual(agg["num_folds"], 1)
        # Single-fold bootstrap CI is undefined.
        self.assertTrue(math.isnan(agg["mean_ic_1d_ci_low"]))
        self.assertTrue(math.isnan(agg["mean_ic_1d_ci_high"]))

    def test_all_nan_metric_propagates_to_nan(self):
        nan = float("nan")
        folds = [
            _make_fold(0, ic_1d=nan, ic_5d=nan, ret=nan, dd=nan, ir=nan),
            _make_fold(1, ic_1d=nan, ic_5d=nan, ret=nan, dd=nan, ir=nan),
        ]
        agg = compute_aggregate(folds)
        # nanmean of all-NaN is NaN by numpy convention — preserved.
        self.assertTrue(math.isnan(agg["mean_ic_1d"]))
        # The valid-folds count surfaces the all-skip case.
        self.assertEqual(agg["valid_folds_ic_1d"], 0)

    def test_mixed_nan_uses_valid_only(self):
        nan = float("nan")
        folds = [
            _make_fold(0, ic_1d=0.05),
            _make_fold(1, ic_1d=nan),
            _make_fold(2, ic_1d=0.07),
        ]
        agg = compute_aggregate(folds)
        # mean of (0.05, 0.07), NOT (0.05, NaN, 0.07).
        self.assertAlmostEqual(agg["mean_ic_1d"], 0.06)
        self.assertEqual(agg["valid_folds_ic_1d"], 2)

    def test_multi_fold_bootstrap_ci_brackets_mean(self):
        folds = [_make_fold(i, ic_1d=0.04 + 0.005 * i) for i in range(8)]
        agg = compute_aggregate(folds)
        lo = agg["mean_ic_1d_ci_low"]
        hi = agg["mean_ic_1d_ci_high"]
        mean = agg["mean_ic_1d"]
        self.assertFalse(math.isnan(lo))
        self.assertFalse(math.isnan(hi))
        self.assertLessEqual(lo, mean)
        self.assertLessEqual(mean, hi)

    def test_bootstrap_seed_in_payload(self):
        agg = compute_aggregate([_make_fold(0)], seed=123)
        self.assertEqual(agg["bootstrap_seed"], 123)

    def test_worst_drawdown_is_min_not_mean(self):
        # max_drawdown is negative; "worst" means most-negative.
        folds = [
            _make_fold(0, dd=-0.05),
            _make_fold(1, dd=-0.15),
            _make_fold(2, dd=-0.10),
        ]
        agg = compute_aggregate(folds)
        self.assertAlmostEqual(agg["worst_drawdown"], -0.15)


# ---------------------------------------------------------------------------
# compute_test_window_coverage
# ---------------------------------------------------------------------------


class TestWindowCoverageTests(unittest.TestCase):
    def test_empty_folds_returns_none_mode(self):
        out = compute_test_window_coverage([])
        self.assertEqual(out["mode"], "none")
        self.assertEqual(out["gap_count"], 0)
        self.assertEqual(out["overlap_count"], 0)

    def test_continuous_back_to_back_windows(self):
        folds = [
            _make_fold(0, test="2024-01-01 ~ 2024-03-31"),
            _make_fold(1, test="2024-04-01 ~ 2024-06-30"),
            _make_fold(2, test="2024-07-01 ~ 2024-09-30"),
        ]
        out = compute_test_window_coverage(folds)
        self.assertEqual(out["mode"], "continuous")
        self.assertEqual(out["gap_count"], 0)
        self.assertEqual(out["overlap_count"], 0)

    def test_gapped_windows(self):
        folds = [
            _make_fold(0, test="2024-01-01 ~ 2024-03-31"),
            _make_fold(1, test="2024-05-01 ~ 2024-07-31"),  # April gap
        ]
        out = compute_test_window_coverage(folds)
        self.assertEqual(out["mode"], "gapped")
        self.assertEqual(out["gap_count"], 1)
        self.assertGreater(out["max_gap_days"], 0)

    def test_overlapping_windows(self):
        folds = [
            _make_fold(0, test="2024-01-01 ~ 2024-04-30"),
            _make_fold(1, test="2024-04-01 ~ 2024-07-31"),  # April overlap
        ]
        out = compute_test_window_coverage(folds)
        self.assertEqual(out["mode"], "overlapping")
        self.assertEqual(out["overlap_count"], 1)
        self.assertGreater(out["max_overlap_days"], 0)

    def test_mixed_gap_and_overlap(self):
        folds = [
            _make_fold(0, test="2024-01-01 ~ 2024-04-30"),
            _make_fold(1, test="2024-04-01 ~ 2024-07-31"),  # overlap
            _make_fold(2, test="2024-09-01 ~ 2024-12-31"),  # then gap
        ]
        out = compute_test_window_coverage(folds)
        self.assertEqual(out["mode"], "mixed")

    def test_malformed_period_raises(self):
        f = _make_fold(0, test="not a period")
        with self.assertRaises(WalkForwardError):
            compute_test_window_coverage([f])

    def test_inverted_period_raises(self):
        f = _make_fold(0, test="2024-12-31 ~ 2024-01-01")
        with self.assertRaises(WalkForwardError):
            compute_test_window_coverage([f])


# ---------------------------------------------------------------------------
# extract_cost_metrics
# ---------------------------------------------------------------------------


class ExtractCostMetricsTests(unittest.TestCase):
    def _good_risk_analysis(self) -> dict:
        return {
            "excess_return_with_cost": {
                "annualized_return": 0.12,
                "max_drawdown": -0.08,
                "information_ratio": 0.65,
            },
        }

    def test_happy_path(self):
        ann, dd, ir = extract_cost_metrics(self._good_risk_analysis(), fold_index=3)
        self.assertAlmostEqual(ann, 0.12)
        self.assertAlmostEqual(dd, -0.08)
        self.assertAlmostEqual(ir, 0.65)

    def test_missing_excess_return_block_raises(self):
        with self.assertRaises(WalkForwardError) as cm:
            extract_cost_metrics({"other_key": {}}, fold_index=2)
        self.assertIn("excess_return_with_cost", str(cm.exception))
        self.assertIn("Fold 2", str(cm.exception))

    def test_non_dict_cost_block_raises(self):
        bad = {"excess_return_with_cost": "garbage"}
        with self.assertRaises(WalkForwardError):
            extract_cost_metrics(bad, fold_index=1)

    def test_missing_required_metric_raises(self):
        bad = {"excess_return_with_cost": {
            "annualized_return": 0.1,
            # missing max_drawdown
            "information_ratio": 0.5,
        }}
        with self.assertRaises(WalkForwardError) as cm:
            extract_cost_metrics(bad, fold_index=0)
        self.assertIn("max_drawdown", str(cm.exception))


# ---------------------------------------------------------------------------
# attribution_section_for_fold
# ---------------------------------------------------------------------------


class AttributionSectionTests(unittest.TestCase):
    def test_no_result_with_reason_emits_skipped(self):
        out = attribution_section_for_fold(None, "no_positions_from_backtest")
        self.assertEqual(out["status"], "skipped")
        self.assertEqual(out["skipped_reason"], "no_positions_from_backtest")

    def test_no_result_no_reason_emits_unknown_reason(self):
        out = attribution_section_for_fold(None, None)
        self.assertEqual(out["status"], "skipped")
        self.assertEqual(out["skipped_reason"], "unknown_reason")

    def test_with_result_emits_ok_status(self):
        # Use a SimpleNamespace-style stub rather than constructing the
        # real AttributionResult — the attribution_section_for_fold
        # function reads via attribute access, so a stub with the
        # right attributes is sufficient AND keeps this test
        # decoupled from AttributionResult's full schema (which
        # has Brinson + monthly + drawdown sub-blocks we don't need).
        from types import SimpleNamespace

        sector_stub = SimpleNamespace(
            sector="Tech",
            portfolio_weight=0.3, benchmark_weight=0.2,
            allocation_effect=0.01, selection_effect=0.005,
            total_effect=0.016,
        )
        result_stub = SimpleNamespace(
            sector_taxonomy="GICS",
            attribution_method="brinson",
            bench_weight_method="cap_weighted",
            total_portfolio_return=0.10,
            total_benchmark_return=0.07,
            total_excess_return=0.03,
            total_allocation_effect=0.01,
            total_selection_effect=0.015,
            total_interaction_effect=0.005,
            sector_effects_sum=0.03,
            reconciliation_residual=0.0,
            sector_attribution=(sector_stub,),
        )
        out = attribution_section_for_fold(result_stub, None)
        self.assertEqual(out["status"], "ok")
        self.assertIsNone(out["skipped_reason"])
        self.assertEqual(out["sector_taxonomy"], "GICS")
        self.assertEqual(len(out["sector_attribution"]), 1)


# ---------------------------------------------------------------------------
# build_aggregate_report
# ---------------------------------------------------------------------------


class BuildAggregateReportTests(unittest.TestCase):
    def test_report_has_all_top_level_keys(self):
        config = WalkForwardConfig(output_dir="output/wf")
        folds = [_make_fold(0), _make_fold(1, test="2023-07-01 ~ 2023-09-30")]
        agg = compute_aggregate(folds)
        report = build_aggregate_report(
            config=config, folds=folds, aggregate_metrics=agg,
        )
        for key in (
            "generated_at", "config", "folds", "aggregate_metrics",
            "test_window_coverage", "num_folds",
        ):
            self.assertIn(key, report)
        self.assertEqual(report["num_folds"], 2)
        # Folds list mirrors the input.
        self.assertEqual(len(report["folds"]), 2)
        # Coverage section is embedded.
        self.assertIn("mode", report["test_window_coverage"])

    def test_prediction_shape_serializes_to_list_not_tuple(self):
        # JSON has no tuple — tuple → list at report build time so the
        # downstream report's shape is byte-stable across serialization.
        config = WalkForwardConfig(output_dir="output/wf")
        folds = [_make_fold(0)]
        report = build_aggregate_report(
            config=config, folds=folds, aggregate_metrics={},
        )
        self.assertIsInstance(
            report["folds"][0]["prediction_shape"], list,
        )

    def test_git_provenance_recorded_when_supplied(self):
        # the run-comparison pre-registration gate reads git_commit; the builder records
        # whatever provenance the I/O boundary captured (PR-3b-i).
        config = WalkForwardConfig(output_dir="output/wf")
        report = build_aggregate_report(
            config=config, folds=[_make_fold(0)], aggregate_metrics={},
            git_provenance={"commit": "abc1234def", "dirty": True},
        )
        self.assertEqual(report["git_commit"], "abc1234def")
        self.assertIs(report["git_dirty"], True)

    def test_git_provenance_defaults_to_none(self):
        # a synthetic report (no provenance supplied) carries null git fields — additive,
        # and the gate then fails loud on that run rather than trusting an absent commit.
        config = WalkForwardConfig(output_dir="output/wf")
        report = build_aggregate_report(
            config=config, folds=[_make_fold(0)], aggregate_metrics={},
        )
        self.assertIsNone(report["git_commit"])
        self.assertIsNone(report["git_dirty"])

    def test_capture_git_provenance_shape_never_raises(self):
        # runs in the repo (commit is a sha) or outside one (None); either way the shape is
        # {'commit': str|None, 'dirty': bool|None} and it never raises.
        gp = capture_git_provenance()
        self.assertEqual(set(gp), {"commit", "dirty"})
        self.assertIsInstance(gp["commit"], (str, type(None)))
        self.assertIsInstance(gp["dirty"], (bool, type(None)))

    def test_capture_git_provenance_keeps_commit_when_dirty_probe_fails(self):
        # codex P2 on #313: rev-parse succeeds but the dirty probe fails/times out ->
        # the commit must be KEPT (dirty degrades to None), otherwise a valid checkout's
        # run loses git_commit and the ancestor gate rejects it needlessly.
        import subprocess
        from types import SimpleNamespace
        from unittest.mock import patch

        from src.core import git_provenance as gp_mod

        calls = {"n": 0}

        def fake_run(cmd, **kwargs):
            calls["n"] += 1
            if "rev-parse" in cmd:
                return SimpleNamespace(stdout="abc123\n")
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=5)

        # patch.object on the module OBJECT (not a string target) — identity-safe.
        with patch.object(gp_mod, "subprocess") as fake_sp:
            fake_sp.run = fake_run
            fake_sp.SubprocessError = subprocess.SubprocessError
            fake_sp.TimeoutExpired = subprocess.TimeoutExpired
            gp = gp_mod.capture_git_provenance()
        self.assertEqual(gp["commit"], "abc123")
        self.assertIsNone(gp["dirty"])
        self.assertEqual(calls["n"], 2)

    def test_write_aggregate_report_records_injected_git_provenance(self):
        import json
        import tempfile

        # git_provenance is INJECTED by the caller (the engine captures it at RUN START,
        # not write time — codex P1 on #313); the writer passes it through verbatim.
        config = WalkForwardConfig(output_dir="output/wf")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "walk_forward_report.json"
            write_aggregate_report(
                path=path, config=config, folds=[_make_fold(0)], aggregate_metrics={},
                git_provenance={"commit": "deadbeef", "dirty": False},
            )
            data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["git_commit"], "deadbeef")
        self.assertIs(data["git_dirty"], False)


# ---------------------------------------------------------------------------
# Regression for bug.md P3-27: ``write_positions`` was missing
# ``ensure_ascii=False`` while its sibling write methods
# (``write_fold_report``, ``write_aggregate_report``) included it. CJK
# instrument identifiers or path segments would round-trip through
# ``\uXXXX`` escapes inconsistently across the codebase.
# ---------------------------------------------------------------------------


class WritePositionsAsciiTests(unittest.TestCase):
    def test_write_positions_does_not_escape_cjk(self) -> None:
        import json
        import tempfile

        from src.core.walk_forward.aggregate import write_positions

        # Instrument label contains literal CJK — must survive
        # round-trip without ``\uXXXX`` escapes.
        positions = {
            "2024-01-02": {"中证500.SH": 0.4, "SH600000": 0.6},
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "positions.json"
            write_positions(path, positions)
            raw = path.read_text(encoding="utf-8")
            payload = json.loads(raw)
        self.assertIn("中证500.SH", raw, (
            "CJK instrument id was escaped to \\uXXXX — P3-27 regression: "
            "write_positions must pass ensure_ascii=False like sibling writers"
        ))
        self.assertEqual(payload["2024-01-02"]["中证500.SH"], 0.4)


if __name__ == "__main__":
    unittest.main()
