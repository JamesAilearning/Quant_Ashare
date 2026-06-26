# Tasks: Daily-update single-flight lock

## OpenSpec (propose stage)

- [x] Draft proposal.md / tasks.md
- [x] Draft `specs/v2-daily-data-update/spec.md` delta (ADDED concurrent-run refusal)
- [x] `openspec validate daily-update-single-flight --strict` green

## Implementation

- [x] `src/data_pipeline/single_flight.py`: `single_flight(provider_dir)` context manager
      (`O_EXCL` pidfile, sibling path) + three-valued `_pid_is_alive` (stale reclaim /
      fail-closed on unknown) + `AlreadyRunningError`
- [x] `src/data_pipeline/daily_update.py`: `EXIT_ALREADY_RUNNING = 17` + exit-code table
- [x] `scripts/daily_update.py`: acquire the lock around `run_daily_update`; `--dry-run`
      exempt; a refusal returns `EXIT_ALREADY_RUNNING`
- [x] `docs/runbook_daily_update_scheduling.md`: Task Scheduler registration + pre-close
      scheduling caveat + exit-code monitoring + manual `daily_recommend` morning step +
      rollback + one-week supervised trial
- [x] tests `tests/data_pipeline/test_single_flight.py`: acquire/release, live-holder
      refused, stale reclaimed, unknown/garbage held (fail-closed), body-raises releases,
      CLI -> `EXIT_ALREADY_RUNNING`, dry-run exempt

## Verify

- [x] `pytest tests/data_pipeline/ -q` green; ruff + mypy --strict clean
- [x] `openspec validate daily-update-single-flight --strict` green
