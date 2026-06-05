## ADDED Requirements

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
