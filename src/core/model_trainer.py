"""Model trainer — supports LGBModel, XGBModel, and CatBoostModel.

Provides a contract-friendly interface that accepts a DatasetH from
``FeatureDatasetBuilder`` and produces predictions + a serialized model
artifact.

Boundaries
----------
- This module does NOT call ``qlib.init``. Callers must initialize via
  ``src.core.qlib_runtime.init_qlib_canonical`` first.
- Importing this module does NOT import qlib. The qlib import is lazy.
- ``fit()`` invocation is per-model-type: LGBModel takes extra kwargs,
  XGB / CatBoost do not.  See :meth:`_fit_dispatch`.
"""

from __future__ import annotations

import os
import pickle
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from src.core.logger import get_logger
from src.core.qlib_runtime import is_canonical_qlib_initialized

_logger = get_logger(__name__)


class ModelTrainerError(RuntimeError):
    """Raised on structural misuse or training failures."""


SUPPORTED_MODEL_TYPES = ("LGBModel", "XGBModel", "CatBoostModel")


@dataclass(frozen=True)
class ModelTrainConfig:
    """Frozen configuration for model training."""

    model_type: str
    num_boost_round: int = 1000
    early_stopping_rounds: int = 50
    learning_rate: float = 0.0421
    max_depth: int = 8
    num_leaves: int = 210
    seed: int = 42


@dataclass(frozen=True)
class ModelTrainResult:
    """Result of model training and prediction.

    Attributes
    ----------
    predictions : pd.Series
        (datetime, instrument) MultiIndex prediction scores.
    model_artifact_path : str
        Path to the pickled model.
    train_metrics : mapping
        Per-dataset evals_result from the framework (best-effort; LGBModel
        populates it fully, XGB / CatBoost may leave it empty).
    prediction_shape : tuple[int, ...]
    best_iteration : int | None
        Best boosting round per the model's early-stopping decision, or None
        if the underlying model doesn't expose it.
    final_valid_loss : float | None
        Loss at ``best_iteration`` on the validation segment, best-effort.
    """

    predictions: Any
    model_artifact_path: str
    train_metrics: Mapping[str, Any]
    prediction_shape: tuple[int, ...]
    best_iteration: int | None = None
    final_valid_loss: float | None = None


