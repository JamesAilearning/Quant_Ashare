## Why

The V2 skeleton, canonical backtest contract, and benchmark data contract foundation are established, but taxonomy artifacts still lack an explicit contract boundary.  
Without a taxonomy contract, future industry-aware runtime work can drift in temporal interpretation, mapping consistency, and validation behavior.

## What Changes

- Define a V2 taxonomy data contract foundation (contract-only, no industry-aware runtime behavior).
- Define taxonomy artifact source-of-truth boundaries.
- Define required taxonomy metadata/provenance fields.
- Define supported temporal validity modes for taxonomy mappings.
- Define validation expectations for:
  - missing files
  - schema mismatch
  - stale data
  - incomplete coverage
  - inconsistent mappings
  - temporal leakage / lookahead risk
- Define operator-facing taxonomy contract status expectations.
- Define explicit boundary between taxonomy contract validation and future industry-aware runtime semantics.

## Capabilities

### New Capabilities
- `v2-taxonomy-data-contract`: taxonomy artifact contract, temporal-validity boundary, validation expectations, and operator-facing status expectations.

### Modified Capabilities
- None.

## Impact

- Affected areas:
  - `openspec/changes/define-v2-taxonomy-data-contract-foundation/*`
  - future implementation touchpoints in `src/contracts/`, `src/data/`, and `tests/governance/` (not implemented in this change)
- No runtime industry-aware behavior is implemented or changed in this change.
- No canonical official-metrics definition is changed in this change.
