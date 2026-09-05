## Why

Rotation currently binds the incoming member's actual training config, dates and
clean registered source, but does not require that config to be the registered
model family. A truthfully recorded retuned member can therefore replace an
official member despite both light gates passing.

## What Changes

- Require the incoming rotation member to match the committed registered family
  before model loading, backup or installation, using the certification revision
  already pinned by this execution.
- Reuse bootstrap's same-family keys/defaults and provider identity semantics.
  Compare the committed bootstrap presets' common non-window fields; exclude only
  their six train/valid/test boundaries and structural `extends` field.
- Bind the exact config parsed for this comparison to the already manifest-bound
  sidecar. Reject unavailable or inconsistent registration evidence without
  changing the incumbent or gate artifacts.
- Keep all existing window checks, light gates, source checks and schemas intact.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `v2-daily-stock-recommendation`: add explicit incoming quarterly member family
  eligibility, applying the existing same-family maintenance policy at execute time.

## Impact

Rotation executor, shared bootstrap configuration helpers, synthetic executor
tests and production runbook. No training, live data repair, deployment, serving
Git dependency, producer/report schema change, performance gate or recertification.
Surviving members and direct/manual serving manifest replacement are out of scope.
