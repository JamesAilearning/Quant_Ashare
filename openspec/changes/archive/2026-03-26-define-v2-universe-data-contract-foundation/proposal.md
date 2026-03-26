## Why

V2 baseline already defines canonical backtest contract plus benchmark and taxonomy data-contract foundations.  
Universe artifacts remain undefined at contract level, leaving a governance and auditability gap before runtime pipeline implementation.

## What Changes

- Define a V2 universe data contract foundation (contract-only, no runtime universe-selection behavior).
- Define universe artifact source-of-truth boundaries.
- Define required metadata/provenance fields.
- Define supported validity modes for universe membership timelines.
- Define validation expectations for:
  - missing files
  - schema mismatch
  - stale data
  - incomplete coverage
  - membership inconsistencies
  - temporal leakage / lookahead risk
- Define operator-facing universe contract status expectations.
- Define explicit boundary between universe contract validation and runtime universe-selection semantics.

## Capabilities

### New Capabilities
- `v2-universe-data-contract`: universe artifact contract, temporal-validity boundary, validation expectations, and operator-facing status expectations.

### Modified Capabilities
- None.

## Impact

- Affected areas:
  - `openspec/changes/define-v2-universe-data-contract-foundation/*`
  - future implementation touchpoints in `src/contracts/`, `src/data/`, and `tests/governance/` (not implemented in this change)
- No runtime trading behavior is implemented or changed in this change.
- No canonical official-metrics definition is changed in this change.
