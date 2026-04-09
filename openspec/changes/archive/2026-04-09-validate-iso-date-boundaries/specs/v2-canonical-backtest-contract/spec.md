## ADDED Requirements

### Requirement: Canonical backtest input SHALL validate `evaluation_start` / `evaluation_end` as ISO dates with start <= end

`CanonicalBacktestContract.validate_input` SHALL parse
`evaluation_start` and `evaluation_end` as strict ISO `YYYY-MM-DD`
dates AFTER the existing non-empty check, using the shared
`_shared_validators.parse_iso_date` helper with
`error_cls=CanonicalBacktestContractError`. It SHALL additionally
verify that the parsed `evaluation_start` is less than or equal to
the parsed `evaluation_end` and SHALL raise
`CanonicalBacktestContractError` otherwise. A single-day window
(`evaluation_start == evaluation_end`) SHALL be accepted.

#### Scenario: evaluation_start is not a valid ISO date
- **WHEN** a caller supplies `evaluation_start="banana"`
- **THEN** `CanonicalBacktestContract.validate_input` raises
  `CanonicalBacktestContractError`
- **AND** the error message contains the offending string `banana`

#### Scenario: evaluation_end uses a non-ISO separator
- **WHEN** a caller supplies `evaluation_end="2026/02/27"`
- **THEN** `CanonicalBacktestContract.validate_input` raises
  `CanonicalBacktestContractError`
- **AND** the error message contains the offending string `2026/02/27`

#### Scenario: evaluation_start is after evaluation_end
- **WHEN** a caller supplies `evaluation_start="2026-02-27"` and
  `evaluation_end="2026-02-01"`
- **THEN** `CanonicalBacktestContract.validate_input` raises
  `CanonicalBacktestContractError`
- **AND** the error message names both `evaluation_start` and
  `evaluation_end`

#### Scenario: single-day evaluation window is accepted
- **WHEN** a caller supplies `evaluation_start="2026-02-27"` and
  `evaluation_end="2026-02-27"`
- **THEN** `CanonicalBacktestContract.validate_input` returns the
  validated input without raising
