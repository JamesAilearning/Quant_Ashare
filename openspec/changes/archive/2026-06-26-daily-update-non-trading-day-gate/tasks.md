# Tasks: Daily-update trading-calendar gate

## OpenSpec (propose stage)

- [x] Draft proposal.md / tasks.md
- [x] Draft `specs/v2-daily-data-update/spec.md` delta (ADDED non-trading-day no-op)
- [x] `openspec validate daily-update-non-trading-day-gate --strict` green

## Implementation

- [x] `src/data_pipeline/daily_update.py`: `_run_date_is_non_trading(run_date)` (pure
      weekend check) + the gate in `run_daily_update` (after the dry-run preview, before
      Stage 0), returning `EXIT_OK` with a log on a non-trading day
- [x] tests: pure `_run_date_is_non_trading` (Mon-Fri trade / Sat-Sun not) + weekend run
      is a clean no-op (no stage) + dry-run preview precedes the gate
- [x] `_config` test helper made `now`-overridable (weekend dates)

## Follow-up (out of scope, documented)

- [ ] holiday-awareness via SSE `trade_cal` (a network call) — wired by the PR-P
      scheduler entry, not the orchestrator default

## Verify

- [x] `pytest tests/data_pipeline/test_daily_update.py -q` green (19 passed)
- [x] fast suite green (2674 passed / 28 skipped); ruff + mypy --strict clean
