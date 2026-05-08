## ADDED Requirements

### Requirement: Attribution SHALL reject non-finite return and weight inputs

Performance attribution SHALL treat NaN and infinite return or position weight
values as malformed boundary input instead of allowing pandas aggregation
defaults to skip or propagate them silently.

#### Scenario: return series contains NaN
- **WHEN** attribution receives a return or benchmark return series containing
  NaN or Inf
- **THEN** `PerformanceAttribution.analyze` raises `PerformanceAttributionError`
- **AND** no Brinson or monthly decomposition result is emitted

#### Scenario: position weights contain NaN
- **WHEN** attribution receives positions with NaN or Inf weights
- **THEN** those weights are rejected as unusable input
- **AND** attribution does not emit valid-looking zero effects from corrupted
  positions

### Requirement: Attribution SHALL fail when no instrument returns are available for Brinson analysis

Brinson attribution SHALL require at least one finite instrument close-return
observation overlapping the analyzed instruments.

#### Scenario: all instrument close data is missing
- **WHEN** qlib returns no usable close observations for all analyzed
  instruments
- **THEN** attribution raises `PerformanceAttributionError`
- **AND** it does not emit all-zero allocation and selection effects
