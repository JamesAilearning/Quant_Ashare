# v2-taxonomy-data-contract Specification

## Purpose
TBD - created by archiving change define-v2-taxonomy-data-contract-foundation. Update Purpose after archive.
## Requirements
### Requirement: V2 SHALL define taxonomy artifact source-of-truth boundaries

The system SHALL define taxonomy artifact identity and source-of-truth rules explicitly, without implicitly introducing industry-aware runtime semantics.

#### Scenario: source-of-truth boundary is inspected
- **WHEN** maintainers inspect taxonomy contract documentation
- **THEN** taxonomy source-of-truth rules are explicit
- **AND** contract scope does not silently define runtime industry behavior

### Requirement: Taxonomy contract SHALL require provenance metadata

The taxonomy contract SHALL define required metadata/provenance fields for auditable operation, including source context and snapshot/version context.

#### Scenario: taxonomy metadata contract is reviewed
- **WHEN** contributors inspect taxonomy contract requirements
- **THEN** required provenance fields are explicitly listed
- **AND** missing/invalid provenance is classified by validation rules

### Requirement: Taxonomy contract SHALL define supported temporal validity modes

The taxonomy contract SHALL define supported temporal validity modes and their expected schema semantics, including `static`, `trade_date`, and `range`.

#### Scenario: temporal mode contract is reviewed
- **WHEN** maintainers inspect taxonomy temporal-validity requirements
- **THEN** supported temporal modes are explicitly listed
- **AND** expected schema interpretation is explicit for each supported mode

### Requirement: Taxonomy contract SHALL define explicit validation expectations

The taxonomy contract SHALL define validation expectations for missing artifacts, unreadable files, schema mismatch, stale data, incomplete coverage, inconsistent mappings, and temporal leakage/lookahead risk.

#### Scenario: invalid taxonomy artifact is evaluated
- **WHEN** taxonomy contract validation is performed
- **THEN** failure categories are explicit and auditable
- **AND** validation output distinguishes warnings and errors by policy
- **AND** temporal leakage risks are explicitly checked

### Requirement: Taxonomy contract SHALL define operator-facing status requirements

The contract SHALL define what status fields operators must see for taxonomy artifact health, including artifact presence, metadata presence, temporal mode, snapshot/coverage context, mapping consistency status, and warning/error summaries.

#### Scenario: operator-facing taxonomy status is inspected
- **WHEN** maintainers review status requirements
- **THEN** required status fields are explicit and testable
- **AND** contract health remains informational unless policy elevates severity

### Requirement: Taxonomy contract SHALL preserve canonical-governance boundaries

Taxonomy contract validation SHALL remain separate from canonical official-metrics path definition and SHALL NOT introduce industry-aware runtime semantics or governance meaning changes.

#### Scenario: governance boundary is reviewed
- **WHEN** contributors inspect taxonomy contract and canonical backtest contract together
- **THEN** canonical official-metrics definition remains unchanged
- **AND** taxonomy contract changes do not silently alter trading semantics

