# v2-daily-stock-recommendation Specification (delta)

## MODIFIED Requirements

### Requirement: ST staleness SHALL be judged by the embedded snapshot date

The ST/name staleness guard SHALL date the active-stocks snapshot by its
EMBEDDED `snapshot_date` column (stamped by the fetcher at fetch time), not by
file mtime — mtime is rewritten by sync / copy tools, so a stale snapshot can
look fresh and pass silently. A snapshot whose embedded date lags the as-of date
by more than `st_snapshot_max_age_days` SHALL be refused. A file WITHOUT the
embedded column (written before the stamp existed) SHALL fail loud with a
re-fetch instruction — never a silent mtime fallback. A malformed embedding
(empty / all-null, multiple distinct values, non-YYYYMMDD) SHALL also fail loud.

#### Scenario: staleness reads the embedded date, not mtime
- **WHEN** the snapshot's embedded snapshot_date lags the as-of date beyond
  tolerance, however fresh the file's mtime is
- **THEN** `recommend` refuses (DailyRecommendationError)

#### Scenario: an old-format snapshot fails loud
- **WHEN** the snapshot has no embedded snapshot_date column
- **THEN** `recommend` refuses with a re-fetch instruction rather than falling
  back to mtime

#### Scenario: a fresh embedded date passes
- **WHEN** the embedded snapshot_date is within tolerance of the as-of date
- **THEN** the guard passes and returns the snapshot date

#### Scenario: conflicting embedded dates fail loud
- **WHEN** the snapshot carries more than one distinct snapshot_date value
- **THEN** `recommend` refuses (corrupt / hand-merged file)

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
