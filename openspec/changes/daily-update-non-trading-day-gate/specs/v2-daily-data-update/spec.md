## ADDED Requirements

### Requirement: The daily update SHALL no-op cleanly on a non-trading day

When the run date is NOT a trading day, `run_daily_update` SHALL return exit 0 (success)
WITHOUT running any stage, fetching, or touching the bundle — so a scheduled daily run
does not run the full fetch/build/swap pipeline (or churn the bundle with a no-data
swap) on a closed day. The gate SHALL be placed AFTER the dry-run preview (a dry-run
still prints the plan) and BEFORE any data touch.

The trading-day determination SHALL be offline and deterministic (a weekend check), so
the orchestrator hot path and the test suite take no network. A-share weekday holidays
are NOT skipped by this gate — they fall through to the normal run, whose fetch/freshness
gates already no-op gracefully on a day with no new bar — so a weekday holiday is
handled, never wrongly skipped.

#### Scenario: a weekend run is a clean no-op
- **WHEN** `run_daily_update` runs with a run date on a Saturday or Sunday
- **THEN** it returns exit 0 and runs NO stage (no fetch, build, or swap)

#### Scenario: a trading-day run proceeds normally
- **WHEN** `run_daily_update` runs with a run date on a weekday
- **THEN** the gate passes and the full pipeline runs as before

#### Scenario: the dry-run preview precedes the gate
- **WHEN** `run_daily_update` runs with `--dry-run` on a non-trading day
- **THEN** the plan is still previewed and the gate does not pre-empt the preview
