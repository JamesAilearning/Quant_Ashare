## ADDED Requirements

### Requirement: The daily update SHALL refuse a concurrent run on the same provider

The `daily_update` CLI SHALL acquire a process-exclusive single-flight lock keyed to the
provider directory BEFORE any bundle mutation, so a scheduled firing and a manual run (or
a hung run and the next day's firing) targeting the same provider cannot overlap — the
swap is crash-atomic but NOT run-concurrent, and overlapping runs would race the
`provider` / `.bak` / `.new` triplet and could corrupt the bundle.

The lock SHALL be an OS advisory lock (`fcntl.flock` / `msvcrt.locking`), NOT a pidfile,
so the kernel releases it when the holder exits — including on a crash or kill — leaving
no stale lock to reclaim and no PID-reuse / corrupt-lock wedge. A second acquirer that
cannot take the lock SHALL fail fast with a distinct exit code (17, already-running) and
run NO stage. A `--dry-run` mutates nothing and SHALL be exempt.

The lock file SHALL be a sibling of the provider dir (not a child), since the swap renames
the provider dir wholesale; and single-flight SHALL be per-provider, so runs against
different providers never contend.

#### Scenario: a concurrent run on the same provider is refused
- **WHEN** the `daily_update` CLI starts while another run holds the lock for that provider
- **THEN** it exits 17 (already-running) and runs no fetch/build/swap stage

#### Scenario: the lock is released when the holder exits
- **WHEN** a run that held the lock exits (normally or by crash) and a later run starts
- **THEN** the later run acquires the lock and proceeds — no manual clearing is needed

#### Scenario: runs against different providers do not contend
- **WHEN** two `daily_update` runs target DIFFERENT provider dirs at the same time
- **THEN** each takes its own per-provider lock and runs

#### Scenario: a dry-run is exempt from the lock
- **WHEN** the `daily_update` CLI runs with `--dry-run` while the lock is held
- **THEN** it previews the plan and is not blocked by the lock
