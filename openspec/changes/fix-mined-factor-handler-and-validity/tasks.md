# Tasks: Fix MinedFactor handler integration bugs and validity sanity-check overlap

## OpenSpec (propose stage)

- [x] Draft proposal.md / design.md / tasks.md
- [x] Draft `specs/v2-mined-factor-handler/spec.md` deltas
      (MODIFIED — index order; ADDED — universe-name resolution)
- [x] Draft `specs/v2-factor-mining-foundations/spec.md` deltas
      (MODIFIED — sanity check uses finite-cell denominator)
- [x] `openspec validate fix-mined-factor-handler-and-validity --strict` —
      green

## Implementation

### Bug #1 — fitness `_extreme_outlier_frac` NaN double-count

- [x] `src/factor_mining/fitness.py::_extreme_outlier_frac` —
      switch denominator to finite-cell count
- [x] All-NaN factor returns 0.0 (coverage check binds)
- [x] Docstring spells out the separation from the coverage check
      and cites v1 §5.2

### Bug #2 — handler passes raw universe name to qlib

- [x] `src/data/mined_factor_handler.py::_make_qlib_handler` —
      resolve `str` instruments via `D.list_instruments(D.instruments(name),
      start_time, end_time, as_list=True)`
- [x] List inputs pass through unchanged
- [x] qlib import stays inside the function body (lazy)

### Bug #3 — handler MultiIndex order is wrong for qlib

- [x] `src/data/mined_factor_handler.py::make_mined_factor_features` —
      `reorder_levels(["datetime", "instrument"])` per stacked entry
- [x] Final `features` index has names `("datetime", "instrument")`
      AND is monotonically increasing
- [x] `_build_label_dataframe` reorders the label too (same fix
      pattern, same downstream `StaticDataLoader` contract)

## Tests

### `tests/logic/factor_mining/test_fitness.py` — 3 new sanity tests

- [x] `test_sanity_does_not_count_nan_as_outlier`
- [x] `test_sanity_extreme_outliers_in_finite_cells_still_fail`
- [x] `test_sanity_all_nan_factor_returns_zero_outlier_frac`

### `tests/logic/test_mined_factor_handler.py` — 1 modified + 3 new tests

- [x] MODIFY `test_make_features_returns_multiindex_dataframe` —
      assertion changes to `["datetime", "instrument"]`
- [x] `test_make_features_index_order_is_datetime_then_instrument` —
      explicit regression for bug #3
- [x] `test_factory_resolves_universe_name_to_ticker_list` — qlib
      stubbed via `monkeypatch.setitem(sys.modules, ...)`; asserts
      `D.list_instruments` resolves the name and the list is what
      reaches `DataHandlerLP`
- [x] `test_factory_passes_through_explicit_ticker_list` — verifies
      list inputs do NOT trigger `D.list_instruments`

## Validation

- [x] `pytest tests/logic/factor_mining/test_fitness.py -q` — 22/22
- [x] `pytest tests/logic/test_mined_factor_handler.py -q` — 18/18
- [x] `pytest tests/logic/ -q` — full suite green (1118 passed, 19
      skipped, 1 warning, 34 subtests in 1m49s)
- [x] `ruff check src/ tests/ scripts/` — green (full-repo, as CI
      does on ubuntu-latest 3.11)
- [x] `openspec validate fix-mined-factor-handler-and-validity --strict` —
      green
- [x] D5 grep still zero matches under `src/factor_mining/`:
      `grep -rn "qlib\.data\|qlib\.init\|from qlib" src/factor_mining/`
- [ ] CI green on push (no `--admin` merge)

## Operator follow-up (after this PR merges)

- [ ] Re-run the end-to-end MinedFactor walk-forward on the B-min
      bundle with default `FitnessConfig` (no
      `extreme_outlier_frac_max: 0.5` workaround):
      `python scripts/run_walk_forward.py config_walk_mined_pit_smoke.yaml`
      (the smoke YAML stays as a local-only operator artefact, not
      checked in)
- [ ] Re-tune GP defaults (`pop≥200`, `gen≥30`, novelty pressure)
      in a separate PR — the smoke `pop=30 gen=5` was a wiring
      verification, not a real production config

## Deferred (NOT this proposal)

- GP default re-tune (separate PR — depends on this hot-fix).
- B-std / B-full PIT bundle expansion (separate operator action —
  doesn't change any code path this PR touches).
- Multi-vintage PIT comparison.
- GPU.
