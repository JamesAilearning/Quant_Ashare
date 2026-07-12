# v2-financial-pit-contract Specification

## Purpose
TBD - created by archiving change add-financial-pit-contract. Update Purpose after archive.
## Requirements
### Requirement: Raw financial filings SHALL be ingested versioned with provenance

The system SHALL ingest raw income-statement, balance-sheet, and cash-flow
records from the data provider with per-record provenance: source endpoint,
fetch batch identifier, content hash, and `update_flag`. BOTH the
`update_flag=0` (as-originally-reported) and `update_flag=1` (revised) rows
for a given `(instrument, report_period)` SHALL be preserved; the ingest
SHALL NOT silently deduplicate, overwrite, or collapse versions. A re-fetch
whose content hash differs from the stored record SHALL be detected and
recorded, never silently replacing the prior version in place.

#### Scenario: both original and revised versions are retained
- **WHEN** a `(instrument, report_period)` has both an `update_flag=0` and an
  `update_flag=1` row from the provider
- **THEN** both rows are stored with distinct provenance, and neither is
  dropped or overwritten

#### Scenario: a changed re-fetch is recorded, not silently replaced
- **WHEN** the same record is re-fetched with a different content hash
- **THEN** the divergence is recorded (new fetch batch + hash) rather than
  overwriting the prior stored content in place

### Requirement: Financial-data availability SHALL be keyed on announcement date, never report-period end

Every financial observation SHALL carry `report_period` (the quarter it
describes, `end_date`), `announcement_date` (`f_ann_date`, falling back to
`ann_date` with the fallback recorded), and `available_from_trade_date` —
the first trading day STRICTLY AFTER `announcement_date` (announcements are
assumed post-close absent an intraday timestamp). All PIT joins SHALL use
`available_from_trade_date`. The report-period-end date SHALL NEVER be used
as an availability date.

#### Scenario: a filing is invisible before its announcement
- **WHEN** the view is queried as of a trade date earlier than a record's
  `announcement_date`
- **THEN** that record is not visible (the report-period-end look-ahead is
  refused)

#### Scenario: a post-close announcement takes effect the next trading day
- **WHEN** a record's `announcement_date` falls on trading day D
- **THEN** its `available_from_trade_date` is the next trading day after D,
  and the value is first usable then

#### Scenario: a missing announcement date fails loud, never defaults to period end
- **WHEN** both `f_ann_date` and `ann_date` are absent for a record
- **THEN** the record is treated as unavailable and reported, NEVER assigned
  an availability date derived from `report_period`

### Requirement: The as-originally-reported value SHALL be the PIT default; undatable restatements SHALL NOT be backfilled

For each `report_period`, the view SHALL serve the value from that period's
FIRST/ONLY recorded disclosure, keyed to its `available_from_trade_date`:
when the period has multiple version rows it SHALL prefer the `update_flag=0`
(as-originally-reported) row, and when the period has NO `update_flag=0` row
it SHALL serve the sole `update_flag=1` row — which is that period's original
disclosure of record. (The provider does not always retain an `update_flag=0`
row for recent periods; discarding a period that exists only as
`update_flag=1` would drop genuinely-available data and stale the served
value by 1–2 years.) As of a query trade date, the view SHALL serve the
LATEST `report_period` whose `available_from_trade_date` is on or before that
date — it SHALL NOT carry forward from an older period when a newer period is
already announced and available. The view SHALL NEVER serve a restated value
in place of its original: a `report_period` that has both `update_flag` rows
SHALL always resolve to `update_flag=0`. Because the provider assigns NO
independent announcement date to a later restatement, a revised value that
cannot be dated to a restatement announcement SHALL NOT be applied to any
date earlier than it was actually knowable; that restatement-undatable
condition SHALL be recorded as a known PIT limitation. This rule's
point-in-time safety is STRUCTURAL — it serves only first/sole disclosures
and never a restatement over its original — and therefore does not depend on
`update_flag=0` and `update_flag=1` values being equal across the universe.

#### Scenario: a recent period that exists only as update_flag=1 is served (no staleness)
- **WHEN** a `report_period`'s only recorded row is `update_flag=1`, its
  `available_from_trade_date` is on or before the query trade date, and it is
  the latest such period
- **THEN** that period is served (it is the period's original disclosure of
  record) — the view does NOT discard it and fall back to an older
  `update_flag=0` period

#### Scenario: a period with both versions serves the original
- **WHEN** a `report_period` has both an `update_flag=0` and an
  `update_flag=1` row
- **THEN** the `update_flag=0` value is served — a restated value is never
  served in place of its original

#### Scenario: as-of selects the latest available period, not an older one
- **WHEN** several `report_period`s have `available_from_trade_date` on or
  before the query trade date
- **THEN** the view serves the LATEST of them (by period end), resolving its
  version by the prefer-`update_flag=0`-else-sole-`update_flag=1` rule

#### Scenario: a later restatement does not rewrite history
- **WHEN** a `report_period` is later revised (`update_flag=1`) with no
  independent restatement announcement date, and an `update_flag=0` original
  exists
- **THEN** the view continues to serve the original `update_flag=0` value at
  its `available_from_trade_date` — the revised number is not backfilled to
  the original availability date

### Requirement: Missing data SHALL stay missing and fail loud; carry-forward is as-of, not imputation

The view SHALL carry forward the latest ALREADY-ANNOUNCED statement as-of the
query date (a deliberate as-of hold), and SHALL NOT impute missing values. A
field that is missing, not-yet-announced as of the query date, or of unknown
schema SHALL remain missing and be explicitly reported. The view SHALL NEVER
substitute zero, a cross-sectional or industry median, the latest value, or
any future value for a missing field.

