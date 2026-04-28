"""Tests for ``walk_forward_compare`` — diffing two walk-forward runs."""

from __future__ import annotations

import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.walk_forward_compare import (  # noqa: E402
    AggregateDiff,
    ConfigDiff,
    FoldCountSummary,
    FoldDiff,
    MetricDiff,
    WalkForwardComparison,
    WalkForwardCompareError,
    _classify_improvement,
    compare_reports,
    format_comparison,
    load_report,
    to_dict,
    write_comparison,
)


# ---------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------


def _baseline_report() -> dict:
    """Mirror what ``WalkForwardEngine._build_aggregate_report`` writes —
    config + fold list + aggregate metrics. Two folds is enough to
    exercise pairing, period mismatch, and aggregate diffs."""
    return {
        "generated_at": "2026-04-25T22:00:00",
        "config": {
            "instruments": "csi300",
            "learning_rate": 0.0421,
            "num_leaves": 210,
            "lambda_l2": 0.0,
        },
        "folds": [
            {
                "fold_index": 0,
                "test_period": "2024-04-01 ~ 2024-06-30",
                "ic_1d": 0.012,
                "ic_5d": 0.018,
                "annualized_return": -0.05,
                "max_drawdown": -0.04,
                "information_ratio": -0.5,
            },
            {
                "fold_index": 1,
                "test_period": "2024-07-01 ~ 2024-09-30",
                "ic_1d": 0.009,
                "ic_5d": 0.012,
                "annualized_return": -0.15,
                "max_drawdown": -0.08,
                "information_ratio": -1.2,
            },
        ],
        "aggregate_metrics": {
            "mean_ic_1d": 0.0146,
            "mean_information_ratio": -0.18,
            "worst_drawdown": -0.0881,
            "num_folds": 2.0,
        },
        "num_folds": 2,
    }


def _variant_report() -> dict:
    """Same shape as baseline but with config knobs and metrics shifted —
    fold 0 improves, fold 1 degrades on most metrics. Lets a single
    test exercise both directions of the improvement classifier."""
    return {
        "generated_at": "2026-04-25T22:30:00",
        "config": {
            "instruments": "csi300",   # unchanged
            "learning_rate": 0.005,    # ↓
            "num_leaves": 64,          # ↓
            "lambda_l2": 1.0,          # ↑
        },
        "folds": [
            {
                "fold_index": 0,
                "test_period": "2024-04-01 ~ 2024-06-30",
                "ic_1d": 0.026,                # +
                "ic_5d": 0.028,                # +
                "annualized_return": 0.07,     # +
                "max_drawdown": -0.02,         # less negative → +
                "information_ratio": 0.8,      # +
            },
            {
                "fold_index": 1,
                "test_period": "2024-07-01 ~ 2024-09-30",
                "ic_1d": 0.005,                # ↓
                "ic_5d": 0.008,                # ↓
                "annualized_return": -0.20,    # ↓
                "max_drawdown": -0.10,         # more negative → ↓
                "information_ratio": -1.5,     # ↓
            },
        ],
        "aggregate_metrics": {
            "mean_ic_1d": 0.0133,
            "mean_information_ratio": -0.31,
            "worst_drawdown": -0.10,
            "num_folds": 2.0,
        },
        "num_folds": 2,
    }


