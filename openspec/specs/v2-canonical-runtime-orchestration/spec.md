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

### Requirement: PIT-handler walk-forward configs SHALL require post_adjusted

A `WalkForwardConfig` using a PIT feature handler SHALL require `adjust_mode ==
"post_adjusted"` (the PIT handler today is `"MinedFactor"`).

The PIT bin bundle is written post-adjusted and `PITDataProvider` initialises
the canonical qlib runtime in `post_adjusted` mode, so mined factor values are
physically constructed on post-adjusted prices. Evaluating them under a
different runtime adjustment mode would either abort every fold with a cryptic
`QlibRuntimeInitError` (the single-canonical-runtime guard rejecting the
mismatched second init) or — if that guard were relaxed — silently score the
factors against mismatched prices. The constraint SHALL therefore be enforced
at `WalkForwardConfig` construction (`__post_init__`), raising a typed
`WalkForwardError` whose message names the offending `adjust_mode` and the
required value, BEFORE any qlib init, feature building, model training, or
backtest. Non-PIT handlers (e.g. `"Alpha158"`) SHALL be unaffected and MAY use
any supported `adjust_mode`.

#### Scenario: MinedFactor handler with a non-post adjust_mode is rejected at construction
- **WHEN** a caller constructs `WalkForwardConfig(feature_handler="MinedFactor", adjust_mode="pre_adjusted")`
- **THEN** a typed `WalkForwardError` is raised at construction time
- **AND** the message states the PIT/MinedFactor path requires `adjust_mode: "post_adjusted"` and names the offending value
- **AND** no qlib runtime init, feature building, or backtest has run

#### Scenario: MinedFactor handler with post_adjusted is accepted
- **WHEN** a caller constructs `WalkForwardConfig(feature_handler="MinedFactor", adjust_mode="post_adjusted")`
- **THEN** construction succeeds with no error

#### Scenario: a non-PIT handler is unaffected by the PIT post-adjusted rule
- **WHEN** a caller constructs `WalkForwardConfig(feature_handler="Alpha158", adjust_mode="pre_adjusted")`
- **THEN** construction succeeds — the post-adjusted requirement applies only to PIT feature handlers

### Requirement: Walk-forward fold generation SHALL embargo the Alpha158 label lookahead

`WalkForwardEngine` SHALL generate fold boundaries such that, for every
fold, there are at least the label-lookahead embargo's trading days
strictly between `train_end` and `valid_start`, and the same strictly
between `valid_end` and `test_start`. The gap size SHALL come from the ONE
shared horizon-driven derivation
(`label_lookahead_days(label_horizon_days)` in
`src/data/_segment_embargo.py`; `v2-label-horizon-config` governs the
horizon semantics): the default `label_horizon_days=1` SHALL yield exactly
the historical `LABEL_LOOKAHEAD_DAYS` value — default runs unchanged — and
`H>1` widens the gap to `H+1` trading days. The gap SHALL be created by an
embargo gap (the gap trading days belong to no segment), NOT by weakening,
lowering, or bypassing the embargo guard; generator and guard SHALL read
the same derivation so they cannot drift.

The month-aligned start anchors (`train_start`, `valid_start`,
`test_start`) and `test_end` SHALL be preserved; the embargo gap SHALL be
created by pulling the segment end boundaries (`train_end`, `valid_end`)
back onto the trading calendar.

#### Scenario: generated folds satisfy the embargo guard

- **WHEN** `WalkForwardEngine` generates folds for a config whose nominal
  month-aligned boundaries would be adjacent
- **THEN** every generated fold passes `validate_segment_embargo`
  (both `train_end→valid_start` and `valid_end→test_start` have at least
  the horizon-driven gap's trading days between them; at the default
  horizon that gap equals `LABEL_LOOKAHEAD_DAYS`)
- **AND** `FeatureDatasetBuilder.build` does not reject any fold for an
  embargo violation

#### Scenario: train label window does not reach into the valid segment

- **WHEN** a fold is generated with `LABEL_LOOKAHEAD_DAYS = 2`
- **THEN** the trading days the last train row's Alpha158 label reads
  (`train_end` + 1 and + 2 trading days) lie strictly inside the
  discarded embargo gap
- **AND** none of those days fall within `[valid_start, valid_end]`

#### Scenario: the embargo guard is not weakened

- **WHEN** this change is applied
- **THEN** `src/data/_segment_embargo.py` keeps `LABEL_LOOKAHEAD_DAYS` as
  the H=1 value and `FeatureDatasetBuilder`'s embargo validation is not
  weakened
- **AND** the fold generator obtains its gap size from the shared
  horizon-driven derivation (`label_lookahead_days`), never a hardcoded
  constant

#### Scenario: quarter-grid fold anchors are preserved

- **WHEN** folds are generated over a multi-year range with quarterly
  stepping
- **THEN** `valid_start` and `test_start` remain on their month-aligned
  nominal dates (the embargo gap is taken from the segment tails, not by
  shifting the start anchors)

