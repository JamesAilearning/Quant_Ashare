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

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from src.core.logger import get_logger
from src.core.model_config_projection import build_model_train_config
from src.core.qlib_runtime import is_canonical_qlib_initialized

_logger = get_logger(__name__)


class HyperparamOptimizerError(RuntimeError):
    """Raised on structural misuse of the optimizer."""


# Valid optimization targets. Typos like "IC_1D", "ic1d", "ic_5D" used to
# silently fall through to the default ic_1d branch — the user would see a
# successful optimization and never know the wrong metric was maximized.
_VALID_OPTIMIZATION_METRICS = ("ic_1d", "ic_5d")


@dataclass(frozen=True)
class HyperparamSearchSpace:
    """Defines the search space for LGBModel hyperparameters."""

    num_boost_round_range: tuple[int, int] = (100, 2000)
    learning_rate_range: tuple[float, float] = (0.01, 0.1)
    max_depth_range: tuple[int, int] = (4, 12)
    num_leaves_range: tuple[int, int] = (31, 512)
    early_stopping_rounds_range: tuple[int, int] = (20, 100)
    lambda_l1_range: tuple[float, float] = (0.0, 10.0)
    lambda_l2_range: tuple[float, float] = (0.0, 10.0)
    min_data_in_leaf_range: tuple[int, int] = (5, 100)
    feature_fraction_range: tuple[float, float] = (0.6, 1.0)
    bagging_fraction_range: tuple[float, float] = (0.6, 1.0)
    bagging_freq_range: tuple[int, int] = (0, 5)


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

    # Reproducibility. Threads through to optuna's TPESampler so the
    # trial sequence is deterministic across runs; previous behaviour
    # (sampler unseeded) made best_params unreproducible. The seed is
    # NOT forwarded to ModelTrainer's per-trial config — each trial
    # builds its own ``ModelTrainConfig`` via ``build_model_train_config``
    # which carries its own ``seed`` field (defaulted by the projector).
    seed: int = 42

    def __post_init__(self) -> None:
        if self.optimization_metric not in _VALID_OPTIMIZATION_METRICS:
            raise HyperparamOptimizerError(
                "optimization_metric must be one of "
                f"{_VALID_OPTIMIZATION_METRICS}; got {self.optimization_metric!r}. "
                "Typos used to silently fall back to 'ic_1d' — the user "
                "believed they were optimizing a different metric than they "
                "actually were."
            )
        if not isinstance(self.n_trials, int) or isinstance(self.n_trials, bool):
            raise HyperparamOptimizerError(
                f"n_trials must be an int, got {type(self.n_trials).__name__}."
            )
        if self.n_trials < 1:
            raise HyperparamOptimizerError(
                f"n_trials must be >= 1; got {self.n_trials}."
            )
        # ``seed`` must be a real int — not None, not bool, not float.
        # Optuna's ``TPESampler(seed=None)`` is the unseeded default,
        # which would silently reintroduce the nondeterministic trial
        # sequence this field was added to eliminate. Untyped YAML
        # loaders happily pass ``None`` for an unset key, so reject
        # explicitly at the contract boundary. Codex P2 on PR #174.
        if not isinstance(self.seed, int) or isinstance(self.seed, bool):
            raise HyperparamOptimizerError(
                f"seed must be an int, got {type(self.seed).__name__}. "
                "None / bool are rejected because optuna's TPESampler "
                "treats seed=None as the unseeded default, which silently "
                "restores nondeterministic trial order."
            )


