# Add Operator UI Job Progress

## Why

Long-running UI jobs, especially full-market Tushare provider ingestion, can
appear stuck because the Config & Run page only shows a running status and log
paths. Operators need a lightweight progress indicator while preserving the
existing CLI boundary and canonical artifact semantics.

## What Changes

- Add operator UI progress metadata derived from job status, logs, config, and
  existing artifacts.
- Show progress bars for recent UI-launched jobs.
- Keep progress display informational only; it must not recompute official
  metrics or influence runtime execution.

## Non-Goals

- Do not modify canonical training, backtest, attribution, or Tushare ingest
  semantics.
- Do not add a competing progress protocol inside core runtime modules.
- Do not parse or compute official metrics for progress display.
