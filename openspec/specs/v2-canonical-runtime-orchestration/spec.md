# v2-canonical-runtime-orchestration Specification

## Purpose

Define orchestration requirements for runtime entry points that construct
canonical qlib initialization and backtest requests.
## Requirements
### Requirement: Canonical runtime orchestration SHALL preserve backtest execution semantics

Runtime orchestration components and shipped runtime CLIs SHALL preserve
caller-supplied canonical backtest controls when constructing canonical
runtime/backtest config instead of substituting hidden defaults. This includes
price adjustment mode, signal-to-execution lag, execution price kind, minimum
cost, and limit threshold.

#### Scenario: Pipeline rejects boolean signal lag
- **WHEN** a caller constructs `PipelineConfig` with
  `signal_to_execution_lag=True` or `signal_to_execution_lag=False`
- **THEN** Pipeline config construction raises a typed `PipelineError`
- **AND** no feature building, model training, qlib initialization, or backtest
  work starts before the malformed operator config is rejected

#### Scenario: Pipeline constructs a canonical backtest request
- **WHEN** `Pipeline.run()` builds `CanonicalBacktestInput`
- **THEN** the request's `adjust_mode` matches `PipelineConfig.adjust_mode`
- **AND** qlib runtime initialization records the same provider adjustment mode
- **AND** exchange and cost controls come from `PipelineConfig`

#### Scenario: WalkForward constructs a canonical backtest request
- **WHEN** `WalkForwardEngine` builds a fold's `CanonicalBacktestInput`
- **THEN** the request's `adjust_mode` comes from `WalkForwardConfig`
- **AND** the request's `signal_to_execution_lag` comes from
  `WalkForwardConfig`
- **AND** the exchange config's `execution_price_kind`, `min_cost`, and
  `limit_threshold` come from `WalkForwardConfig`

#### Scenario: Walk-forward CLI initializes qlib runtime
- **WHEN** `scripts/run_walk_forward.py` loads a walk-forward YAML config
- **THEN** the returned `WalkForwardConfig.adjust_mode` is also used as
  `QlibRuntimeConfig.data_adjust_mode`
- **AND** qlib runtime initialization cannot silently use an adjustment mode
  different from the fold backtest requests

#### Scenario: WalkForward receives an unsupported canonical control
- **WHEN** a caller constructs `WalkForwardConfig` with an unsupported
  adjustment mode, execution price kind, signal lag, cost, or limit threshold
- **THEN** a typed `WalkForwardError` or canonical contract error is raised
- **AND** no fold backtest is executed with hidden substitute semantics

### Requirement: Canonical runtime orchestration SHALL keep risk constraints out of the canonical core path

Risk-constraint behavior SHALL NOT be executable through the canonical
`src.core` runtime layer when it modifies predictions after signal generation
and before backtest execution, unless a dedicated approved runtime
specification defines those trading semantics.

#### Scenario: canonical core risk-constraint path is called
- **WHEN** a caller imports `src.core.risk_constraints` and tries to apply risk
  constraints
- **THEN** the call fails closed with a typed risk-constraint error
- **AND** the error explains that executable behavior is experimental
- **AND** no predictions are filtered or reweighted through the canonical core
  path

#### Scenario: experimental risk constraints are inspected
- **WHEN** maintainers inspect executable risk-constraint behavior
- **THEN** the implementation is located under an explicitly experimental
  namespace
- **AND** it is not treated as an official canonical metrics path

### Requirement: Pipeline attribution SHALL use an explicit validated taxonomy artifact when configured

Pipeline attribution SHALL only replace the board heuristic with a real
industry taxonomy when the operator supplies an explicit static taxonomy CSV,
manifest, and taxonomy id. The artifact SHALL be loaded through the taxonomy
artifact loader and validated through the taxonomy data contract before its map
is passed to attribution.

#### Scenario: no Pipeline attribution taxonomy is configured
- **WHEN** `Pipeline.run()` builds `AttributionConfig` without taxonomy artifact
  settings
- **THEN** attribution uses its default board heuristic
- **AND** the attribution result remains labeled with the board-heuristic
  taxonomy id

#### Scenario: valid Pipeline attribution taxonomy is configured
- **WHEN** `PipelineConfig` includes a static taxonomy artifact path, manifest
  path, and taxonomy id
- **THEN** Pipeline loads the artifact profile through `TaxonomyArtifactLoader`
- **AND** Pipeline validates the profile through `TaxonomyDataContract`
- **AND** Pipeline reads the CSV mapping only after validation has no errors
- **AND** `AttributionConfig.industry_map_override` and
  `AttributionConfig.industry_taxonomy_id` are set together

#### Scenario: incomplete Pipeline attribution taxonomy settings are provided
- **WHEN** only some of artifact path, manifest path, and taxonomy id are set
- **THEN** Pipeline config construction raises a typed `PipelineError`
- **AND** no implicit board/taxonomy mixing is performed

#### Scenario: invalid Pipeline attribution taxonomy artifact is configured
- **WHEN** the configured taxonomy artifact or manifest fails contract
  validation
- **THEN** Pipeline raises a typed `PipelineError`
- **AND** it does not silently fall back to the board heuristic

#### Scenario: unsupported Pipeline attribution taxonomy mode is configured
- **WHEN** `PipelineConfig.industry_temporal_mode` is not `static`
- **THEN** Pipeline raises a typed `PipelineError`
- **AND** no runtime attribution map is built from an unsupported temporal mode

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

### Requirement: Walk-forward optional attribution SHALL report degradation without discarding completed fold outputs

Optional walk-forward attribution failures SHALL be represented as a
degraded attribution block rather than replacing the entire fold with a failed
placeholder after a fold completes model prediction, signal analysis, and
canonical backtest. Configuration or artifact load failures that affect every
fold MAY still fail closed.

#### Scenario: unexpected attribution error after fold backtest
- **WHEN** walk-forward attribution raises an unexpected exception after the
  fold's canonical backtest has completed
- **THEN** the fold remains successful for backtest and signal-analysis outputs
- **AND** the fold attribution section is marked skipped with an explicit
  `unexpected_error` reason
- **AND** the fold is not replaced by the top-level NaN placeholder

### Requirement: Walk-forward aggregate reports SHALL expose test-window coverage diagnostics

Walk-forward aggregate reports SHALL make test-period coverage behavior visible
when fold test windows are continuous, gapped, or overlapping. The diagnostics
SHALL be informational and SHALL NOT silently change canonical metric
calculation or reject otherwise valid rolling configurations.

#### Scenario: generated test windows have gaps
- **WHEN** generated walk-forward test windows leave calendar-day gaps between
  consecutive test periods
- **THEN** the aggregate report records a gapped coverage mode
- **AND** it records the number of gaps

#### Scenario: generated test windows overlap
- **WHEN** generated walk-forward test windows overlap between consecutive
  folds
- **THEN** the aggregate report records an overlapping coverage mode
- **AND** it records the maximum overlap depth

#### Scenario: generated test windows are continuous
- **WHEN** generated walk-forward test windows are ordered and neither gapped
  nor overlapping
- **THEN** the aggregate report records continuous coverage diagnostics
