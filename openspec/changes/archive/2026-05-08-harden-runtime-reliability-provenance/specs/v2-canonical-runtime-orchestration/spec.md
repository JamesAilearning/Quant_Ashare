## ADDED Requirements

### Requirement: Walk-forward ensemble predictions SHALL be explicit and reproducible

The system SHALL allow walk-forward ensembling to change official fold
predictions only when the engine persists the exact post-ensemble prediction
series consumed by `SignalAnalyzer` and `BacktestRunner`, and the canonical
backtest `predictions_ref` points at that materialized prediction artifact.

#### Scenario: ensemble is enabled for a fold
- **WHEN** `WalkForwardConfig.ensemble_window > 1` and at least one prior model
  contributes predictions
- **THEN** the fold writes a prediction artifact containing the averaged
  prediction series used for signal analysis and backtest
- **AND** the fold's `CanonicalBacktestInput.predictions_ref` references that
  prediction artifact instead of only the current model pickle
- **AND** the fold report records the current model reference, contributing
  prior model references, and prediction artifact hash

#### Scenario: ensemble is a no-op for a fold
- **WHEN** `ensemble_window == 1` or no prior model contributes predictions
- **THEN** the fold still records ensemble metadata with `used=False`
- **AND** the canonical backtest `predictions_ref` references the materialized
  current-fold prediction artifact

### Requirement: Walk-forward ensemble priors SHALL NOT change the signal index implicitly

Prior model predictions SHALL only contribute to an ensemble when their
`(datetime, instrument)` index exactly matches the current fold prediction
index. Index mismatches SHALL be skipped or failed loudly and recorded in
fold metadata; pandas union alignment SHALL NOT change the signal universe.

#### Scenario: prior prediction index matches current prediction index
- **WHEN** a prior model returns a `pandas.Series` whose index equals the
  current fold prediction index
- **THEN** it may contribute to the averaged predictions
- **AND** its real fold index and model path are recorded as contributing
  provenance

#### Scenario: prior prediction index differs
- **WHEN** a prior model returns a `pandas.Series` with different dates,
  instruments, or index ordering
- **THEN** the prior is not included in the averaged predictions
- **AND** the fold metadata records an index-mismatch rejection
- **AND** the resulting prediction series keeps exactly the current fold index

### Requirement: Runtime CLIs SHALL require explicit qlib provider selection

Shipped runtime CLIs that initialize canonical qlib SHALL require an explicit
provider URI from operator config and SHALL NOT substitute a machine-local
default path for official metric runs.

#### Scenario: walk-forward YAML omits provider_uri
- **WHEN** `scripts/run_walk_forward.py` loads a YAML file without
  `provider_uri`
- **THEN** config loading raises a typed error before qlib initialization
- **AND** no default path such as `D:/qlib_data/my_cn_data` is used

### Requirement: Optional post-backtest pipeline steps SHALL report degradation without discarding official backtest output

After canonical backtest completion, the system SHALL NOT let optional factor
analysis or chart generation failures erase completed backtest/report outputs.
Their failures SHALL be logged and represented as skipped/degraded optional
outputs.

#### Scenario: factor analysis raises after backtest
- **WHEN** `Pipeline.run()` completes the canonical backtest and optional factor
  analysis raises
- **THEN** the pipeline continues to write the main report
- **AND** the report records a factor-analysis skipped reason

#### Scenario: chart generation raises after report write
- **WHEN** chart generation fails after the pipeline report has been written
- **THEN** `Pipeline.run()` still returns `PipelineResult`
- **AND** the failure is logged as an optional visualization degradation