#### Scenario: as-of carry-forward holds the last announced period, not a fill
- **WHEN** no new statement has been announced between two query dates
- **THEN** the view serves the last already-announced period's value (as-of
  hold), and serves NA where no period has yet been announced

#### Scenario: missing rd_exp is served as NA, never zero
- **WHEN** a firm-period has no `rd_exp` disclosed
- **THEN** the view returns NA for `rd_exp` (never 0), preserving the
  survivorship-sensitive cohort signal for Gate-3

#### Scenario: an unknown or absent field fails loud
- **WHEN** a requested field is absent or of unrecognized schema
- **THEN** the view raises fail-loud rather than returning a default value

### Requirement: The financial-feature universe SHALL exclude financial-sector issuers by a stable rule

The research universe for financial-PIT features SHALL exclude
financial-sector issuers (banks, brokers, insurers) using a stable industry
list, cross-checked against `oper_cost` absence. Field-absence SHALL be the
cross-check, NOT the primary exclusion rule. The industry classification
SHALL be recorded as a current snapshot (no PIT industry data available),
documented as acceptable because sector membership is near-static.

#### Scenario: a bank is excluded by the list and agrees with the cross-check
- **WHEN** an issuer is on the financial-sector list
- **THEN** it is excluded from the financial-feature universe, and its
  missing `oper_cost` agrees with the exclusion as a cross-check (a
  disagreement is reported, not silently resolved)

### Requirement: A single research-side FinancialPITDataView SHALL be the sole access path, isolated from canonical runtime

All evaluators and feature code SHALL reach financial data ONLY through
`FinancialPITDataView`; direct reads of the raw filings SHALL be forbidden
and enforced. The view SHALL be research-only and physically and semantically
isolated from the canonical feature registry and the production runtime —
there SHALL be no import or wiring path from this view into canonical
training or `daily_recommend`.

#### Scenario: a direct raw-filing read is rejected
- **WHEN** code outside the view reads the raw filing store directly
- **THEN** a governance test flags it (the view is the only sanctioned path)

#### Scenario: the view is absent from the canonical runtime graph
- **WHEN** the canonical feature registry / training / `daily_recommend`
  import graph is inspected
- **THEN** `FinancialPITDataView` does not appear in it (research/production
  isolation holds)

### Requirement: Governance tests SHALL pin PIT correctness and coverage acceptance

The change SHALL ship governance tests covering: announcement look-ahead
refusal, next-trading-day effect, original-disclosure-first, missing→
fail-loud (no 0/median/latest/future fill), raw-read rejection, delist and
index-membership boundaries reusing the existing PIT universe, and coverage
acceptance per the Gate-1 full-sample (n=627, incl. delisted) table. A field
whose coverage regresses below the recorded acceptance floor SHALL fail loud.

#### Scenario: a coverage regression fails loud
- **WHEN** a required field's coverage falls below the Gate-1 acceptance
  floor recorded for it
- **THEN** the coverage governance test fails (the drop is never silently
  tolerated)

#### Scenario: delisted names are present with no survivorship gap
- **WHEN** the financial-feature universe is assembled over a historical
  window
- **THEN** it includes delisted CSI300-ever members (via the existing PIT
  universe), so the panel carries no survivorship gap in the financial data

### Requirement: The version-collapse residual SHALL be measurable and its serve-rule invariant enforced

The contract layer SHALL provide an audit (`version_collapse_residual`) that,
across every `report_period` with BOTH an `update_flag=0` and an
`update_flag=1` row, measures — per charter field, over the both-version
periods where AT LEAST ONE version discloses the field (a both-NA period is
NOT a comparison, matching the audit) — the fraction whose values DIFFER (a
genuine restatement, INCLUDING an NA↔non-NA transition) versus are EQUAL (a
version marker only). A governance
test SHALL enforce, on a deterministic fixture, the audit MECHANISM and the
serve-rule INVARIANT: a differing both-version period ALWAYS resolves to
`update_flag=0`, so a non-zero residual is a SIZE, never a look-ahead. Because
the full universe is not ingested at contract time (a Gate-3 activity), the
measured residual SHALL be recorded whenever the audit is run over an ingested
store — sized on the Gate-2 smoke store, and produced over the full
CSI300-ever universe by the SAME audit once that store is ingested — as the
documented bound on the one residual the data cannot rule out: a recent
`update_flag=1`-only period silently correcting a first-announced value the
provider no longer stores.

#### Scenario: the audit reports the differing-version fraction
- **WHEN** the version-collapse audit runs over a set of both-version
  `report_period`s
- **THEN** it reports, per charter field, the fraction whose `update_flag=0`
  and `update_flag=1` values differ — counting an NA↔non-NA transition as a
  difference, and NOT counting a both-NA period (it is no comparison) — and
  that figure is the recorded restatement residual

#### Scenario: a differing both-version period still serves the original
- **WHEN** a `report_period` has `update_flag=0` ≠ `update_flag=1` (a genuine
  restatement)
- **THEN** the view serves the `update_flag=0` value (no look-ahead), and the
  occurrence is counted in the audited residual

#### Scenario: the mechanism and invariant are enforced without the full store
- **WHEN** CI runs without an ingested universe store
- **THEN** the governance test enforces the audit mechanism and the
  serve-rule-resolves-to-`update_flag=0` invariant on a deterministic fixture,
  and the full-universe residual is produced by the same audit at ingest time
  (it is not silently skipped)

