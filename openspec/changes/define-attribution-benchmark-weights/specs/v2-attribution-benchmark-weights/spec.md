## ADDED Requirements

### Requirement: Attribution SHALL label benchmark weight semantics

Performance attribution SHALL include benchmark weight method metadata in
Brinson attribution output. Equal-weight benchmark attribution SHALL be labeled
as an equal-weight proxy, not as index-relative market-cap attribution.

#### Scenario: default attribution runs
- **WHEN** attribution is run without explicit benchmark weights
- **THEN** Brinson output records `benchmark_weight_method="equal_weight_proxy"`
- **AND** the report notes that allocation and selection effects use an equal-weight benchmark proxy

### Requirement: Attribution SHALL support explicit benchmark weights

Performance attribution SHALL allow callers to provide explicit static
benchmark weights by instrument. Positive supplied weights SHALL be normalized
over the analyzed instruments and used for Brinson benchmark sector weights.

#### Scenario: explicit benchmark weights are supplied
- **WHEN** attribution config provides benchmark weights for analyzed instruments
- **THEN** Brinson output records `benchmark_weight_method="explicit"`
- **AND** allocation and selection effects use the supplied normalized benchmark weights

#### Scenario: explicit benchmark weights are invalid
- **WHEN** supplied benchmark weights are empty, non-numeric, or have no positive overlap with analyzed instruments
- **THEN** attribution raises `PerformanceAttributionError`

### Requirement: Market-cap benchmark weights SHALL fail without an approved source

The reserved market-cap benchmark method SHALL NOT silently fall back to
equal-weight attribution. It SHALL require explicit benchmark weights until a
future approved source-of-truth change defines automatic market-cap weights.

#### Scenario: market-cap method is requested without weights
- **WHEN** attribution config requests market-cap benchmark weighting without explicit weights
- **THEN** attribution raises `PerformanceAttributionError`
- **AND** Brinson output is not produced