def _seed_everything(seed: int) -> None:
    """Seed python / numpy / (best-effort) lightgbm / xgboost / catboost."""
    os.environ["PYTHONHASHSEED"] = str(seed)
    random.seed(seed)
    try:
        import numpy as np
        np.random.seed(seed)
    except ImportError:  # pragma: no cover - numpy is a hard dep in practice
        pass
    # LightGBM / XGBoost / CatBoost read their own seeds via model kwargs —
    # seeding here catches any non-deterministic pre-processing (e.g. qlib
    # handler sampling) that uses numpy / python random directly.


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

        _seed_everything(config.seed)

        model = cls._create_model(config)

        evals_result: dict = {}
        cls._fit_dispatch(model, dataset, config, evals_result)

        predictions = model.predict(dataset, segment=predict_segment)

        if predictions is None or (hasattr(predictions, "empty") and predictions.empty):
            raise ModelTrainerError(
                f"Model produced no predictions for segment '{predict_segment}'."
            )

        artifact_path = Path(model_artifact_path)
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        with artifact_path.open("wb") as f:
            pickle.dump(model, f)

        best_iter, final_val = cls._extract_training_diagnostics(
            model, config.model_type, evals_result,
        )
        if best_iter is not None:
            _logger.info("Best iteration: %d", best_iter)
        if final_val is not None:
            _logger.info("Final valid loss: %.6f", final_val)

        return ModelTrainResult(
            predictions=predictions,
            model_artifact_path=str(artifact_path),
            train_metrics=dict(evals_result),
            prediction_shape=tuple(predictions.shape),
            best_iteration=best_iter,
            final_valid_loss=final_val,
        )

    @classmethod
    def _fit_dispatch(
        cls, model: Any, dataset: Any, config: ModelTrainConfig, evals_result: dict,
    ) -> None:
        """Call ``model.fit`` with the kwargs each framework actually accepts.

        LGBModel's fit() accepts num_boost_round/early_stopping_rounds/evals_result
        as overrides. XGBModel and CatBoostModel read these from __init__ only
        and would raise TypeError if we forward extra kwargs. This dispatch
        keeps the multi-model claim honest.
        """
        if config.model_type == "LGBModel":
            model.fit(
                dataset,
                num_boost_round=config.num_boost_round,
                early_stopping_rounds=config.early_stopping_rounds,
                evals_result=evals_result,
            )
        else:
            # XGB / CatBoost: plain fit(dataset). Hyperparams already baked in
            # at _create_model time; no evals_result hook is exposed.
            model.fit(dataset)

    @classmethod
    def _extract_training_diagnostics(
        cls, model: Any, model_type: str, evals_result: dict,
    ) -> tuple[int | None, float | None]:
        """Best-effort extraction of ``best_iteration`` and final valid loss.

        Each framework exposes these differently; we try the known shapes and
        return ``(None, None)`` on any failure rather than poisoning the output.
        """
        best_iter: int | None = None
        final_val: float | None = None

        # best_iteration
        try:
            inner = getattr(model, "model", None)
            if inner is None:
                pass
            elif model_type == "LGBModel":
                # lightgbm.Booster
                bi = getattr(inner, "best_iteration", None)
                if bi is not None:
                    best_iter = int(bi)
            elif model_type == "XGBModel":
                bi = getattr(inner, "best_iteration", None)
                if bi is not None:
                    best_iter = int(bi)
            elif model_type == "CatBoostModel":
                # catboost.CatBoostRegressor exposes get_best_iteration()
                getter = getattr(inner, "get_best_iteration", None)
                if callable(getter):
                    bi = getter()
                    if bi is not None:
                        best_iter = int(bi)
        except Exception:
            best_iter = None

        # final_valid_loss from evals_result (LGBModel only, really)
        try:
            if evals_result:
                # evals_result shape: {dataset_name: {metric_name: [values...]}}
                # Prefer "valid" / "valid_1" / "validation" keys; fall back to
                # anything containing "val".
                candidates = [k for k in evals_result if any(
                    tag in k.lower() for tag in ("valid", "val", "eval")
                )]
                if candidates:
                    losses = evals_result[candidates[0]]
                    metric_name = next(iter(losses))
                    values = losses[metric_name]
                    if values:
                        final_val = float(
                            values[best_iter - 1]
                            if best_iter is not None and 0 < best_iter <= len(values)
                            else values[-1]
                        )
        except Exception:
            final_val = None

        return best_iter, final_val

    @classmethod
    def _create_model(cls, config: ModelTrainConfig) -> Any:
        """Create the appropriate qlib model instance.

        Seeds are passed through each framework's native kwarg so the model's
        own RNG is deterministic (Python/numpy seeding only helps with
        pre-processing). For LGBModel we use ``seed``; XGB uses ``seed``;
        CatBoost uses ``random_seed``.
        """
        try:
            from qlib.contrib.model.gbdt import LGBModel  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ModelTrainerError(
                "qlib is not importable; cannot train model."
            ) from exc

        if config.model_type == "LGBModel":
            return LGBModel(
                loss="mse",
                num_boost_round=config.num_boost_round,
                early_stopping_rounds=config.early_stopping_rounds,
                learning_rate=config.learning_rate,
                max_depth=config.max_depth,
                num_leaves=config.num_leaves,
                seed=config.seed,
            )
        elif config.model_type == "XGBModel":
            try:
                from qlib.contrib.model.xgboost import XGBModel  # type: ignore[import-not-found]
            except ImportError as exc:
                raise ModelTrainerError(
                    "xgboost is not installed. Run: pip install xgboost"
                ) from exc
            return XGBModel(
                n_estimators=config.num_boost_round,
                early_stopping_rounds=config.early_stopping_rounds,
                learning_rate=config.learning_rate,
                max_depth=config.max_depth,
                seed=config.seed,
            )
        elif config.model_type == "CatBoostModel":
            try:
                from qlib.contrib.model.catboost_model import CatBoostModel  # type: ignore[import-not-found]
            except ImportError as exc:
                raise ModelTrainerError(
                    "catboost is not installed. Run: pip install catboost"
                ) from exc
            return CatBoostModel(
                loss="RMSE",
                iterations=config.num_boost_round,
                learning_rate=config.learning_rate,
                depth=config.max_depth,
                random_seed=config.seed,
            )
        else:
            raise ModelTrainerError(
                f"Unsupported model_type '{config.model_type}'."
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

        if not isinstance(config.seed, int) or isinstance(config.seed, bool):
            raise ModelTrainerError(
                f"seed must be an int, got {type(config.seed).__name__}."
            )

        if not str(model_artifact_path or "").strip():
            raise ModelTrainerError("model_artifact_path must be non-empty.")
