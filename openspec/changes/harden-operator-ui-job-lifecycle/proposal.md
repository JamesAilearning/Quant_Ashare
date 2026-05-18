# Harden Operator UI Job Lifecycle

## Why

The Streamlit operator UI launches long-running jobs in subprocesses. Recent
review found several lifecycle reliability gaps: job IDs were not consistently
path-guarded, job status writes were duplicated across runner and manager, the
runner parsed YAML by string splitting, and stopped jobs could leave confusing
status artifacts.

## What Changes

- Reuse the same child-directory guard for job `stop()` and `status()` as job
  deletion already uses.
- Share job.json read/write helpers between manager and runner with atomic
  update behavior.
- Parse job config YAML structurally when discovering `output_dir`.
- Record stopped status from the runner when it receives a stop signal.
- Handle operator-facing validation errors in Config & Run without crashing the
  Streamlit page.
- Make Run History robust to nullable timestamps.

## Non-Goals

- Do not change pipeline, walk-forward, qlib, Tushare, training, or backtest
  semantics.
- Do not introduce a second official metric or job execution path.
- Do not delete result directories when deleting job records; saved provider
  deletion remains a separate explicit UI action.
