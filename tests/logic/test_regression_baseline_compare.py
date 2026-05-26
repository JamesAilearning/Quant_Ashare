"""Pure-function tests for ``src.core.regression_baseline.compare_metrics``.

The E2E walk-forward regression test
(``tests/regression/test_walk_forward_aggregate_baseline``) wraps a
slow real run around this comparator. The comparator itself is pure
dict arithmetic and is tested here without ``RUN_E2E`` so the
tolerance semantics are pinned even when the heavy run is skipped.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.regression_baseline import (  # noqa: E402
    DEFAULT_RELATIVE_TOLERANCE,
    compare_metrics,
)


class CompareMetricsBasicTests(unittest.TestCase):
    def test_identical_metrics_no_drift(self):
        actual = {"ir": 0.5, "ic": 0.04}
        self.assertEqual(compare_metrics(actual, actual), [])

    def test_drift_within_tolerance_passes(self):
        # 5% default tolerance; 0.50 → 0.52 is 4% drift.
        self.assertEqual(
            compare_metrics({"ir": 0.52}, {"ir": 0.50}, tolerance=0.05),
            [],
        )

    def test_drift_exceeding_tolerance_flagged(self):
        # 0.50 → 0.60 is 20% drift, > 5%.
        drifts = compare_metrics(
            {"ir": 0.60}, {"ir": 0.50}, tolerance=0.05,
        )
        self.assertEqual(len(drifts), 1)
        self.assertIn("ir", drifts[0])
        self.assertIn("20.00%", drifts[0])

    def test_negative_baseline_uses_absolute_distance(self):
        """Drawdown values are negative; the comparator should treat
        them by relative distance just like positives. -0.10 → -0.13
        is 30% drift."""
        drifts = compare_metrics(
            {"max_drawdown": -0.13},
            {"max_drawdown": -0.10},
            tolerance=0.05,
        )
        self.assertEqual(len(drifts), 1)
        self.assertIn("max_drawdown", drifts[0])


class CompareMetricsKeysFilterTests(unittest.TestCase):
    def test_keys_filter_skips_unchecked_metrics(self):
        """When ``keys`` is supplied, metrics outside the list are
        not flagged even if they drift."""
        actual = {"ir": 0.5, "ic": 0.04, "bootstrap_seed": 999}
        baseline = {"ir": 0.5, "ic": 0.04, "bootstrap_seed": 42}
        # bootstrap_seed drifts massively but isn't in ``keys``.
        self.assertEqual(
            compare_metrics(actual, baseline, keys=("ir", "ic")),
            [],
        )

    def test_keys_filter_with_missing_key_in_baseline_flags_usage_error(self):
        drifts = compare_metrics(
            {"ir": 0.5}, {"ir": 0.5}, keys=("ir", "nonexistent"),
        )
        self.assertEqual(len(drifts), 1)
        self.assertIn("nonexistent", drifts[0])
        self.assertIn("not present in baseline", drifts[0])


class CompareMetricsNaNHandlingTests(unittest.TestCase):
    def test_baseline_nan_is_skipped(self):
        """``NaN baseline = no expectation`` — comparator must not
        flag a real actual value when the baseline is NaN."""
        self.assertEqual(
            compare_metrics({"ir": 0.5}, {"ir": float("nan")}),
            [],
        )

    def test_actual_nan_with_real_baseline_is_drift(self):
        """A run that now reports NaN where the baseline had a real
        number IS a regression."""
        drifts = compare_metrics(
            {"ir": float("nan")}, {"ir": 0.5},
        )
        self.assertEqual(len(drifts), 1)
        self.assertIn("NaN", drifts[0])


class CompareMetricsMissingKeysTests(unittest.TestCase):
    def test_missing_actual_key_is_drift(self):
        drifts = compare_metrics({}, {"ir": 0.5})
        self.assertEqual(len(drifts), 1)
        self.assertIn("missing from actual", drifts[0])

    def test_extra_actual_keys_silently_ignored(self):
        """New metrics are additive, not regressions. The comparator
        only checks keys present in the baseline."""
        self.assertEqual(
            compare_metrics({"ir": 0.5, "new_metric": 99}, {"ir": 0.5}),
            [],
        )


class CompareMetricsNonNumericTests(unittest.TestCase):
    def test_string_baseline_silently_skipped(self):
        """Provenance fields (strings) live in the same dict; skip
        them silently."""
        self.assertEqual(
            compare_metrics(
                {"bundle_tag": "2026-04-01", "ir": 0.5},
                {"bundle_tag": "2026-03-01", "ir": 0.5},
            ),
            [],
        )

    def test_bool_baseline_treated_as_non_numeric(self):
        """``bool`` is an int subclass — make sure True/False values
        in the baseline don't get into the numeric comparison."""
        self.assertEqual(
            compare_metrics({"flag": True}, {"flag": False}),
            [],
        )

    def test_actual_non_numeric_with_numeric_baseline_is_drift(self):
        drifts = compare_metrics({"ir": "missing"}, {"ir": 0.5})
        self.assertEqual(len(drifts), 1)
        self.assertIn("non-numeric", drifts[0])

    def test_non_numeric_baseline_with_explicit_keys_is_drift(self):
        """Codex P2 on PR #166: when the caller explicitly lists a key
        in ``keys``, a non-numeric baseline value at that key is a
        malformed fixture — must flag, not silently skip. Otherwise
        an entirely-non-numeric baseline would pass the drift check
        with zero effective comparisons (false green)."""
        drifts = compare_metrics(
            {"ir": 0.5},
            {"ir": "0.12"},  # baseline accidentally a string
            keys=("ir",),
        )
        self.assertEqual(len(drifts), 1)
        self.assertIn("non-numeric", drifts[0])
        # Helpful message points operator at the regenerate path.
        self.assertIn("malformed", drifts[0])

    def test_non_numeric_baseline_without_explicit_keys_still_skipped(self):
        """The default mode (``keys=None``) tolerates non-numeric
        baseline values — the dict often carries provenance + metrics
        side-by-side. Only the explicit-keys path treats it as drift."""
        self.assertEqual(
            compare_metrics({"ir": 0.5}, {"ir": "0.12"}),
            [],
        )

    def test_all_requested_keys_non_numeric_fails_loudly(self):
        """Regression for the false-green scenario Codex described:
        every requested key has a non-numeric baseline → 100% of
        comparisons "skip" → test passes with zero coverage. The
        fix surfaces N drifts, one per malformed key."""
        baseline = {"ir": "0.5", "ic": "0.04"}
        drifts = compare_metrics(
            {"ir": 0.99, "ic": 0.99},
            baseline,
            keys=("ir", "ic"),
        )
        self.assertEqual(len(drifts), 2)
        self.assertTrue(all("non-numeric" in d for d in drifts))


