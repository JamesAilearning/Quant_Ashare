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

### Requirement: Taxonomy contract SHALL detect snapshot_at vs artifact data mismatch

The taxonomy data contract SHALL surface a `temporal_leakage` error whenever the
manifest-declared `snapshot_at` does not equal the actual maximum effective date
in the taxonomy artifact, for `trade_date` and `range` temporal modes. The
`static` temporal mode is exempt because the artifact carries no date column.

#### Scenario: trade_date mode with snapshot_at newer than artifact max date
- **WHEN** a `TaxonomyArtifactProfile` is supplied with `has_snapshot_at_mismatch=True`
- **AND** the request `temporal_mode` is `trade_date`
- **THEN** `TaxonomyDataContract.validate_and_build_status` returns `contract_health="error"`
- **AND** the `errors` tuple contains `temporal_leakage`

#### Scenario: range mode with snapshot_at older than artifact max date
- **WHEN** a `TaxonomyArtifactProfile` is supplied with `has_snapshot_at_mismatch=True`
- **AND** the request `temporal_mode` is `range`
- **THEN** `TaxonomyDataContract.validate_and_build_status` returns `contract_health="error"`
- **AND** the `errors` tuple contains `temporal_leakage`

#### Scenario: static mode is exempt from snapshot_at-vs-data check
- **WHEN** a `TaxonomyArtifactProfile` is supplied with `has_snapshot_at_mismatch=False`
- **AND** the request `temporal_mode` is `static`
- **THEN** the contract does not raise a snapshot_at-mismatch error


### Requirement: Taxonomy contract SHALL reuse shared validator helpers

Taxonomy contract implementation SHALL delegate presence, metadata, required-
column, staleness, coverage, temporal, and snapshot_at-mismatch checks to the
shared validator helpers in `src/contracts/_shared_validators.py`, and SHALL
NOT duplicate those patterns inline. Taxonomy-specific checks (temporal_mode
vs metadata consistency, mapping consistency) SHALL remain in the taxonomy
contract module.

#### Scenario: refactor keeps public error-code constants stable
- **WHEN** maintainers grep for `ISSUE_MISSING_ARTIFACT`, `ISSUE_MISSING_MANIFEST`, `ISSUE_SCHEMA_MISMATCH`, `ISSUE_STALE_DATA`, `ISSUE_INCOMPLETE_COVERAGE`, `ISSUE_INCONSISTENT_MAPPINGS`, `ISSUE_TEMPORAL_LEAKAGE` in `src/contracts/taxonomy_data_contract.py`
- **THEN** each constant is still defined and exported from the taxonomy contract module

#### Scenario: taxonomy-specific checks stay in-module
- **WHEN** maintainers inspect taxonomy contract code
- **THEN** `has_inconsistent_mappings` and `temporal_mode` vs metadata checks remain in the taxonomy contract module
- **AND** shared helpers are only used for generic patterns