@dataclass(frozen=True)
class HyperparamTrialResult:
    """Result of a single trial."""

    trial_number: int
    params: Mapping[str, Any]
    state: str  # Optuna TrialState name (COMPLETE, FAIL, PRUNED, ...)
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

        from pathlib import Path

        import optuna

        output_dir = Path(config.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Build dataset once (shared across trials)
        _logger.info("Building feature dataset (shared across trials)...")
        dataset = cls._build_dataset(config)

        trial_results: list[HyperparamTrialResult] = []

        def objective(trial: optuna.Trial) -> float:
            params = cls._suggest_params(trial, config.search_space)

            ic_1d, ic_5d, ir = cls._evaluate_params(
                params, dataset, config, output_dir
            )

            trial.set_user_attr("ic_1d", ic_1d)
            trial.set_user_attr("ic_5d", ic_5d)
            trial.set_user_attr("ir", ir)

            if config.optimization_metric == "ic_5d":
                return ic_5d
            return ic_1d

        # Suppress optuna's verbose logging
        optuna.logging.set_verbosity(optuna.logging.WARNING)

        # No pruner is configured. ``MedianPruner`` was registered in an
        # earlier revision, but ``objective`` returns the final IC
        # directly without ever calling ``trial.report(value, step)`` /
        # ``trial.should_prune()`` mid-train, which is the only thing
        # pruners hook into. As a result the registered pruner never
        # fired — a no-op that misleadingly suggested half-time runs.
        # Wiring real pruning would need a step-by-step training loop
        # exposing intermediate validation IC; that is a separate piece
        # of work. Document the absence here so a future reader does
        # not re-add a pruner without also adding the report-loop.
        #
        # Sampler: TPESampler with an explicit seed so the trial sequence
        # is reproducible. The previous unseeded default made two runs
        # of the same config explore different regions of the search
        # space, so ``best_params`` was non-deterministic across runs.
        sampler = optuna.samplers.TPESampler(seed=config.seed)
        study = optuna.create_study(
            direction="maximize",
            study_name="lgb_hyperparam_search",
            sampler=sampler,
        )

        _logger.info("Starting %d trials...", config.n_trials)
        study.optimize(objective, n_trials=config.n_trials, catch=(Exception,))

        # Build trial_results from study.trials (the source of truth).
        # Failed trials (caught by ``catch=(Exception,)``) are included
        # with NaN IC/IR values — the previous ``objective``-scoped
        # append missed them, making ``n_trials_completed`` lower than
        # ``n_trials`` whenever any trial failed.
        for t in study.trials:
            attrs = t.user_attrs
            trial_results.append(HyperparamTrialResult(
                trial_number=t.number,
                params=dict(t.params),
                state=t.state.name,
                ic_1d=float(attrs.get("ic_1d", float("nan"))),
                ic_5d=float(attrs.get("ic_5d", float("nan"))),
                ir=float(attrs.get("ir", float("nan"))),
            ))

        completed = [t for t in study.trials if t.state.name == "COMPLETE"]
        if not completed:
            raise HyperparamOptimizerError(
                "All hyperparameter trials failed; no completed trial available."
            )
        best = study.best_trial
        # ``study.best_trial`` is only valid when ≥1 trial COMPLETED
        # (we checked ``completed`` above) and the trial's objective
        # actually returned a value. Optuna types ``Trial.value`` as
        # ``float | None``, but COMPLETE trials always have a value
        # — assert here to narrow ``float | None`` → ``float`` and
        # surface the unlikely "completed but no value" case loudly.
        if best.value is None:
            raise HyperparamOptimizerError(
                f"Best trial #{best.number} is COMPLETE but has no "
                f"objective value — Optuna study state is inconsistent."
            )
        _logger.info(
            "Best trial #%d: IC=%.4f, params=%s",
            best.number, best.value, best.params,
        )

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
        """Suggest hyperparameters for a trial.

        The ``num_leaves`` range is intersected with ``[2, 2**max_depth]``
        so the combined suggestion respects the LightGBM invariant
        ``num_leaves <= 2**max_depth`` — otherwise LightGBM silently clips
        and ModelTrainer's validator raises, wasting Optuna budget on
        trials that can never actually run.
        """
        num_boost_round = trial.suggest_int(
            "num_boost_round",
            space.num_boost_round_range[0],
            space.num_boost_round_range[1],
        )
        learning_rate = trial.suggest_float(
            "learning_rate",
            space.learning_rate_range[0],
            space.learning_rate_range[1],
            log=True,
        )
        max_depth = trial.suggest_int(
            "max_depth", space.max_depth_range[0], space.max_depth_range[1],
        )

        # Clamp num_leaves range to the LightGBM invariant. ``max_depth``
        # may be deeper than 30 (unlikely with our defaults but not
        # forbidden); guard the shift to avoid overflow.
        leaf_cap = (1 << max_depth) if max_depth <= 30 else space.num_leaves_range[1]
        leaves_low = max(2, min(space.num_leaves_range[0], leaf_cap))
        leaves_high = max(leaves_low, min(space.num_leaves_range[1], leaf_cap))
        num_leaves = trial.suggest_int("num_leaves", leaves_low, leaves_high)

        # Cap early_stopping_rounds at num_boost_round so validation can
        # actually trigger stopping — ModelTrainer rejects the reverse.
        es_low = space.early_stopping_rounds_range[0]
        es_high = min(space.early_stopping_rounds_range[1], num_boost_round)
        es_high = max(es_low, es_high)
        early_stopping_rounds = trial.suggest_int(
            "early_stopping_rounds", es_low, es_high,
        )
        lambda_l1 = trial.suggest_float(
            "lambda_l1", space.lambda_l1_range[0], space.lambda_l1_range[1],
        )
        lambda_l2 = trial.suggest_float(
            "lambda_l2", space.lambda_l2_range[0], space.lambda_l2_range[1],
        )
        min_data_in_leaf = trial.suggest_int(
            "min_data_in_leaf",
            space.min_data_in_leaf_range[0],
            space.min_data_in_leaf_range[1],
        )
        feature_fraction = trial.suggest_float(
            "feature_fraction",
            space.feature_fraction_range[0],
            space.feature_fraction_range[1],
        )
        bagging_fraction = trial.suggest_float(
            "bagging_fraction",
            space.bagging_fraction_range[0],
            space.bagging_fraction_range[1],
        )
        bagging_freq = trial.suggest_int(
            "bagging_freq",
            space.bagging_freq_range[0],
            space.bagging_freq_range[1],
        )

        return {
            "num_boost_round": num_boost_round,
            "learning_rate": learning_rate,
            "max_depth": max_depth,
            "num_leaves": num_leaves,
            "early_stopping_rounds": early_stopping_rounds,
            "lambda_l1": lambda_l1,
            "lambda_l2": lambda_l2,
            "min_data_in_leaf": min_data_in_leaf,
            "feature_fraction": feature_fraction,
            "bagging_fraction": bagging_fraction,
            "bagging_freq": bagging_freq,
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

        from src.core.model_trainer import ModelTrainer
        from src.core.signal_analyzer import SignalAnalysisConfig, SignalAnalyzer

        with tempfile.TemporaryDirectory() as tmpdir:
            model_path = str(Path(tmpdir) / "model.pkl")

            model_result = ModelTrainer.train_and_predict(
                config=build_model_train_config(params, model_type="LGBModel"),
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

            # Structural: SignalAnalyzer must populate both periods we asked
            # for. Falling back to 0.0 here was the old behaviour and would
            # feed Optuna a plausible-looking "this hyperparam set scored
            # zero" signal for what is really a broken analysis run, quietly
            # poisoning ``best_params``.
            missing = [p for p in (1, 5) if p not in signal_result.ic_summary]
            if missing:
                raise HyperparamOptimizerError(
                    "SignalAnalyzer did not return IC for forward period(s) "
                    f"{missing}; cannot compute ic_1d/ic_5d. Analyzer output "
                    f"keys: {sorted(signal_result.ic_summary.keys())}."
                )

            # Values are allowed to be NaN (validation data too short to
            # produce valid cross-sectional IC); Optuna treats NaN returns
            # as failed trials, which is exactly what we want — failed
            # trials will not be picked as ``best_trial``. The trial result
            # still records the NaN for downstream visibility.
            ic_1d = float(signal_result.ic_summary[1]["mean_ic"])
            ic_5d = float(signal_result.ic_summary[5]["mean_ic"])
            ir = float(signal_result.ic_summary[1]["ir"])

        return ic_1d, ic_5d, ir
