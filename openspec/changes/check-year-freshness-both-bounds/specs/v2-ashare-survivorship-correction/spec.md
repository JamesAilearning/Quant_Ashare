## MODIFIED Requirements

### Requirement: Per-(ticker, year) resume SHALL be content-fresh, not existence-based

On a freshness scan, an existing per-`(ticker, year)` file SHALL be positively
resume-verified (daily / adj_factor / daily_basic) ONLY when all stored
trade dates are real, eight-ASCII-digit YYYYMMDD dates in that year and their
minimum/maximum span both expected session bounds. Raw date values SHALL retain
the existing string normalization; malformed rows SHALL NOT be dropped to
manufacture usable bounds. A wider valid same-year file SHALL remain reusable.

The expected first and last session SHALL derive from the requested year slice,
further bounded by the ticker's listing window. The first SHALL ceil to the
first actual TRADING day on or after the clipped start; the last SHALL floor
to the last actual TRADING day on or before the clipped end. Only an unavailable
exchange calendar SHALL use the weekday approximation; an empty calendar SHALL
remain no-session evidence. Malformed listing dates SHALL be unknown, and a
reversed listing pair SHALL be an unknown window, not evidence of a no-data miss.
A slice the valid window misses entirely expects no data (an empty placeholder
is truthful), and a mid-slice delisting caps expectation at the delist date.
Existing holiday-only wider-file preservation and clean window-miss placeholder
verification SHALL remain distinct. Missing first-session evidence alone SHALL
NOT establish a clean placeholder.

The per-year endpoint SHALL reject non-ASCII or impossible requested dates with
`TushareFetcherError` before reading year units or requesting calendar/data.
Invalid requested dates SHALL NOT become no-session evidence or leak an
uncaught date-parsing exception through the CLI.

A file short at either expected edge, with malformed/out-of-year dates,
suspiciously empty (data possible per the listing window), or unreadable SHALL
be selected for a one-call replacement of the requested year slice, subject to
the non-destructive replacement guard below. A failed re-pull SHALL keep the old
file and record a hole; the file remains stale, so the next run re-attempts it
without extra bookkeeping. Prior-manifest holes SHALL continue to pierce resume
skips, but SHALL NOT bypass the write guard. Existing trailing post-fetch checks
SHALL remain unchanged; this positive-reuse rule SHALL NOT introduce a leading
systemic-shortfall threshold or certify every internal/vendor-returned date.

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
Existing blind-watermark and holiday-only no-write skips SHALL remain unchanged.

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
- **WHEN** a re-run encounters a valid same-year file spanning both expected
  session bounds, including weekends/holidays and listing-window clipping
- **THEN** the unit is skipped with no data API call and positive verification

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

#### Scenario: a fresh tail cannot conceal missing leading history
- **WHEN** a file has July-to-December records but the scanned request starts
  in January and expects an earlier first session
- **THEN** it is selected for guarded refetch, not positive verification
- **AND** if the request ends before the stored December rows, the existing
  write guard preserves the file with zero data calls and an unsafe-overwrite hole

#### Scenario: invalid dates cannot manufacture either coverage bound
- **WHEN** any stored date is invalid, null or in another year, even alongside
  otherwise sufficient boundary dates
- **THEN** positive reuse is refused and the existing guarded refetch applies
- **AND** invalid dates are never removed to make the file appear reusable

#### Scenario: unknown or empty calendar meanings remain separate
- **WHEN** the calendar is unavailable, or a valid available calendar has no
  session in the requested slice
- **THEN** only the former uses weekday approximation, while the latter retains
  the existing holiday-only preservation behavior without overwriting wider data

#### Scenario: an impossible request date fails at the endpoint boundary
- **WHEN** a per-year endpoint receives an impossible requested start/end date,
  including an invalid start that the weekday ceiling cannot parse
- **THEN** it raises TushareFetcherError before any calendar/data API call or
  year-file replacement, and the CLI reports a controlled failure
