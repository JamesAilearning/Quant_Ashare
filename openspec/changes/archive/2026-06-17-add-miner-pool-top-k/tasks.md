# Tasks: Add `pool_top_k` truncation to the miner CLI

## OpenSpec (propose stage)

- [x] Draft proposal.md / tasks.md
- [x] Draft `specs/v2-factor-mining-foundations/spec.md` delta
      (MODIFIED — Miner CLI gains `pool_top_k` knob)
- [x] `openspec validate add-miner-pool-top-k --strict` green

## Implementation

- [x] `MinerConfig.pool_top_k: int | None = None`
- [x] `load_config` parses + validates positive-int / None
- [x] `_truncate_pool_to_top_k(pool, k) -> FactorPool` helper
- [x] `run_mining` reassigns pool to truncated pool when
      `pool_top_k` set and `len(pool) > pool_top_k`
- [x] Config snapshot records `pool_top_k`,
      `full_pool_size_pre_truncation`, `saved_pool_size`
- [x] CLI print line shows `(top-K by fitness)` when truncated

## Tests

- [x] `test_load_config_pool_top_k_absent_is_none`
- [x] `test_load_config_pool_top_k_parsed_as_int`
- [x] `test_load_config_pool_top_k_zero_raises`
- [x] `test_load_config_pool_top_k_negative_raises`
- [x] `test_run_mining_with_pool_top_k_truncates`
- [x] `test_run_mining_with_pool_top_k_larger_than_pool_is_noop`
- [x] `test_run_mining_records_pool_top_k_in_config_snapshot`

## Validation

- [x] `pytest tests/logic/factor_mining/test_miner.py -q` — 19/19
- [x] `pytest tests/logic/ -q` — full suite green (1125 passed, 19
      skipped, 4 warnings, 34 subtests in 1m51s)
- [x] `ruff check src/ tests/ scripts/` — green
- [x] `openspec validate add-miner-pool-top-k --strict` — green
- [x] D5 grep zero matches under `src/factor_mining/`
- [x] **Empirical verification on B-min csi300 2024-2025 bundle**:
      - Took the existing 2151-factor pool from the post-PR #136
        bake-off and truncated to top-50 via `_truncate_pool_to_top_k`.
      - Walk-forward bake-off completed in 5 minutes (vs ~50 min
        with the 2151-factor pool) — **4/5 folds succeeded** (fold 4
        is the known calendar edge-case bug, unrelated to this PR;
        the 2151-factor run had only 2/5 succeed).
      - **`design_doc_ir_threshold_met = TRUE`** — MinedFactor wins
        on 3/4 design-doc §10 success metrics:
        - IR: -1.66 (vs Alpha158 -1.98; +16.5% relative improvement)
        - AnnRet: -12.0% (vs -12.7%)
        - Drawdown: -8.05% (vs -9.65%)
        - IC_1d: +0.0176 (vs +0.0324 — only metric where Alpha158
          still wins, on raw signal correlation)
      - Confirms both upstream failure modes (qlib multiprocessing
        `[Errno 22]` + LightGBM overfit) are resolved by top-K
        truncation alone; no qlib / scipy patches needed.
- [ ] CI green on push (no `--admin` merge)

## Deferred (NOT this proposal)

- Underlying qlib + scipy + Windows multiprocessing bug fix.
- Alternate truncation sort keys.
- Per-validator / promote-time truncation.
- GPU.
