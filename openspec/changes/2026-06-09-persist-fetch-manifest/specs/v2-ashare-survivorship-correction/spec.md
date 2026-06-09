# v2-ashare-survivorship-correction Specification (delta)

## ADDED Requirements

### Requirement: Fetch runs SHALL persist coverage + holes to a manifest and self-heal precisely

The Tushare fetch SHALL persist each run's per-endpoint coverage and holes to
`{output_dir}/fetch_manifest.json` — a document carrying `schema_version`,
`fetched_at`, and, per endpoint, `status` / `coverage_start_date` /
`coverage_end_date` / `units_written` / `holes[]` (each hole keeping the P3-4a
`reason_class` / `attempts` / `last_error`). The manifest SHALL be written
ATOMICALLY (temp file + rename) so a crash mid-write never exposes a half-written
manifest — the prior file stays intact until the complete new one is swapped in.
Reading SHALL treat a MISSING manifest as a fresh start (not an error) and SHALL
fail loud on an unknown `schema_version`, a MISSING required field (e.g. the
`endpoints` member or any per-endpoint / per-hole key), a non-object JSON document
(e.g. `[]`), a non-UTF-8 / corrupt-encoding file, or malformed JSON, rather than
parsing an unrecognized / partial shape (which the next merge could treat as "no
prior holes" and erase recorded ones). Every manifest read / merge / write
failure (including a refused narrower-scope merge) SHALL surface through the CLI
as a clean non-zero exit, not an escaping traceback.

Each run SHALL be merged onto the prior manifest: for an endpoint that ran this
run, a hole whose exact `(endpoint, unit)` was re-attempted-and-succeeded SHALL
be REMOVED (self-healed); a unit that still failed SHALL keep its hole with its
attempt count ACCUMULATED across runs; and an endpoint that did NOT run this run
SHALL be preserved untouched. Because the merge matches holes by exact
`(endpoint, unit)`, each hole's `unit` SHALL be STABLE across runs: in particular
an `index_weight` hole SHALL identify the whole index (`index={code}`), NOT the
first-failing year, which varies run-to-run and would make a re-failed index look
like a different unit so the merge would drop the prior un-healed hole. Coverage SHALL reflect what was ACTUALLY fetched,
not what was requested: a run that wrote nothing for an endpoint (every file
skipped by resume — e.g. a wider run that skips a prior narrow aggregate file
like `namechange` / `suspend_d` / `index_weight`) SHALL NOT advance that
endpoint's coverage to its requested range; only a run that wrote data advances
coverage (to the widest range seen). When such a skipped endpoint has NO prior
coverage to keep — the FIRST manifest built over a pre-existing dump — its
coverage SHALL be recorded EMPTY (not the requested range), so a gate cannot
mistake a stale narrow dump for the requested range. The merge SHALL NOT remove a hole that did
not self-heal (that would be a silent partial) and SHALL NOT retain a hole that
did self-heal (that would be a false alarm). A full `clear` SHALL be available
for a fresh rebuild. The manifest SHALL be written on the completed-run path
(with or without holes) and SHALL be skipped under `--dry-run`. On ANY HARD abort
(a non-retryable error) the completed-run manifest update does not run, yet the
aborted run may have left PARTIAL output (files written before the abort, with or
without a recorded hole — e.g. `stock_basic` writes `active_stocks` then aborts on
the delisted call). The manifest SHALL therefore be INVALIDATED on any hard abort,
so a stale "complete" manifest never covers a possibly-partial dir; a re-run
rebuilds it. Self-heal assumes
full-scope runs: a NARROWER-scope re-run of a date-scoped endpoint that still has
UNRESOLVED holes (one whose `[coverage_start_date, coverage_end_date]` no longer
covers the recorded coverage) does NOT re-attempt every prior hole, so the merge
SHALL REFUSE it (fail-loud) rather than silently drop the out-of-range holes
(`stock_basic` is exempt — it re-fetches the whole universe regardless of
date). This capability SHALL only record — it SHALL NOT gate any consumer (P3-4c)
and SHALL NOT itself drive narrower incremental fetches (P3-6).

#### Scenario: a run writes the manifest atomically with an injectable timestamp
- **WHEN** a fetch run completes and builds its manifest (with an injected
  timestamp for determinism)
- **THEN** the manifest records each endpoint's status / coverage_end_date /
  units_written / holes and is written via temp-file + rename
- **AND** a write whose rename fails leaves the PRIOR manifest intact and valid
  (no half-written file)

#### Scenario: a missing manifest is fresh and a malformed one fails loud
- **WHEN** the manifest file does not exist
- **THEN** reading returns a fresh/empty result, not an error
- **AND** WHEN the manifest carries an unknown or missing `schema_version`, is
  missing a required field (the `endpoints` member or any per-endpoint / per-hole
  key), or is malformed JSON, reading raises rather than silently parsing a
  partial shape

#### Scenario: a self-healed hole is removed and an un-healed hole is kept
- **WHEN** the prior manifest recorded a hole for `(endpoint, unit)` and this run
  re-ran that endpoint
- **THEN** if the unit succeeded this run its hole is removed (self-healed)
- **AND** if the unit still failed its hole is kept with its attempt count
  accumulated, and coverage advances (only when data was actually written)

#### Scenario: merge red line — an endpoint that did not run keeps its holes (no wrong-removal)
- **WHEN** the prior manifest has holes in endpoint A and endpoint B, and this
  run ran ONLY endpoint A
- **THEN** endpoint A's holes are re-resolved from this run, while endpoint B's
  holes are preserved untouched — a hole that did not self-heal is never silently
  removed (no silent partial)

#### Scenario: merge red line — a healed hole does not linger (no false alarm)
- **WHEN** the prior manifest has two holes in one endpoint and this run heals
  one but the other still fails
- **THEN** the healed unit's hole is dropped while the still-failing unit's hole
  is kept — a healed hole never lingers, and a still-failing one is never lost

#### Scenario: merge red line — a narrower-scope re-run is refused (no scope-drop)
- **WHEN** the prior manifest covers a wider date range for a date-scoped endpoint
  that still has UNRESOLVED holes, and this run re-fetches that endpoint over a
  NARROWER range that no longer covers the recorded coverage
- **THEN** the merge raises rather than treating the never-re-attempted
  out-of-range holes as self-healed
- **AND** a same-or-wider range merges normally, a hole-free narrower run is
  allowed (no holes at risk), and `stock_basic` (date-agnostic) is not refused

#### Scenario: coverage reflects what was fetched, not what was requested
- **WHEN** a wider run SKIPS a prior narrow aggregate file (`namechange` /
  `suspend_d` / `index_weight`) because it already exists, writing nothing
- **THEN** the endpoint's coverage stays at the actually-fetched narrow range,
  not the wider requested one — so a downstream gate cannot mistake the skipped
  wider request for fetched data
- **AND** a run that actually wrote data advances coverage to the widest range

#### Scenario: clear resets the manifest for a fresh rebuild
- **WHEN** the manifest is cleared
- **THEN** it is removed and the next read reports a fresh start
