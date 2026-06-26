## ADDED Requirements

### Requirement: The daily update SHALL refuse a concurrent run sharing any mutable input

The `daily_update` CLI SHALL acquire a process-exclusive single-flight lock on EVERY
mutable input a run touches — the provider dir AND the shared raw inputs (the tushare
dump, the delisted registry) — BEFORE any mutation, so two runs that share ANY of them
cannot overlap. The swap is crash-atomic but NOT run-concurrent (overlapping runs would
race the `provider` / `.bak` / `.new` triplet), and the fetch / registry stages write
fixed-name temp files under the shared raw paths (overlapping runs would clobber them even
with different providers).

Each lock SHALL be an OS advisory lock (`fcntl.flock` / `msvcrt.locking`), NOT a pidfile,
so the kernel releases it when the holder exits — including on a crash or kill — leaving
no stale lock to reclaim and no PID-reuse / corrupt-lock wedge. The locks SHALL be taken
in a canonical order so exactly one of two contending runs wins (the other refuses
cleanly; the non-blocking locks cannot deadlock). A run that cannot take every lock SHALL
fail fast with a distinct exit code (17, already-running), release any lock it took, and
run NO stage. A `--dry-run` mutates nothing and SHALL be exempt.

Each lock file SHALL be a sibling of its resource (not a child), since the swap renames
the provider dir wholesale; and runs that share NO mutable input SHALL NOT contend.

#### Scenario: a run sharing a mutable input with a live run is refused
- **WHEN** the `daily_update` CLI starts while another run holds a lock for any shared input (same provider, tushare dump, or registry)
- **THEN** it exits 17 (already-running) and runs no fetch/build/swap stage

#### Scenario: distinct providers sharing a raw input are serialized
- **WHEN** two runs use DIFFERENT `--provider-dir` but the SAME `--tushare-dir` (or `--delisted-registry`)
- **THEN** the second is refused — they would otherwise clobber the shared raw temp files

#### Scenario: the lock is released when the holder exits
- **WHEN** a run that held the locks exits (normally or by crash) and a later run starts
- **THEN** the later run acquires the locks and proceeds — no manual clearing is needed

#### Scenario: runs that share no mutable input do not contend
- **WHEN** two `daily_update` runs target fully disjoint provider / tushare / registry paths
- **THEN** each takes its own locks and runs

#### Scenario: a dry-run is exempt from the lock
- **WHEN** the `daily_update` CLI runs with `--dry-run` while a lock is held
- **THEN** it previews the plan and is not blocked
