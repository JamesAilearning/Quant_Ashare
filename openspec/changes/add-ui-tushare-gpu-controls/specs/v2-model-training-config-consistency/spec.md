## ADDED Requirements

### Requirement: Model training SHALL support explicit compute-device selection without silent fallback

Pipeline, walk-forward, and shared model-training config projection SHALL carry
an explicit `compute_device` field. The default SHALL be `cpu`. A `gpu` request
SHALL be passed to the supported model backend and SHALL NOT silently fall back
to CPU if the local backend cannot satisfy it.

#### Scenario: CPU default is used

- **WHEN** existing pipeline or walk-forward YAML omits `compute_device`
- **THEN** config construction uses `compute_device: "cpu"`
- **AND** existing CPU training behavior remains unchanged

#### Scenario: LightGBM GPU is requested

- **WHEN** `ModelTrainConfig(model_type="LGBModel", compute_device="gpu")` is used
- **THEN** `ModelTrainer` passes `device_type="gpu"` to qlib `LGBModel`
- **AND** LightGBM backend failures are surfaced as training failures rather than CPU fallback

#### Scenario: unsupported GPU model is requested

- **WHEN** `compute_device="gpu"` is paired with a model family not approved for GPU in this runtime
- **THEN** config validation raises a typed error before model training starts
