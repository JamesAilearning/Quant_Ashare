# v2-benchmark-data-contract Specification

## Purpose
TBD - created by archiving change define-v2-benchmark-data-contract-foundation. Update Purpose after archive.
## Requirements
### Requirement: V2 SHALL define benchmark artifact source-of-truth boundaries

The system SHALL define how benchmark artifacts are identified and where contract validation resolves source-of-truth, without implicitly changing runtime benchmark-selection semantics.

#### Scenario: source-of-truth boundary is inspected
- **WHEN** maintainers inspect benchmark contract documentation
- **THEN** benchmark artifact identity and source-of-truth rules are explicit
- **AND** contract scope does not silently define runtime selection precedence

### Requirement: Benchmark contract SHALL require provenance metadata

The benchmark contract SHALL define required metadata/provenance fields for auditable operation, including source context and snapshot/coverage context.

#### Scenario: benchmark metadata contract is reviewed
- **WHEN** contributors inspect benchmark contract requirements
- **THEN** required provenance fields are explicitly listed
- **AND** missing/invalid provenance is classified by validation rules

### Requirement: Benchmark contract SHALL define explicit validation expectations

The benchmark contract SHALL define validation expectations for missing files, unreadable artifacts, schema mismatch, stale data, incomplete coverage, and temporal integrity issues.

#### Scenario: invalid benchmark artifact is evaluated
- **WHEN** benchmark artifact validation is performed
- **THEN** failure categories are explicit and auditable
- **AND** validation output distinguishes warnings and errors by policy
- **AND** temporal leakage/lookahead risks are explicitly checked

### Requirement: Benchmark contract SHALL define operator-facing status requirements

The contract SHALL define what status fields operators must see for benchmark artifact health, including artifact presence, metadata presence, snapshot/coverage context, and warning/error summaries.

#### Scenario: operator-facing benchmark status is inspected
- **WHEN** maintainers review status requirements
- **THEN** required status fields are explicit and testable
- **AND** contract health remains informational unless policy elevates severity

### Requirement: Benchmark contract SHALL preserve canonical-governance boundaries

Benchmark contract validation SHALL remain separate from canonical official-metrics path definition and SHALL NOT introduce competing official-metrics semantics.

#### Scenario: governance boundary is reviewed
- **WHEN** contributors inspect benchmark contract and canonical backtest contract together
- **THEN** canonical official-metrics path remains unchanged
- **AND** benchmark contract changes do not silently alter trading semantics

### Requirement: Benchmark contract SHALL detect snapshot_at vs artifact data mismatch

The benchmark data contract SHALL surface a `temporal_issue` error whenever the
manifest-declared `snapshot_at` does not equal the actual maximum row date in the
benchmark artifact. This protects downstream consumers from silently trusting a
manifest that lies about the freshness of the underlying data, in either direction
(claiming newer than reality, or claiming older than reality).

#### Scenario: manifest snapshot_at is newer than artifact max row date
- **WHEN** a `BenchmarkArtifactProfile` is supplied with `has_snapshot_at_mismatch=True`
- **THEN** `BenchmarkDataContract.validate_and_build_status` returns `contract_health="error"`
- **AND** the `errors` tuple contains `temporal_issue`

#### Scenario: manifest snapshot_at is older than artifact max row date
- **WHEN** a `BenchmarkArtifactProfile` is supplied with `has_snapshot_at_mismatch=True`
- **THEN** `BenchmarkDataContract.validate_and_build_status` returns `contract_health="error"`
- **AND** the `errors` tuple contains `temporal_issue`

#### Scenario: manifest snapshot_at exactly equals artifact max row date
- **WHEN** a `BenchmarkArtifactProfile` is supplied with `has_snapshot_at_mismatch=False`
- **AND** all other validations pass
- **THEN** `BenchmarkDataContract.validate_and_build_status` returns `contract_health="ok"`


### Requirement: Benchmark contract SHALL reuse shared validator helpers

Benchmark contract implementation SHALL delegate presence, metadata, required-
column, staleness, coverage, temporal, and snapshot_at-mismatch checks to the
shared validator helpers in `src/contracts/_shared_validators.py`, and SHALL
NOT duplicate those patterns inline. Benchmark-specific error codes SHALL
remain the sole truth source for their own strings.

#### Scenario: refactor keeps public error-code constants stable
- **WHEN** maintainers grep for `ISSUE_MISSING_ARTIFACT`, `ISSUE_MISSING_MANIFEST`, `ISSUE_SCHEMA_MISMATCH`, `ISSUE_STALE_DATA`, `ISSUE_INCOMPLETE_COVERAGE`, `ISSUE_TEMPORAL_ISSUE` in `src/contracts/benchmark_data_contract.py`
- **THEN** each constant is still defined and exported from the benchmark contract module
- **AND** the shared validator module does not redefine them

#### Scenario: inline duplication is eliminated
- **WHEN** maintainers read `BenchmarkDataContract.validate_and_build_status`
- **THEN** presence, metadata, columns, staleness, coverage and snapshot_at-mismatch checks are delegated to shared helpers
- **AND** no copy-paste of those checks exists between benchmark, universe, and taxonomy contracts
