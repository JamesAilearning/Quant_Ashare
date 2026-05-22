# Tasks: PIT turnover-rate field

## Phase 1 — OpenSpec scaffolding (THIS PR)

- [x] Create change folder `add-pit-turnover-field/`
- [x] Write `proposal.md`
- [x] Write `design.md` (Tushare endpoint choice, storage layout,
      bin builder change, backwards compatibility, validation, tests)
- [x] Write `specs/v2-ashare-survivorship-correction/spec.md` with
      ADDED requirement for the `turn` field
- [ ] `openspec validate add-pit-turnover-field --strict` — green
- [ ] User sign-off before any code lands

> Phase 1 is spec-only. No Python lands in this PR.

## Phase 2 — Tushare ingest (follow-up PR)

- [ ] 2.1 Extend `TushareFetcher.ENDPOINTS` from 6 to 7 to include
      `daily_basic`
- [ ] 2.2 Add a `_fetch_daily_basic` method in
      [src/data/tushare/fetcher.py](src/data/tushare/fetcher.py)
      mirroring the per-ticker / per-year parquet layout of
      `_fetch_daily` and `_fetch_adj_factor`
- [ ] 2.3 Per-file resume / atomic write parity with existing
      endpoints
- [ ] 2.4 Update `scripts/data_pipeline/01_fetch_tushare.py`
      `--endpoints` default + help text to reflect 7 endpoints
- [ ] 2.5 Unit tests in `tests/data_pipeline/test_tushare_fetcher.py`
      (or equivalent) covering happy path, resume, and rate-limit
      handling for `daily_basic`

## Phase 3 — Bin builder + tests (follow-up PR)

- [ ] 3.1 Add `_load_daily_basic` to
      [src/data/pit/qlib_bin_builder.py](src/data/pit/qlib_bin_builder.py)
      mirroring `_load_adj_factor`
- [ ] 3.2 Merge `daily_basic.turnover_rate` into
      `_apply_adjustment`'s output as a new column `turn`
- [ ] 3.3 Add `"turn"` to `BIN_FEATURE_FIELDS`
- [ ] 3.4 Update module docstring (current "6 fields" line and the
      `Scope` section)
- [ ] 3.5 Update [tests/data_pipeline/test_qlib_bin_builder.py](tests/data_pipeline/test_qlib_bin_builder.py)
      coverage matrix (per `design.md` §6):
  - [ ] Happy-path: synthetic `daily_basic` + `daily` → bin's
        `turn.day.bin` matches source
  - [ ] Missing `daily_basic/` subtree → all-NaN `turn.day.bin`
  - [ ] Delisted ticker → NaN after delist, valid before
  - [ ] Suspended-trading gap (no row in `daily_basic` for that
        date inside listing window) → NaN, not zero

## Phase 4 — Migration documentation + governance (follow-up PR)

- [ ] 4.1 Update `docs/pit/pit_universe_design.md` to document the
      7-field bin layout and the `daily_basic` ingest step
- [ ] 4.2 Add a migration note to `docs/pit/migration_guide.md`
      telling operators how to upgrade an existing 6-field bundle
      (re-run Phase A.1 with `--endpoints` including `daily_basic`,
      then re-run Phase B.2)
- [ ] 4.3 Add a governance regression test pinning
      `BIN_FEATURE_FIELDS == ("open", "high", "low", "close",
      "volume", "money", "turn")` under
      `tests/governance/test_pit_bin_field_set.py`

## Phase 5 — Factor-mining FeatureRegistry update (separate OpenSpec change, not this capability)

- [ ] 5.1 Once factor mining's `src/factor_mining/grammar.py` exists
      (post-factor-mining-Phase-1), move `$turn` from `V2` to `V1`
      in `FeatureRegistry`
- [ ] 5.2 Add `$turn` to the terminal registry as `T_SCALE_PURE`
      (turnover rate is dimensionless ratio, not adj_factor-tainted)
- [ ] 5.3 Update `docs/factor_mining/decisions.md` D3 to reflect the
      7-field universe
- [ ] 5.4 Pin a new scale_invariance.md example covering `$turn`
      composition

> Phase 5 is governed by the factor-mining capability, not the PIT
> capability. Recorded here for cross-dependency tracking.

## Out of scope for this proposal (record-only)

- Additional `daily_basic` fields (pe, pb, total_share, etc.)
- `turnover_rate_f` float-adjusted variant
- VWAP / factor / change exposure in PIT bins
- `v2-tushare-qlib-provider-bundle` adding `turn` (separate capability)
- Automatic re-build orchestration for existing 6-field bundles
