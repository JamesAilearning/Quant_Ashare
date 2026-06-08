# v2-ashare-survivorship-correction Specification (delta)

## ADDED Requirements

### Requirement: Bin builder SHALL reject a non-finite or non-positive adj_factor

The qlib bin builder SHALL refuse to write bins when any `adj_factor` in its RAW
source that would scale a price is non-finite (`inf` / `NaN`) or non-positive
(`<= 0`), because such a factor multiplies silently into the OHLC columns —
`inf` produces `inf` prices, `0` zeroes them, a negative factor sign-flips them,
and a `NaN` yields a wrong / unadjusted price — corrupting the production bundle
in a way that only surfaces much later as nonsensical features. The check SHALL
validate the RAW `adj_factor` source BEFORE any forward-fill / fill-to-`1.0`, so
that a present row whose factor is corrupt (including a raw `NaN`) is caught
rather than masked by the fill. A date that is simply ABSENT from the source (no
row) is NOT corrupt: it SHALL fall through to the no-adjustment default (`1.0`)
and SHALL pass. On violation the builder SHALL raise an explicit error naming the
ticker, the offending trade date(s), and the bad value(s), and SHALL write no
bundle. A source whose factors are entirely finite and strictly positive SHALL
build unchanged.

#### Scenario: a corrupt adj_factor aborts the build
- **WHEN** a ticker's raw `adj_factor` source contains a non-finite (`inf` /
  `NaN`), zero, or negative value on a present row
- **THEN** the builder raises an explicit error and writes no bundle
- **AND** the error names the ticker, the offending trade date, and the value

#### Scenario: a date absent from the adj source fills to 1.0 and is accepted
- **WHEN** a trading day has NO row in the `adj_factor` source and is filled to
  `1.0` (no adjustment)
- **THEN** the guard does not raise and the price is left unadjusted for that day

#### Scenario: a clean adj_factor builds unchanged
- **WHEN** every `adj_factor` value is finite and strictly positive
- **THEN** the guard does not raise and the bins are identical to the pre-guard
  output
