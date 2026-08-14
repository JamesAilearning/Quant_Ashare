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

"Not yet available" applies to the PERIOD, never to the cell: while a filing is
pending, the cell continues to serve the WINNER — the latest report period that
IS already available. Availability makes a period ELIGIBLE to win; it does not
make it the served one. An older period that arrives late (or arrives the same
day as a newer one) therefore does NOT displace an already-available newer
period. Blanking the cell around each filing would manufacture artificial
missing intervals that move both coverage and factor values, and it is not what
"missing stays missing" means — that phrase forbids IMPUTING an absent value,
not serving a genuinely available one.

#### Scenario: a filing is not served before its availability date
- **WHEN** a report period's `available_from_trade_date` is `D`
- **THEN** on every trade date before `D` the panel does NOT serve that period —
  it serves the WINNER, the latest report period already available at that date,
  and the cell is NA only when none is
- **AND** from `D` onward it serves that period's disclosure-of-record value ONLY
  where the period is itself the winner — i.e. no LATER report period is already
  available; otherwise the existing winner keeps being served

#### Scenario: no served record means both value and evidence are NA
- **WHEN** an instrument has no report period whose `available_from_trade_date`
  is on or before trade date `T`
- **THEN** nothing is served at `T`: the panel value AND its evidence are both
  NA — and a filing merely being pending never blanks a cell that an
  already-available period can still serve

#### Scenario: a served winner with a missing field is still NA
- **WHEN** the winning available period simply has no value for the requested
  field
- **THEN** the panel value is NA while its evidence is that record's
  availability date — an NA value is NOT evidence that nothing was served, and
  it SHALL NOT be imputed

#### Scenario: a late-arriving older period does not displace a newer one
- **WHEN** an older report period becomes available AFTER a newer report period
  is already available (or both become available the same day)
- **THEN** the panel keeps serving the newer period — availability makes a
  period ELIGIBLE, it does not make it the winner

#### Scenario: the announcement day itself does not yet serve the new filing
- **WHEN** a filing is announced on trade date `A` (post-close assumption)
- **THEN** the panel on `A` still serves the winner among the periods already
  available (NA if none), not the newly announced one — the new period's
  availability starts strictly after `A`

#### Scenario: a restated period resolves to its original disclosure
- **WHEN** a report period has both an `update_flag=0` and an `update_flag=1` row
- **THEN** that period RESOLVES to the `update_flag=0` value — a restatement
  never overrides its original
- **AND** that resolved value is what the panel serves only where the period is
  itself the winner; version selection decides WHICH VALUE a period has, never
  WHICH PERIOD is served

#### Scenario: a period recorded only as update_flag=1 is served normally
- **WHEN** a report period's only rows are `update_flag=1`, and it is the winner
  at trade date `T`
- **THEN** the panel serves it — that row IS the period's disclosure of record,
  and treating `update_flag=1` as categorically unservable would stale the cell
  by falling back to an older period

### Requirement: The panel SHALL carry machine-verifiable availability evidence from the view

The builder SHALL emit, alongside each field's value frame, an availability
evidence frame of the same shape recording the `available_from_trade_date` of the
record each cell serves, and SHALL assert `available_from <= trade_date` for every
non-NA EVIDENCE cell before returning — keyed on the evidence, not on the value.
Keying the check on non-NA values would leave exactly the served-but-NA-valued
records unchecked, so an early-announcement record whose requested field is NA
could carry future-dated evidence and survive construction.

The evidence SHALL come from the serving view's own public response, obtained in
the SAME call that yields the value — never reconstructed by the builder from
private internals, raw store reads, or inference from sampled dates and value
changes. Inferred provenance is precisely what evidence must exclude, so the view
SHALL expose availability as part of its public as-of response.

Evidence records WHICH DISCLOSURE WAS SERVED, not whether its value is present:
when a served record carries NA for the requested field, its availability date
SHALL still be recorded. Evidence is NA exactly where NO RECORD IS SERVED at
that date — i.e. no period is yet AVAILABLE. "Announced" is the wrong test:
availability starts strictly after the announcement, so on the announcement day
itself a record exists and is announced yet nothing is served, and evidence must
be NA there too — anything else would either invent future-dated evidence (which
the check below rejects) or contradict the announcement-day rule. Collapsing "served but the field is NA" into "no evidence" would also break
the shift diagnostic — a field NA in both periods would then move neither values
nor evidence, and a correct announcement-aware builder would be refused.

A panel that cannot produce this evidence SHALL be refused rather than returned —
an unverifiable panel is indistinguishable from a leaking one.

#### Scenario: a served record with an NA value still carries its availability
- **WHEN** the served disclosure has NA for the requested field
- **THEN** the cell's evidence is that record's availability date, not NA

#### Scenario: value and evidence come from one view response
- **WHEN** the builder obtains a cell's value
- **THEN** that cell's availability evidence comes from the same view response,
  not from a separate lookup or a reconstruction

