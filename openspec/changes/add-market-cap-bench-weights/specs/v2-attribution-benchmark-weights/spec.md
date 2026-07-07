## MODIFIED Requirements

### Requirement: Market-cap benchmark weights SHALL derive from PIT free-float caps or fail loud

`bench_weight_method="market_cap"` SHALL derive benchmark weights from the
PIT bundle's `$circ_mv` (free-float market cap), read through the run-level
`PITDataProvider` (the single sanctioned §4.3.2 access — no new direct
`D.features` bypass), as-of the attribution period's first day: for each
analyzed instrument the LAST published value at or before `T0` within a
bounded lookback. Explicit `benchmark_weights` SHALL keep taking precedence.
The method SHALL NEVER silently fall back to equal weights: with neither
explicit weights nor a provider, attribution SHALL refuse up front; an
instrument with no as-of value in the lookback, or a non-positive/non-finite
cap, SHALL refuse at weight construction, naming instruments. The
approximation SHALL be labeled honestly: `circ_mv` weighting approximates
the official tiered free-float methodology (分级靠档) without reproducing
the tiering steps.

#### Scenario: weights are proportional to as-of free-float caps
- **WHEN** market_cap attribution runs with a provider whose `$circ_mv`
  panel carries values at or before the period start
- **THEN** each instrument's benchmark weight equals its as-of cap divided
  by the universe total, and the fetch window ends at the period start (no
  in-period capitalization can leak in)

#### Scenario: market-cap method without weights and without a provider is refused
- **WHEN** attribution config requests market-cap weighting with neither
  explicit weights nor a `pit_provider`
- **THEN** attribution raises `PerformanceAttributionError` up front
- **AND** Brinson output is not produced

#### Scenario: a constituent without an as-of cap fails loud
- **WHEN** any analyzed instrument has no published `$circ_mv` within the
  lookback up to the period start (or a non-positive/non-finite value)
- **THEN** attribution raises `PerformanceAttributionError` naming the
  instrument(s) — never a silent drop or a silent equal-weight substitute
