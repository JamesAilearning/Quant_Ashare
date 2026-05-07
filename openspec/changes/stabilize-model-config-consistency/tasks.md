## 1. Shared Projection

- [x] 1.1 Add a shared model-training config projection helper that builds `ModelTrainConfig` from runtime configs or sampled params.
- [x] 1.2 Replace duplicated `ModelTrainConfig` construction in `Pipeline.run`.
- [x] 1.3 Replace duplicated `ModelTrainConfig` construction in `WalkForwardEngine._run_single_fold`.

## 2. Hyperparameter Search

- [x] 2.1 Extend `HyperparamSearchSpace` with LightGBM regularization and sampling ranges.
- [x] 2.2 Include those sampled params in `HyperparamOptimizer._sample_params`.
- [x] 2.3 Pass all sampled params through the shared projection in `_evaluate_params`.

## 3. Verification

- [x] 3.1 Add targeted logic tests for shared projection and hyperparameter sampled params.
- [x] 3.2 Run targeted tests for pipeline/walk-forward/hyperparameter config behavior.
- [x] 3.3 Run `openspec validate stabilize-model-config-consistency --strict`.
