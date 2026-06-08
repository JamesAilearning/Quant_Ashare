# Proposal: guard-corrupt-adj-factor-in-bin-builder

## Why

`QlibBinBuilder._apply_adjustment` (the production PIT bin builder, driven by
`scripts/data_pipeline/05_build_qlib_bins.py`) multiplies OHLC prices by
`adj_factor` after only `ffill().fillna(1.0)` — with **no validity check** on
the factor itself. A corrupt `adj_factor` row (non-finite `inf`/`NaN`, `0`, or
negative) therefore flows silently into the bins: `inf` yields `inf` prices, `0`
zeroes them, and a negative factor SIGN-FLIPS them. The bad values are written
straight into the production bundle (`my_cn_data_pit/`) and only surface much
later as nonsensical features / returns deep in a backtest or daily list.

The operator-UI publisher path
(`src/data/tushare/provider_bundle/publisher.py`) already validates its staged
`adj_factor` (non-finite and non-positive checks) and hard-fails before writing
bins. The production builder has **no equivalent guard** — an asymmetry that is
low-risk today only because the curated tushare dump is clean. Ops Phase 3
automates the tushare fetch, which makes a stray corrupt `adj_factor` row far
more likely; this guard is a prerequisite for trusting an unattended rebuild
(P1-10).

## What Changes

- `src/data/pit/qlib_bin_builder.py`:
  - `QlibBinBuilder._validate_adj_factor(out, tushare_code)` — fail-loud check
    that every `adj_factor` value that will scale prices is finite and strictly
    positive; raises `QlibBinBuilderError` naming the ticker, the offending
    `trade_date`(s), and the bad value(s).
  - `_apply_adjustment` calls it AFTER `ffill().fillna(1.0)` (so a
    legitimately-missing factor filled to `1.0` still passes) and BEFORE the
    OHLC multiply, on the post-merge column that actually scales prices.
- Mirrors the publisher's existing non-finite / non-positive adjustment-factor
  validation as a DELIBERATE short-term duplicate (the production builder must
  NOT import the publisher — wrong dependency direction); a shared validator is
  deferred to the pending builder-unification (publisher-retirement) assessment.

## Non-Goals

- No change to the success path: a clean `adj_factor` builds identical bins.
- No streaming / memory refactor of the builder (Phase 3 P3-2, deferred).
- No OHLCV-validity guard on the raw prices themselves (separate concern; the
  publisher's `_count_invalid_ohlcv` has no builder equivalent yet).
- No shared validator extraction across the two builders (pending unification).
