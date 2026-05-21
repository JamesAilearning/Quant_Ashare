# Tasks: Pipeline Result Artifact Contract

## OpenSpec

- [x] Add result artifact requirements

## Implementation

- [x] Add pipeline result artifact writer
- [x] Wire artifact writer into Pipeline after report/chart generation
- [x] Persist predictions, standard logs, stage timings, and model artifact paths
- [x] Copy exact UI config into run directory when a UI job completes
- [x] Update Results page to prefer structured artifacts
- [x] Add Results run_id query-param entrypoint
- [x] Render interactive NAV and drawdown charts from nav.parquet
- [x] Surface malformed/unreadable artifact errors without recomputing metrics

## Tests

- [x] Add pipeline result artifact unit tests
- [x] Add artifact read issue unit tests
- [x] Update Results source regression tests
- [x] Run targeted tests, import smoke, ruff, OpenSpec validation, and repo logic/governance tests
