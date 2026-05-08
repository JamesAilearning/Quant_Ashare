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

# Valid DatasetH segment names accepted by qlib. Anything else (e.g. a
# user typo like "tets") used to raise deep inside qlib with a confusing
# error — we reject upfront.
_VALID_PREDICT_SEGMENTS = ("train", "valid", "test")

# Sane upper bounds on gradient-boosting hyperparameters. These aren't
# theoretical limits but "past this is a config bug, not a choice":
#
# - num_boost_round > 100_000 with early stopping rounds typically 20-200
#   means the run will sit in a no-op loop burning CPU long after loss
#   plateaus. The deepest production LGBModel runs in V1 were in the
#   5k-10k range.
# - max_depth > 64 on tabular features is degenerate; LightGBM's default
#   is -1 (unlimited) but every published Alpha158/Alpha360 tuning paper
#   caps it below 12.
# - num_leaves > 100_000 is a memory footgun; the LightGBM docs
#   recommend keeping num_leaves strictly below 2^max_depth, and realistic
#   values are in the 31-1024 range.
# - learning_rate > 1.0 makes LightGBM diverge; we stay below.
_MAX_NUM_BOOST_ROUND = 100_000
_MAX_MAX_DEPTH = 64
_MAX_CATBOOST_DEPTH = 16
_MAX_NUM_LEAVES = 100_000
_MAX_LEARNING_RATE = 1.0


