# Ticker-year update safety

Raw `daily`, `adj_factor`, and `daily_basic` year files have two complementary
checks: content freshness decides whether an existing file can be reused;
the overwrite guard protects its stored history if a refetch is selected.
Neither check merges intervals, certifies vendor completeness, repairs
previously lost rows, or changes production data automatically.

## When an existing file is reusable

For a scanned year with expected trading sessions, **both** ends must cover
the requested interval: the earliest stored date must be no later than the
first expected session, and the latest no earlier than the last. All stored
dates must be real, eight-digit ASCII `YYYYMMDD` dates in that partition's
year; malformed or out-of-year rows cannot be discarded to claim verification.
A July–December file therefore cannot verify a January–December request just
because its December date is current. A valid January–December file can still
be reused for a narrower July–September request without writing it.

Expected sessions are clipped to the request, year, and valid listing window.
The exchange calendar supplies the first/last session; the existing logged,
holiday-unaware weekday fallback is used only when that calendar is unavailable.
An available empty calendar is not replaced with weekdays. Missing or invalid
listing dates are unknown, and a reversed pair makes both bounds unknown.
Legitimate no-session skips and clean out-of-listing placeholders are retained.
Impossible or non-ASCII requested dates fail at the per-year endpoint boundary,
before reading year files or requesting its calendar/data; the CLI reports a
controlled error instead of leaking a date-parsing exception.

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
- Previously attested past-year watermark skips remain blind skips, not new
  verification; `--verify-all-years` forces their content scan. Dry runs remain
  unchanged. A prior-hole forced retry cannot bypass the guard once selected.
- The guard checks the **request interval**, not the returned rows. A vendor
  response truncated inside that interval remains a separate data-quality risk.
- Two-boundary freshness does not prove every session inside the interval
  exists. A vendor response that still starts late is not newly classified as
  a systemic failure; it remains subject to refetch on the next content scan.
  Existing write/manifest accounting and tail-shortfall policy are unchanged;
  neither a written unit nor a complete manifest certifies vendor completeness.
- Aggregate files such as `namechange`, `suspend_d`, and `index_weight` do not
  use this ticker-year guard. It is not a general guarantee that arbitrary
  narrow requests are safe across all endpoints. See the separate
  [aggregate declared-range guard](aggregate-update-safety.md) and its provenance limits.
- Independent concurrent writers to the same raw directory remain unsupported
  by this check; use the existing single-job operational discipline.
