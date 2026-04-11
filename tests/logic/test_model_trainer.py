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


_QLIB_DATA_DIR = Path(r"D:/qlib_data/my_cn_data")
_HAS_QLIB_DATA = _QLIB_DATA_DIR.exists() and (_QLIB_DATA_DIR / "calendars").exists()


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


if __name__ == "__main__":
    unittest.main()
