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

### Requirement: The panel SHALL carry machine-verifiable availability evidence from the view

The builder SHALL emit, alongside each field's value frame, an availability
evidence frame of the same shape recording the `available_from_trade_date` of the
record each cell serves, and SHALL assert `available_from <= trade_date` for every
non-NA cell before returning.

The evidence SHALL come from the serving view's own public response, obtained in
the SAME call that yields the value — never reconstructed by the builder from
private internals, raw store reads, or inference from sampled dates and value
changes. Inferred provenance is precisely what evidence must exclude, so the view
SHALL expose availability as part of its public as-of response.

A panel that cannot produce this evidence SHALL be refused rather than returned —
an unverifiable panel is indistinguishable from a leaking one.

#### Scenario: value and evidence come from one view response
- **WHEN** the builder obtains a cell's value
- **THEN** that cell's availability evidence comes from the same view response,
  not from a separate lookup or a reconstruction

#### Scenario: a panel whose evidence cannot be established is refused
- **WHEN** a field's values cannot be attributed to a dated disclosure record
- **THEN** the builder raises rather than returning the panel

#### Scenario: evidence dated after the trade date fails loud
- **WHEN** any non-NA cell's recorded `available_from` exceeds its trade date
- **THEN** the builder raises — never silently drops or repairs the cell

### Requirement: Cross-endpoint and cross-period alignment SHALL be carried as provenance

The panel SHALL carry, per served field, the report period its value came from,
and — for fields a factor differences across adjacent periods — the prior
period's value with its own period and availability provenance. Any factor
combining fields SERVED FROM DIFFERENT ENDPOINTS SHALL be computed only where
those fields resolve to the SAME report period; a mismatched cross-section SHALL
yield NA rather than a mixed-quarter ratio.

Endpoints are served independently by the view, so without this provenance a
ratio across income and balance-sheet fields can silently combine different
quarters, and a difference across adjacent periods cannot be formed at all.

#### Scenario: a mixed-quarter ratio is refused
- **WHEN** two fields of a ratio resolve to different report periods on a trade date
- **THEN** that instrument's value for that factor is NA on that date

#### Scenario: an adjacent-period difference has its own provenance
- **WHEN** a factor differences a field across adjacent report periods
- **THEN** the prior period's value carries its own period and availability
  evidence, and the difference is NA when the adjacent period is absent

### Requirement: Fundamental terminals SHALL be registered before generation

The generator SHALL only produce expressions over registered terminals: the
grammar intersects the allowed-terminal whitelist with its feature registry and
rejects names outside it, so supplying a fundamental panel by parameter is NOT
sufficient to make fundamental factors reachable. A fundamental GP campaign
SHALL therefore register its fields as terminals with their declared types and
taint rules, covered by tests.

Registration SHALL introduce no qlib or PIT import into the factor-mining
package — it adds terminal symbols and their typing only, leaving the D5 gate
intact.

#### Scenario: an unregistered fundamental field cannot be generated
- **WHEN** a fundamental panel is supplied whose fields are not registered terminals
- **THEN** the generator cannot produce expressions over them

#### Scenario: registration keeps the factor-mining boundary
- **WHEN** fundamental terminals are registered
- **THEN** the factor-mining package still imports neither qlib nor the PIT layer

### Requirement: Leakage canaries SHALL corrupt invariants the builder can observe

Canaries SHALL each corrupt an invariant the builder can actually observe. A
canary that merely OMITS evidence tests only the rejection of evidence-less
input: a value derived from the forward return but carrying copied, plausible
evidence satisfying `available_from <= trade_date` is indistinguishable from a
legitimate filing value by the availability assertion alone, so that canary
would pass while the same semantic leak survives. The guarded invariants are:

* **early announcement** — a record served on a trade date before its
  announcement, violating `available_from <= trade_date`;
* **evidence/value from different sources** — a cell whose value comes from one
  report period while its provenance comes from another; since value and
  provenance SHALL come from a single view response, this can only arise by
  bypassing that path, which is what this canary catches;
* **non-monotone availability** — an instrument whose evidence series decreases
  across trade dates; as-of carry-forward only advances to newer announced
  periods, so a decrease is a structural signature of back-filled information,
  decidable without interpreting the values.

A canary surviving as far as factor-pool admission SHALL be a hard failure of the
guarding test suite.

#### Scenario: the early-announcement canary fails construction
- **WHEN** a store record is served on a date before its announcement
- **THEN** panel construction raises

#### Scenario: value and provenance from different periods is refused
- **WHEN** a cell's value and its availability evidence originate in different
  report periods
- **THEN** panel construction raises

#### Scenario: back-filled information breaks availability monotonicity
- **WHEN** an instrument's availability evidence decreases from one trade date to
  the next
- **THEN** panel construction raises

### Requirement: The panel SHALL demonstrably consume the announcement date

A shift-sensitivity diagnostic SHALL rebuild the panel with every effective
announcement date shifted later by `N` trading days and compare against the
baseline. The PANEL CONTENT SHALL change — unconditionally, on any store whose
shifted disclosures fall within the measured window.

An unchanged panel under a shifted announcement date SHALL be treated as proof
that the builder does not consume the announcement date at all (e.g. it keys on
report period) and SHALL REFUSE — a behavioural check that no amount of correct-
looking code can substitute for.

The IC-series assertion SHALL be required only on a DETERMINISTIC FIXTURE
constructed so that the shift necessarily alters evaluated values or their
cross-sectional ranks, with a stated tolerance. A correct announcement-aware
builder can change panel bytes WITHOUT moving a candidate's IC — the shifted
disclosures may fall between sampled evaluation dates, preserve every rank, or
feed a constant/all-missing candidate — so requiring both unconditionally would
refuse valid implementations.

#### Scenario: shifting announcements changes the panel
- **WHEN** the panel is rebuilt with effective announcement dates shifted by
  `N` trading days
- **THEN** the panel content hash differs from the baseline

#### Scenario: an announcement-insensitive builder is refused
- **WHEN** a shifted rebuild produces a byte-identical panel
- **THEN** the diagnostic REFUSES, reporting that the announcement date is unused

#### Scenario: IC movement is asserted on a fixture built to move it
- **WHEN** the shift is applied to the deterministic fixture whose evaluated
  values or ranks it is constructed to alter
- **THEN** the candidate's IC series differs beyond the stated tolerance

#### Scenario: an unmoved IC off that fixture is not a failure
- **WHEN** a shift changes the panel but leaves a candidate's IC unchanged
  outside that fixture (ranks preserved, or the candidate is constant)
- **THEN** the diagnostic does not refuse on the IC criterion alone

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
