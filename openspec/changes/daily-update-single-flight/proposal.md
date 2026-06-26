# Daily-update single-flight lock (concurrent-run guard)

## Why

阶段5 scheduling: with `daily_update` registered in the Windows Task Scheduler (the PR-P
runbook), two runs can target the same provider concurrently — a manual run started while
the scheduled one is going, or a run that hung past midnight when the next day's firing
starts. The swap (`bundle_swap.swap`) is crash-atomic but explicitly NOT run-concurrent:
between its two renames the live path briefly does not exist, and two runs would fight
over the `provider` / `.bak` / `.new` triplet (double rename, `rmtree` of a `.bak` under
the other's feet) and could corrupt the bundle. The PR-O calendar-gate comment already
designated the scheduler as the mutual-exclusion owner; this makes that guard explicit at
the CLI entry rather than an implicit scheduler assumption.

## What Changes

- A process-exclusive **single-flight lock** (`src/data_pipeline/single_flight.py`): an
  `O_EXCL` pidfile keyed to the provider dir, with a three-valued PID-liveness probe
  (POSIX signal-0 / Windows `tasklist`, mirroring `job_manager`) so a stale lock from a
  crashed run is reclaimed while a live or unprovable holder is never stolen.
- The `daily_update` CLI (`scripts/daily_update.py`) acquires the lock around
  `run_daily_update`; a refusal returns the new `EXIT_ALREADY_RUNNING` (17). `--dry-run`
  is exempt (it mutates nothing).
- A **runbook** (`docs/runbook_daily_update_scheduling.md`): Task Scheduler registration,
  the pre-close scheduling caveat, exit-code monitoring, the manual `daily_recommend`
  morning step, rollback, and the one-week supervised trial.

## Impact

- New behavior at the CLI boundary only. `run_daily_update` (the library) is unchanged and
  stays lock-free / unit-testable; `daily_recommend` is unaffected (not scheduled).
- Single-flight is per-provider, so the test suite (distinct tmp providers) never contends.
