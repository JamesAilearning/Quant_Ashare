## ADDED Requirements

### Requirement: Universe contract SHALL reuse shared validator helpers

Universe contract implementation SHALL delegate presence, metadata, required-
column, staleness, coverage, temporal, and snapshot_at-mismatch checks to the
shared validator helpers in `src/contracts/_shared_validators.py`, and SHALL
NOT duplicate those patterns inline. Universe-specific checks (temporal_mode
vs metadata consistency, membership consistency) SHALL remain in the universe
contract module.

#### Scenario: refactor keeps public error-code constants stable
- **WHEN** maintainers grep for `ISSUE_MISSING_ARTIFACT`, `ISSUE_MISSING_MANIFEST`, `ISSUE_SCHEMA_MISMATCH`, `ISSUE_STALE_DATA`, `ISSUE_INCOMPLETE_COVERAGE`, `ISSUE_INCONSISTENT_MEMBERSHIP`, `ISSUE_TEMPORAL_LEAKAGE` in `src/contracts/universe_data_contract.py`
- **THEN** each constant is still defined and exported from the universe contract module

#### Scenario: universe-specific checks stay in-module
- **WHEN** maintainers inspect universe contract code
- **THEN** `has_inconsistent_membership` and `temporal_mode` vs metadata checks remain in the universe contract module
- **AND** shared helpers are only used for generic patterns
