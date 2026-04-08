## MODIFIED Requirements

### Requirement: Canonical backtest contract SHALL define a strictly typed input boundary

The canonical backtest input SHALL use frozen dataclasses for account and exchange configuration. Free-form dictionaries SHALL be rejected at the validation boundary.

#### Scenario: dict-shaped account_config is rejected
- **WHEN** a caller supplies `account_config` as a `dict`
- **THEN** `CanonicalBacktestContract.validate_input` raises `CanonicalBacktestContractError`
- **AND** the error message identifies `account_config` as the offending field

#### Scenario: dict-shaped exchange_config is rejected
- **WHEN** a caller supplies `exchange_config` as a `dict`
- **THEN** `CanonicalBacktestContract.validate_input` raises `CanonicalBacktestContractError`
- **AND** the error message identifies `exchange_config` as the offending field

## ADDED Requirements

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

### Requirement: Canonical input required-field list SHALL include the new quant-risk fields

`CANONICAL_INPUT_REQUIRED_FIELDS` SHALL include `adjust_mode` and `signal_to_execution_lag`.

#### Scenario: required-field list is inspected
- **WHEN** maintainers read `CanonicalBacktestContract.input_boundary()["required"]`
- **THEN** the returned tuple contains `adjust_mode` and `signal_to_execution_lag`
