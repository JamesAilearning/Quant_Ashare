# Add UI Provider Picker

## Why

Operators can already reuse a previously generated Tushare qlib provider by
copying `output/operator_ui/results/<job_id>/qlib_provider` into `provider_uri`.
That works, but it is easy to paste the wrong parent directory or forget which
Tushare job covers the desired date range.

The UI should make reuse explicit and low-friction by listing previously
UI-managed provider bundles and filling `provider_uri` from the selected bundle.

## What Changes

- Add a read-only provider catalog helper that scans UI-managed Tushare results
  under `output/operator_ui/results/*/qlib_provider`.
- Display reusable providers in Config & Run with coverage, health, and universe
  metadata from the existing provider artifact files.
- Selecting a provider populates the same `provider_uri` field used by existing
  validation and job launch logic.
- Add explicit delete controls for non-running UI job records and UI-managed
  saved provider data.

## Non-Goals

- Do not scan arbitrary machine-local directories.
- Do not initialize qlib, call Tushare, or compute official metrics.
- Do not replace manual `provider_uri`; manual paths remain available.
- Do not delete running jobs; operators must stop them first.
