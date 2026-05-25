## ADDED Requirements

### Requirement: Model training diagnostics SHALL normalize eval histories

`ModelTrainer` SHALL normalize supported framework evaluation histories into a
single nested `{dataset: {metric: values}}` shape before returning
`ModelTrainResult.train_metrics`.

#### Scenario: XGB exposes flat eval-history keys

- **WHEN** an XGB training run exposes eval history using flat keys such as
  `valid-rmse`
- **THEN** the trainer records the history as `{"valid": {"rmse": [...]}}`
- **AND** callers do not need model-family-specific parsing for diagnostics

#### Scenario: CatBoost stores eval history on the inner model

- **WHEN** a CatBoost training run stores eval history on the fitted inner model
- **THEN** the trainer refreshes diagnostics from that inner model
- **AND** returned training metrics are not silently empty when history is
  available

---

### Requirement: Model training diagnostics SHALL use model-specific best-iteration indexing

`ModelTrainer` SHALL derive `final_valid_loss` from the metric value that
corresponds to the selected model family's best-iteration convention.

#### Scenario: LightGBM reports a one-based best iteration

- **WHEN** an LGBModel run reports `best_iteration=3`
- **THEN** `final_valid_loss` is read from metric index `2`

#### Scenario: XGB and CatBoost report zero-based best iterations

- **WHEN** an XGBModel or CatBoostModel run reports `best_iteration=3`
- **THEN** `final_valid_loss` is read from metric index `3`
