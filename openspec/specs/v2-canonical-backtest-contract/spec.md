# v2-canonical-backtest-contract Specification

## Purpose
TBD - created by archiving change define-v2-canonical-backtest-contract. Update Purpose after archive.
## Requirements
### Requirement: V2 SHALL define exactly one canonical official-metrics backtest path

The system SHALL expose exactly one canonical backtest contract for official metrics, based on qlib-native execution semantics, and SHALL NOT define competing official paths.

#### Scenario: official metrics source is declared
- **WHEN** maintainers inspect canonical backtest contract documentation
- **THEN** exactly one official metrics source is defined
- **AND** the source is explicitly labeled canonical
- **AND** no alternative path is labeled official

### Requirement: Canonical backtest contract SHALL define a strictly typed input boundary

The canonical contract SHALL declare required and optional inputs using frozen, typed dataclasses for account and exchange configuration. Free-form dictionaries for those fields SHALL be rejected at the validation boundary. Non-canonical control inputs remain explicitly out of scope.

#### Scenario: canonical input schema is reviewed
- **WHEN** contributors review canonical input definitions
- **THEN** required canonical inputs are clearly listed
- **AND** optional canonical inputs are clearly listed
- **AND** unsupported experimental/research controls are explicitly out of scope

#### Scenario: dict-shaped account_config is rejected
- **WHEN** a caller supplies `account_config` as a `dict`
- **THEN** `CanonicalBacktestContract.validate_input` raises `CanonicalBacktestContractError`
- **AND** the error message identifies `account_config` as the offending field

#### Scenario: dict-shaped exchange_config is rejected
- **WHEN** a caller supplies `exchange_config` as a `dict`
- **THEN** `CanonicalBacktestContract.validate_input` raises `CanonicalBacktestContractError`
- **AND** the error message identifies `exchange_config` as the offending field

### Requirement: Canonical backtest input SHALL require an explicit price-adjustment mode

The canonical backtest input SHALL require an `adjust_mode` field whose value is one of `pre_adjusted`, `post_adjusted`, or `unadjusted`. There SHALL be no default.

#### Scenario: unknown adjust_mode is rejected
- **WHEN** a caller supplies `adjust_mode="auto"`
- **THEN** `CanonicalBacktestContract.validate_input` raises `CanonicalBacktestContractError`
- **AND** the error message lists the allowed values

### Requirement: Canonical backtest input SHALL forbid zero-lag signal execution

The canonical backtest input SHALL require `signal_to_execution_lag >= 1`. A value of zero SHALL be rejected as a look-ahead violation.

#### Scenario: zero lag is rejected
- **WHEN** a caller supplies `signal_to_execution_lag=0`
- **THEN** `CanonicalBacktestContract.validate_input` raises `CanonicalBacktestContractError`
- **AND** the error message mentions look-ahead

#### Scenario: negative lag is rejected
- **WHEN** a caller supplies `signal_to_execution_lag=-1`
- **THEN** `CanonicalBacktestContract.validate_input` raises `CanonicalBacktestContractError`

### Requirement: Canonical exchange config SHALL require an explicit execution price kind

The `CanonicalExchangeConfig` SHALL require an `execution_price_kind` whose value is one of `open`, `close`, or `vwap`. There SHALL be no default.

#### Scenario: unknown execution_price_kind is rejected
- **WHEN** a caller constructs `CanonicalExchangeConfig(..., execution_price_kind="limit", ...)`
- **THEN** a `CanonicalBacktestContractError` is raised during construction
- **AND** the error message lists the allowed values

### Requirement: Canonical exchange config SHALL bound-check cost-model fields

The `CanonicalExchangeCostModel` SHALL enforce bounds on `commission_rate`, `stamp_tax_bps`, `slippage_bps`, and `min_cost`. Out-of-bound values SHALL be rejected at construction.

#### Scenario: commission_rate above cap is rejected
- **WHEN** a caller supplies `commission_rate=0.5`
- **THEN** a `CanonicalBacktestContractError` is raised during construction
- **AND** the error message names the offending field and the cap

#### Scenario: negative min_cost is rejected
- **WHEN** a caller supplies `min_cost=-1.0`
- **THEN** a `CanonicalBacktestContractError` is raised during construction

### Requirement: Canonical input required-field list SHALL include the quant-risk fields

`CANONICAL_INPUT_REQUIRED_FIELDS` SHALL include `adjust_mode` and `signal_to_execution_lag`.

#### Scenario: required-field list is inspected
- **WHEN** maintainers read `CanonicalBacktestContract.input_boundary()["required"]`
- **THEN** the returned tuple contains `adjust_mode` and `signal_to_execution_lag`

### Requirement: Canonical backtest contract SHALL define required outputs for official reporting

The canonical contract SHALL define required output fields for official reporting, including return series, risk-analysis payload, and provenance fields that identify canonical path usage.

#### Scenario: canonical output schema is reviewed
- **WHEN** contributors inspect canonical output definitions
- **THEN** required metric outputs are explicitly listed
- **AND** canonical provenance/status fields are explicitly listed
- **AND** output schema supports auditable official reporting

### Requirement: Canonical contract SHALL keep experimental execution non-official

Experimental execution paths SHALL remain explicitly non-canonical and SHALL NOT be mixed into official metric outputs.

#### Scenario: experimental logic is present in project
- **WHEN** an experimental backtest or risk-control path exists
- **THEN** it is labeled non-canonical
- **AND** official metrics remain sourced only from canonical outputs

### Requirement: Canonical contract SHALL keep research artifacts outside production execution

Research artifacts under `research/factor_lab/` SHALL be treated as non-production and SHALL NOT be consumed by canonical runtime unless promoted through explicit spec-approved changes.

#### Scenario: research boundary is checked
- **WHEN** contributors inspect canonical contract boundaries
- **THEN** research/factor_lab is marked non-production and non-canonical
- **AND** direct runtime coupling from research to canonical execution is disallowed by contract

### Requirement: Canonical contract SHALL forbid implicit fallback semantics

The canonical contract SHALL require explicit behavior for missing dependencies and SHALL NOT allow hidden fallback paths that change official metric meaning without explicit labeling.

#### Scenario: missing canonical dependency occurs
- **WHEN** a required canonical dependency is unavailable
- **THEN** contract behavior is explicitly defined
- **AND** no implicit hidden fallback changes official metric semantics

### Requirement: Canonical contract SHALL define minimum validation and regression expectations

The canonical contract SHALL require minimum validation coverage, including boundary regressions that protect canonical-vs-experimental separation and official-metrics source integrity.

#### Scenario: canonical contract validation baseline is reviewed
- **WHEN** maintainers inspect required validation expectations
- **THEN** minimum regression categories are explicitly defined
- **AND** canonical source integrity checks are part of required validation
- **AND** boundary regressions are required before archive/merge