#### Scenario: a panel whose evidence cannot be established is refused
- **WHEN** a field's values cannot be attributed to a dated disclosure record
- **THEN** the builder raises rather than returning the panel

#### Scenario: evidence dated after the trade date fails loud
- **WHEN** any cell's recorded `available_from` exceeds its trade date
- **THEN** the builder raises — never silently drops or repairs the cell

#### Scenario: an NA-valued cell's evidence is checked too
- **WHEN** a served record's requested field is NA but its recorded
  `available_from` exceeds the trade date
- **THEN** the builder raises — the check keys on the evidence, so an
  early-announced record does not escape by having an NA value

### Requirement: Cross-endpoint period alignment SHALL be enforced where the expression is known

The panel SHALL carry, per served field, the report period its value came from,
and — for fields a factor differences across adjacent periods — the prior
period's value with its own period and availability provenance.

Enforcement of same-period combination SHALL happen at an EXPRESSION-AWARE
point, not in the panel builder. The builder runs before any expression exists
and each terminal resolves to its own value frame, so the builder cannot tell
whether a candidate combines endpoints: masking globally would discard valid
same-endpoint expressions, and masking not at all leaves mixed-quarter ratios
reachable.

The mask SHALL be computed from the SET of endpoints the whole expression
references, and SHALL be applied AT THE TERMINALS — every referenced field's
frame is masked where those endpoints' report periods disagree, BEFORE any
operator consumes it. An expression referencing a single endpoint has a
one-element endpoint set and is therefore never masked.

Masking at any interior node is too late, in either direction:

- Below a rolling parent — `ts_mean(div_safe($revenue, $total_assets), 5)` —
  the periods may align on trade date `T` while the window still averages
  mixed-quarter ratios from earlier dates.
- Above rolling children — `add(ts_mean($revenue, 5), ts_mean($total_assets,
  5))` — the first cross-endpoint node is `add`, by which point each child has
  already aggregated its own misaligned history; alignment at `T` says nothing
  about the dates inside those windows.

Terminal-level masking is the only placement that admits no such topology: no
operator, temporal or otherwise, can observe a misaligned date.

Evaluation SHALL therefore receive the report-period provenance alongside the
value panel; the change SHALL define and wire that provenance-bearing argument
through EVERY path that evaluates an expression — search, validation, AND the
promotion entry point that drives them — since the current calls pass the value
mapping only. The promotion path builds its own `(panel, fwd)` pair and then
adjudicates, so a provenance-less promotion would either fail outright (if
provenance is mandatory) or re-compute unmasked mixed-period values and promote
on them (if optional); either way the metric that decides promotion would not
be the metric the masking defines. The panel-building adapter these entry
points call SHALL therefore carry provenance too, not just the leaf evaluators.

Endpoints are served independently by the view, so without this a ratio across
income and balance-sheet fields silently combines different quarters.

#### Scenario: a mixed-quarter cross-endpoint expression yields NA
- **WHEN** an expression references terminals from different endpoints that
  resolve to different report periods for an instrument on a trade date
- **THEN** that cell evaluates to NA rather than a mixed-quarter value

#### Scenario: a same-endpoint expression is not masked
- **WHEN** an expression references terminals from a single endpoint
- **THEN** no cross-endpoint alignment masking is applied to it

#### Scenario: a rolling operator cannot consume mixed-period inputs
- **WHEN** a cross-endpoint combination is nested under a time-series operator
  and the periods misalign on an EARLIER date inside the window
- **THEN** that earlier input is already NA when the rolling operator consumes
  it, so the current date's result is not contaminated

#### Scenario: rolling children of a cross-endpoint node are masked too
- **WHEN** each endpoint is rolled separately and combined only at the top —
  `add(ts_mean($revenue, 5), ts_mean($total_assets, 5))` — and the periods
  misalign on an earlier date inside those windows
- **THEN** the misaligned date is already NA in BOTH children's inputs, because
  the mask is applied at the terminals — the alignment holding at the current
  date does not rescue the contaminated history

#### Scenario: evaluation receives the period provenance
- **WHEN** an expression is evaluated against a fundamental panel — on the
  search path or on the validation path, whole-sample or per-segment
- **THEN** the report-period provenance reaches that evaluation alongside the
  values, and the same masking applies

#### Scenario: validation adjudicates on the masked values
- **WHEN** validation recomputes a fundamental candidate on its own segments
- **THEN** it evaluates with provenance and masking, so its metrics match what
  the masked definition produces — it neither rejects the candidate for want of
  provenance nor scores it on unmasked mixed-period values

#### Scenario: promotion runs end to end with provenance
- **WHEN** the promotion entry point builds its panel and adjudicates a
  fundamental candidate
- **THEN** provenance reaches every evaluation it triggers, and promotion
  neither fails for want of it nor promotes on unmasked values

