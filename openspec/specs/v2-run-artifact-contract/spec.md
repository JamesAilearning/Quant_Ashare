# v2-run-artifact-contract Specification

## Purpose
TBD - created by archiving change define-v2-run-artifact-contract-foundation. Update Purpose after archive.
## Requirements
### Requirement: V2 SHALL define run-artifact source-of-truth boundaries

The system SHALL define run artifact identity and source-of-truth rules explicitly, without implicitly introducing runtime execution semantics.

#### Scenario: run artifact source boundary is reviewed
- **WHEN** maintainers inspect run-artifact contract documentation
- **THEN** source-of-truth and artifact identity rules are explicit
- **AND** contract scope does not silently define runtime behavior

### Requirement: Run-artifact contract SHALL require reproducibility metadata

The run-artifact contract SHALL define required manifest metadata for reproducibility and auditability, including run identity, config fingerprint, lineage context, and timestamps.

#### Scenario: reproducibility metadata requirements are inspected
- **WHEN** contributors inspect run-artifact contract requirements
- **THEN** required reproducibility fields are explicitly listed
- **AND** missing/invalid fields are classified by validation rules

### Requirement: Run-artifact contract SHALL define explicit validation expectations

The run-artifact contract SHALL define validation expectations for missing artifacts/manifests, schema mismatch, missing reproducibility metadata, lineage inconsistency, and temporal/provenance anomalies.

#### Scenario: invalid run artifact is evaluated
- **WHEN** run-artifact contract validation is performed
- **THEN** failure categories are explicit and auditable
- **AND** validation output distinguishes warnings and errors by policy

### Requirement: Run-artifact contract SHALL define operator-facing status requirements

The contract SHALL define required operator-facing status fields for run artifact health, including artifact/manifest presence, reproducibility metadata completeness, lineage checks, and warning/error summaries.

#### Scenario: operator-facing run status is inspected
- **WHEN** maintainers review status requirements
- **THEN** required status fields are explicit and testable
- **AND** contract health remains informational unless policy elevates severity

### Requirement: Run-artifact contract SHALL preserve canonical-governance boundaries

Run-artifact contract validation SHALL remain separate from canonical official-metrics definition and SHALL NOT introduce competing official-metrics semantics.

#### Scenario: governance boundary is reviewed
- **WHEN** contributors inspect run-artifact contract and canonical backtest contract together
- **THEN** canonical official-metrics definition remains unchanged
- **AND** run-artifact contract changes do not silently alter trading semantics

