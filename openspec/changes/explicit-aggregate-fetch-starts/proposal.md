## Why

Production daily updates request price history from 2018, while existing
namechange, suspension and index aggregate files declare earlier coverage.
The new overwrite guard correctly refuses to truncate these files, but the
single shared request start cannot preserve them without widening price fetches.

## What Changes

- Add explicit optional aggregate start-date configuration to fetch and daily
  update entry points; omitted options retain the existing shared-start behavior.
- Use the selected endpoint start consistently for the aggregate request,
  pre-write range guard and this run's persisted manifest coverage.
- Preserve ticker-year and benchmark ranges, end-date checks, stable hole units,
  unknown-provenance refusal, existing index refresh cadence and dry-run safety.
- Add synthetic CLI/plan/manifest integration regressions using the production
  multi-start shape, including prior holes and invalid options.
- Document migration of scheduled invocations without deleting metadata,
  overriding completeness gates or automatically changing deployments.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `v2-daily-data-update`: explicitly forward aggregate history starts independently
  of price/benchmark starts, validate before operational writes.
- `v2-ashare-survivorship-correction`: endpoint-effective aggregate request and
  manifest bounds with explicit configuration and unchanged overwrite protection.

## Impact

Fetcher configuration, fetch CLI, daily-update configuration/CLI, manifest
producer, synthetic tests and operator documentation. No model, scoring, qlib
metric, UI, cache identity or data-layout changes in this PR. Production deployment
and data migration follow separately only after validation and review.