#### Scenario: an adjacent-period difference has its own provenance
- **WHEN** a factor differences a field across adjacent report periods
- **THEN** the prior period's value carries its own period and availability
  evidence, and the difference is NA when the adjacent period is absent

### Requirement: The terminal names and the view's field names SHALL be bridged explicitly

A registered fundamental terminal SHALL have a defined route to the charter
field it stands for. The GP side and the view side do not share a name space for
FIELDS either: the evaluator resolves only terminals beginning with `$` and
looks them up verbatim in the panel mapping, while
`FinancialPITDataView.as_of` rejects `$revenue` as an unknown charter field and
accepts only the bare charter name `revenue`.
Registering the terminals does not close this gap: a registered `$revenue` still
has no defined route to the view's `revenue`.

The change SHALL therefore define the mapping between registered fundamental
terminals and charter field names as part of the contract — the bridge accepting
charter names and emitting panel keys in terminal form — and SHALL test that a
GP-GENERATED terminal resolves end to end through the bridge, not merely that
the registry and the view each work in isolation.

The fundamental terminals SHALL live OUTSIDE the legacy default feature set and
SHALL be activated only by an explicit fundamental whitelist. Appending them to
the legacy default is the obvious implementation and it silently changes
existing campaigns: the PIT adapter's default field list IS that set, so every
run that leaves its fields unspecified would start requesting non-qlib fields,
and a search with no terminal restriction would be free to breed research-only
terminals inside a legacy campaign. The default set SHALL remain exactly what it
is today, pinned by a regression.

Being opt-in is not enough on its own: the search's point-mutation
replacement pool is built from the LEGACY terminal groups and then intersected
with the campaign whitelist, so for a whitelist containing only fundamental
terminals that intersection is EMPTY — and the resulting error is caught and
the expression returned unchanged, which disables point mutation for the entire
campaign SILENTLY. The change SHALL therefore migrate the replacement pool to
be derived from the registered terminals of the same type rather than from the
legacy groups alone, and SHALL cover it with a synthetic-whitelist regression.
A search operator that degrades to a no-op without saying so is worse than one
that fails.

A whitelist MAY legitimately admit only ONE terminal of a type, in which case no
replacement exists and no mutation is possible. That case SHALL be
DISTINGUISHED from the defect above and SHALL be OBSERVABLE — recorded and
reported as "no replacement available under this whitelist" — never an
indistinguishable silent no-op. The mutation requirement therefore applies when
the whitelist admits at least two terminals of the target type.

#### Scenario: a generated terminal resolves through the bridge to a view field
- **WHEN** GP generates an expression referencing a registered fundamental
  terminal
- **THEN** evaluating it against the bridged panel yields that charter field's
  as-of values — the terminal form is accepted by the evaluator and the charter
  form is what reaches the view

#### Scenario: point mutation still mutates under a fundamental whitelist
- **WHEN** a campaign whitelists at least two fundamental terminals of a type
  and point mutation targets one of them
- **THEN** it produces a different whitelisted terminal of the same type — it
  does not silently return the expression unchanged

#### Scenario: a single-terminal whitelist reports the no-op instead of hiding it
- **WHEN** the whitelist admits only one terminal of the target type, so no
  replacement exists
- **THEN** the engine records and reports that no replacement was available
  under this whitelist, rather than returning the expression unchanged
  indistinguishably from a successful mutation

#### Scenario: the legacy default terminal set is unchanged
- **WHEN** a run leaves its fields unspecified, or a search runs with no
  terminal restriction
- **THEN** it sees exactly the pre-existing default terminals — the fundamental
  group is absent unless explicitly whitelisted

#### Scenario: an unmapped terminal fails loud
- **WHEN** a fundamental terminal has no charter field mapping
- **THEN** the bridge refuses rather than emitting a panel key the evaluator
  would later fail to resolve, or silently omitting the field

### Requirement: Prior-period values SHALL be reachable by generated expressions

Adjacent-period differencing SHALL be expressible by GP, not merely carried in
the panel. The evaluator can consume only registered terminal keys present in
the value mapping, so prior-period values held as `__prior` side-objects are
unreachable from any AST: asset growth and the pure-balance-sheet accrual — the
starter factors this change must run end to end — cannot be written at all.

The change SHALL make prior periods first-class, either as typed prior-period
terminals or as an adjacent-report-period operator, SHALL include them in the
panel keys and in the campaign's frozen terminal/operator whitelist, and SHALL
test both that GP can GENERATE such an expression and that it EVALUATES to the
adjacent-period difference.

#### Scenario: GP can generate and evaluate an adjacent-period difference
- **WHEN** the fundamental terminal/operator set is in force
- **THEN** an adjacent-report-period difference is generatable by GP and
  evaluates to the difference between the served period and its prior period

#### Scenario: a missing adjacent period yields NA
- **WHEN** the prior report period is absent for an instrument on a trade date
- **THEN** the difference is NA — never imputed from a further-back period
  without saying so

### Requirement: The run-bound data contract SHALL record everything needed to rebuild the panel

