# v2-ashare-survivorship-correction Specification (delta)

## ADDED Requirements

### Requirement: The fetch SHALL support refreshing the units a daily update must bring current

The fetch SHALL, when `--refresh-current` (`TushareFetcherConfig.refresh_current`)
is given, ignore resume's exists-skip for exactly: `stock_basic` (both buckets),
the `namechange` / `suspend_d` aggregate files, and the FINAL year of the
requested range for the per-ticker endpoints (`daily` / `adj_factor` /
`daily_basic`). Past years SHALL stay resume-skipped (closed history), and
`index_weight` SHALL NOT be refreshed (one full-range file per index — its
refresh has its own cadence). Without the flag, resume semantics are unchanged.
Re-pulled files keep the atomic write (the old file stays until the new one
lands), so a re-pull that holes leaves yesterday's data on disk AND the hole in
the manifest — the build gate then refuses by default.

#### Scenario: the final year is re-pulled, past years stay skipped
- **WHEN** a refresh-current fetch runs over a dump where every per-ticker file
  exists
- **THEN** only the final-year files are re-fetched and written; earlier years
  are skipped untouched

#### Scenario: the snapshot and aggregates are re-pulled
- **WHEN** a refresh-current fetch runs with stock_basic / namechange /
  suspend_d present on disk
- **THEN** all of them are re-fetched (both stock_basic buckets), and the
  refreshed active_stocks carries TODAY's embedded snapshot_date

#### Scenario: index_weight is not refreshed
- **WHEN** a refresh-current fetch covers index_weight with its files present
- **THEN** they remain resume-skipped with zero API calls

#### Scenario: a prior-manifest hole forces its unit past the exists-skip
- **WHEN** the prior manifest records a hole for a unit whose (stale) file
  exists on disk — e.g. a refresh failure left yesterday's file, and after a
  year boundary the unit is no longer the final year
- **THEN** the fetch re-attempts EXACTLY that unit (the 01 CLI wires the prior
  manifest's holes into the fetcher as force-retry units) while untouched
  siblings stay resume-skipped — so the hole either heals for real or recurs,
  and the merge never wrongly drops a never-re-attempted hole as self-healed

#### Scenario: the snapshot stamp date is injectable per run
- **WHEN** the orchestrator passes `--snapshot-date` (its ONE frozen run date)
- **THEN** stock_basic's embedded snapshot_date carries that date rather than
  the wall-clock date at write time, so a fetch spanning midnight stamps the
  planned date
