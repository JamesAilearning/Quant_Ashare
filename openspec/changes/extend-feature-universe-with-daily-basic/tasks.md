# Tasks: Extend the factor-mining feature universe with daily_basic

## OpenSpec (propose stage — this PR)

- [x] Draft proposal.md / tasks.md
- [x] Draft `specs/v2-factor-mining-foundations/spec.md` delta
- [x] `openspec validate extend-feature-universe-with-daily-basic --strict`

## Implementation (apply stage — NOT this PR)

### Tushare fetcher

- [ ] `src/data/tushare/fetcher.py` — add `daily_basic` to `ENDPOINTS`
- [ ] `_fetch_daily_basic` method, per-(ticker, year), resume on file existence
- [ ] Field list: `ts_code, trade_date, turnover_rate, pe, pb, ps,
      ps_ttm, circ_mv, total_mv, float_share, total_share`
- [ ] Update CLI help text + the `--endpoints` allow-list

### qlib bin builder

- [ ] `src/data/pit/qlib_bin_builder.py` — scan `daily_basic/` alongside `daily/`
- [ ] Emit `features/<ticker>/{pe,pb,ps,turnover_rate,circ_mv,total_mv}.day.bin`
- [ ] Wire the per-field PIT NaN-after-delist mask
- [ ] Bump bundle schema version (or similar invalidation hook) so old
      bundles re-validate

### Factor-mining grammar

- [ ] `src/factor_mining/grammar.py` — extend `FeatureRegistry.V1`
      with `$pe, $pb, $ps, $turnover_rate, $circ_mv, $total_mv`
- [ ] All new terminals: `kind=FLOAT, taint=PURE`
- [ ] Confirm `cs_*` operators accept them without grammar changes

### Configs

- [ ] `config/factor_mining/default.yaml`: extend `data.features` to 12
- [ ] No fitness / GP knob changes

### Tests

- [ ] `tests/logic/data_pipeline/test_fetcher_daily_basic.py`:
      - per-(ticker, year) resume
      - field rename / drop (`close` drop on save)
      - empty-row tickers handled
- [ ] `tests/logic/data_pipeline/test_qlib_bin_builder_daily_basic.py`:
      - reads `daily_basic/` dir
      - writes 6 new field bins per ticker
      - PIT NaN-after-delist mask propagates
- [ ] `tests/logic/factor_mining/test_grammar.py`:
      - `len(FeatureRegistry.V1) == 12`
      - new terminals have `taint=PURE`
      - random generator with `target=(CSF, PURE)` samples the new terminals
- [ ] `tests/logic/factor_mining/test_scale_invariance.py`:
      - `cs_rank($pe)` constructs (PURE input → CSF output)
      - `div_safe($total_mv, $close)` is `PURE` (cap / adjusted close
        cancellation)
      - `cs_rank(add($pe, $close))` is rejected (mixed taint)
- [ ] Synthetic-mode end-to-end smoke (small GP) with all 12 features

### Spec deltas

- [ ] `specs/v2-factor-mining-foundations/spec.md`:
      MODIFIED "Feature universe SHALL be exactly the six PIT bin fields"
      → "twelve PIT bin fields" with explicit enumeration

### Validation

- [ ] `pytest tests/logic/ -q` — full suite green
- [ ] `ruff check src/ tests/ scripts/` — green
- [ ] `openspec validate extend-feature-universe-with-daily-basic --strict`
- [ ] D5 grep zero matches under `src/factor_mining/`
- [ ] CI green on push (no `--admin` merge)

### Empirical (post-merge operator action)

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