A run's persisted data contract SHALL carry every DATA input the fundamental
panel is built from, so a later stage can reconstruct the SAME panel from the
run alone. The BUILDER itself is the one exception, and it is not a loophole:
it arrives by injection (see the injection-seam requirement) because the
isolation gate forbids the alternative, and it is bound instead by its recorded
identity. Data comes from the contract; the builder comes from the seam; both
are verified. The promotion path passes only its persisted data config to the panel
builder, while the view requires a financial store location, a calendar
identity, and the financial-issuer exclusion set at construction — none of
which the current contract records. Without them the panel can only be rebuilt
through an unrecorded external or global dependency, and a promotion that
re-derives its own inputs is adjudicating on data nobody can prove matches what
was mined.

The contract extension SHALL be covered by the same load / hash / migration
handling as the fields already in it — a run hash that ignores the financial
inputs would call two different panels the same run.

Recording the VALUES is not enough: paths and identities survive an in-place
refresh of the store or the calendar artifact unchanged, so a config-only hash
still calls two different panels the same run. The change SHALL therefore
record CONTENT fingerprints of the financial store and the calendar at mining
time and re-verify them at promotion, refusing on drift — the same treatment
the PIT inputs already get, and refusing likewise a fundamental run that
predates fingerprint recording rather than promoting on unverifiable
provenance.

One fingerprint plus a promotion-time recheck is not sufficient. The
fingerprints SHALL be taken BEFORE any panel read and taken AGAIN after the work
that reads it, and the path SHALL refuse BEFORE persisting any artifact if they
differ — the mining path already does exactly this for the PIT inputs, for
exactly this reason. A refresh DURING the panel build would otherwise let the
run record the NEW bytes' identity for a pool mined on the OLD (or mixed) reads,
after which every specified check passes while promotion rebuilds a different
panel.

This two-snapshot rule SHALL apply to PROMOTION as well, not to mining alone: a
single recheck on entry leaves promotion's own rebuild-and-evaluate window
unprotected, so a refresh during it yields results derived from mixed or
refreshed bytes while the recorded identity still matches. Promotion SHALL
fingerprint immediately before its panel read and again after evaluation and
BEFORE it creates the production directory, refusing on any difference — the
refusal must land before production is touched at all.

#### Scenario: promotion rebuilds the mined panel from the run alone
- **WHEN** promotion loads a run and rebuilds its fundamental panel
- **THEN** every DATA input comes from the run's own persisted contract, with no
  ambient path, environment default, or global fallback — the builder arrives by
  injection and is bound by its recorded identity instead

#### Scenario: the run hash covers the fundamental inputs
- **WHEN** two runs differ only in a fundamental panel input (store, calendar,
  or exclusion set)
- **THEN** their run hashes differ

#### Scenario: a refresh during promotion's own read refuses before production
- **WHEN** the financial store or calendar changes while promotion is
  rebuilding or evaluating the panel
- **THEN** promotion refuses before creating the production directory — nothing
  derived from mixed bytes is written and no version label is consumed

#### Scenario: a refresh during the panel build refuses before any artifact
- **WHEN** the financial store or calendar changes between the pre-build
  fingerprint and the post-mining one
- **THEN** mining refuses before persisting anything — neither identity would
  describe the pool that was mined

#### Scenario: an in-place refresh of the store is caught at promotion
- **WHEN** the financial store or the calendar artifact is refreshed in place
  between mining and promotion, leaving every recorded path and identity
  unchanged
- **THEN** promotion refuses — the re-verified content fingerprint no longer
  matches the one recorded at mining time

#### Scenario: a run with no recorded fingerprints is refused
- **WHEN** a fundamental run predates fingerprint recording
- **THEN** promotion refuses rather than proceeding on unverifiable provenance

#### Scenario: a run missing the fundamental inputs fails loud
- **WHEN** a fundamental run's contract lacks a required panel input
- **THEN** loading it raises rather than filling the gap from a default

### Requirement: The fundamental panel SHALL reach mining and promotion through an injection seam

The panel builder SHALL be supplied TO mining and promotion from the script
layer, never imported by them. The panel-building adapter both paths call lives
in the factor-mining package, while the builder and the serving view live in the
research package, and the isolation gate rejects any import of the research
package from `src/` outside it. Requiring that adapter to reconstruct a
fundamental panel by itself — with the gate unchanged, as this change promises —
therefore has NO implementable route: importing the builder breaks the gate, and
not importing it leaves mining and promotion unable to build the panel at all.

Both entry points SHALL therefore accept an injected panel factory, defaulting to
today's behaviour when none is given, and the fundamental campaign SHALL supply
it from `scripts/research/` — the one layer permitted to see both sides, and the
layer this change already uses for orchestration. The injected factory SHALL
consume the run's persisted contract, so the reconstruction guarantee above is
preserved rather than traded away.

