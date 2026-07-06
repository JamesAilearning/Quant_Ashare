# Proposal: market_cap benchmark weights from PIT free-float caps (audit P6)

## Why now

The last open item of the operator-approved 2026-07-01 audit improvement
plan (P1 #319, P2 #320/#321/#329, P4 #331 all shipped). The blocker named in
the plan — daily_basic ingestion — has landed (#182–#188): raw parquet
covers 2018–2026 and the PIT bundle already carries `circ_mv.day.bin`.

## What

Implement the reserved `bench_weight_method="market_cap"` (the baseline spec
explicitly reserved it for "a future approved source-of-truth change" —
this is that change):

1. **Source of truth**: the PIT bundle's `$circ_mv` (free-float cap from
   daily_basic), read through the run-level `PITDataProvider` — the single
   sanctioned §4.3.2 door. No new direct `D.features` bypass; the
   governance whitelist is untouched.
2. **As-of semantics**: for each analyzed instrument, the LAST published
   value at or before the attribution period's first day (strictly
   `<= T0`), within a 30-calendar-day lookback (tolerates suspensions
   without reaching a different capitalization regime). The fetch window
   itself ends at T0, so in-period capitalization can never leak in.
3. **Fail-loud (no-silent-fallback, per plan recommendation)**: no
   provider (and no explicit weights) refuses at `_validate` time; a
   missing/all-NaN as-of value or a non-positive cap refuses at weight
   construction, naming instruments. Equal weights are NEVER published
   under the market_cap label; the existing `_effective_bench_weight_method`
   misnomer discipline is preserved unchanged.
4. **Honest approximation label (project convention)**: `circ_mv`
   weighting approximates the official CSI 300 tiered free-float
   methodology (分级靠档); the tiering steps are not reproduced —
   documented in the config comment and the weights builder docstring.
5. **Universe**: the analyzed (predictions) universe — for canonical
   `csi300` runs it is already PIT-membered upstream via qlib instruments
   intervals (verified: `instruments/csi300.txt` carries (start, end)
   membership ranges); membership is not re-derived in the attribution
   layer.

## Anchor impact

None expected: the default method (`equal_weight_proxy`) is untouched, and
the attribution block is not part of the REGEN-2 pin (proven by #320's
green anchor leg). CI judges as always.

## Out of scope

- Tiered free-float ratios (分级靠档) reproduction.
- Wiring `bench_weight_method` into YAML config surfaces (the attribution
  config is constructed programmatically today; a config surface can follow
  operator demand).
