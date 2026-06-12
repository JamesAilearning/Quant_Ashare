# v2-ashare-survivorship-correction Specification (delta)

## ADDED Requirements

### Requirement: Per-(ticker, year) resume SHALL be content-fresh, not existence-based

An existing per-`(ticker, year)` file (daily / adj_factor / daily_basic)
SHALL be resume-skipped ONLY when its `max(trade_date)` reaches the latest
date this run can expect of it: the last weekday on or before
`min(requested end_date, Dec 31 of the year)`, further bounded by the
ticker's listing window — a slice the window misses entirely expects no data
(an empty placeholder is truthful), and a mid-slice delisting caps the
expectation at the delist date. A file that stops short, is
suspiciously empty (data possible per the listing window), or cannot be read
SHALL be re-pulled for the WHOLE year (one API call) and overwritten on
success. A failed re-pull SHALL keep the old file and record a hole; the
file remains stale, so the next run re-attempts it without extra
bookkeeping. Prior-manifest holes SHALL continue to pierce every skip.

The FINAL requested year SHALL be freshness-scanned on every run. A PAST
year MAY be skipped from scanning only when the previous manifest's
per-endpoint coverage watermark attests everything this run could expect of
it; with no watermark every year is scanned, and an explicit
`--verify-all-years` SHALL force the full sweep.

#### Scenario: a truncated boundary year backfills when the range extends
- **WHEN** a `(ticker, year)` file ends mid-year and a later run requests a
  wider end date
- **THEN** the year is re-pulled in one call and the file reaches the new
  expected end (no more frozen half-years shadowed by exists-skip)

#### Scenario: a complete year file preserves crash-rerun resume
- **WHEN** a re-run encounters a year file already current through its
  expected end (including a weekend end date flooring to Friday)
- **THEN** the unit is skipped with no API call

#### Scenario: tomorrow's run fetches the new day
- **WHEN** today's run wrote the current-year file through today and
  tomorrow's run requests end_date+1
- **THEN** the file is re-pulled and contains the new day's bar

### Requirement: The fetch manifest SHALL never be cleared by a failure path

`fetch_manifest.json` SHALL be deleted ONLY by an explicit operator
instruction (`--reset-manifest`). A refused merge, a manifest write failure,
a hard abort, and an unreadable manifest at run start SHALL each exit
non-zero with the manifest left byte-for-byte as it was, with an error
naming the explicit reset as the deliberate-fresh-start path.

#### Scenario: a refused merge preserves the hole ledger
- **WHEN** a narrower-scope (or disjoint-scope) run's merge is refused
- **THEN** the CLI exits 1 and the manifest bytes are identical to before
  the run

### Requirement: Coverage merge SHALL NOT fabricate or corrupt ranges

Merging coverage ranges separated by a never-fetched gap of more than one
calendar day SHALL be refused (unioning them would claim the gap as
covered). The empty-string "coverage not established" sentinel SHALL never
win a min/max comparison nor trigger the narrower-scope refusal. An endpoint
that ran but established nothing (wrote no unit, holed no unit) SHALL
preserve the prior endpoint record verbatim — its holes are not
self-healed by a run that re-attempted nothing.

#### Scenario: disjoint ranges are refused
- **WHEN** the manifest covers [2000, 2010] and a run covers [2020, 2025]
- **THEN** the merge raises rather than recording "complete [2000, 2025]"
