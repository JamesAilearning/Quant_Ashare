## Why

The V2 skeleton and canonical backtest contract are in place, but benchmark inputs are still undefined at contract level.  
Without an explicit benchmark data contract, future runtime work can drift in source-of-truth rules, provenance quality, and validation behavior, reducing auditability.

## What Changes

- Define a V2 benchmark data contract foundation (contract-only, no runtime selection behavior).
- Define benchmark artifact source-of-truth boundaries.
- Define required benchmark metadata/provenance fields.
- Define validation expectations for:
  - missing files
  - schema mismatch
  - stale artifacts
  - incomplete date coverage
  - temporal issues / lookahead risk
- Define operator-facing benchmark contract status expectations.
- Define explicit boundary between benchmark contract validation and runtime benchmark selection semantics.

## Capabilities

### New Capabilities
- `v2-benchmark-data-contract`: benchmark artifact contract, validation boundary, and operator-facing status expectations.

### Modified Capabilities
- None.

## Impact

- Affected areas:
  - `openspec/changes/define-v2-benchmark-data-contract-foundation/*`
  - future implementation touchpoints in `src/contracts/`, `src/data/`, and `tests/governance/` (not implemented in this change)
- No runtime trading behavior is implemented or changed in this change.
- No canonical official-metrics definition is changed in this change.
