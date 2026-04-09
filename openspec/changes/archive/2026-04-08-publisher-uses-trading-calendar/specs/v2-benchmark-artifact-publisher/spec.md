## ADDED Requirements

### Requirement: Publisher SHALL inject a TradingCalendar into its loader call

`BenchmarkArtifactPublisher.publish` SHALL pass a `TradingCalendar`
instance to the internal `BenchmarkArtifactLoader.load` call so that
the round-trip `BenchmarkArtifactProfile`'s `coverage_ratio` is
computed against the real qlib trading calendar, not the calendar-free
fallback approximation. The default implementation SHALL be
`QlibTradingCalendar`, which lazily fetches the calendar from the
already-initialized canonical qlib runtime.

#### Scenario: round-trip profile uses real trading days
- **WHEN** `BenchmarkArtifactPublisher.publish` is called and the
  internal loader call returns
- **THEN** the loader call received a non-`None` `calendar` keyword argument
- **AND** the calendar argument is an instance of `QlibTradingCalendar`
