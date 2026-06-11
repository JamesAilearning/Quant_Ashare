# Proposal: embed-snapshot-date

## Why

The ST/name staleness guard dated the active-stocks snapshot by file MTIME — a
weak proxy its own docstring flagged: any sync / copy tool that rewrites mtime
makes a stale snapshot look fresh and the guard passes silently, the opposite of
fail-loud. And nothing checked that the snapshot and the price bundle came from
the same update cycle: a rebuilt bundle ranked with a months-old ST/name view
would silently leak a recent ST designation into the buy list.

## What Changes

- **Write side** — `TushareFetcher._fetch_stock_basic` stamps every row of
  `active_stocks.parquet` / `delisted_stocks.parquet` with a `snapshot_date`
  column (`YYYYMMDD`, one value per file) at fetch time. Injectable via
  `TushareFetcherConfig.now` (value-injection, Phase 2 pattern); production =
  system date. An explicit column (not parquet metadata): every current reader
  does subset-column checks, so it is zero-breakage, and a column survives
  copies and pandas round-trips — exactly what mtime does not.
- **Read side** — new `src/data/active_stocks_snapshot.py`:
  `embedded_snapshot_date(df)` returns the single embedded date; fail-loud
  (`SnapshotDateError`) on a missing column (pre-P3-5 file → re-fetch
  instruction), empty/all-null, multiple distinct values, or non-YYYYMMDD.
- **Guards** — `_validate_st_snapshot` now dates the snapshot from the EMBEDDED
  column (mtime retired); an old-format file fails loud, never silently passes.
  New `_assert_st_snapshot_consistent_with_bundle` in `recommend()`: an embedded
  snapshot_date lagging the bundle calendar tail by more than
  `bundle_max_age_days` raises `DailyRecommendationError` (snapshot NEWER than
  the tail is fine — snapshots refresh more often than bundles).

## Non-Goals

- No point-in-time ST history (the known Phase 2 compromise stands).
- No orchestration of fetch/build cycles — P3-6.
- delisted_stocks gets the same stamp (same write loop) but no new guard reads
  it yet.
