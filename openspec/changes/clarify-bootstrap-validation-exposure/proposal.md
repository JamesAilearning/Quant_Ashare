## Why

The production runbook calls the bootstrap ensemble dry-run window
2026-05-06..2026-07-31 fully out of sample for all three members, although it
overlaps m3's registered validation window through 2026-07-07. Validation
participates in early stopping/model selection, so the statement overstates
what this behavior gate proves.

## What Changes

- Correct the operator-facing description, show the exact validation overlap,
  and distinguish behavior checks from independent unseen-data performance.
- Keep the registered dry-run window, gate thresholds, historical artifacts,
  presets and runtime behavior unchanged.
- Add CI-runnable governance checks against committed preset/gate evidence and
  the runbook's explanation; no training, data bundle or metric recomputation.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `v2-daily-stock-recommendation`: add a documentation requirement that
  bootstrap dry-run evidence disclose validation exposure and its limited role.

## Impact

Only the production runbook, governance tests and this OpenSpec change.
No API/schema/dependency change, no promotion or recertification decision, no
production data/model mutation, and no new official metrics path.
