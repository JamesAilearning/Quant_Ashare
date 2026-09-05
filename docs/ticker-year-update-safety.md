# Ticker-year update safety

This guard protects existing raw `daily`, `adj_factor`, and `daily_basic`
parquets from replacement by a request that excludes their stored history.
It does not merge intervals, certify vendor completeness, repair previously
lost rows, or change production data automatically.

## What the operator sees

For example, a year file contains January through June, but an update asks
only for July 1–2. The selected refetch now preserves the entire old file,
does not call the data endpoint for that unit, and logs:

```text
HOLE: daily [ts_code=600000.SH year=2025] (unsafe_overwrite, 0 attempts)
Requested 20250701..20250702 excludes existing history; preserving file.
Retry with a covering range 20250102..20250702.
```

`0 attempts` means refusal before a data API request, not a successful fetch.
Other units continue. The normal CLI exit is `3` (completed with holes), and
the existing manifest/build integrity gates retain the failure. If the prior
manifest has broader unresolved holes, the normal narrower-merge refusal may
instead exit `1` and preserve that prior manifest; inspect the logs as well.
Neither outcome is a clean data update.

## Recovery

1. Inspect the reported unit, the requested range, and the previous manifest.
   Preserve a backup before any repair to an important raw-data directory.
2. For valid dates in the correct year, retry with a range containing **both**
   the existing records and the requested new dates. A January 1-to-as-of
   update normally covers the current-year history. Cover all affected units
   and any wider prior-manifest holes; one unit's suggested bounds may not be
   sufficient for the entire job. No dates are expanded automatically.
3. For an unreadable file, missing date column, or invalid date values, inspect
   the backup before an explicit full-calendar-year repair. Known records in
   a different year require partition repair, not an automatic full-year
   overwrite. For production files prefer rebuilding into a staging directory
   and validating before a separately approved replacement.
4. Recheck the CLI result and manifest after the covering retry. The same
   stable ticker-year hole clears through the existing retry/merge process
   only when the fetch succeeds at the required scope.

Do not delete files, reset the manifest, or enable hole overrides just to make
the error disappear. This document does not authorize replacing production
raw data, rebuilding/swapping the live qlib provider, or changing a model.

## Compatibility and limitations

- Initial partial-year downloads have no prior history to erase and remain
  allowed. Readable empty placeholders, including legacy schema-less ones,
  remain replaceable. Covering year-to-date requests remain allowed.
- No-write resume and dry runs remain unchanged. A prior-hole forced retry
  cannot bypass the guard once a refetch is selected.
- The guard checks the **request interval**, not the returned rows. A vendor
  response truncated inside that interval remains a separate data-quality risk.
- Maximum-date freshness alone does not prove an interval's left edge or
  internal completeness. This guard does not fix or certify that coverage.
- Aggregate files such as `namechange`, `suspend_d`, and `index_weight` do not
  use this ticker-year guard. It is not a general guarantee that arbitrary
  narrow requests are safe across all endpoints.
- Independent concurrent writers to the same raw directory remain unsupported
  by this check; use the existing single-job operational discipline.
