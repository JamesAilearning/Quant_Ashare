## MODIFIED Requirements

### Requirement: Raw financial filings SHALL be ingested versioned with provenance

The system SHALL ingest raw income-statement, balance-sheet, and cash-flow
records from the data provider with per-record provenance: source endpoint,
fetch batch identifier, content hash, and `update_flag`. The versioned
identity of a record SHALL be `(instrument, report_period, update_flag,
EFFECTIVE announcement day)` — the effective announcement day is `f_ann_date`,
falling back to `ann_date` when `f_ann_date` is blank (so a fallback-dated
pair with distinct `ann_date` is two disclosures, never one NA key, while an
`ann_date`-only correction under a present `f_ann_date` is the SAME
disclosure — a changed re-fetch, latest batch current). The provider emits,
for a few `(instrument, report_period, update_flag)` triples, TWO disclosures
with different content distinguishable ONLY by announcement date; each is a
distinct, dated disclosure event and BOTH SHALL be preserved. BOTH the `update_flag=0`
(as-originally-reported) and `update_flag=1` (revised) rows for a given
`(instrument, report_period)` SHALL be preserved; the ingest SHALL NOT
silently deduplicate, overwrite, or collapse versions. Two rows on the SAME
EFFECTIVE announcement day (`f_ann_date`, or `ann_date` when `f_ann_date` is
blank) for one `(instrument, report_period, update_flag)` but with DIFFERENT
content SHALL be refused within one fetch (true ambiguity — the announcement
day cannot order them; an identity dimension the key does not carry). A
re-fetch whose content hash differs from the stored record SHALL be detected
and recorded, never silently replacing the prior version in place.

#### Scenario: both original and revised versions are retained
- **WHEN** a `(instrument, report_period)` has both an `update_flag=0` and an
  `update_flag=1` row from the provider
- **THEN** both rows are stored with distinct provenance, and neither is
  dropped or overwritten

#### Scenario: a late re-announcement of the same version is retained as a distinct disclosure
- **WHEN** the provider returns two rows for one `(instrument, report_period,
  update_flag)` whose content differs and whose `f_ann_date` differs
- **THEN** both rows are stored as distinct disclosure events (neither is
  dropped, and the fetch is NOT refused as ambiguous)

#### Scenario: a same-announcement-day double content is refused as truly ambiguous
- **WHEN** the provider returns, in one fetch, two rows for one `(instrument,
  report_period, update_flag)` on the SAME effective announcement day but
  with different content
- **THEN** the ingest refuses that instrument/endpoint loudly rather than
  collapse arbitrarily

#### Scenario: a changed re-fetch is recorded, not silently replaced
- **WHEN** the same record is re-fetched with a different content hash
- **THEN** the divergence is recorded (new fetch batch + hash) rather than
  overwriting the prior stored content in place

### Requirement: The as-originally-reported value SHALL be the PIT default; undatable restatements SHALL NOT be backfilled

For each `report_period`, the view SHALL serve the value from that period's
DISCLOSURE OF RECORD, keyed to its `available_from_trade_date`: when the
period has multiple version rows it SHALL prefer the `update_flag=0`
(as-originally-reported) row, and when the period has NO `update_flag=0` row
it SHALL serve the `update_flag=1` row — which is that period's original
disclosure of record. (The provider does not always retain an `update_flag=0`
row for recent periods; discarding a period that exists only as
`update_flag=1` would drop genuinely-available data and stale the served
value by 1–2 years.) When ONE version (`update_flag` value) of a period has
MULTIPLE dated disclosures, the disclosure of record for that version SHALL
be the EARLIEST-ANNOUNCED row — ordered by `announcement_date` (the resolved
announcement day), NOT by `available_from_trade_date` alone (two distinct
announcement days can share one availability day across a weekend/holiday) —
with dated disclosures preferred over undated ones; two disclosures on ONE
effective announcement day are ONE identity, so an unresolved same-day double
content SHALL fail loud as duplicate versions (row order never decides); a
LATER same-version
re-announcement is a DATED restatement — recorded with its own announcement
date, NEVER served in place of the record. As of a query trade date, the view SHALL serve the LATEST
`report_period` whose `available_from_trade_date` is on or before that
date — it SHALL NOT carry forward from an older period when a newer period is
already announced and available. The view SHALL NEVER serve a restated value
in place of its original: a `report_period` that has both `update_flag` rows
SHALL always resolve to `update_flag=0`. A revised value that cannot be dated
to a restatement announcement SHALL NOT be applied to any date earlier than
it was actually knowable; that restatement-undatable condition SHALL be
recorded as a known PIT limitation. This rule's point-in-time safety is
STRUCTURAL — it serves only a period's first/sole disclosure and never a
restatement over its record — and therefore does not depend on
`update_flag=0` and `update_flag=1` values being equal across the universe.

#### Scenario: a recent period that exists only as update_flag=1 is served (no staleness)
- **WHEN** a `report_period`'s only recorded rows are `update_flag=1`, the
  record's `available_from_trade_date` is on or before the query trade date,
  and it is the latest such period
- **THEN** that period is served (its earliest-announced `update_flag=1` row
  is the period's original disclosure of record) — the view does NOT discard
  it and fall back to an older `update_flag=0` period

#### Scenario: a period with both versions serves the original
- **WHEN** a `report_period` has both an `update_flag=0` and an
  `update_flag=1` row
- **THEN** the `update_flag=0` value is served — a restated value is never
  served in place of its original

#### Scenario: a late same-version re-announcement is not served over the record
- **WHEN** one `(report_period, update_flag)` has two dated disclosures — an
  original at announcement A and a later re-announcement at B (B after A)
  with different content
- **THEN** the earliest-announced row (A) is the disclosure of record and is
  served from A's `available_from_trade_date`; the B row is a dated
  restatement, recorded but never served, at ANY as-of date

#### Scenario: as-of selects the latest available period, not an older one
- **WHEN** several `report_period`s have `available_from_trade_date` on or
  before the query trade date
- **THEN** the view serves the LATEST of them (by period end), resolving its
  version by the record rule (prefer `update_flag=0`, earliest-announced
  within a version)

#### Scenario: a later restatement does not rewrite history
- **WHEN** a `report_period` is later revised (`update_flag=1`) with no
  independent restatement announcement date, and an `update_flag=0` original
  exists
- **THEN** the view continues to serve the original `update_flag=0` value at
  its `available_from_trade_date` — the revised number is not backfilled to
  the original availability date
