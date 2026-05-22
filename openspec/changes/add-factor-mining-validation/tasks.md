# Tasks: Factor Mining Validation (Phase 6)

## OpenSpec (propose stage)

- [x] Draft `proposal.md`
- [x] Draft `design.md`
- [x] Draft `tasks.md` (this file)
- [x] Draft `specs/v2-factor-mining-foundations/spec.md` deltas
      (MODIFIED data-gate; ADDED validator IS/OOS split, overfit
      rejection, correlation filter, promotion manual-gate)
- [x] `openspec validate add-factor-mining-validation --strict` — green

## Phase 6 Implementation

### 6.1 `src/factor_mining/validator.py`
- [x] `ValidationCriteria` frozen dataclass with D4 defaults
- [x] `FactorValidationResult` frozen dataclass
- [x] `validate_pool(pool, panel, fwd, criteria)` per-factor IS/OOS
      evaluation + threshold checks
- [x] `filter_correlated(results, panel, criteria)` pool-level
      pairwise filter
- [x] `validate_run(run_dir, panel, fwd, criteria)` convenience wrapper
- [x] No `from qlib …`, no `src.pit` import

### 6.2 `src/factor_mining/promote.py`
- [x] `PromotionConfig` / `PromotionDataConfig` / `PromotionReport`
      frozen dataclasses
- [x] `promote_run(config, dry_run=False) -> PromotionReport`
- [x] Synthetic-mode panel builder (mirrors Phase 3 miner)
- [x] PIT-mode panel via `FactorMiningDataView`
- [x] Write `factor_pool.parquet` + `factor_expressions.json` +
      `promotion_report.json` under `production_dir/version/`
- [x] Refuses to overwrite existing version directory in v1
- [x] argparse CLI:
      `python -m src.factor_mining.promote --run … --to … [--config …] [--dry-run]`
- [x] CLI exits 0 on clean run, non-zero on bad input

### 6.3 `docs/factor_mining/user_guide.md`
- [x] Quickstart (smoke miner → promote → bind)
- [x] Real-PIT path (build bundle, fill default.yaml, miner + promote)
- [x] What each artifact means
- [x] Cross-references to design docs

## Tests

### 6.t1 `tests/logic/factor_mining/test_validator.py`
- [x] `test_criteria_defaults_match_d4` — D4 default thresholds
- [x] `test_split_too_short_is_segment_rejects`
- [x] `test_split_too_short_oos_segment_rejects`
- [x] `test_validate_pool_passes_stable_factor`
- [x] `test_validate_pool_rejects_overfit_factor` — engineered
      pattern: high IS IR (factor == fwd on IS), low OOS IR
      (factor random on OOS); validator rejects with explicit
      reason
- [x] `test_filter_correlated_drops_highly_correlated_factor` —
      two factors with corr > max_pool_correlation; the
      lower-fitness one is dropped
- [x] `test_filter_correlated_preserves_uncorrelated_factors`
- [x] `test_validator_does_not_import_qlib_or_pit`

### 6.t2 `tests/logic/factor_mining/test_promote.py`
- [x] `test_promote_run_dry_run_writes_nothing`
- [x] `test_promote_run_writes_production_files` — verifies
      factor_pool.parquet, factor_expressions.json,
      promotion_report.json exist under production/<version>/
- [x] `test_promote_run_kept_pool_has_only_survivors`
- [x] `test_promote_run_refuses_existing_version_dir`
- [x] `test_promote_cli_smoke` — subprocess invocation; exit 0 + files
- [x] `test_promote_cli_dry_run_flag`

## Validation

- [x] `pytest tests/logic/` — all green
- [x] `ruff check src/factor_mining/validator.py src/factor_mining/promote.py tests/...` — green
- [x] `python -c "import src.factor_mining.validator, src.factor_mining.promote"` — succeeds
- [x] `grep -rn "qlib\.data\|qlib\.init\|from qlib" src/factor_mining/` — zero matches
- [x] `openspec validate add-factor-mining-validation --strict` — green

## Operator follow-up (NOT this proposal — Phase 6.3 walk-forward)

- [ ] Operator builds the PIT bundle per `inventory.md` §F.3.
- [ ] Operator runs the miner on a real PIT range.
- [ ] Operator promotes a run via the CLI (manual gate).
- [ ] Operator wires the promoted bundle into the existing
      `config_walk_n*.yaml` walk-forward config (set
      `feature_handler: "MinedFactor"` and bind via
      `register_mined_factor_handler`).
- [ ] Operator runs walk-forward and compares the OOS Sharpe /
      IR to the Alpha158 baseline per design doc §6.3.

## Project Completion

After Phase 6 is archived, the factor-mining subsystem is **feature-
complete per the design doc**:

- Phase 1: operators + expression + grammar — ✓
- Phase 2: PIT adapter + evaluator + fitness + factor pool — ✓
- Phase 3: GP engine + miner CLI + smoke config — ✓
- Phase 4: GPU — SKIPPED (per design doc OPTIONAL)
- Phase 5: MinedFactor handler bridge — ✓
- Phase 6: IS/OOS validator + promotion CLI + user guide — ✓

The remaining "operator follow-up" items (PIT bundle build,
walk-forward bake-off, `.githooks/pre-commit` D5 grep guard wiring)
are the responsibility of the human operator and are documented in
each phase's archived tasks.md.

## Deferred (NOT this proposal)

- Phase 6.3: walk-forward integration (operator follow-up; gated on
  PIT bundle).
- Phase 4: GPU kernels (SKIPPED).
- `.githooks/pre-commit` D5 grep guard wiring (operator task per
  decisions.md D5 action items).
- Streaming / batched validator for pools with > 1000 factors
  (v1 walks serially; sufficient for v1 pool sizes ≤ 200).
- Regime-aware correlation filtering (v1 uses panel-wide
  correlation; Phase 6.x may add).
- Promotion `--force` flag for overwriting an existing version dir
  (v1 raises; operator chooses a new version label).
