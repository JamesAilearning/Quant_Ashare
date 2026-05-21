# Tasks: Pipeline Result Detail Dashboard

## OpenSpec

- [x] Add dashboard requirements for pipeline results

## Implementation

- [x] Refactor Results page into pipeline detail dashboard
- [x] Add readable metric formatting and missing-data states
- [x] Display generated charts by semantic section where available
- [x] Add detail tabs for holdings, trades, config, stage timings, logs, and raw JSON
- [x] Add pipeline result exports for metrics CSV, PDF summary, and full run bundle
- [x] Add re-run action that pre-fills Config & Run from exact config bytes
- [x] Add artifact-level holdings and trades filters with CSV exports
- [x] Add accessibility status role and keyboard shortcut guidance
- [x] Prevent failed pipeline jobs from binding stale run directories or masking failures with stale metadata
- [x] Preserve Tushare provider artifact rendering
- [x] Preserve walk-forward summary rendering

## Tests

- [x] Update Results page and Config & Run source regression tests
- [x] Add export helper behavior tests
- [x] Run targeted tests, import smoke, ruff, OpenSpec validation, and repo logic/governance tests
