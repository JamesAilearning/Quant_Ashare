# Tasks: daily-update-orchestrator

## 1. Implementation
- [x] `TushareFetcherConfig.refresh_current` + `01 --refresh-current`: bypass
      exists-skip for stock_basic (both buckets), namechange / suspend_d, and
      the FINAL year of daily / adj_factor / daily_basic; past years stay
      skipped; index_weight untouched.
- [x] `src/data_pipeline/bundle_swap.py`: two-stage rename swap (`provider →
      .bak`, `.new → provider`), `.bak` kept as rollback (cleared at next
      swap), `check_and_repair` resolving all crash states (complete
      interrupted swap / restore backup-only / remove unproven stale `.new`),
      dry-run reports without mutating.
- [x] `src/data_pipeline/daily_update.py` + `scripts/daily_update.py`: stages
      fetch → snapshot check → 02 → 05 → 03 → 04 (05 first: its promote
      replaces the dir) → 06 on `<provider>.new` → swap. Distinct exit codes
      10-16; explicit-argv path flow (Step 0 verified: all six scripts are
      pure argparse, no QUANT_ coupling); importlib in-process invocation;
      `--allow-holey-fetch` passthrough to the build gate ONLY; `--dry-run`
      prints plan + bundle state, zero side effects.
- [x] Snapshot stage: embedded snapshot_date (P3-5) must equal the run date;
      stale/missing → exit 13 unless --allow-holey-fetch (warn + continue).
- [x] codex P1: prior-manifest holes are wired into the fetcher as
      force_retry_units (01 reads the manifest at run start; corrupt →
      invalidate + exit 1) so a holed unit whose stale file exists — incl.
      across a year boundary — is re-attempted instead of being shadowed by
      the exists-skip and wrongly merge-dropped as self-healed.
- [x] codex P2: the orchestrator freezes ONE run date and uses it for the
      fetch stamp (01 --snapshot-date), the default end_date, and the snapshot
      verification — a fetch spanning midnight cannot fail its own check.

## 2. Tests (all fake runners / synthetic dirs in temp dirs; no real fetch,
##    no real qlib build, never touches real data paths)
- [x] CRASH-INJECTION ×3 (red line): after build (stale `.new` removed, live
      untouched); between rename 1 and 2 (true injection via patched
      Path.rename — next startup COMPLETES the swap); after rename 2 (healthy,
      nothing touched). Plus backup-only restore + orphan `.new` removal +
      repair dry-run no-mutate.
- [x] VALIDATE-FAIL (red line): validate exit ≥ 2 (a check failed) → swap never
      runs, live bundle byte-identical, no `.bak` created, `.new` left for
      autopsy. Exit 1 (warnings-only = every check passed; codex P1) → swap
      proceeds with the warnings logged loudly.
- [x] HOLES (red line): fetch exit 3 without override → stop (exit 12),
      nothing after fetch runs; with --allow-holey-fetch → continues.
- [x] SHORT-CIRCUIT (red line): fetch hard-fail → only fetch ran; rebuild
      stage fail → stops there (exit 14).
- [x] DRY-RUN (red line): zero side effects — no runner called, no repair
      performed, nothing written.
- [x] REFRESH: final-year re-pulled / past years skipped; stock_basic +
      aggregates re-pulled (fresh embedded snapshot_date); index_weight not
      refreshed.
- [x] Snapshot stage: today-stamp passes; stale → exit 13 + nothing after;
      missing file → exit 13; stale + override → continues.
- [x] Plan wiring: staged dir in bins/membership/universe/validate argv;
      --allow-holey-fetch only in bins argv; end_date defaults to now.
- [x] Startup repair runs BEFORE stages (interrupted swap completed, then the
      new run builds + swaps on top).

## 3. Verification
- [x] New test files green (11 swap + 12 orchestrator + 3 refresh);
      full fast suite + pit green; ruff + mypy clean;
      openspec validate --strict.
