# Design: Operator UI Job Progress

## Approach

Progress is derived at the UI job boundary, not inside canonical runtime code.
`JobManager.status()` and `JobManager.list_jobs()` attach a `progress` object
to each returned job snapshot. The progress object contains:

- `percent`: integer from 0 to 100;
- `label`: short operator-facing stage label;
- `detail`: optional artifact/log detail.

## Progress Sources

For all job modes, terminal status is authoritative. For running jobs, progress
is estimated from already-approved UI artifacts:

- job log files under `output/operator_ui/jobs/<job_id>/`;
- the generated config YAML;
- output directories and report files under `output/operator_ui/results/`.

Tushare provider jobs use staged files, qlib provider folders, manifest, and
validation artifacts as coarse milestones.

## Governance Notes

The UI progress layer is informational. It does not call `Pipeline.run()`,
`WalkForwardEngine.run()`, qlib data APIs, Tushare APIs, or metric functions.
It reads only files already produced by the existing CLI-compatible job
runner.
