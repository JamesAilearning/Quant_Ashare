## Why

V2 already has canonical and data-contract foundations, but operator-facing status and workflow boundaries are not yet standardized.  
Without a shared status/workflow contract, health messages can become inconsistent and may blur informational status versus governance meaning.

## What Changes

- Define V2 operator-facing status categories and boundary semantics.
- Define representation rules for:
  - contract health
  - warnings and errors
  - runtime placeholders / not-yet-implemented states
- Define explicit separation between informational status and governance meaning.
- Define minimum workflow/status expectations spanning:
  - canonical runtime boundary
  - data-contract boundaries
  - runtime placeholder boundaries
- Define regression expectations for operator-visible status boundaries.

## Capabilities

### New Capabilities
- `v2-operator-status-workflow-foundation`: a contract-level baseline for operator-facing status and workflow boundaries.

### Modified Capabilities
- None.

## Impact

- Affected areas:
  - `openspec/changes/define-v2-operator-status-and-workflow-foundation/*`
  - future implementation touchpoints in `web/`, `src/contracts/`, and `tests/governance/` (not implemented in this change)
- No runtime trading behavior is implemented or changed in this change.
- No canonical official-metrics definition is changed in this change.
