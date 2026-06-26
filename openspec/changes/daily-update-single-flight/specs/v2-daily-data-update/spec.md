## ADDED Requirements

### Requirement: The daily update SHALL refuse a concurrent run on the same provider

The `daily_update` CLI SHALL acquire a process-exclusive single-flight lock keyed to the
provider directory BEFORE any bundle mutation, so a scheduled firing and a manual run (or
a hung run and the next day's firing) targeting the same provider cannot overlap — the
swap is crash-atomic but NOT run-concurrent, and overlapping runs would race the
`provider` / `.bak` / `.new` triplet and could corrupt the bundle.

A second acquirer SHALL fail fast with a distinct exit code (17, already-running) and run
NO stage. A stale lock whose holder PID is CONFIRMED dead SHALL be reclaimed, so a crashed
prior run does not wedge the schedule; an UNKNOWN liveness (the probe itself failed) or an
unreadable lock SHALL be treated as held (fail-closed) — a lock is never stolen from a run
that cannot be proven gone. A `--dry-run` mutates nothing and SHALL be exempt.

The lock SHALL be a sibling of the provider dir (not a child), since the swap renames the
provider dir wholesale; and single-flight SHALL be per-provider, so runs against different
providers never contend.

#### Scenario: a concurrent run on the same provider is refused
- **WHEN** the `daily_update` CLI starts while a live run holds the lock for that provider
- **THEN** it exits 17 (already-running) and runs no fetch/build/swap stage

#### Scenario: a stale lock from a crashed run is reclaimed
- **WHEN** the lock records a holder PID that is confirmed dead and the CLI starts
- **THEN** the stale lock is reclaimed and the run proceeds normally

#### Scenario: an unprovable holder is treated as held
- **WHEN** the lock's holder liveness cannot be determined (probe failure) or the lock is unreadable
- **THEN** the CLI refuses (exit 17) rather than stealing the lock

#### Scenario: a dry-run is exempt from the lock
- **WHEN** the `daily_update` CLI runs with `--dry-run` while the lock is held
- **THEN** it previews the plan and is not blocked by the lock
