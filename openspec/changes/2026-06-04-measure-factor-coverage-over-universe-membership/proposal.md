# Proposal: measure-factor-coverage-over-universe-membership

## Why

The factor-mining evaluator's `coverage` metric (`evaluator._coverage`)
divides the count of non-NaN factor cells by **all** cells in the
date × ticker matrix. On the synthetic dense panel that Phase 2 was
built against this is ~1.0, so `coverage_min = 0.8` works.

On a real **survivorship-corrected PIT panel** it does not. The panel
is the *union* of every ticker that was ever a universe member over the
window (e.g. csi300 2018-2021 = 477 union tickers), but only ~300 are
members on any given day. The other ~37 % of the union matrix is
legitimately NaN — those tickers are not yet listed, have rotated out
of the index, or have delisted. Counting those non-member cells as
"missing coverage" caps the *achievable* union-coverage at ~0.62, so:

- even a perfect factor like `cs_rank(ts_pctchange($close, 5))` scores
  **0.61 union-coverage** and fails the 0.8 gate;
- therefore `passes_validity` returns False for **every** candidate;
- therefore a full GP run produces **0 factors** (`n_invalid ==
  population` for all generations — observed empirically: a 25k-eval run
  on csi300 2018-2021 returned an empty pool after ~13 h of compute).

Measured **relative to universe membership** (denominator = member cells
only), that same factor scores **0.98**, and the six OHLCV/money + six
`daily_basic` terminals all score 0.97–0.99. The data is fine; the
denominator was wrong.

This mirrors the leakage/semantics fix already applied to the benchmark
loader in `2026-04-08-wire-trading-calendar-into-coverage` — there the
fix was "coverage denominator = real trading days, not a constant"; here
it is "coverage denominator = universe members, not the union".

## Goals

- **Members-relative coverage.** `evaluator._coverage` SHALL accept an
  optional universe-membership mask and, when supplied, use the count of
  member cells as the denominator (numerator = member cells with a
  finite factor value).
- **Keep `coverage_min = 0.8`.** The threshold is correct once the
  denominator is correct (a real factor scores ~0.98); no threshold
  tuning, so the validity gate keeps its original strictness against
  genuinely-undefined factors.
- **Backward compatible.** The mask is optional. With no mask
  (synthetic / dense panels, or any existing caller), coverage falls
  back to the all-cells fraction — byte-for-byte the current behaviour.
- **D5 strict gate preserved.** The mask is produced by the pit_adapter
  (`FactorMiningDataView.universe_mask`, the single PIT door) and passed
  in as a plain DataFrame; `evaluator.py` gains no qlib / `src.pit`
  import.

## Non-Goals

- **Not** changing `coverage_min` (stays 0.8) or any other fitness
  weight / validity threshold.
- **Not** changing the GP algorithm, grammar, operators, or the label /
  forward-return definition.
- **Not** changing the variance or extreme-outlier validity gates.
- **Not** wiring the mask into the `MinedFactor` handler's WF path — that
  path evaluates frozen expressions and never calls `_coverage`.

## What Changes

1. `src/factor_mining/evaluator.py`:
   - `_coverage(factor_values, universe_mask=None)` — members-only
     denominator when `universe_mask` is given (reindexed to the factor's
     index/columns, `fillna(False)`); all-cells fallback when None.
   - `evaluate_factor(..., *, method, universe_mask=None)` — new optional
     keyword forwarded to `_coverage`.
2. `src/factor_mining/gp_engine.py`:
   - `GPEngine.run(..., universe_mask=None)` stores the mask;
     `evaluate_individual` forwards `self._universe_mask` to
     `evaluate_factor`. (No signature change to `evaluate_individual`.)
3. `src/factor_mining/miner.py`:
   - `build_universe_mask(config)` — returns
     `FactorMiningDataView.universe_mask()` in PIT mode, `None` in
     synthetic mode. `run_mining` calls it and passes the result to
     `engine.run`.
4. Tests (`tests/logic/factor_mining/test_evaluator.py`):
   - members-relative coverage rescues a factor whose non-member union
     cells are NaN (0.75 union → 1.0 members);
   - a member cell that is NaN still reduces coverage (gate still bites);
   - `universe_mask=None` reproduces the legacy all-cells fraction.

## Impact

- **Affected specs**: `v2-factor-mining-foundations` (MODIFIED — the
  evaluator coverage requirement).
- **Affected code**:
  - `src/factor_mining/evaluator.py` (`_coverage`, `evaluate_factor`)
  - `src/factor_mining/gp_engine.py` (`run`, `evaluate_individual`, init)
  - `src/factor_mining/miner.py` (`build_universe_mask`, `run_mining`)
- **Affected tests**: `tests/logic/factor_mining/test_evaluator.py`
  (3 new cases). Existing `test_evaluate_factor_coverage_excludes_nan_cells`
  (mask-free) stays green unchanged.
- **Backward compatibility**: the `universe_mask` parameter is optional
  everywhere; synthetic mode passes None and behaves exactly as before.
- **Risk**: low. Pure metric-denominator change behind an optional
  parameter; no qlib import added (D5 preserved); the 0.8 gate is
  unchanged so genuinely-undefined factors are still rejected.
