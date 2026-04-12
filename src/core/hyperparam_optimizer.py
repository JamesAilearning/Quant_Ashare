"""Hyperparameter optimizer using Optuna for LGBModel tuning.

Searches for optimal LightGBM hyperparameters by training on the train
segment and evaluating IC on the validation segment. The objective is
to maximize mean rank IC (1-day forward) on the validation set.

Boundaries
----------
- Requires canonical qlib init.
- Uses FeatureDatasetBuilder + ModelTrainer + SignalAnalyzer internally.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from src.core.qlib_runtime import is_canonical_qlib_initialized


class HyperparamOptimizerError(RuntimeError):
    """Raised on structural misuse of the optimizer."""


@dataclass(frozen=True)
class HyperparamSearchSpace:
    """Defines the search space for LGBModel hyperparameters."""

    num_boost_round_range: tuple[int, int] = (100, 2000)
    learning_rate_range: tuple[float, float] = (0.01, 0.1)
    max_depth_range: tuple[int, int] = (4, 12)
    num_leaves_range: tuple[int, int] = (31, 512)
    early_stopping_rounds_range: tuple[int, int] = (20, 100)


@dataclass(frozen=True)
class HyperparamOptConfig:
    """Configuration for hyperparameter optimization."""

    # Data
    instruments: str = "csi300"
    feature_handler: str = "Alpha158"
    train_start: str = "2022-01-01"
    train_end: str = "2024-06-30"
    valid_start: str = "2024-07-01"
    valid_end: str = "2024-12-31"
    test_start: str = "2025-01-01"
    test_end: str = "2025-06-30"

    # Search
    n_trials: int = 50
    search_space: HyperparamSearchSpace = field(default_factory=HyperparamSearchSpace)
    optimization_metric: str = "ic_1d"  # "ic_1d" or "ic_5d"

    # Output
    output_dir: str = "output/hyperparam"


@dataclass(frozen=True)
class HyperparamTrialResult:
    """Result of a single trial."""

    trial_number: int
    params: Mapping[str, Any]
    ic_1d: float
    ic_5d: float
    ir: float


@dataclass(frozen=True)
class HyperparamOptResult:
    """Result of the full optimization run."""

    best_params: Mapping[str, Any]
    best_ic: float
    best_trial_number: int
    all_trials: Sequence[HyperparamTrialResult]
    n_trials_completed: int


class HyperparamOptimizer:
    """Optuna-based hyperparameter search for LGBModel."""

    @classmethod
    def optimize(cls, config: HyperparamOptConfig) -> HyperparamOptResult:
        if not is_canonical_qlib_initialized():
            raise HyperparamOptimizerError(
                "Canonical qlib runtime must be initialized before optimization."
            )

        import optuna
        from pathlib import Path

        output_dir = Path(config.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Build dataset once (shared across trials)
        print("[HyperOpt] Building feature dataset (shared across trials)...")
        dataset = cls._build_dataset(config)

        trial_results: list[HyperparamTrialResult] = []

        def objective(trial: optuna.Trial) -> float:
            params = cls._suggest_params(trial, config.search_space)

            ic_1d, ic_5d, ir = cls._evaluate_params(
                params, dataset, config, output_dir
            )

            trial_results.append(HyperparamTrialResult(
                trial_number=trial.number,
                params=params,
                ic_1d=ic_1d,
                ic_5d=ic_5d,
                ir=ir,
            ))

            if config.optimization_metric == "ic_5d":
                return ic_5d
            return ic_1d

        # Suppress optuna's verbose logging
        optuna.logging.set_verbosity(optuna.logging.WARNING)

        study = optuna.create_study(
            direction="maximize",
            study_name="lgb_hyperparam_search",
        )

        print(f"[HyperOpt] Starting {config.n_trials} trials...")
        study.optimize(objective, n_trials=config.n_trials)

        best = study.best_trial
        print(f"\n[HyperOpt] Best trial #{best.number}: "
              f"IC={best.value:.4f}, params={best.params}")

        return HyperparamOptResult(
            best_params=dict(best.params),
            best_ic=best.value,
            best_trial_number=best.number,
            all_trials=trial_results,
            n_trials_completed=len(trial_results),
        )

    @classmethod
    def _build_dataset(cls, config: HyperparamOptConfig) -> Any:
        """Build feature dataset (called once, reused across trials)."""
        from src.data.feature_dataset_builder import FeatureDatasetBuilder, FeatureDatasetConfig

        result = FeatureDatasetBuilder.build(FeatureDatasetConfig(
            instruments=config.instruments,
            feature_handler=config.feature_handler,
            train_start=config.train_start,
            train_end=config.train_end,
            valid_start=config.valid_start,
            valid_end=config.valid_end,
            test_start=config.test_start,
            test_end=config.test_end,
        ))
        return result.dataset

    @staticmethod
    def _suggest_params(trial: Any, space: HyperparamSearchSpace) -> dict[str, Any]:
        """Suggest hyperparameters for a trial."""
        return {
            "num_boost_round": trial.suggest_int(
                "num_boost_round", space.num_boost_round_range[0], space.num_boost_round_range[1]
            ),
            "learning_rate": trial.suggest_float(
                "learning_rate", space.learning_rate_range[0], space.learning_rate_range[1], log=True
            ),
            "max_depth": trial.suggest_int(
                "max_depth", space.max_depth_range[0], space.max_depth_range[1]
            ),
            "num_leaves": trial.suggest_int(
                "num_leaves", space.num_leaves_range[0], space.num_leaves_range[1]
            ),
            "early_stopping_rounds": trial.suggest_int(
                "early_stopping_rounds",
                space.early_stopping_rounds_range[0],
                space.early_stopping_rounds_range[1],
            ),
        }

    @classmethod
    def _evaluate_params(
        cls,
        params: dict[str, Any],
        dataset: Any,
        config: HyperparamOptConfig,
        output_dir: Any,
    ) -> tuple[float, float, float]:
        """Train with given params and evaluate IC on validation predictions."""
        import tempfile
        from pathlib import Path

        from src.core.model_trainer import ModelTrainConfig, ModelTrainer
        from src.core.signal_analyzer import SignalAnalysisConfig, SignalAnalyzer

        with tempfile.TemporaryDirectory() as tmpdir:
            model_path = str(Path(tmpdir) / "model.pkl")

            model_result = ModelTrainer.train_and_predict(
                config=ModelTrainConfig(
                    model_type="LGBModel",
                    num_boost_round=params["num_boost_round"],
                    early_stopping_rounds=params["early_stopping_rounds"],
                    learning_rate=params["learning_rate"],
                    max_depth=params["max_depth"],
                    num_leaves=params["num_leaves"],
                ),
                dataset=dataset,
                model_artifact_path=model_path,
                predict_segment="valid",
            )

            # Compute IC on validation predictions
            signal_result = SignalAnalyzer.analyze(
                predictions=model_result.predictions,
                config=SignalAnalysisConfig(
                    forward_periods=(1, 5),
                    compute_turnover=False,
                ),
            )

            ic_1d = signal_result.ic_summary.get(1, {}).get("mean_ic", 0.0)
            ic_5d = signal_result.ic_summary.get(5, {}).get("mean_ic", 0.0)
            ir = signal_result.ic_summary.get(1, {}).get("ir", 0.0)

        return ic_1d, ic_5d, ir
