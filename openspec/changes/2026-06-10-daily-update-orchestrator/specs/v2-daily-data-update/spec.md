# v2-daily-data-update Specification

## ADDED Requirements

### Requirement: A single entry point SHALL orchestrate the daily data update fail-loud

`scripts/daily_update.py` SHALL run the full update as ordered stages — fetch
(with `--refresh-current`) → active-stocks snapshot check → full rebuild into
`<provider>.new` (registry → bins → index membership → universe; bins BEFORE
the instruments writers because its staging-promote replaces the output dir) →
validation of the STAGED bundle → atomic swap. Every stage failure SHALL
short-circuit the remaining stages and exit with a code that identifies the
failing stage. Paths SHALL flow into every pipeline script as explicit CLI
argv (no environment-variable coupling). `--allow-holey-fetch` SHALL pass
through to the build gate only and SHALL NOT touch the recommend-side
override. `--dry-run` SHALL print each stage's argv and the bundle state and
SHALL execute and mutate nothing.

#### Scenario: stages run in order and the bundle is swapped
- **WHEN** every stage succeeds
- **THEN** the stages ran fetch → registry → bins → membership → universe →
  validate in that order, the staged bundle is promoted live, and the previous
  bundle is kept as the rollback backup

#### Scenario: a failing stage short-circuits the rest
- **WHEN** the fetch fails hard (or any rebuild stage fails)
- **THEN** no later stage runs and the exit code identifies the failing stage

#### Scenario: fetch holes stop the run unless overridden
- **WHEN** the fetch completes with holes (exit 3) and `--allow-holey-fetch`
  was not given
- **THEN** the run stops before rebuilding (the build gate would refuse the
  dump anyway); with the override it continues and the bundle is stamped
  built-from-holey-fetch by the build layer

#### Scenario: the snapshot stage proves the refresh landed
- **WHEN** after the fetch the embedded snapshot_date of active_stocks.parquet
  does not equal the run date (or the stamp is missing/unreadable)
- **THEN** the run refuses (exit 13) unless `--allow-holey-fetch` sanctioned
  partial data, in which case it warns and continues

#### Scenario: dry-run executes nothing
- **WHEN** `--dry-run` is given (even with a repairable crash state on disk)
- **THEN** the plan and the bundle state are printed, no stage runs, and
  nothing on disk changes

### Requirement: The bundle swap SHALL be atomic, validated-only, and crash-repairable

The live provider bundle SHALL only ever be replaced by a two-stage rename
swap — stage 1 `provider → provider.bak`, stage 2 `provider.new → provider` —
executed ONLY after the staged bundle passed validation. A failed validation
SHALL never reach the swap and SHALL leave the live bundle untouched. The
backup SHALL be kept as instant rollback until the next swap. At startup the
orchestrator SHALL detect and resolve every reachable crash state: a swap
interrupted between the two renames (backup + staged present, live missing —
stage 1 having run proves validation passed) SHALL be COMPLETED; a
backup-only state SHALL be RESTORED; a stale staged bundle (which cannot be
proven validated) SHALL be REMOVED loudly and never auto-promoted.

#### Scenario: crash after build, before swap
- **WHEN** a prior run died leaving the live bundle plus a stale `.new`
- **THEN** the next startup removes the stale `.new` loudly and the live
  bundle is byte-identical untouched

#### Scenario: crash between the two renames
- **WHEN** a prior run died after stage 1 (backup exists, live missing,
  staged present)
- **THEN** the next startup completes stage 2 — the validated staged bundle
  goes live and the backup is preserved

#### Scenario: crash after the swap completed
- **WHEN** both renames finished before the crash
- **THEN** the next startup recognizes the healthy post-swap state and touches
  nothing

#### Scenario: validation failure never swaps
- **WHEN** validation of the staged bundle FAILS (validator exit >= 2 — a check
  did not pass)
- **THEN** the swap never runs, the live bundle is untouched, and the staged
  bundle is left in place for inspection

#### Scenario: warnings-only validation is a pass
- **WHEN** the validator returns its warnings-only code (exit 1 — every check
  passed; routine when reference cases are present)
- **THEN** the swap proceeds, with the warnings surfaced loudly in the log —
  a valid bundle is never wedged behind a benign warning
