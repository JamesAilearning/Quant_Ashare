## REMOVED Requirements

### Requirement: Canonical backtest input SHALL forbid zero-lag signal execution

**Reason**: The old requirement made `signal_to_execution_lag=1` a no-op and
forced callers to pass `2` for T+1 execution, which is counterintuitive and can
hide look-ahead bias.

**Migration**: Same-day execution users must now set
`signal_to_execution_lag=0`. The default `signal_to_execution_lag=1` means one
trading-row delayed execution.

## ADDED Requirements

### Requirement: Canonical backtest input SHALL define explicit signal lag semantics

The canonical backtest input SHALL define `signal_to_execution_lag` as the
number of trading rows by which prediction signals are delayed before execution.
`0` SHALL mean explicit same-day execution/no shift. Positive values SHALL shift
signals by exactly that many trading rows. Negative values and booleans SHALL be
rejected.

#### Scenario: zero lag is explicit same-day execution
- **WHEN** a caller supplies `signal_to_execution_lag=0`
- **THEN** `CanonicalBacktestContract.validate_input` accepts the request
- **AND** `BacktestRunner` leaves prediction timestamps unchanged

#### Scenario: one lag shifts by one trading row
- **WHEN** a caller supplies `signal_to_execution_lag=1`
- **THEN** `BacktestRunner` delays predictions by one trading row per instrument
- **AND** same-day execution is not used

#### Scenario: negative lag is rejected
- **WHEN** a caller supplies `signal_to_execution_lag=-1`
- **THEN** `CanonicalBacktestContract.validate_input` raises `CanonicalBacktestContractError`

#### Scenario: boolean lag is rejected
- **WHEN** a caller supplies `signal_to_execution_lag=True`
- **THEN** `CanonicalBacktestContract.validate_input` raises `CanonicalBacktestContractError`
