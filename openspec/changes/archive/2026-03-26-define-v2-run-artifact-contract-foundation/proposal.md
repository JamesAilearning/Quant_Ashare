## Why

Phase 1 contract foundations are in place (canonical backtest + benchmark/taxonomy/universe contracts), but V2 still lacks a run artifact and reproducibility contract.  
Before runtime pipeline implementation expands, V2 needs explicit, auditable boundaries for run manifests, config fingerprints, artifact lineage, and operator-visible run status.

## What Changes

- Define a V2 run-artifact contract foundation (contract-only, no runtime execution semantics).
- Define source-of-truth rules for run artifacts and sidecar manifest metadata.
- Define required reproducibility metadata fields (run id, config fingerprint, input contract snapshots, code/commit context, timestamps).
- Define validation expectations for:
  - missing artifacts/manifests
  - schema mismatch
  - missing reproducibility metadata
  - lineage inconsistency
  - temporal/provenance anomalies
- Define operator-facing run contract status requirements.
- Define explicit boundary between run-artifact validation and runtime trading semantics.

## Capabilities

### New Capabilities
- `v2-run-artifact-contract`: run artifact and reproducibility metadata contract with explicit validation/status boundaries.

### Modified Capabilities
- None.

## Impact

- Affected areas:
  - `openspec/changes/define-v2-run-artifact-contract-foundation/*`
  - future implementation touchpoints in `src/contracts/`, `src/core/`, and `tests/governance/` (not implemented in this change)
- No runtime trading behavior is implemented or changed in this change.
- No canonical official-metrics definition is changed in this change.
