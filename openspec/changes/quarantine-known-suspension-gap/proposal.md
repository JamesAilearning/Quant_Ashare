## Why

An isolated production acceptance run observed eight previously retained
`suspend_d` business keys disappear for 688766.SH. The user approved explicit
single-security isolation so this known historical incident need not block
otherwise valid daily recommendations; missing records must not become clean data.

## What Changes

- Add one opt-in, reviewed suspension-history quarantine policy for the exact
  eight known keys. No numeric tolerance, generic security exemption or old-row union.
- Preserve content-addressed raw evidence and carry structured quarantine evidence
  through the existing hole ledger and bundle stamp; they remain incomplete.
- Permit only that verified incident through separately opted-in update/build and
  serving gates. Unknown/mixed holes and missing prerequisites still refuse.
- Exclude the quarantined security before Top-K, retain its score/audit row and
  disclose the policy in JSON, CSV, CLI and operator views. Do not change holdings, models,
  features, original index membership or historical selection.
- Refuse quarantined bundles in both historical metric engines and direct OOS,
  shared backtest and model-rotation qualification entry points; no reassignment of
  existing performance certification. Re-fetching all original keys clears the
  incident only after verification and rebuilding.

## Capabilities

### New Capabilities

- `scoped-suspension-quarantine`: exact incident evidence, lifecycle and serving isolation.

### Modified Capabilities

- `v2-ashare-survivorship-correction`: explicitly qualified build exception, with
  incompleteness and evidence preserved rather than a general partial-data override.

## Impact

Fetch DTO/manifest, raw acquisition, bundle integrity/build, daily-update and
recommendation config/CLI/output, symmetric historical-engine entry guards,
direct historical evaluation guards, read-only UI disclosure, synthetic regression
tests and operator runbook. Production publication remains a
separate serial, reversible acceptance step. No production data or scheduler is
changed by implementing this feature.
