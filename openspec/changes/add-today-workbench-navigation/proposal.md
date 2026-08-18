## Why

The operator console has the data, production, recommendation, and job
surfaces needed for daily work, but they are organised by implementation
module. Operators must navigate several pages to learn whether data is
healthy, which model is current, whether a signal is trustworthy, or whether
an operation failed.

## What Changes

- Add a read-only Today Workbench that summarises existing data-update,
  incumbent-identity, recommendation-artifact, and job artifacts.
- Reorganise the navigation around daily decisions, research and validation,
  and production governance while retaining all existing pages.
- Give a successful daily-signal run a direct, date-specific route to its
  already-published recommendation artifact.
- Refuse to describe the newest recommendation file as a current signal when
  its artifact shape, date, or provenance cannot be verified.

## Capabilities

### New Capabilities

- `v2-today-workbench`: a read-only operator landing page and task-oriented
  navigation that aggregates existing, governed artifacts without creating a
  new trading or metrics path.

### Modified Capabilities

- `v2-run-center-page`: a successful daily-signal run exposes a direct route
  to the verified recommendation artifact it published.

## Impact

- Adds `web/operator_ui/pages/today_workbench.py` plus pure UI helpers and
  targeted logic/governance tests.
- Updates `web/operator_ui/app.py` navigation and the successful path in
  `web/operator_ui/pages/run_center.py`.
- Does not change canonical qlib runtime behavior, training, backtesting,
  factor mining, automatic trading, or any official metric.
