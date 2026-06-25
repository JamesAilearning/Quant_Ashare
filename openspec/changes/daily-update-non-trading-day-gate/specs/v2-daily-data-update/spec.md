## ADDED Requirements

### Requirement: The daily update SHALL no-op cleanly on a non-trading day

`run_daily_update` SHALL return exit 0 (success) on a default run whose date is NOT a
trading day (no explicit end-date requested), WITHOUT running any fetch/build/swap stage
— so a scheduled daily run does not run the full pipeline (or churn the bundle with a
no-data swap) on a closed day.

The gate SHALL be placed AFTER the dry-run preview (a dry-run still prints the plan) and
AFTER the Stage 0 startup crash-repair, so that an interrupted prior swap (a crash that
left the live provider missing with a `.bak`/`.new` pair) is ALWAYS completed — even on
a closed day — rather than leaving readers without a live bundle until the next trading
day. The gate SHALL NOT fire for an explicit end-date run: an operator-supplied
`--end-date` is a deliberate backfill / catch-up (e.g. recovering a missed Friday update
on Saturday) and SHALL run, never silently no-op.

The trading-day determination SHALL be offline and deterministic (a weekend check), so
the orchestrator hot path and the test suite take no network. A-share weekday holidays
are NOT skipped by this gate — they fall through to the normal run, whose fetch/freshness
gates already no-op gracefully on a day with no new bar — so a weekday holiday is
handled, never wrongly skipped.

#### Scenario: a weekend run is a clean no-op
- **WHEN** `run_daily_update` runs with a default run date on a Saturday or Sunday
- **THEN** it returns exit 0 and runs NO fetch/build/swap stage

#### Scenario: a crashed swap is still repaired on a non-trading day
- **WHEN** a prior swap crashed mid-rename (live provider missing, `.bak` + `.new` present) and `run_daily_update` runs on a Saturday
- **THEN** the Stage 0 repair completes the interrupted swap (live provider restored) BEFORE the gate no-ops, so readers are not stranded over the weekend

#### Scenario: an explicit end-date backfill overrides the gate
- **WHEN** `run_daily_update` runs on a Saturday with an explicit `--end-date`
- **THEN** the gate does not fire and the full pipeline runs (a deliberate catch-up)

#### Scenario: a trading-day run proceeds normally
- **WHEN** `run_daily_update` runs with a run date on a weekday
- **THEN** the gate passes and the full pipeline runs as before

#### Scenario: the dry-run preview precedes the gate
- **WHEN** `run_daily_update` runs with `--dry-run` on a non-trading day
- **THEN** the plan is still previewed and the gate does not pre-empt the preview
