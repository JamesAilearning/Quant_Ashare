# v2-attribution-benchmark-weights Specification

## Purpose
TBD - created by archiving change define-attribution-benchmark-weights. Update Purpose after archive.
## Requirements
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

### Requirement: Market-cap benchmark weights SHALL derive from PIT free-float caps or fail loud

`bench_weight_method="market_cap"` SHALL derive benchmark weights from the
PIT bundle's `$circ_mv` (free-float market cap), read through the run-level
`PITDataProvider` (the single sanctioned §4.3.2 access — no new direct
`D.features` bypass), as-of the attribution period's first day: for each
analyzed instrument the LAST published value at or before `T0` within a
bounded lookback (audit P6; the approved source-of-truth change the
previous reservation anticipated). Explicit `benchmark_weights` SHALL keep
taking precedence. The method SHALL NEVER silently fall back to equal
weights: with neither explicit weights nor a provider, attribution SHALL
refuse up front; an instrument with no as-of value in the lookback, or a
non-positive/non-finite cap, SHALL refuse at weight construction, naming
instruments. The approximation SHALL be labeled honestly: `circ_mv`
weighting approximates the official tiered free-float methodology
(分级靠档) without reproducing the tiering steps.

#### Scenario: weights are proportional to as-of free-float caps
- **WHEN** market_cap attribution runs with a provider whose `$circ_mv`
  panel carries values at or before the period start
- **THEN** each instrument's benchmark weight equals its as-of cap divided
  by the universe total, and the fetch window ends at the period start (no
  in-period capitalization can leak in)

#### Scenario: market-cap method without weights and without a provider is refused
- **WHEN** attribution config requests market-cap benchmark weighting with
  neither explicit weights nor a `pit_provider`
- **THEN** attribution raises `PerformanceAttributionError` up front
- **AND** Brinson output is not produced

#### Scenario: a constituent without an as-of cap fails loud
- **WHEN** any analyzed instrument has no published `$circ_mv` within the
  lookback up to the period start (or a non-positive/non-finite value)
- **THEN** attribution raises `PerformanceAttributionError` naming the
  instrument(s) — never a silent drop or a silent equal-weight substitute

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

