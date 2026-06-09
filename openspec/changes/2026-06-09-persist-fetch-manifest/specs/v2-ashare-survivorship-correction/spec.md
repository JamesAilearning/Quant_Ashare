# v2-ashare-survivorship-correction Specification (delta)

## ADDED Requirements

### Requirement: Fetch runs SHALL persist coverage + holes to a manifest and self-heal precisely

The Tushare fetch SHALL persist each run's per-endpoint coverage and holes to
`{output_dir}/fetch_manifest.json` — a document carrying `schema_version`,
`fetched_at`, and, per endpoint, `status` / `coverage_end_date` / `units_written`
/ `holes[]` (each hole keeping the P3-4a `reason_class` / `attempts` /
`last_error`). The manifest SHALL be written ATOMICALLY (temp file + rename) so a
crash mid-write never exposes a half-written manifest — the prior file stays
intact until the complete new one is swapped in. Reading SHALL treat a MISSING
manifest as a fresh start (not an error) and SHALL fail loud on an unknown
`schema_version` (or malformed JSON) rather than parsing an unrecognized shape.

Each run SHALL be merged onto the prior manifest: for an endpoint that ran this
run, a hole whose exact `(endpoint, unit)` was re-attempted-and-succeeded SHALL
be REMOVED (self-healed); a unit that still failed SHALL keep its hole with its
attempt count ACCUMULATED across runs; `coverage_end_date` SHALL only advance;
and an endpoint that did NOT run this run SHALL be preserved untouched. The merge
SHALL NOT remove a hole that did not self-heal (that would be a silent partial)
and SHALL NOT retain a hole that did self-heal (that would be a false alarm). A
full `clear` SHALL be available for a fresh rebuild. The manifest SHALL be
written on the completed-run path (with or without holes) and SHALL be skipped
under `--dry-run`. This capability SHALL only record — it SHALL NOT gate any
consumer (P3-4c) and SHALL NOT drive incremental fetches (P3-6); the merge
assumes full-scope runs.

#### Scenario: a run writes the manifest atomically with an injectable timestamp
- **WHEN** a fetch run completes and builds its manifest (with an injected
  timestamp for determinism)
- **THEN** the manifest records each endpoint's status / coverage_end_date /
  units_written / holes and is written via temp-file + rename
- **AND** a write whose rename fails leaves the PRIOR manifest intact and valid
  (no half-written file)

#### Scenario: a missing manifest is fresh and an unknown schema fails loud
- **WHEN** the manifest file does not exist
- **THEN** reading returns a fresh/empty result, not an error
- **AND** WHEN the manifest carries an unknown or missing `schema_version` (or is
  malformed), reading raises rather than silently parsing it

#### Scenario: a self-healed hole is removed and an un-healed hole is kept
- **WHEN** the prior manifest recorded a hole for `(endpoint, unit)` and this run
  re-ran that endpoint
- **THEN** if the unit succeeded this run its hole is removed (self-healed)
- **AND** if the unit still failed its hole is kept with its attempt count
  accumulated, and `coverage_end_date` only advances

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

#### Scenario: clear resets the manifest for a fresh rebuild
- **WHEN** the manifest is cleared
- **THEN** it is removed and the next read reports a fresh start
