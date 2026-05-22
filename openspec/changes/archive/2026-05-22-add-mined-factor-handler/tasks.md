# Tasks: MinedFactor Handler (Phase 5)

## OpenSpec (propose stage)

- [x] Draft `proposal.md`
- [x] Draft `design.md`
- [x] Draft `tasks.md` (this file)
- [x] Draft `specs/v2-feature-handler-registry/spec.md` (MODIFY:
      add explicit-bind requirement for MinedFactor)
- [x] Draft `specs/v2-mined-factor-handler/spec.md` (NEW capability,
      5 requirements)
- [x] `openspec validate add-mined-factor-handler --strict` — green

## Phase 5 Implementation

### 5.1 `src/data/mined_factor_handler.py`
- [x] `MinedFactorBundle` frozen dataclass (pool_dir, pit_provider_uri,
      delisted_registry_path, universe_name_override) with
      `__post_init__` validation
- [x] `MinedFactorHandlerError` exception
- [x] `make_mined_factor_features(bundle, config, *, panel=None,
      forward_return=None)` — data-pure core: loads pool, evaluates
      each expression, returns `(instrument, datetime)` MultiIndex
      DataFrame with `mf_<hex_hash>` columns sorted by fitness desc
- [x] `register_mined_factor_handler(bundle, *, name="MinedFactor",
      replace=False)` — registers a closure-style factory via
      `register_feature_handler`
- [x] Lazy qlib import inside the factory body only

### 5.2 `research/mined_factors/README.md`
- [x] Documents the runs/ candidates/ production/ structure per
      decisions.md D4
- [x] Example of how to bind a pool via
      `register_mined_factor_handler`
- [x] Notes that the directory contents are gitignored

## Tests

### 5.t1 `tests/logic/test_mined_factor_handler.py`
- [x] `test_bundle_post_init_accepts_valid_pool`
- [x] `test_bundle_post_init_rejects_missing_pool_dir`
- [x] `test_make_features_returns_multiindex_dataframe`
- [x] `test_make_features_column_naming_uses_hex_hash`
- [x] `test_make_features_column_order_is_fitness_desc`
- [x] `test_make_features_empty_pool_raises`
- [x] `test_register_handler_calls_register_feature_handler`
- [x] `test_lazy_qlib_import` — importing the module does NOT pull
      qlib into `sys.modules`
- [x] `test_factory_raises_clearly_in_pit_mode_with_empty_uri`

## Validation

- [x] `pytest tests/logic/test_mined_factor_handler.py tests/logic/factor_mining/ -v` — all green
- [x] `ruff check src/data/mined_factor_handler.py tests/logic/test_mined_factor_handler.py` — green
- [x] `python -c "import src.data.mined_factor_handler"` — succeeds
      AND does NOT import qlib (verified in test_lazy_qlib_import)
- [x] `grep -rn "qlib\.data\|qlib\.init\|from qlib" src/factor_mining/`
      — zero matches (D5 strict gate, unchanged from Phase 3)
- [x] `openspec validate add-mined-factor-handler --strict` — green

## Phase Gate

Phase 6 (`add-factor-mining-validation`) MAY begin after this change
archives. Phase 6 implements IS/OOS validation + promotion CLI;
neither requires a live qlib pipeline run.

## Operator follow-up (NOT this proposal, gates Phase 6 promotion)

- [ ] Operator builds the PIT bundle per `inventory.md` §F.3.
- [ ] Operator runs the Phase 3 miner on a real PIT range and
      generates `research/mined_factors/runs/{run_id}/`.
- [ ] Operator binds the run via
      `register_mined_factor_handler(MinedFactorBundle(pool_dir=...))`
      from the application's pipeline-startup code (or via the
      Phase-5 example in `research/mined_factors/README.md`).
- [ ] Operator runs the training pipeline with
      `feature_handler: "MinedFactor"` and confirms it produces a
      backtest report.
- [ ] Operator notes the Sharpe / IR vs `feature_handler: "Alpha158"`
      baseline. The number itself is "whatever it is" per the
      design doc Phase 5 stopping point.

## Deferred (NOT this proposal)

- Phase 6: IS/OOS validator, promotion CLI, walk-forward integration,
  user-facing docs.
- Multi-pool registration patterns beyond "register one bundle per
  name".
- Streaming / batched factor evaluation for very large pools.
- Switching from 64-bit structural hash to sha256 for column-name
  collision resistance (would only matter at pool sizes > 1e9).