@dataclass(frozen=True)
class ModelTrainConfig:
    """Frozen configuration for model training.

    The LGB regularisation / sampling fields below were added so the
    walk-forward operator can break LGBModel out of the "best_iteration
    plateau" we observed in the first end-to-end run (every fold's
    early-stopping fired after ≤6 rounds because the high default
    learning_rate combined with no L1/L2 regularisation pushed valid
    loss to its local optimum on the first split). The defaults match
    LightGBM's own defaults so existing callers get unchanged behaviour;
    config files (e.g. ``config_walk.yaml``) override them with values
    that let the boosted trees actually train.
    """

    model_type: str
    num_boost_round: int = 1000
    early_stopping_rounds: int = 50
    learning_rate: float = 0.0421
    max_depth: int = 8
    num_leaves: int = 210
    seed: int = 42
    # ---- LGB regularisation / sampling ----
    # All defaults below mirror LightGBM's own defaults so introducing
    # the fields does not change behaviour for callers that don't set
    # them. LGBModel accepts every kwarg LightGBM does via **kwargs.
    lambda_l1: float = 0.0
    lambda_l2: float = 0.0
    min_data_in_leaf: int = 20
    feature_fraction: float = 1.0
    bagging_fraction: float = 1.0
    bagging_freq: int = 0


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
        cls._validate(config, model_artifact_path, predict_segment)

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
        # Atomic write: serialise to a sibling temp file first, then
        # ``os.replace`` it onto the target path. The previous direct-
        # write approach left a partial pickle on disk if the process
        # was killed (Ctrl-C, OOM, disk-full) mid-``pickle.dump`` — the
        # next ``pickle.load`` against that path would then raise
        # ``EOFError`` / ``UnpicklingError`` with a name that suggested
        # corruption rather than "write was interrupted". ``os.replace``
        # is atomic on both POSIX and Windows for files within the same
        # directory, so a reader either sees the previous good copy
        # (if any) or the new complete copy — never a half-written one.
        tmp_path = artifact_path.with_suffix(artifact_path.suffix + ".tmp")
        try:
            with tmp_path.open("wb") as f:
                pickle.dump(model, f)
            os.replace(tmp_path, artifact_path)
        except Exception:
            # Best-effort cleanup; ``os.replace`` failure leaves the tmp
            # file behind, which would otherwise accumulate across runs.
            try:
                tmp_path.unlink()
            except OSError:
                pass
            raise

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

        qlib's LGBModel, XGBModel, and CatBoostModel wrappers all own
        num_boost_round/early_stopping_rounds at fit-time. Forwarding them here
        keeps user config from silently falling back to wrapper defaults.
        """
        if config.model_type == "LGBModel":
            model.fit(
                dataset,
                num_boost_round=config.num_boost_round,
                early_stopping_rounds=config.early_stopping_rounds,
                evals_result=evals_result,
            )
        elif config.model_type in ("XGBModel", "CatBoostModel"):
            model.fit(
                dataset,
                num_boost_round=config.num_boost_round,
                early_stopping_rounds=config.early_stopping_rounds,
                evals_result=evals_result,
            )
        else:
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

        Each framework is imported lazily *inside* its own branch so a
        user running XGB or CatBoost does not hit ``ImportError`` when
        lightgbm is not installed. The previous unconditional
        ``from qlib.contrib.model.gbdt import LGBModel`` at the top of
        the method made the LGB dep a hard requirement for every call
        site, even XGB/CatBoost.
        """
        if config.model_type == "LGBModel":
            try:
                from qlib.contrib.model.gbdt import LGBModel  # type: ignore[import-not-found]
            except ImportError as exc:
                raise ModelTrainerError(
                    "lightgbm / qlib LGBModel is not importable. Run: "
                    "pip install lightgbm"
                ) from exc
            return LGBModel(
                loss="mse",
                num_boost_round=config.num_boost_round,
                early_stopping_rounds=config.early_stopping_rounds,
                learning_rate=config.learning_rate,
                max_depth=config.max_depth,
                num_leaves=config.num_leaves,
                seed=config.seed,
                # LGB regularisation / sampling. LGBModel forwards
                # **kwargs into lightgbm.train, so these reach the
                # underlying booster directly.
                lambda_l1=config.lambda_l1,
                lambda_l2=config.lambda_l2,
                min_data_in_leaf=config.min_data_in_leaf,
                feature_fraction=config.feature_fraction,
                bagging_fraction=config.bagging_fraction,
                bagging_freq=config.bagging_freq,
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
    def _validate(
        cls,
        config: ModelTrainConfig,
        model_artifact_path: str,
        predict_segment: str = "test",
    ) -> None:
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

        # ---- num_boost_round ----
        if (
            not isinstance(config.num_boost_round, int)
            or isinstance(config.num_boost_round, bool)
        ):
            raise ModelTrainerError(
                f"num_boost_round must be int; got "
                f"{type(config.num_boost_round).__name__}."
            )
        if config.num_boost_round <= 0:
            raise ModelTrainerError("num_boost_round must be > 0.")
        if config.num_boost_round > _MAX_NUM_BOOST_ROUND:
            raise ModelTrainerError(
                f"num_boost_round={config.num_boost_round} exceeds sane upper "
                f"bound of {_MAX_NUM_BOOST_ROUND}. Values this large are "
                f"almost always a config bug (misplaced zero); real runs "
                f"plateau well before this."
            )

        # ---- early_stopping_rounds ----
        if (
            not isinstance(config.early_stopping_rounds, int)
            or isinstance(config.early_stopping_rounds, bool)
        ):
            raise ModelTrainerError(
                f"early_stopping_rounds must be int; got "
                f"{type(config.early_stopping_rounds).__name__}."
            )
        if config.early_stopping_rounds <= 0:
            raise ModelTrainerError("early_stopping_rounds must be > 0.")
        if config.early_stopping_rounds > config.num_boost_round:
            raise ModelTrainerError(
                f"early_stopping_rounds ({config.early_stopping_rounds}) "
                f"must be <= num_boost_round ({config.num_boost_round}); "
                "otherwise early stopping can never trigger."
            )

        # ---- learning_rate ----
        if not isinstance(config.learning_rate, (int, float)) or isinstance(
            config.learning_rate, bool
        ):
            raise ModelTrainerError(
                f"learning_rate must be a float; got "
                f"{type(config.learning_rate).__name__}."
            )
        if config.learning_rate <= 0:
            raise ModelTrainerError("learning_rate must be > 0.")
        if config.learning_rate > _MAX_LEARNING_RATE:
            raise ModelTrainerError(
                f"learning_rate={config.learning_rate} exceeds sane upper "
                f"bound of {_MAX_LEARNING_RATE}. Values > 1 make gradient "
                f"boosting diverge — almost certainly a decimal-point typo."
            )

        # ---- max_depth ----
        if (
            not isinstance(config.max_depth, int)
            or isinstance(config.max_depth, bool)
        ):
            raise ModelTrainerError(
                f"max_depth must be int; got {type(config.max_depth).__name__}."
            )
        if config.max_depth <= 0:
            raise ModelTrainerError("max_depth must be > 0.")
        if config.max_depth > _MAX_MAX_DEPTH:
            raise ModelTrainerError(
                f"max_depth={config.max_depth} exceeds sane upper bound of "
                f"{_MAX_MAX_DEPTH}. Depths this large on tabular features "
                f"are degenerate; published Alpha158/Alpha360 tuning stays "
                f"below 12."
            )
        if config.model_type == "CatBoostModel" and config.max_depth > _MAX_CATBOOST_DEPTH:
            raise ModelTrainerError(
                f"CatBoostModel max_depth={config.max_depth} exceeds supported "
                f"upper bound of {_MAX_CATBOOST_DEPTH}. CatBoost rejects deeper "
                "trees internally; fail at the config boundary instead."
            )

        # ---- seed ---- (used by every model type)
        if not isinstance(config.seed, int) or isinstance(config.seed, bool):
            raise ModelTrainerError(
                f"seed must be an int, got {type(config.seed).__name__}."
            )

        # The block below validates LightGBM-specific hyperparameters
        # (``num_leaves``, ``lambda_l1/l2``, ``min_data_in_leaf``,
        # ``feature_fraction``, ``bagging_fraction``, ``bagging_freq``).
        # Gating it on ``model_type == "LGBModel"`` so the *defaults*
        # (e.g. ``num_leaves=210``) don't reject a perfectly legal
        # ``CatBoostModel(max_depth=4)`` config because ``210 > 2^4``.
        # XGB / CatBoost paths in ``_create_model`` simply ignore
        # these fields; we keep them on the dataclass for surface
        # uniformity but only enforce shape when LGB is selected.
        if config.model_type == "LGBModel":
            # ---- num_leaves ----
            if (
                not isinstance(config.num_leaves, int)
                or isinstance(config.num_leaves, bool)
            ):
                raise ModelTrainerError(
                    f"num_leaves must be int; got {type(config.num_leaves).__name__}."
                )
            if config.num_leaves < 2:
                raise ModelTrainerError(
                    "num_leaves must be >= 2 (LightGBM requires at least a root "
                    f"split); got {config.num_leaves}."
                )
            if config.num_leaves > _MAX_NUM_LEAVES:
                raise ModelTrainerError(
                    f"num_leaves={config.num_leaves} exceeds sane upper bound "
                    f"of {_MAX_NUM_LEAVES}."
                )
            # LightGBM invariant: num_leaves <= 2^max_depth. If violated, LGBM
            # silently clips num_leaves, so the model the user thinks they're
            # training is not the model that runs. Reject loudly.
            if config.max_depth <= 30:  # avoid overflow on huge max_depth
                max_leaves_for_depth = 2 ** config.max_depth
                if config.num_leaves > max_leaves_for_depth:
                    raise ModelTrainerError(
                        f"num_leaves ({config.num_leaves}) exceeds "
                        f"2**max_depth ({max_leaves_for_depth}). LightGBM would "
                        f"silently clip, training a shallower model than "
                        f"configured."
                    )

            # ---- lambda_l1 / lambda_l2 ----
            # Negative regularisation makes the objective non-convex, which
            # LightGBM accepts but produces nonsensical models. Reject up
            # front. ``int`` is allowed because ``isinstance(int, float)``
            # is False but ``int`` values like ``0`` / ``1`` are common.
            for name, value in (
                ("lambda_l1", config.lambda_l1),
                ("lambda_l2", config.lambda_l2),
            ):
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    raise ModelTrainerError(
                        f"{name} must be a float; got {type(value).__name__}."
                    )
                if value < 0:
                    raise ModelTrainerError(
                        f"{name} must be >= 0; got {value}. Negative regularisation "
                        "produces ill-defined LGB models."
                    )

            # ---- min_data_in_leaf ----
            if (
                not isinstance(config.min_data_in_leaf, int)
                or isinstance(config.min_data_in_leaf, bool)
            ):
                raise ModelTrainerError(
                    f"min_data_in_leaf must be int; got "
                    f"{type(config.min_data_in_leaf).__name__}."
                )
            if config.min_data_in_leaf < 1:
                raise ModelTrainerError(
                    f"min_data_in_leaf must be >= 1; got {config.min_data_in_leaf}."
                )

            # ---- feature_fraction / bagging_fraction ----
            # Must lie in (0, 1] — LightGBM clips silently for values
            # outside this range, again producing a model the user did not
            # configure.
            for name, value in (
                ("feature_fraction", config.feature_fraction),
                ("bagging_fraction", config.bagging_fraction),
            ):
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    raise ModelTrainerError(
                        f"{name} must be a float; got {type(value).__name__}."
                    )
                if not (0.0 < value <= 1.0):
                    raise ModelTrainerError(
                        f"{name} must be in (0, 1]; got {value}. "
                        "0 disables sampling entirely (no rows / features) and "
                        "values > 1 are clipped silently by LightGBM."
                    )

            # ---- bagging_freq ----
            if (
                not isinstance(config.bagging_freq, int)
                or isinstance(config.bagging_freq, bool)
            ):
                raise ModelTrainerError(
                    f"bagging_freq must be int; got "
                    f"{type(config.bagging_freq).__name__}."
                )
            if config.bagging_freq < 0:
                raise ModelTrainerError(
                    f"bagging_freq must be >= 0; got {config.bagging_freq}. "
                    "Use 0 to disable, or a positive int (e.g. 5) for periodic bagging."
                )

        # ---- model_artifact_path ----
        if not str(model_artifact_path or "").strip():
            raise ModelTrainerError("model_artifact_path must be non-empty.")

        # ---- predict_segment ----
        # Upfront check: a typo like "tets" used to raise deep inside
        # qlib with a cryptic KeyError on the segment lookup.
        if predict_segment not in _VALID_PREDICT_SEGMENTS:
            raise ModelTrainerError(
                f"predict_segment must be one of {_VALID_PREDICT_SEGMENTS}; "
                f"got {predict_segment!r}."
            )
