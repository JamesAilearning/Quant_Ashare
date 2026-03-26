# Run Artifact Contract (V2 Foundation)

Date: 2026-03-26

## Purpose

Define run-artifact and reproducibility validation boundaries before runtime execution semantics are implemented.

## Source-Of-Truth Rule

- Allowed source-of-truth (singular):
  - `explicit_run_artifact_with_manifest`
- Implicit source fallback is forbidden by contract.
- Runtime execution semantics are explicitly out of scope in this change.

## Required Reproducibility Metadata

- `run_id`
- `run_kind`
- `produced_at`
- `config_fingerprint`
- `code_ref`
- `input_contract_snapshots`
- `schema_version`

## Validation Expectations

The contract emits explicit warning/error categories for:

- missing files:
  - `missing_artifact_file`
  - `missing_manifest_file`
- schema mismatch:
  - `schema_mismatch`
- missing reproducibility metadata:
  - `missing_reproducibility_metadata`
- lineage inconsistency:
  - `lineage_inconsistency`
- temporal/provenance anomalies:
  - `temporal_provenance_anomaly`

## Operator-Facing Status Fields

Required status payload fields:

- `contract_name`
- `contract_health`
- `run_id`
- `source_of_truth`
- `artifact_path`
- `manifest_path`
- `artifact_present`
- `manifest_present`
- `metadata_fields_present`
- `metadata_fields_missing`
- `produced_at`
- `reference_date`
- `lineage_consistency_status`
- `warnings`
- `errors`
- `governance_note`
- `runtime_execution_semantics_in_scope`

## Governance Boundary

- Contract health is informational by default.
- This contract does not define or alter runtime execution semantics.
- This contract does not modify canonical official-metrics definitions.
