# Tasks: embed-snapshot-date

## 1. Implementation
- [x] Write side: `_fetch_stock_basic` stamps `snapshot_date` (YYYYMMDD, one value
      per file) on both buckets; injectable via `TushareFetcherConfig.now`
      (value-injection); explicit column chosen over parquet metadata (all readers
      do subset checks → zero breakage; a column survives copies / round-trips).
- [x] Read side: `src/data/active_stocks_snapshot.py` —
      `embedded_snapshot_date(df)`; `SnapshotDateError` on missing column
      (pre-P3-5 → re-fetch instruction), empty/all-null, multiple distinct
      values, non-YYYYMMDD.
- [x] `_validate_st_snapshot`: staleness now reads the embedded date (mtime
      retired); old-format fails loud; returns the snapshot date.
- [x] `recommend()`: `_assert_st_snapshot_consistent_with_bundle(snapshot_date,
      bundle_last_day, bundle_max_age_days)` — lag beyond tolerance refuses;
      newer-than-tail passes.

## 2. Tests (synthetic parquets in temp dirs; no real fetch)
- [x] EMBED ROUND-TRIP: fetch with injected date → both buckets stamped, single
      distinct value, reader round-trips.
- [x] ACCESSOR: missing column → loud (old-format); all-null/empty → loud;
      multiple distinct → loud; non-YYYYMMDD → loud.
- [x] GUARD: embedded date stale vs as-of → refuses (mtime fresh — must not
      matter); fresh embedded → passes + returns date; old-format → loud;
      conflicting dates → loud.
- [x] CONSISTENCY: snapshot lagging bundle tail > bundle_max_age_days → refuses;
      within tolerance (incl. == tol) → passes; newer than tail → passes.

## 3. Verification
- [x] Affected test files green (102 passed); full fast suite + pit green;
      ruff + mypy clean; openspec validate --strict.
