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