The seam SHALL NOT become an unverifiable degree of freedom. Injection means
the two entry points can receive DIFFERENT callables while every recorded config
value, data digest, and store/calendar content fingerprint still matches — so
promotion could rebuild a semantically different panel and nothing would
notice. The run SHALL therefore record an identity for the factory actually
used, and promotion SHALL verify it against the factory it is given, refusing
on mismatch and refusing a run that predates factory-identity recording — the
same treatment the data fingerprints get, because a builder swap moves the
panel exactly as a data swap does.

That identity SHALL NOT be self-declared metadata. A name plus a version string
is advertised BY the factory, so two semantically different factories can
announce the same pair and pass — which recreates precisely the undetectable
swap this exists to close. The identity SHALL instead be either:

- a digest computed by TRUSTED code (not the factory) over the factory's frozen
  implementation and its behaviour-affecting dependencies; or
- the digest of the factory's deterministic PROVENANCE-BEARING OUTPUT,
  recomputed at promotion from the run's recorded inputs and compared. That
  digest SHALL cover EVERY behaviour-affecting output — values, availability
  evidence, AND the report-period provenance (current and prior). Omitting the
  periods would let two factories agree on values and evidence yet return
  different period frames, pass the identity check, and adjudicate the same
  expressions under DIFFERENT terminal-level alignment masks.

The output digest is the stronger of the two because it binds BEHAVIOUR rather
than a claim about behaviour; where both are recorded, a mismatch in either
SHALL refuse.

The output digest alone is NOT sufficient when promotion runs on an EXTENDED
window. The governed `validation_end_date` extension lets promotion evaluate
dates the mining run never covered, and a digest recomputed over the ORIGINAL
window says nothing about them: a swapped callable could reproduce the mining
window's values, evidence, and periods exactly, pass, and then diverge on the
added out-of-sample dates — the very dates the extension exists to adjudicate.
An extended promotion SHALL therefore be bound by ONE of exactly two things:

- the implementation/dependency digest; or
- a baseline persisted BEFORE promotion runs by an independently trusted
  process — the extension together with its EXPECTED effective-window output
  digest.

What is excluded is the third thing, and it is the one the mining-window digest
degrades into: a baseline the promoted factory itself produced.
`validation_end_date` arrives only at promotion, so mining can only ever have
persisted a digest for its ORIGINAL window — comparing an effective-window
digest against that one necessarily differs, and hashing the promoted factory's
own effective output lets the swapped callable AUTHENTICATE ITSELF, a
self-issued certificate. Either admissible route works because its baseline
comes from somewhere promotion did not produce.

The end-to-end test SHALL exercise this seam — mining and promoting a
fundamental run through the real factory — not a stand-in that bypasses it.
Exercising one factory in one test does not constrain the callable either entry
point receives in a real run; only the recorded identity does.

#### Scenario: mining and promotion take the builder as an argument
- **WHEN** a fundamental campaign runs
- **THEN** the panel factory is injected from the script layer, and neither
  entry point imports the research package

#### Scenario: the isolation gate stays green with no re-signing
- **WHEN** the isolation gate runs over the change
- **THEN** it passes unmodified — no `src/` module outside the research package
  imports it

#### Scenario: a swapped factory is caught at promotion
- **WHEN** promotion is given a panel factory whose identity differs from the
  one recorded at mining time
- **THEN** it refuses rather than adjudicating on a panel built by a different
  builder

#### Scenario: a factory cannot pass by declaring the right name
- **WHEN** a different factory implementation advertises the same identifier
  and version as the recorded one
- **THEN** promotion still refuses — the identity is derived from the frozen
  implementation or from the rebuilt provenance-bearing output, not from what
  the factory says about itself

#### Scenario: an extended promotion needs one of the two admissible bindings
- **WHEN** promotion runs with a validation-window extension and no
  pre-authorized baseline exists
- **THEN** the factory is bound by its implementation/dependency digest — a
  match on the mining window's output digest alone does not pass

#### Scenario: a pre-authorized baseline is sufficient on its own
- **WHEN** an independently trusted process persisted the extension and its
  expected effective-window output digest before promotion ran
- **THEN** promotion may verify against that baseline without an
  implementation/dependency digest

#### Scenario: a factory cannot certify its own extended output
- **WHEN** the only evidence offered for an extended promotion is a digest of
  the promoted factory's own effective-window output
- **THEN** promotion refuses — for an extended window the only admissible
  output-digest baseline is the independently trusted pre-authorized one;
  mining cannot supply it, since mining could only ever have covered the
  original window (the implementation/dependency digest remains the separate
  admissible route)

#### Scenario: a run with no recorded factory identity is refused
- **WHEN** a fundamental run predates factory-identity recording
- **THEN** promotion refuses rather than assuming the builder is unchanged

#### Scenario: existing callers are unaffected
- **WHEN** no factory is injected
- **THEN** mining and promotion behave exactly as they do today

### Requirement: Fundamental pools SHALL NOT reach production materialization until that path carries provenance

