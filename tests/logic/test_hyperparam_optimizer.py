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

    def test_rejects_invalid_optimization_metric(self):
        """Typos like 'IC_1D', 'ic1d', 'ic_5D' used to silently fall back
        to ic_1d. Regression guard for P2b."""
        for bad in ("IC_1D", "ic1d", "ic_5D", "sharpe", ""):
            with self.subTest(metric=bad):
                with self.assertRaisesRegex(
                    HyperparamOptimizerError, "optimization_metric must be one of"
                ):
                    HyperparamOptConfig(optimization_metric=bad)

    def test_accepts_both_valid_optimization_metrics(self):
        HyperparamOptConfig(optimization_metric="ic_1d")
        HyperparamOptConfig(optimization_metric="ic_5d")

    def test_rejects_non_positive_n_trials(self):
        for bad in (0, -1, -100):
            with self.subTest(n=bad):
                with self.assertRaisesRegex(HyperparamOptimizerError, "n_trials"):
                    HyperparamOptConfig(n_trials=bad)

    def test_rejects_non_int_n_trials(self):
        """bool is a subclass of int — must be rejected so True doesn't
        silently act as 1 trial."""
        for bad in (True, 1.5, "10"):
            with self.subTest(n=bad):
                with self.assertRaisesRegex(HyperparamOptimizerError, "n_trials"):
                    HyperparamOptConfig(n_trials=bad)

    def test_evaluate_params_raises_on_missing_ic_period(self):
        """If SignalAnalyzer returns an ic_summary missing period 1 or 5,
        _evaluate_params must raise rather than silently return 0.0.
        Regression guard for P1a: Optuna used to be fed spurious zeros
        and picked garbage ``best_params``."""
        from unittest.mock import MagicMock, patch
        from src.core.hyperparam_optimizer import HyperparamOptimizer

        config = HyperparamOptConfig(
            n_trials=1,
            optimization_metric="ic_1d",
        )
        fake_dataset = MagicMock(name="DatasetH")

        fake_model_result = MagicMock()
        fake_model_result.predictions = MagicMock()

        # ic_summary missing period 1 — simulates a broken analyzer call.
        fake_signal_result = MagicMock()
        fake_signal_result.ic_summary = {5: {"mean_ic": 0.03, "ir": 1.2}}

        params = {
            "num_boost_round": 100,
            "early_stopping_rounds": 20,
            "learning_rate": 0.05,
            "max_depth": 6,
            "num_leaves": 63,
        }

        with patch(
            "src.core.model_trainer.ModelTrainer.train_and_predict",
            return_value=fake_model_result,
        ), patch(
            "src.core.signal_analyzer.SignalAnalyzer.analyze",
            return_value=fake_signal_result,
        ):
            import tempfile
            with tempfile.TemporaryDirectory() as tmp:
                with self.assertRaisesRegex(
                    HyperparamOptimizerError,
                    "did not return IC for forward period",
                ):
                    HyperparamOptimizer._evaluate_params(
                        params, fake_dataset, config, Path(tmp),
                    )


