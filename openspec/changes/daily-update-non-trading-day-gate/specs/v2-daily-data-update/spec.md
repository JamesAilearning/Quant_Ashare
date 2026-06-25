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

The no-op SHALL fire only when a real qlib bundle exists after the Stage 0 repair —
keyed on the calendar spine `calendars/day.txt` (the structural marker the codebase
treats as "this is a qlib provider"), NOT a bare path or a merely non-empty directory.
Its premise is "the bundle is already current, skip the redundant refresh", which holds
only if a bundle is present. On a fresh machine, after a first-ever build crashed leaving
only a `.new` that repair cleared, or when the provider path exists but is empty / a stray
file / a half-copied garbage layout (an operator `mkdir`, an antivirus or cloud-sync tool
that deleted a corrupted bundle's files but left the folder), no usable live bundle
exists; the gate SHALL NOT no-op there (that would report success with no readable bundle)
— it falls through to the normal pipeline so a bundle is bootstrapped from history, or the
run fails loud.

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

#### Scenario: a weekend run with no usable live bundle bootstraps instead of no-op'ing
- **WHEN** `run_daily_update` runs on a Saturday with no real qlib bundle — absent (fresh machine, or repair just cleared a stale `.new`) OR a present path that is an empty / stray-file / garbage directory (no `calendars/day.txt`)
- **THEN** the gate does NOT no-op; the full pipeline runs so a bundle is bootstrapped to a live swap (never a green exit with no bundle)

#### Scenario: an explicit end-date backfill overrides the gate
- **WHEN** `run_daily_update` runs on a Saturday with an explicit `--end-date`
- **THEN** the gate does not fire and the full pipeline runs (a deliberate catch-up)

#### Scenario: a trading-day run proceeds normally
- **WHEN** `run_daily_update` runs with a run date on a weekday
- **THEN** the gate passes and the full pipeline runs as before

#### Scenario: the dry-run preview precedes the gate
- **WHEN** `run_daily_update` runs with `--dry-run` on a non-trading day
- **THEN** the plan is still previewed and the gate does not pre-empt the preview
