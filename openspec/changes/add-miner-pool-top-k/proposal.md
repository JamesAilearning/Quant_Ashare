# Add `pool_top_k` truncation to the miner CLI

## Why

A production-scale GP run on real PIT data (`pop=200 gen=20` on the
B-min csi300 2024-2025 bundle) routinely produces O(10³) factors that
pass the v1 §5.2 validity filters. The first real bake-off
(post-PR #136 hot-fix) produced **2151 factors**. Feeding all of them
into the downstream walk-forward triggers two failure modes that
PR #136's tests didn't catch because synthetic-mode pools cap out
around 20-30 factors:

1. **Windows multiprocessing crash in qlib backtest.** `qlib.backtest`
   spawns workers when the prediction DataFrame is large; the worker
   re-imports `scipy.stats._axis_nan_policy`, which calls
   `copy.deepcopy(self.sections)` on a numpy docstring object and
   raises `SystemError: error return without exception set`, surfacing
   to the engine as `BacktestRunnerError: qlib backtest execution
   failed: [Errno 22] Invalid argument`. With the 2151-factor pool,
   3 of 5 folds failed this way; with the 91-factor pool, all 5 folds
   succeeded. The bug is a known
   Windows + Python 3.11 + scipy + qlib + large-DF interaction; it is
   NOT in this codebase.
2. **LightGBM overfitting.** ~250k training samples × 2151 features
   is a feature/sample ratio of ~1:115, well below the rule-of-thumb
   1:10 that LightGBM handles reliably. The bake-off showed IC went
   from -0.004 (91 factors) to +0.011 (2151 factors) — signal IS being
   discovered — but IR went from -2.48 to -4.80 because the model
   couldn't use the extra signal without overfitting.

Both failure modes share a root cause: **the miner has no upper bound
on pool size**. The v1 §6.2 design assumed an operator-curated pool
of O(10-100) factors; the actual O(10³) was an emergent property of
the GP scale. A `pool_top_k` truncation that runs immediately before
`pool.save(...)` solves both problems without changing GP search
behaviour:

- The GP still explores the full O(10³) candidate space (selection
  pressure on fitness is unchanged).
- Only the top-K (by fitness) are persisted to disk.
- Downstream consumers (handler, walk-forward, validator) see a
  manageable feature set.

The truncation must NOT happen inside the GP loop — that would change
selection / novelty behaviour. It happens once, at save time.

### Why truncation by fitness desc

The v1 §5.1 fitness already encodes the operator's objective:
high |IC|, high IR, low turnover-cost, low novelty-overlap, low
complexity. Top-K by fitness keeps the K factors the design doc
already says are most valuable. Other sort keys (IR, rank-IC, etc.)
are deliberately NOT exposed in v1; if an operator wants to slice
differently, they can do it post-hoc on the saved parquet.

### Why not a hard size cap that aborts the run

A hard cap (e.g. "raise if pool > K") would force the operator to
re-tune GP parameters when they only want a different artefact size.
Truncation is the operator-friendlier knob: the GP run stays
reproducible, only the persisted view narrows.

## What Changes

### `src/factor_mining/miner.py`

- **MODIFY `MinerConfig`**: add `pool_top_k: int | None = None`. None
  preserves the existing "save the entire post-GP pool" behaviour;
  a positive int truncates to that many top-fitness entries.
- **MODIFY `load_config`**: parse `pool_top_k` as an optional
  top-level YAML key. Validate it is a positive int (or `null`);
  non-positive ints raise `ValueError`.
- **ADD `_truncate_pool_to_top_k(pool, k) -> FactorPool`**: helper
  that builds a fresh `FactorPool` containing only `pool.top_k(k,
  by="fitness")`. Deterministic per Phase 1's stable structural hash.
- **MODIFY `run_mining`**: after `pool = engine.run(...)`, if
  `config.pool_top_k is not None` and `len(pool) > config.pool_top_k`,
  reassign `pool = _truncate_pool_to_top_k(pool, config.pool_top_k)`.
  The returned `RunResult.pool` reflects the saved (truncated) pool.
- **MODIFY `run_mining` config snapshot**: write three new fields to
  the per-run `config.yaml` snapshot — `pool_top_k`,
  `full_pool_size_pre_truncation`, `saved_pool_size` — so operators
  can audit truncation after the fact without re-running.
- **MODIFY CLI print line**: include `(top-K by fitness)` suffix when
  truncation was applied.

### Spec deltas

- **MODIFY `v2-factor-mining-foundations`** — "Miner CLI SHALL run
  end-to-end from a YAML config" requirement gets a new clause: the
  CLI MAY accept an optional `pool_top_k` top-level key; the saved
  pool is truncated to the top-K by fitness when it is set.

### Tests

- **ADD `tests/logic/factor_mining/test_miner.py`** (6 new tests):
  - `test_load_config_pool_top_k_absent_is_none`
  - `test_load_config_pool_top_k_parsed_as_int`
  - `test_load_config_pool_top_k_zero_raises`
  - `test_load_config_pool_top_k_negative_raises`
  - `test_run_mining_with_pool_top_k_truncates` — runs a synthetic
    GP that produces ≥ 10 factors, then re-runs with `pool_top_k =
    full // 2` and asserts the saved pool is exactly the top-K by
    fitness from the untruncated run.
  - `test_run_mining_with_pool_top_k_larger_than_pool_is_noop`
  - `test_run_mining_records_pool_top_k_in_config_snapshot` — verifies
    `full_pool_size_pre_truncation` and `saved_pool_size` land in the
    per-run `config.yaml`.

## Non-Goals

- **No change to GP search behaviour.** The within-generation novelty,
  selection, mutation, crossover, and elite preservation paths are
  not touched. The GP still explores the full O(10³) candidate space
  per generation; only the persisted view narrows.
- **No automatic K selection.** Choosing K is an operator decision;
  the design doc doesn't fix it and this PR doesn't either. Typical
  starting values (per the operator follow-up doc): K=30-100 for
  walk-forward, K=20 for promotion to production.
- **No alternate sort keys.** Truncation is by `fitness` desc only.
  If operators want IR-desc or rank-IC-desc, they can post-hoc the
  saved parquet.
- **No truncation in the validator or promote CLI.** Those have their
  own selection logic (`max_pool_correlation`, OOS thresholds) which
  is separate from save-time truncation.
- **No change to default `pool_top_k`**. Stays `None` (save the entire
  post-GP pool) so existing operator workflows and the smoke test
  continue to produce the same artefacts.
- **No fix for the underlying qlib + scipy + Windows multiprocessing
  bug.** That's an upstream dependency-stack issue; truncating to K
  factors avoids triggering it. A separate PR could pin scipy or
  patch qlib's backtest to force single-process, but that's out of
  scope here.
