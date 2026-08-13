# v2-fundamental-gp-panel — delta

## ADDED Requirements

### Requirement: The fundamental panel SHALL be built only from already-available disclosures

A fundamental date×instrument panel SHALL derive every cell from
`FinancialPITDataView`'s as-of service, whose availability keys on
`available_from_trade_date` — the first trading day STRICTLY AFTER the effective
announcement (`f_ann_date` falling back to `ann_date`). A cell for trade date `T`
SHALL carry the disclosure-of-record value of the latest report period whose
`available_from_trade_date <= T`, and SHALL be NA when no such period exists.

The panel builder SHALL NOT key on `end_date` / report period, SHALL NOT
forward-fill from the announcement day itself, and SHALL NOT impute a missing
value (no 0, no cross-sectional median, no latest, no future). Missing stays
missing.

#### Scenario: a filing is invisible before its availability date
- **WHEN** a report period's `available_from_trade_date` is `D`
- **THEN** the panel value for that instrument is NA on every trade date before `D`
- **AND** it equals the disclosure-of-record value on `D` and thereafter (until a
  newer period becomes available)

#### Scenario: the announcement day itself is still invisible
- **WHEN** a filing is announced on trade date `A` (post-close assumption)
- **THEN** the panel value on `A` is NA — availability starts strictly after `A`

#### Scenario: a restated period still serves its original disclosure
- **WHEN** a report period has both an `update_flag=0` and an `update_flag=1` row
- **THEN** the panel serves the `update_flag=0` value — a restatement never
  overrides its original

### Requirement: The panel SHALL carry machine-verifiable availability evidence

The builder SHALL emit, alongside each field's value frame, an availability
evidence frame of the same shape recording the `available_from_trade_date` of the
record each cell serves. The evidence SHALL be produced by the builder itself
(not reconstructed by a consumer), and the builder SHALL assert
`available_from <= trade_date` for every non-NA cell before returning.

A panel that cannot produce this evidence SHALL be refused rather than returned —
an unverifiable panel is indistinguishable from a leaking one.

#### Scenario: a panel whose evidence cannot be established is refused
- **WHEN** a field's values cannot be attributed to a dated disclosure record
- **THEN** the builder raises rather than returning the panel

#### Scenario: evidence dated after the trade date fails loud
- **WHEN** any non-NA cell's recorded `available_from` exceeds its trade date
- **THEN** the builder raises — never silently drops or repairs the cell

### Requirement: Leakage canaries SHALL be refused before reaching factor evaluation

The panel path SHALL be guarded by canaries that inject known-leaking data and
assert refusal:

* a **future-value canary** — a synthetic field whose value on `T` is derived
  from the `T`-forward return — SHALL be refused by the availability assertion;
* an **early-announcement canary** — a record served on a trade date before its
  announcement — SHALL make panel construction fail.

A canary surviving as far as factor-pool admission SHALL be a hard failure of the
guarding test suite.

#### Scenario: the future-value canary never reaches the evaluator
- **WHEN** a synthetic field carries `T`-forward information at `T`
- **THEN** panel construction refuses it (no availability evidence)

#### Scenario: the early-announcement canary fails construction
- **WHEN** a store record is served on a date before its announcement
- **THEN** panel construction raises

### Requirement: The panel SHALL demonstrably consume the announcement date

A shift-sensitivity diagnostic SHALL rebuild the panel with every effective
announcement date shifted later by `N` trading days and compare against the
baseline. Shifting SHALL change both the panel content and the resulting IC
series beyond tolerance.

An unchanged panel under a shifted announcement date SHALL be treated as proof
that the builder does not consume the announcement date at all (e.g. it keys on
report period) and SHALL REFUSE — a behavioural check that no amount of correct-
looking code can substitute for.

#### Scenario: shifting announcements changes the panel
- **WHEN** the panel is rebuilt with effective announcement dates shifted by
  `N` trading days
- **THEN** the panel content hash differs from the baseline
- **AND** the candidate's IC series differs beyond tolerance

#### Scenario: an announcement-insensitive builder is refused
- **WHEN** a shifted rebuild produces a byte-identical panel
- **THEN** the diagnostic REFUSES, reporting that the announcement date is unused

### Requirement: A fundamental GP campaign SHALL freeze its panel path and terminal set

A pre-registered fundamental GP campaign SHALL: include the panel PIT tests and
canaries in its gate's PIT battery, appended to (never replacing) the canonical
battery; carry rehearsal scenarios in which an injected look-ahead probe and a
surviving canary each force the gate to REFUSE; list the panel builder and the
shift-sensitivity diagnostic among its frozen artifacts; and restrict the
generator to a whitelisted set of pre-registered fundamental terminals.

#### Scenario: the canonical PIT battery cannot be replaced
- **WHEN** a campaign registers additional PIT test targets
- **THEN** they are appended to the canonical battery, which still runs

#### Scenario: a surviving canary forces the gate to refuse
- **WHEN** a rehearsal injects a canary that the panel path fails to reject
- **THEN** the gate REFUSES the campaign

### Requirement: Cross-sectional grouping SHALL be supplied as an as-of resolution

Group labels SHALL be resolved as of the cross-section's trade date — never from
a current snapshot applied retroactively — wherever a fundamental factor is
standardized within a group (industry being the motivating case). The panel
builder SHALL accept the grouping as an optional as-of resolver — a callable of
`(trade_date, instruments)` — so a point-in-time grouping artifact can be
supplied without changing the builder's shape or its guarantees.

Until a point-in-time grouping artifact exists, the resolver SHALL be absent
rather than substituted by a current snapshot: a snapshot applied to historical
cross-sections is systematic future information of exactly the kind the
announcement-date discipline exists to prevent.

#### Scenario: grouping is resolved per cross-section
- **WHEN** a grouping resolver is supplied
- **THEN** each trade date's labels come from that resolver at that date

#### Scenario: no snapshot fallback
- **WHEN** no point-in-time grouping artifact is available
- **THEN** the panel is built without grouping rather than with a current snapshot
