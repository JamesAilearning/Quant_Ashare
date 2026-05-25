# Tasks: UI Provider Picker

## OpenSpec

- [x] Add provider picker requirement

## Implementation

- [x] Add provider catalog helper for UI-managed Tushare provider bundles
- [x] Render provider picker above `provider_uri`
- [x] Populate the existing provider URI state from selected provider
- [x] Keep manual provider URI entry available
- [x] Add safe delete action for UI-managed saved provider data
- [x] Add safe delete action for non-running recent UI jobs
- [x] Display Tushare provider validation/manifest artifacts in Results
- [x] Use provider trading calendars for pipeline and walk-forward date selectors

## Tests

- [x] Add provider catalog unit tests
- [x] Add provider catalog delete tests
- [x] Add job manager delete tests
- [x] Add Config & Run source regression for picker wiring
- [x] Add Config & Run source regression for trading-day selectors
- [x] Add Results source regression for Tushare provider artifact display
- [x] Run targeted tests, import smoke, ruff, OpenSpec validation, and repo logic/governance tests
