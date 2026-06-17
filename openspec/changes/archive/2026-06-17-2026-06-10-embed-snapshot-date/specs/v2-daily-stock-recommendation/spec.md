# v2-daily-stock-recommendation Specification (delta)

## MODIFIED Requirements

### Requirement: Daily recommendation SHALL fail loud when the current-ST source is missing, stale, or malformed

Because excluding ST requires the current name snapshot, the path SHALL treat
that snapshot as REQUIRED and SHALL raise an explicit error and emit no list
(rather than silently producing a list that could include ST names) when any of
the following holds:

- `name_source_parquet` is unset or the file is absent;
- the snapshot is STALE — staleness is judged by the snapshot's EMBEDDED
  `snapshot_date` column (stamped by the fetcher at fetch time), NOT by file
  mtime, which sync / copy tools rewrite so a stale snapshot can look fresh; a
  snapshot whose embedded date lags the as-of date by more than
  `st_snapshot_max_age_days` is refused;
- the snapshot lacks the embedded `snapshot_date` column (written before the
  stamp existed) — it fails loud with a re-fetch instruction, never a silent
  mtime fallback;
- the snapshot is unreadable, is missing the required `ts_code` or `name`
  column, or is empty;
- the embedded snapshot_date is malformed (empty / all-null, multiple distinct
  values, or non-YYYYMMDD).

A snapshot whose embedded date is newer than the as-of date SHALL NOT be treated
as stale.

#### Scenario: a missing current-ST source is rejected
- **WHEN** `recommend` is invoked with no `name_source_parquet` (or a path
  that does not exist)
- **THEN** an explicit error is raised and no list is produced

#### Scenario: a stale snapshot is rejected by its embedded date, not mtime
- **WHEN** the snapshot's embedded snapshot_date lags the as-of date beyond
  `st_snapshot_max_age_days`, however fresh the file's mtime is
- **THEN** `recommend` refuses (DailyRecommendationError) and no list is produced

#### Scenario: an old-format snapshot without the embedded column fails loud
- **WHEN** the snapshot has no embedded snapshot_date column
- **THEN** `recommend` refuses with a re-fetch instruction rather than falling
  back to mtime

#### Scenario: a malformed current-ST snapshot is rejected
- **WHEN** the snapshot is present and fresh but is unreadable, is missing
  the `ts_code` or `name` column, or has zero rows
- **THEN** an explicit error is raised and no list is produced
- **AND** the path does NOT fall back to an empty name map that would
  silently disable ST filtering

#### Scenario: conflicting embedded dates fail loud
- **WHEN** the snapshot carries more than one distinct snapshot_date value
- **THEN** `recommend` refuses (corrupt / hand-merged file)

#### Scenario: a fresh embedded date passes
- **WHEN** the embedded snapshot_date is within tolerance of the as-of date
- **THEN** the guard passes and returns the snapshot date

## ADDED Requirements

### Requirement: Recommendation SHALL refuse an ST snapshot inconsistent with the bundle

`recommend` SHALL check that the ST snapshot and the price bundle come from the
same update cycle: an embedded `snapshot_date` lagging the bundle calendar's
last trading day by more than `bundle_max_age_days` SHALL raise
`DailyRecommendationError` — the ST/name view would predate the prices being
ranked (e.g. the bundle was rebuilt but stock_basic was never re-fetched). A
snapshot NEWER than the bundle tail SHALL pass (snapshots refresh more often
than bundles).

#### Scenario: a snapshot lagging the bundle tail refuses
- **WHEN** the embedded snapshot_date lags the bundle's last trading day by more
  than `bundle_max_age_days`
- **THEN** `recommend` raises rather than ranking on a mismatched pair

#### Scenario: a same-cycle snapshot passes
- **WHEN** the embedded snapshot_date is within `bundle_max_age_days` of the
  bundle tail (or newer than it)
- **THEN** the consistency check passes silently
