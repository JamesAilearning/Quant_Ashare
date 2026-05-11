## ADDED Requirements

### Requirement: Publisher validation SHALL reject non-finite staged OHLCV values

Staged Tushare OHLCV validation SHALL reject non-finite numeric values in price
and traded-value columns before publishing a final qlib provider bundle.

#### Scenario: staged OHLCV contains infinity
- **WHEN** staged daily or benchmark-index data contains `inf` or `-inf` in
  open, high, low, close, volume, or amount
- **THEN** validation health is `error`
- **AND** the validation profile identifies invalid OHLCV as the failure
  category
- **AND** the final provider bundle is not published
