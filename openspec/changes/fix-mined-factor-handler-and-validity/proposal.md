# Fix MinedFactor handler integration bugs and validity sanity-check overlap

## Why

The Phase 6.3 PR (`add-factor-mining-walk-forward`, archived) wired
the MinedFactor handler into `scripts/run_walk_forward.py` and shipped
the synthetic-data and `_make_qlib_handler` contracts as
spec'd. The first end-to-end run against a real PIT bundle
(B-min — csi300 daily 2024-2025, built per `inventory.md` §F.3)
revealed three production bugs that the synthetic-only test suite
could not catch:

1. **`_extreme_outlier_frac` double-counts NaN as "extreme outlier".**
   The denominator was the total cell count, and the numerator counted
   every non-finite cell. On a real factor with coverage 0.81 (i.e.
   19% NaN — well within the design doc's `coverage_min = 0.80`), the
   outlier fraction came out to ~0.19, blowing past the default
   `extreme_outlier_frac_max = 0.05` and rejecting every factor in
   the pool. The sanity check effectively required coverage ≥ 0.95,
   which is far stricter than the `coverage_min = 0.80` the design
   doc spec'd as the binding constraint.

2. **`_make_qlib_handler` passes the raw universe-name string
   ("csi300") to `DataHandlerLP`.** qlib's `StaticDataLoader.load`
   runs `df.loc(axis=0)[:, instruments]` — it treats `instruments`
   as a list of ticker codes to filter by, NOT a qlib universe name.
   The first walk-forward fold raised `KeyError: 'csi300'` deep inside
   pandas MultiIndex lookup. The handler MUST resolve universe-name
   strings to concrete ticker lists via `qlib.data.D.list_instruments`
   before passing them to `DataHandlerLP`.

3. **`make_mined_factor_features` returns a `(instrument, datetime)`
   MultiIndex.** qlib's `StaticDataLoader.load` does
   `df.loc(axis=0)[:, instruments]`, which treats level 0 as the
   datetime axis and level 1 as the instrument filter. The original
   `(instrument, datetime)` order made pandas try to look up `SH600000`
   against the datetime level, raising `KeyError: 'SH600000'`. The
   handler MUST produce `(datetime, instrument)` MultiIndex order to
   match qlib's expectation.

All three bugs were workaround-patched locally to complete the
end-to-end bake-off (csi300 2024-2025, GP pop=30 gen=5: IC=-0.006,
IR=-2.41 — design doc's IR threshold NOT met with the under-sized
config, but the wiring works end-to-end). This change ships the real
fixes plus regression tests.

### Why the bugs slipped through the existing test matrix

The Phase 5 / 6.3 test suite used a **synthetic panel** that:

- Always returned `coverage = 0.95` in the synthetic `EvaluationResult`
  fixture, so bug #1 never hit the outlier threshold in tests.
- Constructed `FeatureDatasetConfig` with `instruments="csi300"` but
  never actually instantiated qlib's `StaticDataLoader` end-to-end —
  `_make_qlib_handler` was mocked or not reached.
- Asserted the MultiIndex shape was a MultiIndex (bug #3) but did not
  assert the *level order* matched qlib's expectation; the test
  literally asserted `["instrument", "datetime"]` which baked the wrong
  order into the spec.

This PR fixes the implementation, fixes the test assertions to match
qlib's real contract, and adds explicit regression tests that
exercise the three failure modes.

## What Changes

### `src/factor_mining/fitness.py` — MODIFY `_extreme_outlier_frac`

- Denominator switches from `arr.size` to the count of finite cells in
  `arr` (`np.isfinite(arr).sum()`).
- Numerator switches from "non-finite OR abs > magnitude" to "finite
  AND abs > magnitude" — only finite cells whose magnitude exceeds
  the sanity bound count as outliers.
- All-NaN factor returns 0.0 (the metric is undefined; coverage_min is
  the binding rejection in this case).
- Docstring updated to call out the separation from the coverage check
  and the v1 §5.2 "Sanity" semantics.

### `src/data/mined_factor_handler.py` — MODIFY two functions

- `make_mined_factor_features`: after `result.stack(future_stack=True)`,
  the per-entry series is `reorder_levels(["datetime", "instrument"])`
  and sorted before being concatenated; the final DataFrame's index
  level names are set to `("datetime", "instrument")`.
- `_make_qlib_handler`: if `config.instruments` is a `str`, call
  `D.list_instruments(D.instruments(name), start_time=...,
  end_time=..., as_list=True)` to resolve to a ticker list before
  passing to `DataHandlerLP`. List inputs (already-resolved) pass
  through unchanged.

### Spec deltas

- **MODIFY `v2-mined-factor-handler`** — change the "make_mined_factor_features
  SHALL produce one column per pool entry" requirement: the
  documented MultiIndex order flips from `(instrument, datetime)` to
  `(datetime, instrument)`. The scenario assertion changes accordingly.
- **ADD `v2-mined-factor-handler`** — new requirement: "Handler factory
  SHALL resolve universe-name strings to ticker lists before passing
  to qlib StaticDataLoader". One scenario for the universe-name path,
  one for the list passthrough.
- **MODIFY `v2-factor-mining-foundations`** — change the "Validity
  filters SHALL enforce coverage, variance, and sanity constraints"
  requirement: the sanity check's denominator is finite cells (not
  all cells), and non-finite cells SHALL NOT count as outliers (they
  are the coverage check's domain).

### Tests

- **MODIFY `tests/logic/test_mined_factor_handler.py`** — the
  `test_make_features_returns_multiindex_dataframe` assertion changes
  from `["instrument", "datetime"]` to `["datetime", "instrument"]`.
- **ADD `tests/logic/test_mined_factor_handler.py`**:
  - `test_make_features_index_order_is_datetime_then_instrument` —
    asserts level-0 values are Timestamps and level-1 are ticker
    strings, and the index is monotonically increasing.
  - `test_factory_resolves_universe_name_to_ticker_list` — stubs qlib
    via `monkeypatch.setitem(sys.modules, ...)` and asserts the
    handler invokes `D.list_instruments` and passes the resolved list
    (not the raw "csi300" string) to `DataHandlerLP`.
  - `test_factory_passes_through_explicit_ticker_list` — verifies the
    list passthrough does NOT call `D.list_instruments`.
- **ADD `tests/logic/factor_mining/test_fitness.py`**:
  - `test_sanity_does_not_count_nan_as_outlier` — 30% NaN factor with
    no magnitude outliers must pass the sanity check (coverage check
    is separate).
  - `test_sanity_extreme_outliers_in_finite_cells_still_fail` — 30%
    NaN + 10% of finite cells set to 1e10 must FAIL (the
    finite-denominator still flags real outliers).
  - `test_sanity_all_nan_factor_returns_zero_outlier_frac` — all-NaN
    must not crash and must report 0% outliers (coverage check binds).

## Non-Goals

- **No change to default thresholds.** `coverage_min = 0.80`,
  `variance_days_frac_min = 0.7`, `extreme_outlier_frac_max = 0.05`,
  `extreme_outlier_magnitude = 1e8` all stay as the v1 §5.2 design.
  This PR is about making the existing thresholds mean what the
  design doc said they mean.
- **No change to the `MinedFactorBundle` constructor or the
  registered-factory closure.** Both bugs are deep inside the
  `make_mined_factor_features` / `_make_qlib_handler` bodies; the
  external surface is unchanged.
- **No change to `WalkForwardEngine`, `FeatureDatasetBuilder`, or the
  qlib runtime bootstrap.** All three bugs were in
  `src/data/mined_factor_handler.py` and `src/factor_mining/fitness.py`.
- **No re-tune of the GP miner config.** The under-sized `pop=30
  gen=5` config was a smoke-test artefact; choosing real production
  GP parameters is a separate PR after this hot-fix lands.
- **No PIT bundle expansion.** The B-min bundle (2024-2025 csi300)
  used to discover the bugs is sufficient for the regression
  tests. B-std / B-full bundle expansion is a separate operator
  follow-up.
- **No GPU.** Phase 4 stays skipped per `decisions.md` and the
  existing v1 §5.3 CPU-only contract.
