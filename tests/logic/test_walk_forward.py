"""Tests for src.core.walk_forward — walk-forward rolling backtest engine."""

import unittest
from unittest.mock import patch

from src.core.walk_forward import (
    WalkForwardConfig,
    WalkForwardEngine,
    WalkForwardError,
)


class WalkForwardValidationTests(unittest.TestCase):
    """Unit tests that do NOT require qlib."""

    def test_rejects_when_qlib_not_initialized(self):
        with patch("src.core.walk_forward.is_canonical_qlib_initialized", return_value=False):
            with self.assertRaises(WalkForwardError):
                WalkForwardEngine.run(WalkForwardConfig())

    def test_window_generation(self):
        config = WalkForwardConfig(
            overall_start="2022-01-01",
            overall_end="2025-12-31",
            train_months=24,
            valid_months=3,
            test_months=3,
            step_months=3,
        )
        windows = WalkForwardEngine._generate_windows(config)
        # First fold: train 2022-01-01~2023-12-31, valid 2024-01-01~2024-03-31, test 2024-04-01~2024-06-30
        self.assertGreater(len(windows), 0)
        first = windows[0]
        self.assertEqual(first[0], "2022-01-01")  # train_start
        self.assertEqual(first[1], "2023-12-31")  # train_end
        self.assertEqual(first[2], "2024-01-01")  # valid_start
        self.assertEqual(first[3], "2024-03-31")  # valid_end
        self.assertEqual(first[4], "2024-04-01")  # test_start
        self.assertEqual(first[5], "2024-06-30")  # test_end

    def test_window_generation_too_short(self):
        config = WalkForwardConfig(
            overall_start="2024-01-01",
            overall_end="2024-06-30",  # too short for 24m train + 3m valid + 3m test
            train_months=24,
            valid_months=3,
            test_months=3,
        )
        windows = WalkForwardEngine._generate_windows(config)
        self.assertEqual(len(windows), 0)

    def test_default_config_values(self):
        cfg = WalkForwardConfig()
        self.assertEqual(cfg.train_months, 24)
        self.assertEqual(cfg.step_months, 3)
        self.assertEqual(cfg.model_type, "LGBModel")

    def test_multiple_folds_generated(self):
        config = WalkForwardConfig(
            overall_start="2020-01-01",
            overall_end="2025-12-31",
            train_months=24,
            valid_months=3,
            test_months=3,
            step_months=3,
        )
        windows = WalkForwardEngine._generate_windows(config)
        # Should have multiple folds
        self.assertGreater(len(windows), 3)
        # Non-overlapping test periods
        for i in range(1, len(windows)):
            prev_test_end = windows[i-1][5]
            curr_test_start = windows[i][4]
            self.assertLessEqual(prev_test_end, curr_test_start)


class WalkForwardConfigValidationTests(unittest.TestCase):
    """Regression guard for P1b: zero-length windows would hang the engine.

    ``step_months=0`` used to put ``_generate_windows`` into an infinite
    loop; ``train_months=0`` would silently make folds with no fit data.
    """

    def test_rejects_zero_step_months(self):
        with self.assertRaisesRegex(WalkForwardError, "step_months"):
            WalkForwardConfig(step_months=0)

    def test_rejects_zero_train_months(self):
        with self.assertRaisesRegex(WalkForwardError, "train_months"):
            WalkForwardConfig(train_months=0)

    def test_rejects_zero_valid_months(self):
        with self.assertRaisesRegex(WalkForwardError, "valid_months"):
            WalkForwardConfig(valid_months=0)

    def test_rejects_zero_test_months(self):
        with self.assertRaisesRegex(WalkForwardError, "test_months"):
            WalkForwardConfig(test_months=0)

    def test_rejects_negative_months(self):
        with self.assertRaisesRegex(WalkForwardError, "step_months"):
            WalkForwardConfig(step_months=-1)

    def test_rejects_non_int_months(self):
        """Also catches bool → would silently act as 1."""
        with self.assertRaisesRegex(WalkForwardError, "step_months must be int"):
            WalkForwardConfig(step_months=True)
        with self.assertRaisesRegex(WalkForwardError, "train_months must be int"):
            WalkForwardConfig(train_months=24.0)

    def test_rejects_bad_overall_start(self):
        with self.assertRaisesRegex(WalkForwardError, "overall_start"):
            WalkForwardConfig(overall_start="not-a-date")

    def test_rejects_bad_overall_end(self):
        with self.assertRaisesRegex(WalkForwardError, "overall_end"):
            WalkForwardConfig(overall_end="2024/01/01")

    def test_rejects_end_before_start(self):
        with self.assertRaisesRegex(WalkForwardError, "must be strictly after"):
            WalkForwardConfig(
                overall_start="2025-01-01", overall_end="2024-12-31",
            )

    def test_rejects_end_equal_start(self):
        with self.assertRaisesRegex(WalkForwardError, "must be strictly after"):
            WalkForwardConfig(
                overall_start="2025-01-01", overall_end="2025-01-01",
            )

    def test_rejects_n_drop_gte_topk(self):
        """n_drop must leave some positions after a rebalance."""
        with self.assertRaisesRegex(WalkForwardError, "n_drop"):
            WalkForwardConfig(topk=10, n_drop=10)
        with self.assertRaisesRegex(WalkForwardError, "n_drop"):
            WalkForwardConfig(topk=10, n_drop=15)

    def test_rejects_non_positive_topk(self):
        with self.assertRaisesRegex(WalkForwardError, "topk"):
            WalkForwardConfig(topk=0)
        with self.assertRaisesRegex(WalkForwardError, "topk"):
            WalkForwardConfig(topk=-5)

    def test_rejects_negative_n_drop(self):
        with self.assertRaisesRegex(WalkForwardError, "n_drop"):
            WalkForwardConfig(n_drop=-1)


from pathlib import Path

_QLIB_DATA_DIR = Path("D:/qlib_data/my_cn_data")


def _qlib_available():
    try:
        import qlib  # noqa: F401
        return _QLIB_DATA_DIR.exists()
    except ImportError:
        return False


from tests.e2e_guard import skip_unless_e2e

@skip_unless_e2e
@unittest.skipUnless(_qlib_available(), "requires qlib + local data bundle")
class WalkForwardE2ETests(unittest.TestCase):
    """E2E test with 2 folds using short windows."""

    @classmethod
    def setUpClass(cls):
        from src.core.qlib_runtime import (
            QlibRuntimeConfig,
            init_qlib_canonical,
            is_canonical_qlib_initialized,
        )
        if not is_canonical_qlib_initialized():
            init_qlib_canonical(QlibRuntimeConfig(
                provider_uri=str(_QLIB_DATA_DIR), region="cn",
            ))

    def test_two_fold_walk_forward(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            config = WalkForwardConfig(
                instruments="csi300",
                overall_start="2024-01-01",
                overall_end="2025-06-30",
                train_months=6,
                valid_months=3,
                test_months=3,
                step_months=3,
                num_boost_round=30,  # fast
                benchmark_code="SH600000",
                output_dir=tmpdir,
            )
            result = WalkForwardEngine.run(config)
            self.assertGreaterEqual(result.num_folds, 2)
            self.assertEqual(len(result.folds), result.num_folds)
            # Check fold structure
            for fold in result.folds:
                self.assertIsNotNone(fold.ic_1d)
                self.assertIsNotNone(fold.annualized_return)
            # Aggregate metrics
            self.assertIn("mean_ic_1d", result.aggregate_metrics)
            self.assertIn("mean_annualized_return", result.aggregate_metrics)


if __name__ == "__main__":
    unittest.main()
