# v2-factor-mining-foundations — delta

## MODIFIED Requirements

### Requirement: Operator library SHALL provide the 28-operator v1 baseline plus explicitly amended extensions

The operator library SHALL implement the 28-operator v1 baseline catalogue
enumerated in `docs/factor_mining/scale_invariance.md` §4: four arithmetic
(`add`, `sub`, `mul`, `div_safe`); five unary (`neg`, `abs`, `sign`,
`log_safe`, `sqrt_safe`); fourteen time-series (`ts_mean`, `ts_std`, `ts_max`,
`ts_min`, `ts_sum`, `ts_delta`, `ts_pctchange`, `ts_rank`, `ts_argmax`,
`ts_argmin`, `ts_corr`, `ts_skew`, `ts_kurt`, `ts_decay_linear`); four
cross-sectional (`cs_rank`, `cs_zscore`, `cs_demean`, `cs_winsorize`); one
conditional (`where`).

Beyond the baseline, the registry MAY carry operators added by an approved
OpenSpec amendment, each recorded here with its justification. Amended
extensions to date:

* `coalesce` (binary, FLOAT×FLOAT→FLOAT, first-non-NA selection, same-taint
  only, non-commutative) — required by the frozen `C3_cash_based_OP` charter
  formula (`docs/prereg/quality_profitability.yaml`), whose
  `coalesce(adv_receipts, contract_liab)` pair must merge per report period
  BEFORE differencing across the 2020 预收→合同负债 reclassification. Both-NA
  stays NA: coalesce SHALL NOT invent a value.

The DEFAULT sampling pool for expression generation and mutation SHALL remain
the 28-operator baseline verbatim (`grammar.V1_OPERATORS`): registering an
amended operator SHALL NOT change what any preset without an explicit
`allowed_operators` whitelist can breed. A campaign that wants an amended
operator SHALL list its complete operator set explicitly, and an
`allowed_operators` entry naming an unregistered operator SHALL be refused
before generation.

`ts_cov` SHALL NOT be implemented (per `scale_invariance.md` §4 —
`cov(a·x, y) = a · cov(x, y)` re-introduces `adj_factor` taint; redundant with
`ts_corr`). All operators SHALL have a CPU reference implementation; no GPU
code SHALL be introduced.

#### Scenario: a developer enumerates the operator registry
- **WHEN** a developer iterates `OperatorRegistry.all_operators()`
- **THEN** the baseline 28 plus every amended extension are returned (29 as of
  the `coalesce` amendment), matching this requirement's enumeration verbatim
  (no `ts_cov`, no GPU variants)

#### Scenario: a frozen preset without an operator whitelist is re-run after an amendment
- **WHEN** expression generation runs with no explicit `allowed_operators`
- **THEN** only the 28-operator baseline is sampled, at every tree depth
- **AND** the amended operators are reachable only through an explicit
  complete whitelist

#### Scenario: a whitelist names an unregistered operator
- **WHEN** `allowed_operators` contains a name not in the registry
- **THEN** generation refuses before sampling (no silent narrowing)

#### Scenario: a contributor proposes adding ts_cov
- **WHEN** a contributor proposes to add `ts_cov` to the registry
- **THEN** the proposal is rejected at review
