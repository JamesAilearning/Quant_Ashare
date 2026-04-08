## ADDED Requirements

### Requirement: V2 SHALL provide a benchmark artifact loader that produces contract-consumable profiles

The system SHALL provide a loader that reads a benchmark artifact csv and its sidecar manifest and produces a `BenchmarkArtifactProfile` consumable by `BenchmarkDataContract.validate_and_build_status` without modification.

#### Scenario: healthy benchmark artifact is loaded
- **WHEN** the loader is given an existing csv with `date,close` columns and a valid manifest
- **THEN** the produced profile has `artifact_present=True` and `manifest_present=True`
- **AND** the profile includes non-empty `snapshot_start`, `snapshot_end`, `rows`, and `columns_present`
- **AND** feeding the profile into `BenchmarkDataContract` yields `contract_health == "ok"`

#### Scenario: benchmark artifact is missing
- **WHEN** the loader is given a non-existent artifact path
- **THEN** the produced profile has `artifact_present=False`
- **AND** feeding it into `BenchmarkDataContract` yields `contract_health == "error"` with `missing_artifact_file`

#### Scenario: benchmark close column contains NaN
- **WHEN** the loader encounters a csv with NaN in the `close` column
- **THEN** the produced profile reports `columns_present` consistent with schema mismatch so the contract yields `schema_mismatch`

#### Scenario: benchmark artifact is future-dated
- **WHEN** the loader reads a csv whose last date is after the supplied reference date
- **THEN** the produced profile sets `has_future_data=True`
- **AND** feeding it into `BenchmarkDataContract` yields an error with `temporal_issue`

### Requirement: Benchmark artifact loader SHALL NOT implement benchmark selection semantics

The loader SHALL only materialize profile data from explicit paths. It SHALL NOT resolve benchmark identifiers via implicit fallback, environment lookup, or registry defaults.

#### Scenario: loader boundary is inspected
- **WHEN** maintainers read the loader source
- **THEN** the loader exposes only explicit path-based load functions
- **AND** no implicit benchmark selection or default-resolution logic is present
