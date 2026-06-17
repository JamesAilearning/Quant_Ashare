## MODIFIED Requirements

### Requirement: Canonical exchange config SHALL bound-check cost-model fields

The `CanonicalExchangeCostModel` SHALL enforce bounds on
`commission_rate`, `slippage_bps`, and `min_cost`. The CN-market
stamp tax SHALL be carried by `stamp_tax_schedule` (see "ADDED
Requirements" below), NOT by a single scalar `stamp_tax_bps`. Out-
of-bound values SHALL be rejected at construction.

#### Scenario: commission_rate above cap is rejected
- **WHEN** a caller supplies `commission_rate=0.5`
- **THEN** a `CanonicalBacktestContractError` is raised during construction
- **AND** the error message names the offending field and the cap

#### Scenario: negative min_cost is rejected
- **WHEN** a caller supplies `min_cost=-1.0`
- **THEN** a `CanonicalBacktestContractError` is raised during construction

#### Scenario: legacy stamp_tax_bps kwarg is rejected at construction
- **WHEN** a caller supplies `stamp_tax_bps=10.0` to
  `CanonicalExchangeCostModel(...)`
- **THEN** a `TypeError` is raised (the field no longer exists)
- **AND** the error message guides the caller toward
  `stamp_tax_schedule` and the default constant

## ADDED Requirements

### Requirement: Stamp tax SHALL be represented as a time-ordered schedule

The `CanonicalExchangeCostModel.stamp_tax_schedule` field SHALL be a
non-empty tuple of `StampTaxScheduleEntry` instances, each carrying
an `effective_from: date` and a `bps: float`. Entries SHALL be
strictly monotone in `effective_from` (ascending, no duplicates).
Each `bps` value SHALL be in `[0, STAMP_TAX_BPS_MAX]`.

#### Scenario: well-formed schedule is accepted
- **WHEN** a caller constructs a schedule
  `((2008-09-19, 10.0), (2023-08-28, 5.0))`
- **THEN** `CanonicalExchangeCostModel(stamp_tax_schedule=...)`
  constructs cleanly

#### Scenario: empty schedule is rejected
- **WHEN** a caller passes `stamp_tax_schedule=()`
- **THEN** a `CanonicalBacktestContractError` is raised at construction
- **AND** the message identifies the field as the offender

#### Scenario: non-monotone schedule is rejected
- **WHEN** a caller passes a schedule whose dates are not strictly
  ascending — for example `((2023-08-28, 5.0), (2008-09-19, 10.0))`
- **THEN** a `CanonicalBacktestContractError` is raised
- **AND** the message names both the field and the offending pair

#### Scenario: duplicate effective_from is rejected
- **WHEN** the schedule contains two entries with the same date
- **THEN** a `CanonicalBacktestContractError` is raised

#### Scenario: bps above cap is rejected
- **WHEN** any schedule entry's `bps` exceeds `STAMP_TAX_BPS_MAX`
- **THEN** a `CanonicalBacktestContractError` is raised at
  construction, identifying the offending entry's date and bps

### Requirement: A default CN schedule SHALL be exposed for ergonomic configs

The module SHALL expose `CN_STAMP_TAX_SCHEDULE_DEFAULT` as a
module-level constant of type
`tuple[StampTaxScheduleEntry, ...]`. It SHALL include at minimum
the 2023-08-28 transition: an entry with `effective_from=2008-09-19,
bps=10.0` followed by an entry with `effective_from=2023-08-28,
bps=5.0`. Configs that do not opt into a custom schedule SHALL
resolve to this default.

#### Scenario: default schedule has the 2023-08-28 reform
- **WHEN** a caller reads `CN_STAMP_TAX_SCHEDULE_DEFAULT`
- **THEN** the returned tuple contains an entry with
  `effective_from == date(2023, 8, 28)` and `bps == 5.0`
- **AND** at least one earlier entry exists whose `bps == 10.0`

### Requirement: The runtime SHALL collapse a schedule into a per-run scalar
The backtest runtime SHALL resolve a `stamp_tax_schedule` into a
single scalar suitable for `exchange_kwargs["close_cost"]` by:

* When the backtest period is covered by exactly one schedule
  entry: the runtime SHALL use that entry's `bps`.
* When the period crosses one or more transitions: the runtime
  SHALL use the trading-day-weighted average of the per-segment
  rates, AND SHALL emit a single `WARN`-level log per
  `BacktestRunner.run` call. The log SHALL include each crossed
  transition's date, the pre-transition bps, the post-transition
  bps, AND the weighted scalar that was used.
* When the period starts before the schedule's first
  `effective_from`: the runtime SHALL raise
  `CanonicalBacktestContractError` instead of extrapolating. The
  error SHALL name both the period start and the schedule's
  earliest date.

#### Scenario: period within one schedule entry
- **WHEN** the period is `2024-01-01 → 2024-12-31` and the schedule
  is the default
- **THEN** the resolved scalar equals `5.0`
- **AND** no WARN log is emitted

#### Scenario: period crosses 2023-08-28 transition
- **WHEN** the period is `2022-01-01 → 2024-12-31` and the schedule
  is the default
- **THEN** the resolved scalar is strictly between `5.0` and `10.0`
- **AND** exactly one WARN log is emitted, mentioning
  `2023-08-28`, `10.0`, `5.0`, and the resolved scalar

#### Scenario: period precedes schedule start
- **WHEN** the period starts at `2005-01-01` and the schedule's
  first entry is `2008-09-19`
- **THEN** the runtime raises `CanonicalBacktestContractError`
- **AND** the message names both `2005-01-01` and `2008-09-19`

### Requirement: Config layers SHALL accept the schedule or its default

`PipelineConfig.stamp_tax_schedule` and the walk-forward equivalent SHALL accept either:

* `None` (interpreted as `CN_STAMP_TAX_SCHEDULE_DEFAULT`), OR
* a `Sequence[Mapping[str, Any]]` whose entries each have
  `effective_from` (ISO date string or `datetime.date`) and `bps`
  (real number).

The legacy scalar `stamp_tax_bps` field SHALL NOT exist on the
config dataclasses, AND the YAML loaders SHALL raise
`PipelineConfigError` / `WalkForwardConfigError` when the legacy
key is present in the input mapping. The error message SHALL
include a copy-pasteable migration snippet.

#### Scenario: legacy YAML key is rejected
- **WHEN** the YAML input contains `stamp_tax_bps: 10.0`
- **THEN** loading raises with a message that:
  * names the legacy key,
  * names the replacement `stamp_tax_schedule`,
  * shows a YAML snippet of the two-entry default,
  * references audit P0-4.

#### Scenario: schedule omitted defaults to the canonical CN schedule
- **WHEN** the YAML input does not set `stamp_tax_schedule`
- **THEN** the resolved `CanonicalExchangeCostModel` carries
  `CN_STAMP_TAX_SCHEDULE_DEFAULT` verbatim

### Requirement: A governance test SHALL forbid regression to a scalar field

A test under `tests/governance/` SHALL assert that:

* `CanonicalExchangeCostModel` has no public field named
  `stamp_tax_bps`,
* `PipelineConfig` has no field named `stamp_tax_bps`,
* the walk-forward config dataclass has no field named
  `stamp_tax_bps`,
* none of the shipped `config*.yaml` files contain the literal
  top-level key `stamp_tax_bps`.

#### Scenario: governance test catches a scalar field re-introduction
- **WHEN** a future change re-adds `stamp_tax_bps: float` to any of
  the three dataclasses above
- **THEN** the governance test fails, identifying the offending
  dataclass + field
