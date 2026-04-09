## ADDED Requirements

### Requirement: Benchmark loader SHALL accept an optional TradingCalendar for accurate coverage accounting

`BenchmarkArtifactLoader.load` SHALL accept an optional
`calendar: TradingCalendar` keyword argument. When supplied, the loader
SHALL compute `coverage_ratio` using
`calendar.count_trading_days(snapshot_start, snapshot_end)` as the
denominator. When `calendar` is `None`, the loader SHALL fall back to
the existing calendar-free approximation
(`expected_rows ≈ span_days × 0.63`) and SHALL preserve the prior
behavior so that all existing callers and tests remain valid without
modification.

#### Scenario: calendar injection drives accurate coverage
- **WHEN** `BenchmarkArtifactLoader.load(..., calendar=cal)` is called
  with a `StaticTradingCalendar` whose dates exactly match the csv rows
- **THEN** the produced profile has `coverage_ratio == 1.0`
- **AND** feeding the profile into `BenchmarkDataContract` yields
  `contract_health == "ok"` for an otherwise healthy artifact

#### Scenario: calendar with extra trading days surfaces incomplete coverage
- **WHEN** the injected `StaticTradingCalendar` reports more trading
  days inside `[snapshot_start, snapshot_end]` than the csv contains
- **THEN** `coverage_ratio` is strictly less than `1.0`
- **AND** when the ratio falls below `min_coverage_ratio`, the contract
  surfaces `incomplete_coverage`

#### Scenario: omitting calendar preserves legacy fallback
- **WHEN** `BenchmarkArtifactLoader.load(...)` is called without a
  `calendar` argument
- **THEN** `coverage_ratio` is computed using the legacy
  `span_days × 0.63` approximation
- **AND** existing tests continue to pass unchanged
