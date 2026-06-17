# v2-ashare-survivorship-correction Specification (delta)

## ADDED Requirements

### Requirement: stock_basic snapshots SHALL embed their snapshot date

The Tushare fetch SHALL stamp every row of `active_stocks.parquet` and
`delisted_stocks.parquet` with a `snapshot_date` column (`YYYYMMDD`, exactly one
value per file) recording when the snapshot was taken. Downstream staleness /
consistency guards read THIS instead of file mtime, which copies and sync tools
silently rewrite. The stamp date SHALL be injectable for tests
(`TushareFetcherConfig.now`, value-injection) and default to the system date in
production. The column is additive: every existing reader checks required
columns as a subset, so the stamp breaks none of them.

#### Scenario: a fetched snapshot carries the stamp
- **WHEN** `stock_basic` is fetched (with an injected date for determinism)
- **THEN** both written buckets carry a `snapshot_date` column whose single
  distinct value is that date, and the embedded-date reader round-trips it

#### Scenario: existing readers are unaffected
- **WHEN** the builder / universe / registry / ST readers load a stamped file
- **THEN** their required-column subset checks pass unchanged
