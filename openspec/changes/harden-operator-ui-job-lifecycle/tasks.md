# Tasks: Harden Operator UI Job Lifecycle

## OpenSpec

- [x] Add lifecycle hardening requirements

## Implementation

- [x] Add shared job.json IO helpers for manager and runner
- [x] Guard job IDs in `JobManager.stop()` and `JobManager.status()`
- [x] Parse runner `output_dir` with YAML instead of string splitting
- [x] Record stopped status when runner receives stop signals
- [x] Handle UI config validation failures without crashing Config & Run
- [x] Make Run History timestamp display robust to `None`

## Tests

- [x] Add path traversal regression tests for stop/status
- [x] Add runner YAML parsing regression test
- [x] Add runner stop-signal status regression test
- [x] Add UI source regression tests for handled validation and nullable timestamps
- [x] Run targeted tests, import smoke, ruff, OpenSpec validation, and repo logic/governance tests
