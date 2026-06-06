# v2-model-training-config-consistency Specification

## Purpose
TBD - created by archiving change stabilize-model-config-consistency. Update Purpose after archive.
## Requirements
### Requirement: Runtime flows SHALL share model-training config projection

The system SHALL construct `ModelTrainConfig` for pipeline, walk-forward, and
hyperparameter optimizer flows through a shared projection boundary instead of
duplicating field-by-field construction in each runtime module.

#### Scenario: pipeline and walk-forward use the same model fields
- **WHEN** pipeline and walk-forward configs carry the same non-default model settings
- **THEN** their projected `ModelTrainConfig` values are equivalent for all supported model-training fields

#### Scenario: a new supported model field is added
- **WHEN** a model-training field is added to the shared projection contract
- **THEN** tests fail unless every runtime projection path carries that field consistently

### Requirement: Hyperparameter optimization SHALL search supported regularization fields

The hyperparameter optimizer SHALL include the LightGBM regularization and
sampling fields supported by `ModelTrainConfig` in its sampled trial params.

#### Scenario: trial params are sampled
- **WHEN** a hyperparameter trial is created
- **THEN** the params include `lambda_l1`, `lambda_l2`, `min_data_in_leaf`, `feature_fraction`, `bagging_fraction`, and `bagging_freq`
- **AND** those params are passed into `ModelTrainConfig` during trial evaluation

### Requirement: Existing flat runtime config files SHALL remain compatible

The change SHALL preserve the current flat YAML/config field names for pipeline
and walk-forward entry points.

#### Scenario: existing config is loaded
- **WHEN** an existing pipeline or walk-forward config contains flat model fields
- **THEN** the config still loads without requiring nested model sections

### Requirement: Model training SHALL apply configured early stopping to supported model families

`ModelTrainer` SHALL forward configured `num_boost_round` and
`early_stopping_rounds` to supported qlib model families whose `fit()` method
owns those controls, including XGBModel and CatBoostModel.

#### Scenario: CatBoost training starts
- **WHEN** `ModelTrainConfig(model_type="CatBoostModel")` sets
  `early_stopping_rounds`
- **THEN** `ModelTrainer` passes that value into `CatBoostModel.fit`
- **AND** CatBoost does not silently use its wrapper default instead

### Requirement: Model training SHALL enforce model-family-specific depth bounds

`ModelTrainer` SHALL validate depth bounds according to the selected model
family so invalid CatBoost depths are rejected before reaching framework
internals.

#### Scenario: CatBoost depth exceeds supported bound
- **WHEN** a caller configures `CatBoostModel` with `max_depth > 16`
- **THEN** `ModelTrainer` raises `ModelTrainerError`
- **AND** no qlib/CatBoost training call is attempted

### Requirement: Default model hyperparameters SHALL be the tuned, non-pathological set

The default LightGBM hyperparameters carried by `ModelTrainConfig`, `PipelineConfig`, and `WalkForwardConfig` SHALL be the regularised, tuned set that lets `LGBModel` train past the best_iteration=1 plateau (NOT the qlib-Alpha158 learning_rate/num_leaves combo with zero L1/L2 and no subsampling); these defaults SHALL be mutually identical across the three dataclasses AND SHALL equal the resolved model configuration of the canonical tuned walk-forward config (`config_walk.yaml`), so a config that overrides every model field resolves to the same values as one that overrides none ("explicit = default").

#### Scenario: an under-specified config inherits the tuned defaults
- **WHEN** a runtime config sets only a subset of model params (e.g.
  `learning_rate`) and omits `num_leaves`, `max_depth`, and the
  regularisation/sampling fields
- **THEN** the projected `ModelTrainConfig` carries the tuned defaults
  — `num_leaves=64`, `max_depth=6`, `lambda_l2=1.0`,
  `min_data_in_leaf=50`, `feature_fraction=0.8`, `bagging_fraction=0.8`,
  `bagging_freq=5` (with `learning_rate` honoured from the config)
- **AND** it does NOT carry the pathological `num_leaves=210` /
  `max_depth=8` / zero-regularisation set

#### Scenario: the three dataclasses' model defaults stay consistent
- **WHEN** the default model hyperparameters are compared across
  `ModelTrainConfig`, `PipelineConfig`, and `WalkForwardConfig`
- **THEN** every supported model field has the same default in all
  three dataclasses
- **AND** changing one default without the others fails the
  consistency test

#### Scenario: defaults equal the tuned walk-forward reference
- **WHEN** `config_walk.yaml` is projected to a `ModelTrainConfig`, and
  an all-default config is projected to a `ModelTrainConfig`
- **THEN** the two projected configs are equal for all supported model
  fields
- **AND** the canonical walk-forward regression baseline therefore does
  not drift as a result of changing the defaults

