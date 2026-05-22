# Tasks: PIT Universe Foundation

## OpenSpec (this PR — Phase 0.1)

- [x] Add `v2-pit-universe-foundation` capability spec with entity model,
      NaN-gap invariant, query-layer contract, migration safety, scope
      boundary rules
- [x] Add proposal, design, and tasks
- [x] Copy canonical design doc into `docs/pit/pit_universe_design.md`
- [ ] `openspec validate pit-universe-foundation --strict` — green

## Phase 0.2 — User-curated reference cases (NOT this PR)

- [ ] User commits `tests/pit/reference_cases.yaml` with ≥10 hand-verified
      cases covering both ticker-reuse and pure-delisting patterns
- [ ] Agent does NOT auto-generate this seed (design §11, §14 point 7)

## Phase 0.3 — Tushare access validation (NOT this PR)

- [ ] One-off script confirms `TUSHARE_TOKEN` works for `stock_basic`,
      `namechange`, `index_weight` endpoints
- [ ] Account tier recorded in proposal (basic / 2000pt / pro)

## Phase A — Foundation (follow-up PRs)

- [ ] A.1 Tushare ingestion script (`scripts/data_pipeline/01_fetch_tushare.py`)
- [ ] A.2 Entity resolution algorithm (`scripts/data_pipeline/02_resolve_entities.py`
      plus `tests/pit/test_entity_resolution.py`)
- [ ] A.3 Extend `reference_cases.yaml` with cited rows (each row's PR body
      cites the Tushare response that justifies it)
- [ ] A.4 Index membership resolver
      (`scripts/data_pipeline/03_resolve_index_membership.py`)

## Phase B — Data Pipeline (follow-up PRs)

- [ ] B.1 Universe file builder
- [ ] B.2 qlib bin builder with NaN gaps
- [ ] B.3 PIT validation suite (Stage 6.A-E, including qlib operator
      `min_periods` boundary check)
- [ ] B.4 Re-run §2 survivorship verification — verdict GOOD

## Phase C — Query Layer (follow-up PRs)

- [ ] C.1 `PITDataProvider` class (`src/pit/query.py`)
- [ ] C.2 LRU cache layer (`src/pit/cache.py`)
- [ ] C.3 Parametrized invariant tests (using real qlib operators, not
      pandas rolling)
- [ ] C.4 Spot-check tests against `reference_cases.yaml`

## Phase D — Integration (follow-up PRs)

- [ ] D.1 Wire factor mining evaluator to PIT layer
- [ ] D.2 Wire training pipeline to PIT layer
- [ ] D.3 Wire backtester to PIT layer (skip orders on tickers not in
      universe on the trade date)
- [ ] D.4 Migration guide (`docs/pit-migration-guide.md`)
- [ ] D.5 Calibration run — side-by-side metrics comparison vs legacy
      provider

## Phase Gating (per design §15.5)

Each phase boundary requires an explicit user signal before the next phase
starts. Signals: (a) "Phase X acceptance ✓" in the merge commit, OR
(b) checkbox ticked here on `main`, OR (c) direct OpenCode-session message.
