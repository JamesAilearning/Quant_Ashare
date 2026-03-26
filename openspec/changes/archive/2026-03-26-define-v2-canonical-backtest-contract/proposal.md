## Why

V2 needs an explicit canonical backtest contract before runtime implementation expands, otherwise official metrics semantics can drift across competing paths. Defining this contract now preserves auditability and keeps canonical vs experimental boundaries unambiguous.

## What Changes

- Define the canonical official-metrics backtest contract for V2.
- Define canonical path interfaces, responsibilities, and boundary ownership.
- Define canonical input contract (required inputs, optional inputs, explicit exclusions).
- Define canonical output contract (required metrics payload and provenance fields).
- Define explicit out-of-scope items for canonical execution (experimental constraints/research logic).
- Define minimum validation and regression expectations to protect canonical semantics.
- Keep this change contract-only (no runtime backtest implementation).

## Capabilities

### New Capabilities
- `v2-canonical-backtest-contract`: define a single canonical official-metrics contract and explicit separation from experimental/research execution.

### Modified Capabilities
- None.

## Impact

- Affected areas:
  - `openspec/changes/define-v2-canonical-backtest-contract/*`
  - future implementation touchpoints in `src/core/`, `src/contracts/`, and tests (not implemented in this change)
- No trading runtime semantics are implemented or changed in this change.
- No competing official metrics path is introduced.
