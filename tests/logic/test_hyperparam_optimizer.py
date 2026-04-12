"""Tests for src.core.hyperparam_optimizer — Optuna-based hyperparameter search."""

import unittest
from unittest.mock import patch
from pathlib import Path

from src.core.hyperparam_optimizer import (
    HyperparamOptConfig,
    HyperparamOptimizer,
    HyperparamOptimizerError,
    HyperparamSearchSpace,
)


class HyperparamOptimizerValidationTests(unittest.TestCase):
    """Unit tests that do NOT require qlib."""

    def test_rejects_when_qlib_not_initialized(self):
        with patch("src.core.hyperparam_optimizer.is_canonical_qlib_initialized", return_value=False):
            with self.assertRaises(HyperparamOptimizerError):
                HyperparamOptimizer.optimize(HyperparamOptConfig())

    def test_default_config(self):
        cfg = HyperparamOptConfig()
        self.assertEqual(cfg.n_trials, 50)
        self.assertEqual(cfg.optimization_metric, "ic_1d")

    def test_search_space_defaults(self):
        space = HyperparamSearchSpace()
        self.assertEqual(space.num_boost_round_range, (100, 2000))
        self.assertEqual(space.learning_rate_range, (0.01, 0.1))
        self.assertEqual(space.max_depth_range, (4, 12))


_QLIB_DATA_DIR = Path("D:/qlib_data/my_cn_data")


def _qlib_available():
    try:
        import qlib  # noqa: F401
        import optuna  # noqa: F401
        return _QLIB_DATA_DIR.exists()
    except ImportError:
        return False


@unittest.skipUnless(_qlib_available(), "requires qlib + optuna + local data")
class HyperparamOptimizerE2ETests(unittest.TestCase):
    """E2E test with minimal trials."""

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

    def test_3_trial_optimization(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            config = HyperparamOptConfig(
                instruments="csi300",
                train_start="2024-01-01",
                train_end="2024-06-30",
                valid_start="2024-07-01",
                valid_end="2024-09-30",
                test_start="2024-10-01",
                test_end="2024-12-31",
                n_trials=3,
                search_space=HyperparamSearchSpace(
                    num_boost_round_range=(30, 100),
                    learning_rate_range=(0.01, 0.1),
                    max_depth_range=(4, 8),
                    num_leaves_range=(31, 128),
                    early_stopping_rounds_range=(20, 50),
                ),
                output_dir=tmpdir,
            )
            result = HyperparamOptimizer.optimize(config)
            self.assertEqual(result.n_trials_completed, 3)
            self.assertIn("num_boost_round", result.best_params)
            self.assertIn("learning_rate", result.best_params)
            self.assertIsInstance(result.best_ic, float)
            self.assertEqual(len(result.all_trials), 3)


if __name__ == "__main__":
    unittest.main()