A pool containing fundamental terminals SHALL NOT be materializable into the
production factor directory while that path lacks provenance. The production
materialization path evaluates a promoted pool's expressions against a qlib
panel with no period provenance, so a fundamental pool arriving
there would either fail on unresolvable financial terminals or — if a panel
were injected — materialize WITHOUT the terminal-level alignment mask that
decided its promotion. A factor adjudicated under one definition and served
under another is the same defect class as adjudicating on a different metric.

Wiring that consumer is OUT OF SCOPE for this change, which delivers the bridge
and its defenses only. Therefore this change SHALL make the boundary a
MACHINE-ENFORCED REFUSAL, not a documented caveat: writing a pool containing
fundamental terminals into the production directory SHALL fail loud, naming the
follow-up change that lifts the block. A note in a document is not a boundary —
the refusal must be executable and tested.

The refusal SHALL be enforced AT THE WRITER — the promotion path that writes
into the production directory — BEFORE ANY part of that write, INCLUDING the
creation of the target directory. Refusing merely before the pool is saved is
not enough: the target directory is created before the survivor pool is
assembled, so a refusal placed after it leaves an empty production version
directory behind, and the next attempt then fails on the directory already
existing — a refused promotion would have mutated production and permanently
consumed that version label. The regression SHALL assert the target path itself
is ABSENT after a refusal, not merely that no pool file was written.

Placing the check only in the consumer is later still: promotion would have
written a fundamental pool into production and the refusal would fire at some
subsequent materialization, if ever. A consumer-side check MAY remain as
defense in depth, but it does not discharge this requirement.

#### Scenario: a fundamental pool is refused before the production write
- **WHEN** promotion is about to write a survivor pool containing fundamental
  terminals into the production directory
- **THEN** it fails loud and names what must land first

#### Scenario: a refused promotion leaves production untouched
- **WHEN** promotion refuses a fundamental pool
- **THEN** the target version directory does not exist afterwards — the version
  label is not consumed and a later retry is not blocked by a leftover
  directory

#### Scenario: a price-volume pool is unaffected
- **WHEN** a pool contains no fundamental terminals
- **THEN** production materialization proceeds exactly as today

### Requirement: The universe mask SHALL apply the same financial exclusion as the panel

The membership mask that defines the coverage denominator SHALL exclude exactly
the issuers the fundamental panel excludes. The view omits every configured
financial issuer, while the mask is built independently from the qlib
membership frame, which still marks those issuers as members. Their cells then
sit in the denominator as permanently uncovered, depressing measured coverage —
which feeds candidate admission and fitness. A candidate would be judged on a
denominator that counts issuers its own data source refuses to serve.

The exclusion applied SHALL be the run's PERSISTED exclusion set, not one
re-derived at mask-construction time, so the mask and the panel cannot drift
apart between mining and promotion.

#### Scenario: excluded issuers leave the coverage denominator
- **WHEN** coverage is measured for a fundamental run whose view excludes
  financial issuers
- **THEN** those issuers' cells are outside the denominator — not counted as
  uncovered members

#### Scenario: a price-volume run's denominator is unchanged
- **WHEN** a run has no financial exclusion configured
- **THEN** the mask is exactly what it is today

### Requirement: The panel SHALL emit one frozen instrument namespace aligned with the GP inputs

The panel's instrument labels SHALL be emitted in the SAME namespace as the GP
panel and forward-return frames it is joined against. The view normalizes
instruments to the store's native `ts_code` form (`600000.SH`) while the
factor-mining panels carry qlib labels (`SH600000`); the two do not intersect,
and the repository already treats such a mix as a hard error. A bridge that
emits the view's namespace unchanged therefore produces a panel that silently
fails to join.

The chosen namespace SHALL be asserted by a test comparing the panel's columns
against the forward-return frame's, exactly — not merely for non-empty overlap.

#### Scenario: panel columns match the forward-return columns exactly
- **WHEN** a fundamental panel is built for a universe
- **THEN** its instrument labels equal the forward-return frame's labels

#### Scenario: an unconverted namespace is caught by the alignment test
- **WHEN** the panel is emitted in the view's `ts_code` namespace
- **THEN** the alignment test fails rather than the mismatch surfacing later as
  an empty join

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

Canaries SHALL each corrupt an invariant the builder can COMPUTE from its own
inputs. Two classes of candidate canary are explicitly excluded because the
builder cannot decide them, and a test asserting them would only be validating
its own mock:

* one that merely OMITS evidence — a forward-return-derived value carrying
  copied, plausible evidence satisfying `available_from <= trade_date` is
  indistinguishable from a real filing by the availability assertion, so such a
  canary passes while the same semantic leak survives;
* one that mislabels a value with ANOTHER period's provenance — with no
  independent record identity the builder cannot detect the lie, and if the
  single-response path is bypassed the builder never runs at all. Value-to-
  provenance correspondence is an invariant OF THE VIEW, guarded by the
  canonical PIT battery, and SHALL NOT be restated as a panel-level canary.

