# v2-universe-data-contract Specification

## Purpose
TBD - created by archiving change define-v2-universe-data-contract-foundation. Update Purpose after archive.
## Requirements
### Requirement: V2 SHALL define universe artifact source-of-truth boundaries

The system SHALL define universe artifact identity and source-of-truth rules explicitly, without implicitly introducing runtime universe-selection semantics.

#### Scenario: source-of-truth boundary is inspected
- **WHEN** maintainers inspect universe contract documentation
- **THEN** universe source-of-truth rules are explicit
- **AND** contract scope does not silently define runtime selection behavior

### Requirement: Universe contract SHALL require provenance metadata

The universe contract SHALL define required metadata/provenance fields for auditable operation, including source context and snapshot/version context.

#### Scenario: universe metadata contract is reviewed
- **WHEN** contributors inspect universe contract requirements
- **THEN** required provenance fields are explicitly listed
- **AND** missing/invalid provenance is classified by validation rules

### Requirement: Universe contract SHALL define supported temporal validity modes

The universe contract SHALL define supported temporal validity modes and expected membership schema interpretation.

#### Scenario: temporal mode contract is reviewed
- **WHEN** maintainers inspect universe temporal-validity requirements
- **THEN** supported temporal modes are explicitly listed
- **AND** expected schema interpretation is explicit for each mode

### Requirement: Universe contract SHALL define explicit validation expectations

The universe contract SHALL define validation expectations for missing artifacts, unreadable files, schema mismatch, stale data, incomplete coverage, membership inconsistency, and temporal leakage/lookahead risk.

#### Scenario: invalid universe artifact is evaluated
- **WHEN** universe contract validation is performed
- **THEN** failure categories are explicit and auditable
- **AND** validation output distinguishes warnings and errors by policy
- **AND** temporal leakage risks are explicitly checked

### Requirement: Universe contract SHALL define operator-facing status requirements

The contract SHALL define required operator-facing status fields for universe artifact health, including artifact presence, metadata presence, temporal mode, snapshot/coverage context, consistency status, and warning/error summaries.

#### Scenario: operator-facing universe status is inspected
- **WHEN** maintainers review status requirements
- **THEN** required status fields are explicit and testable
- **AND** contract health remains informational unless policy elevates severity

### Requirement: Universe contract SHALL preserve canonical-governance boundaries

Universe contract validation SHALL remain separate from canonical official-metrics path definition and SHALL NOT introduce competing official-metrics semantics.

#### Scenario: governance boundary is reviewed
- **WHEN** contributors inspect universe contract and canonical backtest contract together
- **THEN** canonical official-metrics definition remains unchanged
- **AND** universe contract changes do not silently alter trading semantics

