## Why

The merged Today Workbench must be a conservative, read-only operating
summary. It can currently describe an artifact with a missing or unsupported
schema version as a current signal, and its job summary invokes the normal
job-list reconciliation path, which can write status changes merely because an
operator opens the page.

## What Changes

- Require the workbench to accept a daily-recommendation artifact only when
  its schema version exactly matches the supported producer version.
- Add a non-mutating unified job-list reader and use it exclusively for the
  workbench's operational summary.
- Preserve the Jobs page's existing zombie reconciliation behavior; this
  change narrows only the workbench's read boundary.

## Capabilities

### New Capabilities

- `v2-today-workbench-read-boundaries`: fail-closed artifact-version handling
  and non-mutating job reads for the Today Workbench.

### Modified Capabilities

- None.

## Impact

- Updates the workbench's pure classification helper, job-list IO helpers,
  and the workbench page import/call site.
- Adds focused logic and source-boundary regression tests.
- Does not change canonical qlib runtime behavior, training, backtesting,
  data selection, daily recommendation generation, or official metrics.
