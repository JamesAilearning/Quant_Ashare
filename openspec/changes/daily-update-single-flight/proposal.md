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

- A process-exclusive **single-flight lock** (`src/data_pipeline/single_flight.py`): OS
  advisory locks (`fcntl.flock` / `msvcrt.locking`, non-blocking), one per mutable input
  (provider dir, tushare dump, registry), taken in canonical order so exactly one of two
  contending runs wins. The kernel releases each on process exit — even on crash — so there
  is no stale lock, no PID-liveness probing, and no reclaim race (a naive pidfile +
  stale-reclaim is inherently racy; two reclaimers can both proceed).
- The `daily_update` CLI (`scripts/daily_update.py`) acquires locks on the provider +
  tushare dump + registry around `run_daily_update`; a contention refusal returns the new
  `EXIT_ALREADY_RUNNING` (17), and a lock-setup failure (unwritable path) returns
  `EXIT_CONFIG` (2). `--dry-run` is exempt (it mutates nothing).
- A **runbook** (`docs/runbook_daily_update_scheduling.md`): Task Scheduler registration,
  the pre-close scheduling caveat, exit-code monitoring, the manual `daily_recommend`
  morning step, rollback, and the one-week supervised trial.

## Impact

- New behavior at the CLI boundary only. `run_daily_update` (the library) is unchanged and
  stays lock-free / unit-testable; `daily_recommend` is unaffected (not scheduled).
- Single-flight is per-provider, so the test suite (distinct tmp providers) never contends.
