## MODIFIED Requirements

### Requirement: Per-(ticker, year) resume SHALL be content-fresh, not existence-based

An existing per-`(ticker, year)` file (daily / adj_factor / daily_basic) SHALL be resume-skipped ONLY when its `max(trade_date)` reaches the latest
date this run can expect of it: the last actual TRADING day (from the exchange
calendar — the last-weekday heuristic only when the calendar is unavailable) on
or before `min(requested end_date, Dec 31 of the year)`, further bounded by the
ticker's listing window — a slice the window misses entirely expects no data
(an empty placeholder is truthful), and a mid-slice delisting caps the
expectation at the delist date. A file that stops short, is
suspiciously empty (data possible per the listing window), or cannot be read
SHALL be selected for a one-call replacement of the requested year slice,
subject to the non-destructive replacement guard below. A failed re-pull
SHALL keep the old file and record a hole; the file remains stale, so the
next run re-attempts it without extra bookkeeping. Prior-manifest holes
SHALL continue to pierce resume skips, but SHALL NOT bypass the write guard.

Before replacing an existing file with a partial-calendar-year request,
the fetcher SHALL establish that every stored trade date is a real YYYYMMDD
date in that year and is contained in the requested interval. If dates are
unreadable/invalid or any stored row falls outside the requested interval,
the fetcher SHALL preserve the original bytes, make no data API call for that
unit, and record an `unsafe_overwrite` hole under the stable ticker-year unit
with zero attempts. It SHALL NOT increment written or verified counts for
that refused unit. Known bounds SHALL yield guidance covering the union of
the old and requested intervals; unknown bounds SHALL require explicit
full-calendar-year repair. The fetcher SHALL NOT silently widen the request
or combine old and new rows. First-time partial requests and replacements
containing all existing valid dates SHALL remain allowed. Explicit
full-calendar-year replacement SHALL retain its existing corrupt-file repair
behavior unless valid dates establish that the file contains known out-of-year
rows, which SHALL require explicit partition repair rather than deletion.
Readable empty placeholders SHALL remain replaceable even without a date column.
Existing no-write resume skips SHALL remain unchanged.

The FINAL requested year SHALL be freshness-scanned on every run. A PAST
year MAY be skipped from scanning only when the previous manifest's
per-endpoint coverage watermark attests everything this run could expect of
it; with no watermark every year is scanned, and an explicit
`--verify-all-years` SHALL force the full sweep.

#### Scenario: a truncated boundary year backfills when the range extends
- **WHEN** a `(ticker, year)` file ends mid-year and a later run requests a
  wider end date while retaining a start covering its existing rows
- **THEN** the year slice is re-pulled in one call and the file reaches the new
  expected end (no more frozen half-years shadowed by exists-skip)

#### Scenario: a complete year file preserves crash-rerun resume
- **WHEN** a re-run encounters a year file already current through its
  expected end (including a weekend end date flooring to Friday)
- **THEN** the unit is skipped with no API call

#### Scenario: tomorrow's run fetches the new day
- **WHEN** today's run wrote the current-year file through today and
  tomorrow's run requests end_date+1 with a start covering existing rows
- **THEN** the file is re-pulled and contains the new day's bar

#### Scenario: a narrow stale or forced retry cannot erase earlier history
- **WHEN** a partial-year refetch is selected but the old file contains dates
  before or after that request, including an expected-empty pre-listing slice
- **THEN** the original file is unchanged and an unsafe-overwrite hole is
  recorded without a data API call, even when the unit has a prior hole
- **AND** other units continue through the existing fetch loop

#### Scenario: a corrupt partial-year file cannot be silently replaced
- **WHEN** a partial-year refetch cannot establish valid old date bounds
- **THEN** it preserves the file and records an unsafe-overwrite hole
- **AND** explicit full-calendar-year repair remains possible
