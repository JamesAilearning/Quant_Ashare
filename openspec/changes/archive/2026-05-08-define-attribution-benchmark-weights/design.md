## Context

Performance attribution has Brinson allocation/selection/effect calculations,
but the benchmark weights are currently generated as equal weight over the
analyzed instruments. The module also reserves a market-cap method constant, but
there is no approved source for index weights in this change.

## Goals / Non-Goals

**Goals:**

- Make benchmark weight method explicit in `AttributionConfig` and results.
- Support caller-supplied benchmark weights for real index-relative Brinson
  attribution.
- Keep equal-weight attribution available but label it as a proxy.
- Reject market-cap/index-weight requests without an explicit weight source.

**Non-Goals:**

- No automatic qlib `$circulating_market_cap` lookup in attribution.
- No benchmark weight artifact publisher/loader in this change.
- No change to canonical backtest output or official metrics.

## Decisions

1. **Use explicit override weights before automatic market-cap lookup.**

   A caller-supplied mapping makes provenance clear and avoids silently assuming
   a qlib field has the correct benchmark constituent weights.

2. **Label default equal weight as proxy.**

   Default behavior remains compatible, but output metadata and report text
   must make clear the Brinson benchmark is not an index weight source.

3. **Reserve market-cap as a loud failure until sourced.**

   The reserved method is accepted only when paired with explicit weights. This
   prevents "market_cap" from silently degenerating to equal weight.

## Risks / Trade-offs

- **Risk: existing users see new warnings/metadata** -> Mitigation: metrics stay
  unchanged for default equal-weight proxy.
- **Risk: weight overrides do not sum to 1** -> Mitigation: normalize positive
  weights and reject all-zero/negative-only payloads.
- **Risk: temporal benchmark weights are needed later** -> Mitigation: current
  mapping is a static foundation; temporal artifacts can be added through a
  later contract.

## Migration Plan

1. Add benchmark weight config/result metadata.
2. Resolve benchmark weights from explicit override or equal-weight proxy.
3. Add tests for explicit weights, equal-weight metadata, and loud market-cap
   failure without weights.
