# Design: Operator UI Job Lifecycle Hardening

## Boundary

This change is UI lifecycle plumbing only. It does not modify canonical runtime
execution, official metrics, qlib initialization, or Tushare provider bundle
semantics. UI jobs still launch the existing CLI entrypoints through
`web.operator_ui.job_runner`.

## Path Safety

All job-id based access to `output/operator_ui/jobs/<job_id>` goes through the
same child-directory guard. The guard rejects empty IDs, path separators, and
resolved paths outside the UI job root before reading or writing job state.

## Job State IO

`job_manager` and `job_runner` use one shared job.json helper. Writes are
read-merge-replace updates protected by a lightweight lock file so concurrent
manager/runner updates do not silently overwrite each other.

## Runner Config Parsing

The runner reads `config.yaml` with YAML parsing when discovering `output_dir`.
If parsing fails or the field is absent, the job status remains success/failed
from the actual CLI exit and the UI simply has no discovered `run_dir`.

## Stop Handling

`JobManager.stop()` sends the platform-appropriate termination signal and
records `stopped` only after the termination command succeeds. The runner also
installs a SIGTERM handler that writes `stopped` when the runner itself receives
a stop signal.
