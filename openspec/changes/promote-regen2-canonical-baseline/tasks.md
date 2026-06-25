# Tasks: Promote REGEN-2 (total-return) to the canonical walk-forward baseline

## OpenSpec (propose stage)

- [x] Draft proposal.md / tasks.md
- [x] Draft `specs/v2-canonical-backtest-contract/spec.md` delta (deferral → applied)
- [x] `openspec validate promote-regen2-canonical-baseline --strict` green

## Implementation (atomic — single PR)

- [x] (a) Swap canonical root fixture `tests/regression/fixtures/walk_forward_baseline_metrics.json`
  REGEN-A → REGEN-2 (regenerated on the canonical numpy<2 stack, `_status.canonical=true`);
  delete `tests/regression/fixtures/regen2_tr/`
- [x] (b) Migrate `tests/governance/test_regen_baseline_value_pin.py`:
  - [x] floor `> 0.40` → two-sided band `0.20 < IR < 0.35` (brackets 0.278; excludes
        0.4815 / 0.3672 / off-pin 0.162 / 0)
  - [x] `regen` "REGEN-A" → "REGEN-2"; `benchmark_note` deferral → APPLIED
        (assert `SH000300TR` / "total-return", not "deferred")
  - [x] `per_fold >= 22` → `>= 23`; keep T+1 / limit / ST + SE/noise/NOT/live caveat tokens
  - [x] pin comment carries the fold-0 degenerate-tie-break fragility + a new test
        machine-izes `fold0_known_limitation`
- [x] (c) Split the REGEN-A replay test: preserve `regen_a/walk_forward_baseline_metrics_regen_a.json`
        + repoint `test_walk_forward_replay_baseline.py` (RUN_E2E control) there; repoint the
        CI-real `test_walk_forward_replay_baseline_regen2.py` at the root
- [x] (d) Flip 9 default sites `SH000300 → SH000300TR` (config_walk, config, config_smoke,
        wf/config.py, pipeline.py, presets/{default,production,smoke}, UI config_run) + comments
- [x] (e) Apply this contract delta to `openspec/specs/v2-canonical-backtest-contract/spec.md`
- [x] (f) `test_canonical_benchmark_default_consistency.py`: SEMANTIC invariants — in-code
        dataclass defaults + EVERY tracked config YAML == SH000300TR, REGEN-A control stays
        SH000300, TR↔price suffix-pairing intact (not a hand-list of 9 sites)
- [x] (g) Update `tests/regression/fixtures/README.md` REGEN-A → REGEN-2

## Must-not-touch (stay price / REGEN-A) — VERIFIED UNCHANGED

- [x] `scripts/regen/replay_frozen_baseline.py` BENCHMARK = "SH000300" + deferral note (only `--out` default repointed)
- [x] `tests/regression/fixtures/regen_a/frozen_fold_scores.pkl.gz`
- [x] `scripts/data_pipeline/07_ingest_benchmark.py` price ingest (DEFAULT_INDEX_MAP keeps both codes)
- [x] `src/contracts/benchmark_data_contract.py` `tr_price_pairs`
- [x] `openspec/changes/archive/**`

## Verify

- [x] `pytest tests/governance/ -q` green (value-pin now REGEN-2; consistency-guard added) — 185 passed
- [x] fast suite green (`pytest --ignore=...regen2...`) — 2666 passed, 28 skipped
- [x] no stray reference to the deleted `regen2_tr/` staging
- [ ] CI ubuntu-3.12 cireal green on the pushed branch (reproduces the new root)
