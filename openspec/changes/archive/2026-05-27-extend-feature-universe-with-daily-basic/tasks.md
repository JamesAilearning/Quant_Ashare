# Tasks: Extend the factor-mining feature universe with daily_basic

## OpenSpec (propose stage — PR #182)

- [x] Draft proposal.md / tasks.md
- [x] Draft `specs/v2-factor-mining-foundations/spec.md` delta
- [x] `openspec validate extend-feature-universe-with-daily-basic --strict`

## Implementation (apply stage — PRs #184, #185, #187)

### Tushare fetcher (PR #184)

- [x] `src/data/tushare/fetcher.py` — add `daily_basic` to `ENDPOINTS`
- [x] `_fetch_daily_basic` method, per-(ticker, year), resume on file existence
- [x] Field list: `ts_code, trade_date, turnover_rate, pe, pb, ps,
      ps_ttm, circ_mv, total_mv, float_share, total_share`
- [x] Update CLI help text + the `--endpoints` allow-list (via `ENDPOINTS`
      tuple membership)

### qlib bin builder (PR #185)

- [x] `src/data/pit/qlib_bin_builder.py` — scan `daily_basic/` alongside `daily/`
- [x] Emit `features/<ticker>/{pe,pb,ps,turnover_rate,circ_mv,total_mv}.day.bin`
- [x] Wire the per-field PIT NaN-after-delist mask (implicit via the
      existing delist-row-drop pass that runs over all columns)
- [ ] ~~Bump bundle schema version~~ — deferred; the silent
      backward-compat skip in PR #185 (missing daily_basic dir → emit
      OHLCV-only) means old bundles re-validate without an explicit
      version bump

### Factor-mining grammar (PR #187)

- [x] `src/factor_mining/grammar.py` — extend `FeatureRegistry.V1`
      with `$pe, $pb, $ps, $turnover_rate, $circ_mv, $total_mv`
- [x] All new terminals: `kind=FLOAT, taint=PURE`
- [x] Confirm `cs_*` operators accept them without grammar changes
- [x] `_random_leaf` PURE pool extended to include `V1_FUNDAMENTAL`

### Configs

- [ ] ~~`config/factor_mining/default.yaml`: extend `data.features` to 12~~
      — N/A: default.yaml does NOT enumerate features in the
      `data:` block; the feature universe is sourced from
      `FeatureRegistry.V1` at runtime. Config has nothing to extend.
- [x] No fitness / GP knob changes

### Tests

- [x] `tests/logic/data_pipeline/test_fetcher_daily_basic.py` (PR #184)
- [x] `tests/logic/data_pipeline/test_qlib_bin_builder_daily_basic.py` (PR #185)
- [x] `tests/logic/factor_mining/test_grammar.py` (PR #187):
      - `len(FeatureRegistry.V1) == 12`
      - new terminals have `taint=PURE`
      - random generator samples the new terminals (1000-sample test)
      - deferred terminals (`$pe_ttm`, `$float_share`) still rejected
- [x] `tests/logic/factor_mining/test_scale_invariance.py` (PR #187):
      - `cs_rank($pe)`, `cs_rank($pb)`, etc. construct directly
      - **CORRECTION**: `div_safe($total_mv, $close)` is **ADJ_TAINTED**
        (not PURE as the proposal originally claimed) — `$total_mv` is
        PURE but `$close` is ADJ_TAINTED, and `_rule_div_safe` only
        cancels when both sides match. Pinned as the "intuitive trap"
        example.
      - `cs_rank(add($pe, $close))` rejected at inner `add`
- [x] Synthetic panel + PIT adapter emit all 12 fields (PR #187 —
      `_synthetic_panel.py` updated to log-normal fundamentals; PIT
      adapter inherits from `FeatureRegistry.V1`)

### Spec deltas (PR #187)

- [x] `specs/v2-factor-mining-foundations/spec.md`:
      MODIFIED "Feature universe SHALL be exactly the six PIT bin fields"
      requirement body to enumerate twelve terminals + scenarios for
      the three corrected taint cases

### Validation (each sub-PR)

- [x] `pytest tests/logic/ -q` — green (1835 passed at PR #187 merge)
- [x] `ruff check src/ tests/ scripts/` — green
- [x] `openspec validate extend-feature-universe-with-daily-basic --strict` — green
- [x] D5 grep zero matches under `src/factor_mining/`
- [x] CI green on push (no `--admin` merge) — PR #184, #185, #187 all
      passed 6/6 ubuntu/windows × Python 3.10/3.11/3.12

### Empirical (post-merge operator action — NOT in this archive)

- [ ] Operator: pull Tushare `daily_basic` for 2018-2025 (~3-4 h)
- [ ] Operator: rebuild qlib bundle (~15 min)
- [ ] Operator: SH000300 backfill (~30 s)
- [ ] Operator: GP miner with 12-feature universe, soft fitness,
      pop=200 gen=20, pool_top_k=50 (~3-4 h)
- [ ] Operator: walk-forward bake-off × 2 (~3 h total)
- [ ] Operator: compare CLI → IR threshold check
- [ ] Operator: write empirical update to
      `docs/factor_mining/empirical_results_b_std.md` §"Follow-up"

## Deferred (NOT this proposal, NOT the apply-stage scope)

- Financial-statement ingest (`income` / `balancesheet`) with PIT
  announcement-date alignment.
- Industry / size cross-sectional buckets for `cs_*` operators.
- Multi-frequency data (intraday).
- Auto-promote of resulting v1 factor pool.
- GP fitness re-tuning post-extension (separate empirical change).
- New operator families targeting fundamentals (e.g. `ts_yoy_growth`).
