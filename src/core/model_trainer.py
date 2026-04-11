"""Model trainer — thin wrapper around qlib LGBModel for training and prediction.

Provides a contract-friendly interface that accepts a DatasetH from
``FeatureDatasetBuilder`` and produces predictions + a serialized model
artifact.

Boundaries
----------
- This module does NOT call ``qlib.init``. Callers must initialize via
  ``src.core.qlib_runtime.init_qlib_canonical`` first.
- Importing this module does NOT import qlib. The qlib import is lazy.
"""

from __future__ import annotations

import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from src.core.qlib_runtime import is_canonical_qlib_initialized


class ModelTrainerError(RuntimeError):
    """Raised on structural misuse or training failures."""


SUPPORTED_MODEL_TYPES = ("LGBModel",)


@dataclass(frozen=True)
class ModelTrainConfig:
    """Frozen configuration for model training."""

    model_type: str
    num_boost_round: int = 1000
    early_stopping_rounds: int = 50
    learning_rate: float = 0.0421
    max_depth: int = 8
    num_leaves: int = 210


@dataclass(frozen=True)
class ModelTrainResult:
    """Result of model training and prediction."""

    predictions: Any  # pd.Series with (datetime, instrument) MultiIndex
    model_artifact_path: str
    train_metrics: Mapping[str, Any]
    prediction_shape: tuple[int, ...]


class ModelTrainer:
    """Trains a qlib model and generates predictions.

    Usage::

        result = ModelTrainer.train_and_predict(
            config=ModelTrainConfig(model_type="LGBModel"),
            dataset=feature_result.dataset,
            model_artifact_path="output/model.pkl",
        )
        predictions = result.predictions  # pass to BacktestRunner
    """

    @classmethod
    def train_and_predict(
        cls,
        *,
        config: ModelTrainConfig,
        dataset: Any,
        model_artifact_path: str,
        predict_segment: str = "test",
    ) -> ModelTrainResult:
        cls._validate(config, model_artifact_path)

        try:
            from qlib.contrib.model.gbdt import LGBModel  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ModelTrainerError(
                "qlib is not importable; cannot train model."
            ) from exc

        if config.model_type == "LGBModel":
            model = LGBModel(
                loss="mse",
                num_boost_round=config.num_boost_round,
                early_stopping_rounds=config.early_stopping_rounds,
                learning_rate=config.learning_rate,
                max_depth=config.max_depth,
                num_leaves=config.num_leaves,
            )
        else:
            raise ModelTrainerError(
                f"Unsupported model_type '{config.model_type}'."
            )

        evals_result: dict = {}
        model.fit(
            dataset,
            num_boost_round=config.num_boost_round,
            early_stopping_rounds=config.early_stopping_rounds,
            evals_result=evals_result,
        )

        predictions = model.predict(dataset, segment=predict_segment)

        if predictions is None or (hasattr(predictions, "empty") and predictions.empty):
            raise ModelTrainerError(
                f"Model produced no predictions for segment '{predict_segment}'."
            )

        artifact_path = Path(model_artifact_path)
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        with artifact_path.open("wb") as f:
            pickle.dump(model, f)

        return ModelTrainResult(
            predictions=predictions,
            model_artifact_path=str(artifact_path),
            train_metrics=dict(evals_result),
            prediction_shape=tuple(predictions.shape),
        )

    @classmethod
    def _validate(cls, config: ModelTrainConfig, model_artifact_path: str) -> None:
        if not is_canonical_qlib_initialized():
            raise ModelTrainerError(
                "Canonical qlib runtime is not initialized. "
                "Call src.core.qlib_runtime.init_qlib_canonical(...) first."
            )

        if config.model_type not in SUPPORTED_MODEL_TYPES:
            raise ModelTrainerError(
                f"model_type must be one of {SUPPORTED_MODEL_TYPES}, "
                f"got '{config.model_type}'."
            )

        if config.num_boost_round <= 0:
            raise ModelTrainerError("num_boost_round must be > 0.")

        if config.early_stopping_rounds <= 0:
            raise ModelTrainerError("early_stopping_rounds must be > 0.")

        if config.learning_rate <= 0:
            raise ModelTrainerError("learning_rate must be > 0.")

        if not str(model_artifact_path or "").strip():
            raise ModelTrainerError("model_artifact_path must be non-empty.")