class CompareMetricsNearZeroBaselineTests(unittest.TestCase):
    def test_near_zero_baseline_uses_absolute_tolerance(self):
        """Relative tolerance is meaningless when baseline ≈ 0; the
        comparator falls back to absolute tolerance."""
        # baseline = 0, actual = 0.01, abs_tol default = 0.05 → OK
        self.assertEqual(
            compare_metrics({"ir": 0.01}, {"ir": 0.0}),
            [],
        )
        # baseline = 0, actual = 0.10, abs_tol = 0.05 → drift
        drifts = compare_metrics({"ir": 0.10}, {"ir": 0.0})
        self.assertEqual(len(drifts), 1)
        self.assertIn("baseline≈0", drifts[0])

    def test_absolute_tolerance_override(self):
        # tolerance=0.05 default, absolute_tolerance=0.02 → tighter
        drifts = compare_metrics(
            {"ir": 0.03},
            {"ir": 0.0},
            absolute_tolerance=0.02,
        )
        self.assertEqual(len(drifts), 1)


class CompareMetricsDefaultsTests(unittest.TestCase):
    def test_default_tolerance_is_5_percent(self):
        """5% is the documented default; lock it in."""
        self.assertAlmostEqual(DEFAULT_RELATIVE_TOLERANCE, 0.05)


if __name__ == "__main__":
    unittest.main()
