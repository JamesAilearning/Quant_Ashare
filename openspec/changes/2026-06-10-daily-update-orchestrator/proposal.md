# Proposal: daily-update-orchestrator

## Why

Bringing the system current is today a hand-run chain of six numbered scripts
with hand-typed paths; nothing guarantees the order, nothing verifies the
active-stocks snapshot actually refreshed, a half-finished rebuild can leave the
LIVE bundle replaced by an unvalidated one, and resume's exists-skip means a
plain re-run of 01 never fetches today's bars at all (the current-year files
already exist). P3-4b/4c built the manifest + gates; P3-6a wires them into one
fail-loud, crash-safe entry point.

## What Changes

- **`--refresh-current` (fetcher + 01)**: ignore resume's exists-skip for
  exactly the units a daily update must bring current — `stock_basic` (both
  buckets), the `namechange` / `suspend_d` aggregates, and the FINAL year of
  the requested range for the per-ticker endpoints (daily / adj_factor /
  daily_basic). Past years stay resume-skipped. `index_weight` is NOT refreshed
  (one full-range file per index; hundreds of calls per day — refresh
  membership on its own cadence).
- **`scripts/daily_update.py`** (thin CLI) + **`src/data_pipeline/daily_update.py`**:
  fetch → snapshot check → rebuild → validate → swap, each stage fail-loud and
  short-circuiting, each failure a DISTINCT exit code (10-16). Paths flow
  END-TO-END as explicit argv into the numbered scripts' `main(argv)` entry
  points (all six are pure-argparse — no `QUANT_*` coupling in the chain; the
  scripts are loaded via importlib, in-process). `--allow-holey-fetch` passes
  through to the build gate ONLY — the recommend-side override is untouched.
  `--dry-run` prints every stage's argv + the bundle state and executes nothing.
- **Snapshot stage**: after the fetch, the embedded `snapshot_date` (P3-5) of
  `active_stocks.parquet` must equal the run date — proof the refresh landed.
  Stale/missing → refuse (exit 13), unless `--allow-holey-fetch` (which already
  sanctions partial data; the manifest carries the stock_basic hole and the
  bundle gets stamped built-from-holey-fetch downstream).
- **Rebuild order 02 → 05 → 03 → 04 into `<provider>.new`**: 05's
  staging-promote REPLACES its output dir, so instruments written by 03/04 must
  land after it. 06 validates `<provider>.new` — never the live bundle.
- **`src/data_pipeline/bundle_swap.py`** — the atomic-swap contract:
  stage 1 `provider → provider.bak`, stage 2 `provider.new → provider` (two
  same-volume renames; a crash can only land BETWEEN them, never mid-copy).
  `.bak` is kept as instant rollback and cleared at the next swap.
  `check_and_repair` at startup resolves every reachable crash state: mid-swap
  (`.bak` + `.new`, no live — stage 1 proves validation passed) → COMPLETE the
  swap; backup-only → RESTORE; stale `.new` (cannot be proven validated) →
  REMOVE loudly. Dry-run reports without mutating.

## Non-Goals

- No scheduling (Phase 4 owns cron/automation).
- No recommend-side override (`--allow-holey-recommend` stays a separate,
  manual decision at the trade boundary).
- No validator extension (06's check list is what it is).
- No true manifest-driven incremental fetch (refresh-current re-pulls the final
  year wholesale; coverage-based incremental is future work).
- No index_weight refresh cadence.
- No reader-concurrent swap (versioned dirs + junction/symlink indirection):
  the two-rename swap is crash-atomic, and Phase 4 scheduling serializes the
  update against readers; a concurrent reader in the rename window fails loud.
