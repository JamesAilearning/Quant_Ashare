# Add Factor Mining Validation — IS/OOS validator + promotion CLI + user guide

## Why

Phase 5 (`add-mined-factor-handler`, archived) wired the factor-pool
output into the existing feature-handler registry. Phase 6 is the
last phase: it makes mined factors **trustworthy** before they reach
production by adding:

- `validator.py` — IS/OOS validation. Splits a factor pool's
  evaluation into in-sample (IS) and out-of-sample (OOS) date
  ranges; rejects factors whose OOS IR / OOS RankIC fall below
  configured thresholds. The validator demonstrably rejects the
  classic "high IS IR, ~0 OOS IR" overfit pattern (per design doc
  §6 Phase 6 acceptance).
- `promote.py` — promotion CLI. Validates a Phase 3 miner run,
  drops factors that fail OOS criteria and pairwise pool
  correlation, copies the survivors into a versioned production
  directory under `research/mined_factors/production/{version}/`.
  Per `decisions.md` D4 ("Manual gated"), the CLI runs only when
  the operator invokes it — no auto-promotion.
- `docs/factor_mining/user_guide.md` — short user-facing guide so a
  new user can run the smoke miner → validate → promote sequence
  from the docs alone (design doc §6.4).

The 6.3 walk-forward integration (per design doc §6 Phase 6 row) is
deferred to operator follow-up: it requires a built PIT bundle plus
a walk-forward run, which `inventory.md` §F.3 documents as not yet
available on this machine. The Phase 6 work here ships the validator
and promote contract; the walk-forward bake-off vs Alpha158 baseline
is a downstream pipeline-run task.

### Why split validation from promotion

The validator is a pure metric function (pool + panel + criteria →
per-factor pass/fail). The promotion CLI orchestrates the validator,
adds pool-level pairwise correlation filtering (per `decisions.md`
D4 criteria), and performs the file-copy + manifest write. Splitting
them lets the validator be unit-tested without touching the
filesystem and lets the CLI be a thin wrapper that's easier to
maintain.

### Why "manual gated" stays manual

`decisions.md` D4 locked promotion as **manual gated** — no
auto-promotion. The CLI is invoked by a human; it rejects bad runs
with explicit reasons but never silently advances anything. Phase 6
preserves this. The `--dry-run` flag prints the would-be promotion
report without touching disk.

## What Changes

- **Add `src/factor_mining/validator.py`** — pure-Python validator:
  - `ValidationCriteria` frozen dataclass (`is_oos_split_date`,
    `min_oos_ir`, `min_oos_rank_ic_mean`, `max_pool_correlation`,
    `min_obs_per_segment`).
  - `FactorValidationResult` frozen dataclass (`expr_hash`,
    `expr_str`, `passes`, `reasons`, IS metrics, OOS metrics).
  - `validate_pool(pool, panel, forward_return, criteria) -> list`
    — per-factor walk: evaluate on IS panel slice, evaluate on
    OOS panel slice, check OOS thresholds, return result.
  - `filter_correlated(results, panel, criteria) -> list` —
    pool-level pairwise filter: drop any factor whose correlation
    against a higher-fitness already-kept factor exceeds
    `max_pool_correlation`.

- **Add `src/factor_mining/promote.py`** — promotion CLI:
  - `PromotionConfig` frozen dataclass (run_dir, production_dir,
    `ValidationCriteria`, data-source spec mirroring Phase 3's
    `DataConfig`).
  - `promote_run(config, *, dry_run=False) -> PromotionReport` —
    orchestrates: load pool → build panel → validate → filter
    correlated → copy survivors → write
    `promotion_report.json`.
  - `PromotionReport` frozen dataclass (counts, per-factor
    results, output path).
  - `__main__` argparse CLI:
    `python -m src.factor_mining.promote --run <run_dir>
    --to <version> [--config <path>] [--dry-run]`.

- **Add `docs/factor_mining/user_guide.md`** — short user-facing
  guide. Covers: smoke miner, validator-as-library, promote CLI,
  bind-into-pipeline. Cross-references the existing design docs
  for depth.

- **MODIFY `v2-factor-mining-foundations`** — extend the
  "Phase 1 SHALL NOT access qlib …" data-gate requirement so the
  rule explicitly covers Phase 6 modules (`validator.py` and
  `promote.py`). Neither imports qlib; PIT-mode data goes through
  `FactorMiningDataView` like Phase 3's miner.

- **ADD requirements** under `v2-factor-mining-foundations`:
  - Validator IS/OOS split contract.
  - Validator overfit-rejection guarantee (a factor with high IS
    IR but near-zero OOS IR SHALL fail).
  - Promotion CLI manual-gate contract (no auto-promotion;
    `--dry-run` flag).
  - Promotion writes a JSON manifest documenting every
    accept/reject decision.

## Non-Goals

- **No walk-forward integration.** Design doc §6.3 — operator
  follow-up; requires PIT bundle + walk-forward harness. Phase 6
  ships the validator + promote contract; walk-forward is a Phase
  6.1 (operator) task per `inventory.md` §F.3.
- **No edits to Phase 1-5 source modules**. Phase 6 is purely
  additive in `src/factor_mining/{validator,promote}.py` plus the
  docs file plus its tests.
- **No edits to `src.pit`, `src.data.pit`, `src.core._ic_utils`,
  `src.data.feature_dataset_builder`,
  `src.data.mined_factor_handler`.** Phase 6 reuses these as
  upstream layers.
- **No automatic promotion.** Per `decisions.md` D4 — human in the
  loop.
- **No GPU code.** Phase 4 skipped.
- **No edits to `decisions.md` or `inventory.md`.** They are the
  current source of truth.
- **No streaming validator for very large pools.** v1 walks the
  pool serially; this is fine for typical pool sizes (≤ 200
  factors).
- **No standalone web UI for the promotion gate.** CLI only in
  v1; the existing operator UI may surface promote results in a
  later phase.
- **No commit of any actual mined-factor run.** Tests use synthetic
  pools and synthetic panels.
