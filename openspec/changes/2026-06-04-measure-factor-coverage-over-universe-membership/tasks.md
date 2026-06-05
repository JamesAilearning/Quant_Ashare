# Tasks: measure-factor-coverage-over-universe-membership

## 1. Evaluator
- [x] `_coverage(factor_values, universe_mask=None)` — members-only
      denominator when mask supplied; all-cells fallback when None.
- [x] `evaluate_factor(..., *, method, universe_mask=None)` forwards the
      mask to `_coverage`; docstring updated.

## 2. Engine plumbing
- [x] `GPEngine.__init__` initialises `self._universe_mask = None`.
- [x] `GPEngine.run(..., universe_mask=None)` stores the mask.
- [x] `evaluate_individual` forwards `self._universe_mask` to
      `evaluate_factor` (signature unchanged).
- [x] Checkpoint resume/reuse guard (Codex P1+P2 on #217): persist a
      coverage-cache key ("all_cells" / "members:<mask fingerprint>") in
      `save_checkpoint`, restore in `load_checkpoint`, and discard
      `fitness_cache`/`_all_evaluated` in `run` when the key differs — a mode
      change OR a different member mask (fingerprint), not just the coarse
      members/all-cells mode. `run` also assigns the mask on every call so a
      mask-free reuse resets to all-cells.

## 3. Miner wiring
- [x] `build_universe_mask(config)` — `FactorMiningDataView.universe_mask()`
      in PIT mode, `None` in synthetic mode (D5: mask via pit_adapter only).
- [x] `run_mining` calls `build_universe_mask` and passes it to
      `engine.run`.

## 4. Tests
- [x] members-relative coverage rescues a factor whose non-member union
      cells are NaN (union < gate → members ≥ gate).
- [x] a NaN on a *member* cell still reduces coverage (gate still bites).
- [x] `universe_mask=None` reproduces the legacy all-cells fraction
      (existing `test_evaluate_factor_coverage_excludes_nan_cells` stays
      green).
- [x] a member cell ABSENT from the factor (mask-only member) counts as
      uncovered — denominator over the mask domain, not dropped/inflated.
- [x] Resume/reuse guard (test_gp_engine): checkpoint persists/restores the
      coverage-cache key; members→all-cells AND different-member-mask resumes
      discard the cache and warn; a matching (same mask) resume keeps it; a
      mask-free reuse resets to all-cells.

## 5. Validation
- [x] Real-PIT spot-check: cs_rank(ts_pctchange($close,5)) members
      coverage ≈ 0.98 (≥ 0.8); 12 terminals all 0.97–0.99.
- [x] daily_basic 6 fields not pathologically sparse (min $pe ≈ 0.97).
- [x] Smoke (200×20) on csi300 2018-2021: pool 0→2001; n_invalid
      500/500→8–69 per gen; pool coverage min 0.800 / median 0.907 /
      max 0.991 (all ≥ 0.8).

## 6. Quality gates
- [x] `ruff check` clean on changed files.
- [x] `mypy` (CI command) clean on changed files.
- [x] `pytest tests/logic/factor_mining/` green (330 passed, incl. the 3
      new coverage tests).
- [x] D5 import-gate test still green (evaluator gains no qlib/pit import).
