# Design: UI Provider Picker

## Boundary

The picker is UI-only. It reads existing artifacts produced by UI-managed
Tushare jobs and feeds the selected absolute `qlib_provider` path into the
existing `provider_uri` input. Training still flows through the same config,
`JobManager`, CLI, qlib initialization, and canonical backtest path.

## Catalog Source

The catalog scans only:

`output/operator_ui/results/*/qlib_provider`

Each candidate is included only when the `qlib_provider` directory exists. The
catalog then reuses the provider metadata inspection helper to read validation,
manifest, calendar, and instrument files. Broken or incomplete candidates remain
visible with warnings/errors rather than being silently rewritten.

## UI Behavior

The Config & Run page shows a selectbox above `provider_uri`. The first option
is manual entry. Other options are reusable providers ordered by job id in
reverse lexical order, which matches the timestamp-based job id convention.
When an operator selects an existing provider, the UI writes its path into the
existing `training_provider_uri` session-state key before rendering the text
input. The text input remains visible and editable.

## Delete Behavior

Saved provider deletion removes only a UI-managed result directory selected by
job id under `output/operator_ui/results`. It refuses arbitrary paths and
requires the selected result directory to contain `qlib_provider`.

Recent job deletion removes only a UI job directory selected by job id under
`output/operator_ui/jobs`. It refuses arbitrary paths and refuses jobs whose
status is still `running`; operators must stop the job before deleting the job
record and logs.