The guarded invariants are those the builder can decide:

* **early announcement** — a cell whose evidence exceeds its trade date,
  violating `available_from <= trade_date`;
* **non-monotone availability** — an instrument whose evidence series decreases
  across trade dates; as-of carry-forward only advances to newer announced
  periods, so a decrease is a structural signature of back-filled information,
  decidable without interpreting the values.

A canary surviving as far as factor-pool admission SHALL be a hard failure of the
guarding test suite.

#### Scenario: the early-announcement canary fails construction
- **WHEN** a store record is served on a date before its announcement
- **THEN** panel construction raises

#### Scenario: back-filled information breaks availability monotonicity
- **WHEN** an instrument's availability evidence decreases from one trade date to
  the next
- **THEN** panel construction raises

#### Scenario: value-to-provenance correspondence is left to the view's battery
- **WHEN** the panel-level canary suite is defined
- **THEN** it does not include a mislabelled-provenance canary, which the
  builder cannot decide

### Requirement: The panel SHALL demonstrably consume the announcement date

A shift-sensitivity diagnostic SHALL rebuild the panel with every effective
announcement date shifted later by `N` trading days and compare against the
baseline.

The assertion SHALL be on WHICH RECORD IS SERVED, not on a combined hash, and it
SHALL be framed per SAMPLED DATE in terms of the WINNING record, not per
disclosure in terms of an interval.

For each sampled trade date the diagnostic SHALL compute, source-side, two
winners under the canonical as-of rule — the latest `report_period` whose
availability is on or before that date, after disclosure-of-record selection:
`W_base` under the ORIGINAL availability dates and `W_shift` under the SHIFTED
ones. Where `W_base` and `W_shift` differ, the baseline panel SHALL serve
`W_base` and the shifted rebuild SHALL serve `W_shift` (or NA where no record is
yet available). The served report-period / record provenance is what must move;
the diagnostic SHALL REFUSE when it does not.

A values-plus-evidence hash is NOT sufficient on its own, and the reason is the
defect this exists to catch: a report-period-keyed builder can keep serving the
SAME (still-future) disclosure and its value while copying that record's newly
shifted `available_from_trade_date` into the evidence. The evidence bytes then
change, the combined hash changes, and an announcement-BLIND value selection
passes. Evidence movement can be copied; a change in WHICH PERIOD IS SERVED
cannot be, without actually consuming the announcement date.

Values MAY legitimately be equal across the shift — a delayed filing may repeat
the preceding period's value, or the field may be NA in both — so value
inequality SHALL NOT be required. The hash MAY still be recorded as a
convenience, but it SHALL NOT be the criterion.

"Falls within the measured window" is NOT the right precondition: a shifted
filing need not touch any requested field, endpoint, or instrument. A
revenue-only panel correctly ignores a balance-sheet-only filing, so shifting
that filing moves neither values nor evidence — and an unconditional rule would
REFUSE a builder that is behaving exactly right. The diagnostic SHALL therefore
establish RELEVANCE first, and relevance SHALL mean that the WINNER MOVES: for
some requested field there is at least one sampled trade date whose `W_base` and
`W_shift`, as defined above, differ.

A per-record interval test is NOT equivalent and is wrong in both directions.
The view serves only the winner at each date, so when two disclosure-of-record
periods have overlapping shift intervals — an annual and a Q1 report becoming
available the same day, say — a per-record test marks BOTH relevant, yet the
baseline never serves the annual one at all; asserting that the baseline serves
every crossing record would REFUSE a correct builder. Comparing winners asks
exactly the question the serve-rule answers.

The source rows SHALL be reduced by the CANONICAL DISCLOSURE-OF-RECORD
SELECTION before either winner is computed — per `report_period`: prefer the
`update_flag=0` row, fall back to `update_flag=1` when the period has NO
`update_flag=0` row, and within the selected version take the
EARLIEST-ANNOUNCED disclosure.

What is discarded is precisely the NON-SELECTED rows: an `update_flag=1` row
that sits ALONGSIDE a preferred `update_flag=0` row for the same period, and
later re-announcements of an already-selected version. `update_flag=1` is NOT
categorically excluded — a period whose only rows are `update_flag=1` IS its own
disclosure of record and IS served, and dropping such periods would erase recent
filings from relevance entirely, letting the diagnostic report INCONCLUSIVE for
an announcement-blind builder. Letting a non-selected row move a winner would
demand movement a correct panel must not make, and the diagnostic would REFUSE
the builder for obeying the serve-rule; dropping a selected one disarms the
diagnostic. Both errors are avoided by applying the canonical selection exactly
as the view does.

