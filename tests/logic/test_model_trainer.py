"""Unit tests for ModelTrainer."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.model_trainer import (
    ModelTrainer,
    ModelTrainerError,
    ModelTrainConfig,
)


class ModelTrainerStructuralTests(unittest.TestCase):
    """Structural validation — no qlib needed."""

    def test_unsupported_model_type_rejected(self) -> None:
        with patch("src.core.model_trainer.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaisesRegex(ModelTrainerError, "model_type"):
                ModelTrainer.train_and_predict(
                    config=ModelTrainConfig(model_type="RandomForest"),
                    dataset=None,
                    model_artifact_path="/tmp/model.pkl",
                )

    def test_negative_num_boost_round_rejected(self) -> None:
        with patch("src.core.model_trainer.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaisesRegex(ModelTrainerError, "num_boost_round"):
                ModelTrainer.train_and_predict(
                    config=ModelTrainConfig(model_type="LGBModel", num_boost_round=0),
                    dataset=None,
                    model_artifact_path="/tmp/model.pkl",
                )

    def test_negative_learning_rate_rejected(self) -> None:
        with patch("src.core.model_trainer.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaisesRegex(ModelTrainerError, "learning_rate"):
                ModelTrainer.train_and_predict(
                    config=ModelTrainConfig(model_type="LGBModel", learning_rate=-0.01),
                    dataset=None,
                    model_artifact_path="/tmp/model.pkl",
                )

    def test_empty_artifact_path_rejected(self) -> None:
        with patch("src.core.model_trainer.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaisesRegex(ModelTrainerError, "model_artifact_path"):
                ModelTrainer.train_and_predict(
                    config=ModelTrainConfig(model_type="LGBModel"),
                    dataset=None,
                    model_artifact_path="",
                )

    def test_qlib_not_initialized_rejected(self) -> None:
        with patch("src.core.model_trainer.is_canonical_qlib_initialized", return_value=False):
            with self.assertRaisesRegex(ModelTrainerError, "not initialized"):
                ModelTrainer.train_and_predict(
                    config=ModelTrainConfig(model_type="LGBModel"),
                    dataset=None,
                    model_artifact_path="/tmp/model.pkl",
                )

    def test_negative_early_stopping_rejected(self) -> None:
        with patch("src.core.model_trainer.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaisesRegex(ModelTrainerError, "early_stopping_rounds"):
                ModelTrainer.train_and_predict(
                    config=ModelTrainConfig(model_type="LGBModel", early_stopping_rounds=0),
                    dataset=None,
                    model_artifact_path="/tmp/model.pkl",
                )

    def test_non_int_seed_rejected(self) -> None:
        with patch("src.core.model_trainer.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaisesRegex(ModelTrainerError, "seed"):
                ModelTrainer.train_and_predict(
                    config=ModelTrainConfig(model_type="LGBModel", seed=3.14),  # type: ignore[arg-type]
                    dataset=None,
                    model_artifact_path="/tmp/model.pkl",
                )

    def test_bool_seed_rejected(self) -> None:
        # bool is subtype of int; must be explicitly rejected.
        with patch("src.core.model_trainer.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaisesRegex(ModelTrainerError, "seed"):
                ModelTrainer.train_and_predict(
                    config=ModelTrainConfig(model_type="LGBModel", seed=True),  # type: ignore[arg-type]
                    dataset=None,
                    model_artifact_path="/tmp/model.pkl",
                )


class FitDispatchTests(unittest.TestCase):
    """_fit_dispatch must pass LGB-only kwargs only to LGBModel."""

    def _make_model(self):
        class _M:
            def __init__(self):
                self.fit_calls = []
            def fit(self, dataset, **kwargs):
                self.fit_calls.append((dataset, kwargs))
        return _M()

    def test_lgb_receives_extra_kwargs(self) -> None:
        model = self._make_model()
        evals: dict = {}
        ModelTrainer._fit_dispatch(
            model, dataset="DS",
            config=ModelTrainConfig(model_type="LGBModel", num_boost_round=7, early_stopping_rounds=3),
            evals_result=evals,
        )
        self.assertEqual(len(model.fit_calls), 1)
        dataset_arg, kwargs = model.fit_calls[0]
        self.assertEqual(dataset_arg, "DS")
        self.assertEqual(kwargs["num_boost_round"], 7)
        self.assertEqual(kwargs["early_stopping_rounds"], 3)
        self.assertIs(kwargs["evals_result"], evals)

    def test_xgb_receives_only_dataset(self) -> None:
        model = self._make_model()
        ModelTrainer._fit_dispatch(
            model, dataset="DS",
            config=ModelTrainConfig(model_type="XGBModel"),
            evals_result={},
        )
        _, kwargs = model.fit_calls[0]
        self.assertEqual(kwargs, {})  # no extra kwargs forwarded

    def test_catboost_receives_only_dataset(self) -> None:
        model = self._make_model()
        ModelTrainer._fit_dispatch(
            model, dataset="DS",
            config=ModelTrainConfig(model_type="CatBoostModel"),
            evals_result={},
        )
        _, kwargs = model.fit_calls[0]
        self.assertEqual(kwargs, {})


class TrainingDiagnosticsTests(unittest.TestCase):
    """_extract_training_diagnostics best-effort extraction."""

    def test_lgb_best_iteration_from_inner_model(self) -> None:
        class _Inner: best_iteration = 42
        class _M: model = _Inner()
        best_iter, _ = ModelTrainer._extract_training_diagnostics(_M(), "LGBModel", {})
        self.assertEqual(best_iter, 42)

    def test_xgb_best_iteration_from_inner_model(self) -> None:
        class _Inner: best_iteration = 17
        class _M: model = _Inner()
        best_iter, _ = ModelTrainer._extract_training_diagnostics(_M(), "XGBModel", {})
        self.assertEqual(best_iter, 17)

    def test_catboost_best_iteration_via_getter(self) -> None:
        class _Inner:
            def get_best_iteration(self): return 99
        class _M: model = _Inner()
        best_iter, _ = ModelTrainer._extract_training_diagnostics(_M(), "CatBoostModel", {})
        self.assertEqual(best_iter, 99)

    def test_missing_inner_returns_none(self) -> None:
        class _M: model = None
        best_iter, final_val = ModelTrainer._extract_training_diagnostics(_M(), "LGBModel", {})
        self.assertIsNone(best_iter)
        self.assertIsNone(final_val)

    def test_final_valid_loss_from_evals_result(self) -> None:
        class _Inner: best_iteration = 3
        class _M: model = _Inner()
        evals = {"valid": {"l2": [0.5, 0.4, 0.3, 0.35]}}
        best_iter, final_val = ModelTrainer._extract_training_diagnostics(_M(), "LGBModel", evals)
        self.assertEqual(best_iter, 3)
        # best_iter=3 → values[2] = 0.3
        self.assertAlmostEqual(final_val, 0.3)

    def test_diagnostic_extraction_never_raises(self) -> None:
        # Malformed evals_result must not poison the output.
        class _M: model = None
        best_iter, final_val = ModelTrainer._extract_training_diagnostics(
            _M(), "LGBModel", {"valid": "not a dict"},
        )
        self.assertIsNone(best_iter)
        self.assertIsNone(final_val)


class SeedEverythingTests(unittest.TestCase):
    def test_seed_sets_python_and_numpy(self) -> None:
        import random as _random
        from src.core.model_trainer import _seed_everything
        _seed_everything(1234)
        a1 = _random.random()
        _seed_everything(1234)
        a2 = _random.random()
        self.assertEqual(a1, a2)

        try:
            import numpy as np
        except ImportError:
            return
        _seed_everything(1234)
        n1 = np.random.rand(3).tolist()
        _seed_everything(1234)
        n2 = np.random.rand(3).tolist()
        self.assertEqual(n1, n2)


_QLIB_DATA_DIR = Path(r"D:/qlib_data/my_cn_data")
_HAS_QLIB_DATA = _QLIB_DATA_DIR.exists() and (_QLIB_DATA_DIR / "calendars").exists()


from tests.e2e_guard import skip_unless_e2e

@skip_unless_e2e
@unittest.skipUnless(_HAS_QLIB_DATA, "qlib data bundle not available")
class ModelTrainerE2ETests(unittest.TestCase):
    """E2E tests that require real qlib data."""

    _dataset = None

    @classmethod
    def setUpClass(cls) -> None:
        from src.core.qlib_runtime import (
            QlibRuntimeConfig,
            init_qlib_canonical,
            is_canonical_qlib_initialized,
        )
        if not is_canonical_qlib_initialized():
            init_qlib_canonical(QlibRuntimeConfig(
                provider_uri=str(_QLIB_DATA_DIR), region="cn",
            ))

        from src.data.feature_dataset_builder import (
            FeatureDatasetBuilder,
            FeatureDatasetConfig,
        )
        result = FeatureDatasetBuilder.build(FeatureDatasetConfig(
            instruments="csi300",
            feature_handler="Alpha158",
            train_start="2024-01-01", train_end="2025-06-30",
            valid_start="2025-07-01", valid_end="2025-09-30",
            test_start="2025-10-01", test_end="2025-12-31",
        ))
        cls._dataset = result.dataset

    def test_lgb_train_and_predict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = ModelTrainer.train_and_predict(
                config=ModelTrainConfig(
                    model_type="LGBModel",
                    num_boost_round=50,
                    early_stopping_rounds=10,
                ),
                dataset=self._dataset,
                model_artifact_path=str(Path(tmp) / "model.pkl"),
            )
            self.assertGreater(result.prediction_shape[0], 0)
            self.assertTrue(Path(result.model_artifact_path).exists())

    def test_model_pickle_is_loadable(self) -> None:
        import pickle
        with tempfile.TemporaryDirectory() as tmp:
            result = ModelTrainer.train_and_predict(
                config=ModelTrainConfig(
                    model_type="LGBModel",
                    num_boost_round=20,
                    early_stopping_rounds=5,
                ),
                dataset=self._dataset,
                model_artifact_path=str(Path(tmp) / "model.pkl"),
            )
            with open(result.model_artifact_path, "rb") as f:
                loaded = pickle.load(f)
            self.assertIsNotNone(loaded)

    def test_xgb_train_and_predict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = ModelTrainer.train_and_predict(
                config=ModelTrainConfig(
                    model_type="XGBModel",
                    num_boost_round=30,
                    early_stopping_rounds=10,
                ),
                dataset=self._dataset,
                model_artifact_path=str(Path(tmp) / "xgb_model.pkl"),
            )
            self.assertGreater(result.prediction_shape[0], 0)
            self.assertTrue(Path(result.model_artifact_path).exists())

    def test_catboost_train_and_predict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = ModelTrainer.train_and_predict(
                config=ModelTrainConfig(
                    model_type="CatBoostModel",
                    num_boost_round=30,
                    early_stopping_rounds=10,
                ),
                dataset=self._dataset,
                model_artifact_path=str(Path(tmp) / "cat_model.pkl"),
            )
            self.assertGreater(result.prediction_shape[0], 0)
            self.assertTrue(Path(result.model_artifact_path).exists())


if __name__ == "__main__":
    unittest.main()
