## ADDED Requirements

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