Both winners SHALL be computed from the SOURCE DATA — the store's disclosure
dates, the shift, the requested fields, and the sampled calendar — and SHALL NOT
consult the rebuilt panel. Defining relevance as "the baseline serves it and the
shifted rebuild does not" is circular against exactly the defect this diagnostic
exists to catch: an announcement-BLIND builder (one keyed on report period)
keeps serving the same disclosure after the shift, so relevance would never be
established, the diagnostic would return INCONCLUSIVE, and the
unmoved-served-record refusal below would never fire. The panel output SHALL be used only for the
sensitivity assertion, never to decide whether the assertion applies.

Merely being served on some sampled date is not enough either — if a
disclosure's original and shifted availability dates both precede the first
sampled date, or fall between the same two sparse sample dates, the winner is
the same on every sampled date and no cell can move, and an unconditional rule
would REFUSE a correct builder. Where no sampled date has a moving winner the
diagnostic SHALL report INCONCLUSIVE rather than REFUSE — an inconclusive diagnostic is a signal to
widen the sample or use the deterministic fixture, not a verdict on the
builder. Running the assertion on a
deterministic relevant-record fixture satisfies this by construction.

Given a relevant shifted disclosure, a served record that does not move SHALL
be treated as proof that the builder does not consume the announcement date at
all (e.g. it keys on report period) and SHALL REFUSE — a behavioural check that
no amount of correct-looking code can substitute for.

#### Scenario: an irrelevant shifted filing does not refuse the builder
- **WHEN** every shifted disclosure belongs to a field, endpoint, or instrument
  the requested panel does not serve
- **THEN** the diagnostic reports INCONCLUSIVE rather than REFUSE — an
  unmoved served record is expected here, not evidence of an announcement-blind
  builder

#### Scenario: a shift that never moves a winner is inconclusive
- **WHEN** a shifted disclosure is of a requested field, but the source-side
  winner is identical under original and shifted availability at every sampled
  date
- **THEN** the diagnostic reports INCONCLUSIVE — no sampled cell could have
  moved, so an unmoved served record says nothing about the builder

#### Scenario: a moving winner must change which record is served
- **WHEN** at a sampled trade date the source-side winners differ — `W_base`
  under original availability, `W_shift` under shifted availability
- **THEN** the baseline serves `W_base` and the shifted rebuild serves `W_shift`
  (or NA if none is yet available), and a served record that does not move
  REFUSES

#### Scenario: an overlapping filing that never wins is not asserted on
- **WHEN** two disclosure-of-record periods have overlapping shift intervals —
  an annual and a Q1 report becoming available the same day — so the annual one
  is never the winner at any sampled date
- **THEN** the diagnostic asserts nothing about it; only the dates where the
  winner itself moves are asserted on

#### Scenario: an announcement-blind builder cannot escape via INCONCLUSIVE
- **WHEN** the builder keys on report period, so the shifted rebuild keeps
  serving the same disclosure as the baseline
- **THEN** relevance is still established from the source data, the diagnostic
  reaches its assertion, and the unmoved served record REFUSES — it does not
  report INCONCLUSIVE

#### Scenario: copied evidence does not satisfy the assertion
- **WHEN** a rebuild keeps serving the same disclosure but stamps it with the
  shifted availability date, so values-plus-evidence bytes differ
- **THEN** the diagnostic still REFUSES — the served report period did not move,
  and a changed hash is not the criterion

#### Scenario: a non-selected row cannot establish relevance
- **WHEN** the only source row whose shift would move anything is one the
  canonical selection discards — an `update_flag=1` row alongside a preferred
  `update_flag=0` row for the same period, or a later re-announcement of the
  selected version
- **THEN** the diagnostic reports INCONCLUSIVE — a correct panel is unchanged
  there, and demanding movement would refuse it for obeying the serve-rule

#### Scenario: an update-flag-1-only period still participates in relevance
- **WHEN** a report period's only rows are `update_flag=1`, so that row IS its
  disclosure of record
- **THEN** it takes part in the winner computation like any other selected
  record — it is NOT discarded as a restatement, and a shift that moves the
  winner through it establishes relevance

The IC-series assertion SHALL be required only on a DETERMINISTIC FIXTURE
constructed so that the shift necessarily alters evaluated values or their
cross-sectional ranks, with a stated tolerance. A correct announcement-aware
builder can change panel bytes WITHOUT moving a candidate's IC — the shifted
disclosures may fall between sampled evaluation dates, preserve every rank, or
feed a constant/all-missing candidate — so requiring both unconditionally would
refuse valid implementations.

#### Scenario: an announcement-insensitive builder is refused
- **WHEN** a shifted rebuild whose relevance is established keeps serving the
  same report period at the moving-winner dates
- **THEN** the diagnostic REFUSES, reporting that the announcement date is unused

#### Scenario: an unchanged value with a moved served period is not a failure
- **WHEN** at a moving-winner date the shifted rebuild serves `W_shift` but that
  period repeats the same value (or the field is NA in both), leaving values
  identical
- **THEN** the diagnostic does not refuse — the served record moved, which is
  what is asserted; value inequality is not required

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
