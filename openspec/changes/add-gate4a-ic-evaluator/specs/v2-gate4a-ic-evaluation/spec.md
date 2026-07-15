# Gate-4A IC evaluation (quality_profitability_v1)

## ADDED Requirements

### Requirement: Every decision-level 4A run SHALL be gate-accepted before ignition

The evaluator SHALL invoke `scripts/research/gate3_prereg_gate.py` with the
requested candidate and that candidate's FROZEN per-candidate dev run-config
stub, and SHALL refuse to evaluate unless the gate prints ACCEPT. The full
gate output SHALL be archived inside the run artifact. A ledger pre-run
entry SHALL be committed before the run; a post-run entry SHALL record the
results verbatim (including negatives).

#### Scenario: gate refusal aborts the run
- **WHEN** the pre-registration gate refuses (dirty tree, window drift,
  binding mismatch, manifest mismatch, or any other refusal)
- **THEN** the evaluator aborts without computing any IC and surfaces the
  gate's refusal reason

### Requirement: Rebalance stamps SHALL mirror the canonical fold_phase schedule

Fold geometry SHALL be derived from the gated frozen run-config chain
(never hardcoded). Within each dev fold's test window the stamp schedule
SHALL be the in-window trading days `[phase::cadence]` (cadence/phase from
the frozen chain) with the LAST in-window day excluded (its lag-1
execution day is out of window — the canonical fillable rule). Stamps
whose forward horizon contains zero trading days SHALL be dropped and
counted. The count of primary stamps SHALL equal the fold count or the
run SHALL abort.

#### Scenario: long quarter carries a tail stamp
- **WHEN** a dev fold's test window has more than `cadence` fillable
  trading days (e.g. a 66-trading-day Q3)
- **THEN** the schedule keeps a tail stamp at position `cadence`, whose
  cross-section is fully recomputed at that stamp's own dates

#### Scenario: frozen dev window yields one primary stamp per fold
- **WHEN** the evaluator derives folds from the frozen dev config
  (overall 2018-01-01..2024-12-31, 24m/3m/3m step 3m)
- **THEN** 19 dev folds are derived and exactly 19 primary stamps feed
  the registered aggregate

### Requirement: The registered metric SHALL aggregate primary stamps only

`rank_ic_mean`, `ic_ir` and companion statistics SHALL be computed over
the PRIMARY stamps only (one per fold — the quarterly horizon that IS the
frozen `ic_forward_horizon: primary_holding_period`). Tail-stamp ICs
(shorter horizons) SHALL be evaluated and reported as diagnostics and
SHALL NOT be mixed into the registered series. No per-candidate
significance threshold SHALL be applied — fold-level series are persisted
for the frozen full-batch FWER adjudication.

#### Scenario: tail ICs stay diagnostic
- **WHEN** a run evaluates folds containing tail stamps
- **THEN** the artifact reports tail ICs in a separate diagnostics block
  and the registered aggregate is computed from exactly the primary series

### Requirement: Forward returns SHALL be fold-contained close-to-close with lag 1

