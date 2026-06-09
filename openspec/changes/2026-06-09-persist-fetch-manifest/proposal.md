# Proposal: persist-fetch-manifest

## Why

P3-4a made the Tushare fetch continue past a transient hole and exit non-zero,
but the holes lived ONLY in memory + the exit code: once the process ended, the
record of WHICH units holed (and how far each endpoint covered) was gone. A
re-run re-fetches missing files (file-existence resume), but nothing knows
whether the previous run's holes were filled, lingering, or new — and no
downstream consumer can refuse a holey dump because there is no durable record
of the holes.

P3-4b persists each run's coverage + holes to `{output_dir}/fetch_manifest.json`
and merges runs so a re-fetched unit SELF-HEALS its hole. This is the durable
ledger P3-4c will gate on and P3-6 will drive incremental fetches from — but 4b
itself only records; it gates nothing and drives nothing.

## What Changes

- New `src/data/tushare/fetch_manifest.py`:
  - `FetchManifest` / `EndpointCoverage` dataclasses + the `fetch_manifest.json`
    schema (`schema_version`, `fetched_at`, per-endpoint `status` /
    `coverage_end_date` / `units_written` / `holes[]`, holes carrying the 4a
    `reason_class` / `attempts` / `last_error`).
  - `build_manifest(results, holes, end_date, *, now=None)` — THIS run's manifest
    from the fetcher's results + holes. `now` is injectable (value-injection,
    the same pattern as the Phase 2 staleness `recommend(..., now=...)`); the
    production default is the system clock (`datetime.now(tz=timezone.utc)`, the
    repo's established timestamp idiom).
  - `read_manifest(path)` — missing → `None` (fresh start, not an error); unknown
    `schema_version` / malformed → `FetchManifestError` (fail-loud).
  - `merge_manifest(prev, current)` — the self-heal merge (see below).
  - `write_manifest(path, manifest)` — ATOMIC (temp + `os.replace`).
  - `clear_manifest(path)` — full clear for a fresh rebuild.
- `scripts/data_pipeline/01_fetch_tushare.py`: on the completed-run path (not
  `--dry-run`), read the prior manifest, build this run's manifest from
  `results` + `fetcher.holes` + `config.end_date`, merge, and write atomically.

### The self-heal merge (the red line)

Per endpoint that ran this run, `current`'s holes ARE the post-run truth (a
full-scope re-run re-attempts every missing unit):

- a prev hole ABSENT from `current`'s holes self-healed → **removed**;
- a recurring hole is **kept** with its attempt count **accumulated**;
- `coverage_end_date` only **advances**.

An endpoint that did NOT run this run is **preserved verbatim** from `prev`.
Precision is the red line: a hole is removed ONLY when its exact `(endpoint,
unit)` succeeded this run — never wrongly removed (a silent partial), never left
lingering after it healed (a false alarm).

## Non-Goals

- No downstream gating — a builder / daily-list refusing a holey manifest is
  **P3-4c**. 4b only records.
- No incremental drive — a NARROWER-range incremental re-run (which would need
  the merge to gain per-unit scope awareness so it does not self-heal units it
  never re-attempted) is **P3-6**. 4b assumes full-scope runs.
- No change to the fetcher's continue-on-error, retry/backoff, or resume.
- The manifest is written only on the completed-run path; a hard abort leaves
  the prior manifest untouched (the run is incomplete; holes are still logged).
