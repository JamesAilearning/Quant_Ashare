# v2-ashare-survivorship-correction Specification (delta)

## ADDED Requirements

### Requirement: Bin builder SHALL reject a non-finite or non-positive adj_factor

The qlib bin builder SHALL refuse to write bins when any `adj_factor` that would
scale a price is non-finite (`inf` / `NaN`) or non-positive (`<= 0`), because
such a factor multiplies silently into the OHLC columns — `inf` produces `inf`
prices, `0` zeroes them, and a negative factor sign-flips them — corrupting the
production bundle in a way that only surfaces much later as nonsensical features.
The check SHALL run AFTER missing factors are filled to `1.0` (a legitimately
missing factor means "no adjustment" and SHALL pass) and BEFORE the prices are
multiplied. On violation it SHALL raise an explicit error naming the ticker, the
offending trade date(s), and the bad value(s), and SHALL write no bundle. A
factor column that is entirely finite and strictly positive SHALL build
unchanged.

#### Scenario: a corrupt adj_factor aborts the build
- **WHEN** a ticker's `adj_factor` contains a non-finite (`inf`/`NaN`), zero, or
  negative value on a date that scales its price
- **THEN** the builder raises an explicit error and writes no bundle
- **AND** the error names the ticker, the offending trade date, and the value

#### Scenario: a missing adj_factor filled to 1.0 is accepted
- **WHEN** a ticker has no `adj_factor` for some trading day and it is filled to
  `1.0` (no adjustment)
- **THEN** the guard does not raise and the price is left unadjusted for that day

#### Scenario: a clean adj_factor builds unchanged
- **WHEN** every `adj_factor` value is finite and strictly positive
- **THEN** the guard does not raise and the bins are identical to the pre-guard
  output
