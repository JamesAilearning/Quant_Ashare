## ADDED Requirements

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
