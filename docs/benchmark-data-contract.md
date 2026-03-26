# Benchmark Data Contract (V2 Foundation)

Date: 2026-03-26

## Purpose

Define benchmark artifact validation boundaries before runtime benchmark-selection behavior is implemented.

## Source-Of-Truth Rule

- Allowed source-of-truth (singular):
  - `explicit_artifact_with_manifest`
- Implicit source fallback is forbidden by contract.
- Runtime benchmark-selection semantics are explicitly out of scope in this change.

## Required Provenance Metadata

- `benchmark_code`
- `source_name`
- `source_uri`
- `snapshot_at`
- `schema_version`

## Validation Expectations

The contract emits explicit warning/error categories for:

- missing files:
  - `missing_artifact_file`
  - `missing_manifest_file`
- schema mismatch:
  - missing required metadata fields
  - missing required data columns (`date`, `close`)
- stale data:
  - `stale_data` (warning by default policy)
- incomplete coverage:
  - `incomplete_coverage` (warning by default policy)
- temporal issues:
  - `temporal_issue` (future-known data/metadata or snapshot end-date beyond reference date)

## Operator-Facing Status Fields

Required status payload fields:

- `contract_name`
- `contract_health`
- `benchmark_code`
- `source_of_truth`
- `artifact_path`
- `manifest_path`
- `artifact_present`
- `manifest_present`
- `metadata_fields_present`
- `metadata_fields_missing`
- `snapshot_start`
- `snapshot_end`
- `rows`
- `columns_present`
- `stale_days`
- `coverage_ratio`
- `warnings`
- `errors`
- `governance_note`
- `selection_semantics_in_scope`

## Governance Boundary

- Contract health is informational by default.
- This contract does not define or alter runtime benchmark selection semantics.
- This contract does not modify canonical official-metrics definitions.
