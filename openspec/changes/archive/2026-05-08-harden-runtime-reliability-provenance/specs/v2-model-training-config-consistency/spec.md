## ADDED Requirements

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
