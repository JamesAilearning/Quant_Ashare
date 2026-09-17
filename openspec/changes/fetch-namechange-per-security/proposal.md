## Why

The guarded global announcement-date query can refuse truncation but cannot
recover missing-announcement name history. Production acceptance remains on hold
after an aggregate update lost historical suspension records. The user approved
isolated reconstruction covering listed, delisted and previously observed codes,
followed by independent validation before a production switch.

## What Changes

- Add an explicit `namechange_mode=per_security_full` option to the existing
  fetcher and daily-update entry points; keep `date_range` as the compatible
  default. Never fall back between strategies.
- Freeze and validate the union of both stock-basic snapshots and retained
  name-history codes, then query every code serially without date filters.
- Preserve null announcement dates and all source history, check bounded
  resources, response identity and retained keys, and publish only one complete
  candidate atomically. Refuse ambiguous blind resume in the new mode.
- Document and test mode propagation, failed prerequisites, late failures,
  coverage preservation and safe operational migration.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `v2-ashare-survivorship-correction`: explicit full per-security name-history
  acquisition through the existing producer and publication boundary.

## Impact

Fetcher/config, pure acquisition helper, fetch and daily-update CLIs, synthetic
tests and operator documentation. No raw or manifest schema changes, new metric
path, model change, stock-selection change, provider deployment or task enablement
is a side effect of this code change. Production reconstruction and publication
remain separately validated operational steps under the user's authorization.