Each stamp's forward return SHALL be `close[execution day] -> close[next
stamp's execution day, or the fold's last trading day for the final
stamp]`, where the execution day is the stamp plus one trading day. No
dev-fold computation SHALL consume any price after the frozen
end_boundary. Names with no close on the execution day SHALL be dropped
and counted; names losing quotation mid-horizon SHALL use the last
available close and be counted; names with an entry close but zero
post-entry closes SHALL mark a 0.0 return and be counted. None of these
outcomes SHALL be silent.

#### Scenario: holdout prices are untouchable
- **WHEN** any dev fold/stamp computation would require a price after the
  frozen dev end_boundary
- **THEN** the design makes it structurally impossible (horizons end at
  the fold's last in-window trading day at the latest)

### Requirement: The cross-section SHALL be filtered in four counted layers

At each stamp the universe SHALL be: PIT csi300 membership on the signal
day (bundle instruments intervals, delisted included), MINUS the signed
financial-sector exclusion (live graded L/D/P stock_basic fetch,
fail-loud, Step-A rule), MINUS names ST/*ST on the execution day (PIT
namechange reconstruction), MINUS names microstructure-unavailable on the
execution day (canonical mask: suspension with carried close, one-price
lock). Each layer's removals SHALL be counted per stamp.

#### Scenario: ST and untradeable names never enter the IC
- **WHEN** a csi300 member is ST on the execution day, or suspended with
  a carried close, or one-price locked
- **THEN** it is excluded from factor ranking, size deciles and forward
  returns for that stamp, and the exclusion is counted

### Requirement: Size deciles SHALL come from as-of total_mv with a staleness cap

The size ranking SHALL use `$total_mv` from the canonical PIT bundle:
the last available value at a trading date <= the signal day, no older
than 20 trading days (DP1). Names beyond the cap or without a usable
value SHALL be dropped from that stamp and counted. A CSI300-ever member
whose MEMBERSHIP interval overlaps the dev span but which has ZERO
total_mv observations inside that overlap SHALL abort the run (DP3 —
bundle/registry inconsistency, never a silent shrink); members whose
intervals never overlap the span (e.g. pre-span delistings) legitimately
have no panel data and are exempt.
The signal SHALL be the factor's rank within its size decile, using only
data available on the signal day (as_of_or_earlier_only).

#### Scenario: stale market cap never ranks
- **WHEN** a name's latest total_mv observation is older than the cap on
  a signal day
- **THEN** the name is dropped from that stamp's deciles and counted

#### Scenario: a bins-less overlapping member aborts
- **WHEN** a CSI300-ever member's membership interval overlaps the dev
  span but the panel has no total_mv observation inside that overlap
- **THEN** the run fails loud naming the member

#### Scenario: a pre-span delisting is exempt
- **WHEN** an ever-member's membership interval ends before the dev span
  begins
- **THEN** its absence from the panel does not abort the run

### Requirement: All data roots SHALL derive from the gated frozen config chain

Every data root SHALL be taken from the gated frozen run-config chain —
the qlib bundle (provider_uri), trading calendar, membership file, the
namechange snapshot (namechange_path) and the delisted registry
(delisted_registry_path) are frozen literals with NO CLI override. Price/size panels and the
microstructure-mask OHLCV fetch SHALL route through PITDataProvider (the
post-delist mask), never a raw `D.features` read. A chain missing any of
these keys SHALL abort the run.

#### Scenario: an unregistered bundle cannot ride a gate accept
- **WHEN** an operator attempts to point the evaluator at any data root
  other than the frozen chain's literals
- **THEN** no such parameter exists, and a chain missing the keys refuses

### Requirement: Cross-endpoint inputs SHALL be report-period aligned

The evaluator SHALL, for candidates whose inputs span multiple statement
endpoints, obtain per-endpoint report-period metadata from
`FinancialPITDataView.as_of(include_report_periods=True)` and SHALL set
the factor to NA (counted per stamp) for any name whose queried endpoints
serve DIFFERENT report periods. Names with a missing endpoint period stay
governed by the frozen missing policy (fields already NA). The view's
default output SHALL remain byte-identical when the metadata is not
requested.

#### Scenario: a lagging balancesheet cannot mix quarters
- **WHEN** income serves Q2 while balancesheet still serves Q1 for a name
  on a signal day
- **THEN** that name's factor is NA for that stamp and the misalignment
  is counted

### Requirement: Corrupted statistics SHALL abort, never vanish

The run SHALL abort with a named reason on any corrupted statistic: a
non-finite fold/stamp correlation (constant signal or corrupted price
panel), a cross-section smaller than 30 names, or a primary-stamp count
different from the fold count.
Every artifact SHALL echo the pinned semantics, the config chain sha256,
the financial-exclusion provenance, per-layer filter counts and the
fold-level IC series consumed by the batch FWER step.

#### Scenario: constant returns abort
- **WHEN** a stamp's forward-return vector is constant (correlation NaN)
- **THEN** the run aborts naming the stamp instead of silently skipping it