def _write(path: Path, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


# ---------------------------------------------------------------------
# Improvement classifier
# ---------------------------------------------------------------------


class ClassifyImprovementTests(unittest.TestCase):
    """The metric-direction map decides whether a delta is improvement.

    Drift in this map is the highest-impact bug surface for the
    comparator: a metric mis-classified as ``higher_is_better`` would
    turn a real regression into a green ``↑`` arrow on the dashboard.
    These tests pin every metric's direction.
    """

    def test_higher_is_better_metrics_classify_positive_delta_as_improved(self) -> None:
        for name in ("ic_1d", "ic_5d", "annualized_return", "information_ratio",
                     "mean_ic_1d", "mean_information_ratio"):
            self.assertTrue(_classify_improvement(name, +0.01),
                            f"+0.01 on {name} should be improvement")
            self.assertFalse(_classify_improvement(name, -0.01),
                             f"-0.01 on {name} should be degradation")

    def test_drawdown_classified_as_higher_is_better(self) -> None:
        """Drawdowns are reported as negative numbers; a *less negative*
        delta (positive) means the loss got smaller in magnitude."""
        self.assertTrue(_classify_improvement("max_drawdown", +0.02))
        self.assertTrue(_classify_improvement("worst_drawdown", +0.02))
        self.assertFalse(_classify_improvement("max_drawdown", -0.02))

    def test_std_ic_classified_as_lower_is_better(self) -> None:
        self.assertTrue(_classify_improvement("std_ic_1d", -0.01))
        self.assertFalse(_classify_improvement("std_ic_1d", +0.01))

    def test_unclassified_metric_returns_none(self) -> None:
        # ``num_folds`` is not directional — a delta shouldn't be
        # classified as improvement either way.
        self.assertIsNone(_classify_improvement("num_folds", +1.0))
        self.assertIsNone(_classify_improvement("num_folds", -1.0))

    def test_zero_delta_returns_none(self) -> None:
        """Sub-epsilon deltas are noise; classifier returns ``None``
        (neither improved nor degraded) so summary counts stay honest."""
        self.assertIsNone(_classify_improvement("ic_1d", 0.0))
        self.assertIsNone(_classify_improvement("ic_1d", 1e-12))

    def test_nan_delta_returns_none(self) -> None:
        self.assertIsNone(_classify_improvement("ic_1d", float("nan")))


# ---------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------


class LoadReportTests(unittest.TestCase):
    def test_rejects_missing_file(self) -> None:
        with self.assertRaisesRegex(WalkForwardCompareError, "does not exist"):
            load_report("/no/such/path.json")

    def test_rejects_non_object_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            with open(path, "w") as f:
                json.dump([1, 2, 3], f)
            with self.assertRaisesRegex(
                WalkForwardCompareError, "must be a JSON object"
            ):
                load_report(path)

    def test_rejects_missing_required_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            with open(path, "w") as f:
                json.dump({"folds": [], "aggregate_metrics": {}}, f)
            with self.assertRaisesRegex(WalkForwardCompareError, "config"):
                load_report(path)


# ---------------------------------------------------------------------
# End-to-end ``compare_reports``
# ---------------------------------------------------------------------


class CompareReportsTests(unittest.TestCase):
    """Full happy-path: two on-disk reports → :class:`WalkForwardComparison`."""

    def _compare(self) -> WalkForwardComparison:
        with tempfile.TemporaryDirectory() as tmp:
            b = Path(tmp) / "baseline.json"
            v = Path(tmp) / "variant.json"
            _write(b, _baseline_report())
            _write(v, _variant_report())
            return compare_reports(b, v)

    def test_config_diffs_only_list_differing_fields(self) -> None:
        cmp = self._compare()
        diff_fields = {d.field for d in cmp.config_diffs}
        # learning_rate / num_leaves / lambda_l2 changed; instruments did not.
        self.assertEqual(diff_fields, {"learning_rate", "num_leaves", "lambda_l2"})

    def test_fold_summary_reflects_overlap(self) -> None:
        cmp = self._compare()
        self.assertEqual(cmp.fold_summary.baseline_folds, 2)
        self.assertEqual(cmp.fold_summary.variant_folds, 2)
        self.assertEqual(cmp.fold_summary.overlap, 2)
        self.assertEqual(cmp.fold_summary.baseline_only_indices, ())
        self.assertEqual(cmp.fold_summary.variant_only_indices, ())

    def test_fold_zero_improved_across_metrics(self) -> None:
        cmp = self._compare()
        fold0 = cmp.fold_diffs[0]
        for metric in ("ic_1d", "annualized_return", "information_ratio",
                       "max_drawdown"):
            m = fold0.metrics[metric]
            self.assertTrue(m.improved, f"fold0.{metric} should be improved")

    def test_fold_one_degraded_across_metrics(self) -> None:
        cmp = self._compare()
        fold1 = cmp.fold_diffs[1]
        for metric in ("ic_1d", "annualized_return", "information_ratio",
                       "max_drawdown"):
            m = fold1.metrics[metric]
            self.assertFalse(m.improved, f"fold1.{metric} should be degraded")

    def test_aggregate_diff_carries_signed_delta(self) -> None:
        cmp = self._compare()
        ir = cmp.aggregate_diffs["mean_information_ratio"]
        self.assertAlmostEqual(ir.baseline, -0.18)
        self.assertAlmostEqual(ir.variant, -0.31)
        self.assertAlmostEqual(ir.delta, -0.13, places=5)
        # IR is higher_is_better — delta < 0 means degraded.
        self.assertFalse(ir.improved)


class FoldPairingMismatchTests(unittest.TestCase):
    """When two runs cover different windows, the comparator must
    surface the orphan folds via ``FoldCountSummary`` rather than
    silently aligning by list position. Pairing by ``fold_index``
    (not list index) is the regression guard here.
    """

    def test_baseline_only_fold_reported(self) -> None:
        b = _baseline_report()
        v = _variant_report()
        # variant lacks fold_index=1
        v["folds"] = [v["folds"][0]]
        v["num_folds"] = 1

        with tempfile.TemporaryDirectory() as tmp:
            bp = Path(tmp) / "b.json"
            vp = Path(tmp) / "v.json"
            _write(bp, b)
            _write(vp, v)
            cmp = compare_reports(bp, vp)

        self.assertEqual(cmp.fold_summary.overlap, 1)
        self.assertEqual(cmp.fold_summary.baseline_only_indices, (1,))
        self.assertEqual(cmp.fold_summary.variant_only_indices, ())
        # Only the overlapping fold gets a diff entry.
        self.assertEqual([f.fold_index for f in cmp.fold_diffs], [0])

    def test_test_period_mismatch_flagged(self) -> None:
        """Folds with the same fold_index but different test_period
        strings are paired — but the mismatch is surfaced so the
        operator knows the deltas aren't strictly comparable."""
        b = _baseline_report()
        v = _variant_report()
        v["folds"][0]["test_period"] = "2024-05-01 ~ 2024-07-31"  # shifted

        with tempfile.TemporaryDirectory() as tmp:
            bp = Path(tmp) / "b.json"
            vp = Path(tmp) / "v.json"
            _write(bp, b)
            _write(vp, v)
            cmp = compare_reports(bp, vp)

        self.assertFalse(cmp.fold_diffs[0].test_period_match)
        self.assertTrue(cmp.fold_diffs[1].test_period_match)


# ---------------------------------------------------------------------
# Console rendering
# ---------------------------------------------------------------------


class FormatComparisonTests(unittest.TestCase):
    """``format_comparison`` is the operator-facing text. We pin
    enough of its content that a future cosmetic edit cannot silently
    drop a section a dashboard relies on.
    """

    def _render(self) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            b = Path(tmp) / "b.json"
            v = Path(tmp) / "v.json"
            _write(b, _baseline_report())
            _write(v, _variant_report())
            return format_comparison(compare_reports(b, v))

    def test_output_lists_changed_config_keys(self) -> None:
        text = self._render()
        self.assertIn("learning_rate", text)
        self.assertIn("num_leaves", text)
        self.assertIn("lambda_l2", text)

    def test_output_includes_fold_metric_columns(self) -> None:
        text = self._render()
        self.assertIn("ic_1d", text)
        self.assertIn("information_ratio", text)
        self.assertIn("max_drawdown", text)

    def test_output_summary_counts_present(self) -> None:
        text = self._render()
        self.assertIn("improved", text.lower())
        self.assertIn("degraded", text.lower())

    def test_output_aggregate_section_present(self) -> None:
        text = self._render()
        self.assertIn("Aggregate metrics", text)
        self.assertIn("mean_information_ratio", text)


# ---------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------


class WriteComparisonTests(unittest.TestCase):
    """``write_comparison`` must produce strict JSON (no NaN tokens),
    same convention as the rest of the report-writing surface."""

    def test_round_trip_through_strict_json(self) -> None:
        # Inject a NaN aggregate metric to exercise the sanitiser.
        b = _baseline_report()
        v = _variant_report()
        v["aggregate_metrics"]["mean_ic_5d"] = float("nan")

        with tempfile.TemporaryDirectory() as tmp:
            bp = Path(tmp) / "b.json"
            vp = Path(tmp) / "v.json"
            out = Path(tmp) / "compare.json"
            _write(bp, b)
            # ``json.dump`` with default options would emit NaN; route
            # it through the sanitiser path used by the report writers.
            with open(vp, "w") as f:
                json.dump(v, f, default=lambda x: None)

            cmp = compare_reports(bp, vp)
            write_comparison(cmp, out)
            with open(out) as f:
                loaded = json.load(f)

        # ``baseline_path`` round-trips as the original string.
        self.assertEqual(loaded["baseline_path"], str(bp))
        # NaN aggregate landed as null per ``_sanitize_for_json``.
        self.assertIsNone(loaded["aggregate_diffs"]["mean_ic_5d"]["variant"])

    def test_to_dict_structure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            b = Path(tmp) / "b.json"
            v = Path(tmp) / "v.json"
            _write(b, _baseline_report())
            _write(v, _variant_report())
            cmp = compare_reports(b, v)

        d = to_dict(cmp)
        for key in ("baseline_path", "variant_path", "config_diffs",
                    "fold_summary", "fold_diffs", "aggregate_diffs"):
            self.assertIn(key, d)
        self.assertIsInstance(d["fold_diffs"], list)
        self.assertIsInstance(d["aggregate_diffs"], dict)


if __name__ == "__main__":
    unittest.main()
