## ADDED Requirements

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
