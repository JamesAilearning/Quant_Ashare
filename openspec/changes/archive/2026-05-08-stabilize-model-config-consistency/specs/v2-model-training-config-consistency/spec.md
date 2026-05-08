## ADDED Requirements

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
