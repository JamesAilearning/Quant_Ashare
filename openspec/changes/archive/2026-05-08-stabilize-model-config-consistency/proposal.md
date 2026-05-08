## Why

Pipeline, walk-forward, and hyperparameter optimization now maintain overlapping
LightGBM model configuration fields and projection code. Every new model
parameter must be copied into multiple places, which creates a high-risk path
for silent training drift between single-run, walk-forward, and Optuna flows.

## What Changes

- Add a shared model-training configuration projection boundary used by
  `Pipeline`, `WalkForwardEngine`, and `HyperparamOptimizer`.
- Keep existing user-facing YAML fields flat and backward-compatible while
  removing duplicated `ModelTrainConfig` construction.
- Extend hyperparameter search to include the LightGBM regularization and
  sampling knobs already supported by `ModelTrainConfig`.
- Add focused regression tests proving the three runtime flows project the same
  model settings.

## Capabilities

### New Capabilities

- `v2-model-training-config-consistency`: Defines shared model-training config
  projection across pipeline, walk-forward, and hyperparameter optimization.

### Modified Capabilities

- None.

## Impact

- Affected code: `src/core/pipeline.py`, `src/core/walk_forward.py`,
  `src/core/hyperparam_optimizer.py`, and a small shared helper module under
  `src/core/`.
- Affected tests: targeted logic tests for model config projection and Optuna
  search-space propagation.
- No breaking change to existing config files, canonical qlib initialization,
  backtest semantics, or official metric definitions.
