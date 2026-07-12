## MODIFIED Requirements

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

## ADDED Requirements

### Requirement: The version-collapse residual SHALL be audited across the universe and bounded

A governance test SHALL audit — across ALL charter financial fields, the full
CSI300-ever universe, and every `report_period` that has BOTH `update_flag`
rows — the fraction of both-version periods whose `update_flag=0` and
`update_flag=1` values DIFFER (a genuine restatement) versus are EQUAL (a
version marker only). The measured differing-fraction SHALL be recorded as
the bounded restatement residual for the honesty envelope. Because the
serve-rule always resolves a differing both-version period to `update_flag=0`,
a non-zero differing-fraction SHALL NOT introduce look-ahead; the audit SIZES
the residual and is not a safety precondition. The one residual the data
cannot rule out — a recent `update_flag=1`-only period that is a silent
correction of a first-announced value the provider no longer stores — SHALL
be documented as an inherent provider limitation, bounded by the audited
restatement rate.

#### Scenario: the audit records the differing-version fraction
- **WHEN** the version-collapse audit runs over the universe
- **THEN** it reports the fraction of both-version `report_period`s whose
  `update_flag=0` and `update_flag=1` field values differ, and that figure is
  recorded as the documented restatement residual

#### Scenario: a differing both-version period still serves the original
- **WHEN** a `report_period` has `update_flag=0` ≠ `update_flag=1` (a genuine
  restatement)
- **THEN** the view serves the `update_flag=0` value (no look-ahead), and the
  occurrence is counted in the audited residual
