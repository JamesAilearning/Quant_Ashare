# Universe Data Contract (V2 Foundation)

Date: 2026-03-26

## Purpose

Define universe artifact validation boundaries before runtime universe-selection behavior is implemented.

## Source-Of-Truth Rule

- Allowed source-of-truth (singular):
  - `explicit_universe_artifact_with_manifest`
- Implicit source fallback is forbidden by contract.
- Runtime universe-selection semantics are explicitly out of scope in this change.

## Required Provenance Metadata

- `universe_name`
- `source_name`
- `source_uri`
- `snapshot_at`
- `schema_version`
- `temporal_mode`

## Supported Temporal Validity Modes

- `static`
- `trade_date`
- `range`

Expected required columns by mode:
- base columns (all modes): `instrument`, `in_universe`
- `static`: base columns only
- `trade_date`: base columns + `trade_date`
- `range`: base columns + `effective_start`, `effective_end`

## Validation Expectations

The contract emits explicit warning/error categories for:

- missing files:
  - `missing_artifact_file`
  - `missing_manifest_file`
- schema mismatch:
  - missing required metadata fields
  - inconsistent temporal mode metadata
  - missing required columns for selected temporal mode
- stale data:
  - `stale_data` (warning by default policy)
- incomplete coverage:
  - `incomplete_coverage` (warning by default policy)
- membership inconsistencies:
  - `inconsistent_membership`
- temporal leakage/lookahead:
  - `temporal_leakage` (future-effective data, future-known metadata, or snapshot end-date beyond reference date)

## Operator-Facing Status Fields

Required status payload fields:

- `contract_name`
- `contract_health`
- `universe_name`
- `source_of_truth`
- `artifact_path`
- `manifest_path`
- `artifact_present`
- `manifest_present`
- `temporal_mode`
- `metadata_fields_present`
- `metadata_fields_missing`
- `snapshot_start`
- `snapshot_end`
- `rows`
- `columns_present`
- `stale_days`
- `coverage_ratio`
- `membership_consistency_status`
- `warnings`
- `errors`
- `governance_note`
- `runtime_selection_semantics_in_scope`

## Governance Boundary

- Contract health is informational by default.
- This contract does not define or alter runtime universe-selection semantics.
- This contract does not modify canonical official-metrics definitions.
