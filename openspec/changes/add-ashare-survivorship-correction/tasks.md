# Tasks: A-share Survivorship Correction

## OpenSpec (this PR)

- [x] Remove `openspec/changes/pit-universe-foundation/` (capability was
      never archived; no spec delta needed)
- [x] Add `v2-ashare-survivorship-correction` capability with 9
      requirements scoped to the A-share problem
- [x] Rewrite `docs/pit/pit_universe_design.md` (drop entity model;
      replace fabricated examples with verified Tushare data; replace
      ≥10 rule with coverage matrix; add borrow-shell policy)
- [x] Add `scripts/data_quality/verify_survivorship.py` with corrected
      `KNOWN_DELISTED` list (3 verified delistings) and prior-list
      post-mortem in the docstring
- [ ] `openspec validate add-ashare-survivorship-correction --strict` — green
- [ ] `pytest tests/governance/ tests/logic/` — green

## Phase 0.2 — User-curated reference cases (NOT this PR)

- [ ] User commits `tests/pit/reference_cases.yaml` covering the
      delisting eras matrix (see design.md). ~8 cases minimum:
  - [ ] 1 × pre-2020 financial delisting (e.g. 600087 退市长油 2014-06-05)
  - [ ] 1 × 2020-2022 \*ST → 退市 (e.g. 600247 \*ST成城退 2021-03-22)
  - [ ] 1 × 2024+ post-退市新规 (e.g. 000023 \*ST深天退 2024-09-02)
  - [ ] 1 × ChiNext / STAR board delisting (agent pulls Tushare candidates; user verifies)
  - [ ] 1 × same-day multi-stock batch delisting (agent pulls Tushare; user verifies)
  - [ ] 1 × negative control: active stock (e.g. SH600519 贵州茅台)
  - [ ] 2 × CSI300 constituent change (enter + leave; agent has Tushare-verified candidates already)
- [ ] Agent additions in later phases require Tushare API citation per row

## Phase 0.3 — Tushare access validation (NOT this PR)

- [ ] One-off script confirms `TUSHARE_TOKEN` works for `stock_basic`,
      `namechange`, `index_weight` endpoints (token confirmed working
      during this baseline correction work, 5000-point tier)

## Phase A — Foundation (follow-up PRs, scope simplified)

- [ ] A.1 Tushare ingestion script (`scripts/data_pipeline/01_fetch_tushare.py`)
- [ ] A.2 Delisted registry builder (`scripts/data_pipeline/02_build_delisted_registry.py` —
      no longer entity-resolution; one row per ticker via `stock_basic(list_status='D')`)
- [ ] A.3 Extend `reference_cases.yaml` with cited rows
- [ ] A.4 Index membership resolver
      (`scripts/data_pipeline/03_resolve_index_membership.py`)

## Phase B — Data Pipeline (follow-up PRs)

- [ ] B.1 Universe file builder
- [ ] B.2 qlib bin builder with NaN-after-delist (no NaN gap mid-life)
- [ ] B.3 PIT validation suite (qlib operator `min_periods` against
      delist boundary)
- [ ] B.4 Re-run corrected survivorship verification

## Phase C — Query Layer (follow-up PRs)

- [ ] C.1 `PITDataProvider` class — without `resolve_entity`
- [ ] C.2 LRU cache layer
- [ ] C.3 Parametrized invariant tests (real qlib operators)
- [ ] C.4 Spot-check tests against `reference_cases.yaml`

## Phase D — Integration (follow-up PRs)

- [ ] D.1 Wire factor mining evaluator to PIT layer
- [ ] D.2 Wire training pipeline to PIT layer
- [ ] D.3 Wire backtester to PIT layer
- [ ] D.4 Migration guide
- [ ] D.5 Calibration run vs legacy provider

## Phase Gating (unchanged from prior plan)

Each phase boundary requires explicit user signal: merge-commit
"Phase X acceptance ✓" OR checkbox tick on `main` OR direct session
message.