class SuggestParamsClampingTests(unittest.TestCase):
    """P2e regression guards on :meth:`HyperparamOptimizer._suggest_params`.

    The LightGBM invariant is ``num_leaves <= 2**max_depth`` — violate it
    and LightGBM silently clips, making the trial's actual model shallower
    than what the user asked for. ModelTrainer's upfront validator now
    rejects this loudly (see ``test_model_trainer.ModelTrainerUpperBoundsTests``),
    which means if Optuna proposes such a combination the trial raises
    instead of returning a score — wasting Optuna's budget.

    Our fix: ``_suggest_params`` clamps the ``num_leaves`` range to
    ``[2, 2**max_depth]`` *before* calling ``trial.suggest_int``, so
    every trial Optuna runs is valid by construction.
    """

    class _StubTrial:
        """Minimal ``optuna.Trial`` stand-in that records every ``suggest_*``
        call's (name, low, high) tuple and always returns ``low`` (i.e.
        the smallest legal value). That lets us assert on the observed
        bounds without needing an Optuna installation."""

        def __init__(self, forced: dict | None = None) -> None:
            self.calls: list[tuple[str, object, object]] = []
            self._forced = forced or {}

        def suggest_int(self, name, low, high, **_kwargs):
            self.calls.append((name, low, high))
            return self._forced.get(name, low)

        def suggest_float(self, name, low, high, **_kwargs):
            self.calls.append((name, low, high))
            return self._forced.get(name, low)

    def test_num_leaves_range_clamped_to_two_pow_max_depth(self) -> None:
        """When Optuna picks ``max_depth=4``, ``num_leaves`` must be
        suggested with ``high <= 2**4 = 16`` even though the search space
        nominally allows up to 512. Otherwise Optuna is free to propose
        ``num_leaves=300`` and every such trial raises in ModelTrainer."""
        space = HyperparamSearchSpace(
            num_leaves_range=(31, 512),
            max_depth_range=(4, 12),
        )
        trial = self.__class__._StubTrial(forced={"max_depth": 4})

        HyperparamOptimizer._suggest_params(trial, space)

        # Find the num_leaves suggestion.
        num_leaves_calls = [c for c in trial.calls if c[0] == "num_leaves"]
        self.assertEqual(len(num_leaves_calls), 1)
        _, low, high = num_leaves_calls[0]
        self.assertGreaterEqual(low, 2)
        self.assertLessEqual(
            high, 16,
            f"num_leaves high={high} violates 2**max_depth=16; "
            f"Optuna would waste budget on trials ModelTrainer rejects.",
        )
        self.assertLessEqual(low, high)

    def test_num_leaves_low_floored_at_two(self) -> None:
        """Even if the configured low is 1 (or anything below 2),
        the clamped low must be >= 2 — LightGBM requires at least 2
        leaves to split."""
        space = HyperparamSearchSpace(
            num_leaves_range=(1, 4),
            max_depth_range=(4, 12),
        )
        trial = self.__class__._StubTrial(forced={"max_depth": 4})

        HyperparamOptimizer._suggest_params(trial, space)

        _, low, high = [c for c in trial.calls if c[0] == "num_leaves"][0]
        self.assertGreaterEqual(low, 2)
        self.assertLessEqual(high, 16)

    def test_num_leaves_range_preserved_for_deep_trees(self) -> None:
        """With a reasonably deep max_depth (8), the 2**8=256 cap is
        above the configured 512 only partially — the upper bound must
        be the tighter of the two (256)."""
        space = HyperparamSearchSpace(
            num_leaves_range=(31, 512),
            max_depth_range=(4, 12),
        )
        trial = self.__class__._StubTrial(forced={"max_depth": 8})

        HyperparamOptimizer._suggest_params(trial, space)

        _, low, high = [c for c in trial.calls if c[0] == "num_leaves"][0]
        self.assertLessEqual(high, 256)
        self.assertGreaterEqual(high, 31)  # should not collapse range

    def test_early_stopping_capped_at_num_boost_round(self) -> None:
        """If the early-stopping upper bound exceeds num_boost_round,
        stopping can never trigger — ModelTrainer's validator rejects
        the reverse, so Optuna must be kept below that line."""
        space = HyperparamSearchSpace(
            num_boost_round_range=(100, 100),  # pin to 100
            early_stopping_rounds_range=(20, 500),  # nominally up to 500
        )
        trial = self.__class__._StubTrial(forced={"num_boost_round": 100})

        HyperparamOptimizer._suggest_params(trial, space)

        _, low, high = [
            c for c in trial.calls if c[0] == "early_stopping_rounds"
        ][0]
        self.assertLessEqual(high, 100)
        self.assertLessEqual(low, high)


_QLIB_DATA_DIR = Path("D:/qlib_data/my_cn_data")


def _qlib_available():
    try:
        import qlib  # noqa: F401
        import optuna  # noqa: F401
        return _QLIB_DATA_DIR.exists()
    except ImportError:
        return False


from tests.e2e_guard import skip_unless_e2e

@skip_unless_e2e
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
