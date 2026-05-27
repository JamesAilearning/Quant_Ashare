# Extend the factor-mining feature universe with `daily_basic` (PE/PB/turnover/...)

## Why

The B-std empirical evaluation (PR #180,
`docs/factor_mining/empirical_results_b_std.md`) showed that GP factor
mining on csi300 2018-2025 systematically underperforms Alpha158:

| Metric | Alpha158 | MinedFactor (best variant) |
|---|---:|---:|
| OOS IR | **+0.466** | -0.094 |
| OOS IC_1d | **+0.0247** | +0.006 |
| design_doc §10 IR threshold met | — | **FALSE** |

The empirical doc traced the gap to three causes; the largest and the
only one that requires an architectural change (not just a config or
grammar tweak) is **the feature universe is too narrow**. Today,
`FeatureRegistry.V1` exposes six PIT bin fields per `decisions.md` D3:
`$open`, `$high`, `$low`, `$close`, `$volume`, `$money`. Alpha158 has
158 features that combine price, volume, ratio, and **fundamental**
inputs (PE / PB / turnover) sourced from Tushare's `daily_basic`
endpoint. The expressivity gap shows up concretely in the top-50
mined pool, which collapses onto ~5 semantic templates of OHLCV
combinations — versus Alpha158's ~50.

This proposal extends the feature universe by ingesting Tushare
`daily_basic` daily snapshots and exposing the most-cited fundamental
fields as Phase-1 terminals. The GP gains direct access to value
(PE/PB/PS), microstructure (turnover_rate), and size (circ_mv,
total_mv) signal sources that the 158-feature library uses.

This is **not** a fitness-tuning iteration. The previous three
attempts (default / soft / soft-top-20) all moved OOS IR in the right
direction but plateaued because the GP search space itself can't
express PE/PB-style factors. Closing that gap is the single change
most likely to meet §10's IR threshold.

### Scope of this proposal

This is a **propose-stage** OpenSpec change. It documents:

- the spec deltas needed to ship the feature,
- the implementation tasks,
- the cost / risk / time estimates,

so an operator can decide whether to apply it. The current PR adds
the proposal folder only — it does NOT execute the work. The apply
stage is a separate operator-gated commit.

## What changes (when applied)

### Tushare ingest — `src/data/tushare/fetcher.py`

- **ADD `daily_basic` endpoint**: per-(ticker, year) fetch with
  per-file existence resume semantics (identical pattern to `daily` /
  `adj_factor`). Fields fetched: `ts_code, trade_date, close,
  turnover_rate, pe, pb, ps, ps_ttm, circ_mv, total_mv,
  float_share, total_share`. The `close` field is dropped on save
  (we already have a higher-fidelity `close` from `daily` and
  `adj_factor`); keeping it in the fetched payload only to satisfy
  Tushare's "at least one field" requirement.
- **ADD `"daily_basic"` to `ENDPOINTS` tuple** so the CLI accepts it
  via `--endpoints daily_basic`.
- Default `start_date=20180101, end_date=20251231` matches the
  existing B-std calendar; operator can override.

### Bin builder — `src/data/pit/qlib_bin_builder.py`

- **EXTEND `QlibBinBuilder` to read `daily_basic/`** alongside the
  existing `daily/` and `adj_factor/`. Fields written under
  `features/<ticker>/<field>.day.bin` using the same start-idx + LE
  float32 array convention.
- The PIT NaN-after-delist mask applies identically to the
  daily_basic fields (the existing `delisted_registry.parquet` drives
  the mask).
- New canonical bin filenames added to the bundle: `pe.day.bin`,
  `pb.day.bin`, `ps.day.bin`, `turnover_rate.day.bin`,
  `circ_mv.day.bin`, `total_mv.day.bin`. (The other Tushare
  daily_basic fields are not exposed in v2 — keeping the smaller
  set focused on highest-impact factor categories.)

### Factor-mining grammar — `src/factor_mining/grammar.py`

- **EXTEND `FeatureRegistry.V1`** to register six new terminals:
  - `$pe`, `$pb`, `$ps` — value ratios. All have `kind=FLOAT,
    taint=PURE` (a price ratio normalised by a fundamental is
    invariant under qlib's `adj_factor` because both numerator and
    denominator scale identically).
  - `$turnover_rate` — daily turnover. Scale-free per-stock-per-day,
    `kind=FLOAT, taint=PURE`.
  - `$circ_mv`, `$total_mv` — market caps. Reported in absolute
    yuan, do NOT carry the `adj_factor` ladder (Tushare publishes
    the cap as `shares × current_price`, recomputed daily). Hence
    `kind=FLOAT, taint=PURE`. (Compare with `$close` which IS
    `ADJ_TAINTED` because the close in the qlib bundle is
    post-adjustment.)
- The `FeatureRegistry.V1` terminal count grows from **6 → 12**.
- The `cs_*` operator family (cs_rank/cs_zscore/cs_demean/cs_winsorize)
  immediately gains all six new terminals as valid inputs (they are
  all PURE per the scale-invariance gate).

### Configs

- **MODIFY `config/factor_mining/default.yaml`**: extend
  `data.features` list from 6 to 12 entries. The operator template
  documents the new fields and the source endpoint.
- **No change** to fitness weights, GP knobs, validity thresholds —
  this change is purely additive on the feature axis. Whether
  fitness defaults need re-tuning post-extension is a separate
  empirical question and a separate change.

### Tests

- **ADD `tests/logic/data_pipeline/test_fetcher_daily_basic.py`** —
  per-ticker-per-year fetch + resume + field renaming.
- **ADD `tests/logic/data_pipeline/test_qlib_bin_builder_daily_basic.py`** —
  bin builder reads daily_basic dir and writes the six new field bins.
- **EXTEND `tests/logic/factor_mining/test_grammar.py`** — terminal
  count assertion 6→12, new terminals have `taint=PURE`, random
  generator samples them.
- **EXTEND `tests/logic/factor_mining/test_scale_invariance.py`** —
  pinned examples for new terminal taints, including the price/cap
  ratio cancellation (`div_safe($total_mv, $close)` is `PURE`).
- **ADD synthetic-mode integration test** that exercises the full
  6→12 feature path through the GP miner (small pop/gen to stay
  fast).

### Spec deltas

- **MODIFY `v2-factor-mining-foundations`** — the "Feature universe
  SHALL be exactly the six PIT bin fields per D3" requirement is
  superseded by a "Feature universe SHALL include the six OHLCV PIT
  bin fields plus six daily_basic fundamental fields" requirement.
  The new scenario count is 12 enumerated terminals + per-terminal
  taint assertions.

## Cost / wall-clock budget (when applied)

| Step | Estimate |
|---|---|
| Code changes (fetcher + bin builder + grammar + tests + spec) | ~6-8 hours of focused work |
| Tushare `daily_basic` ingest for 2018-2025 (8 years) | **~3-4 hours wall-clock** (rate-limited, resumable) |
| qlib bin rebuild | ~15 min |
| SH000300 benchmark backfill | ~30s |
| GP miner re-run with 12-feature universe, pop=200 gen=20 | ~3-4 hours (per-eval cost grows ~2× because feature panel is larger) |
| Walk-forward bake-off × 2 (Alpha158 + MinedFactor) | ~3 hours |
| Compare + analysis | < 30 min |

**Total wall-clock to a result: ~10-14 hours** (overnight-grade).
**Total developer effort: ~1.5-2 days** if all goes smoothly.

## Risk register

| Risk | Likelihood | Severity | Mitigation |
|---|---|---|---|
| Tushare `daily_basic` schema drift breaks the fetcher | low | medium | Pin the field list explicitly; CI runs against the schema fixture, not live Tushare |
| `$pe` / `$pb` are NaN for loss-making / unlisted stocks → factor coverage drops below `coverage_min` | medium | low | `log_safe` already returns NaN on ≤ 0; the existing validity filters absorb this. Expect coverage to settle around 0.6-0.7 for value-ratio factors (still above the relaxed 0.5 floor) |
| GP picks up `cs_rank($pe)` style trivial factors that look strong IS but are well-known overcrowded trades | medium | medium | The cross-sectional novelty term (`w_corr`) discounts factors highly correlated with existing pool members. Operator review of top expressions remains the manual gate |
| `circ_mv` and `total_mv` taint analysis is wrong — they DO drag adj_factor through Tushare's recomputation | low | medium | Inspect a sample of Tushare daily_basic rows around split events; if the cap ladders with adjustment then mark `ADJ_TAINTED` instead. Verifiable against a known historical split (e.g. 600519 stock splits) |
| GP still doesn't clear §10 IR threshold even with 12 features | medium | low | The empirical doc explicitly says "largest single move on OOS IC, but not guaranteed to clear threshold". The next-most-impactful follow-up after this is `cs_*` industry-bucketed variants (a separate change) |
| Tushare quota cost for `daily_basic` (~5847 tickers × 8 years = ~46k calls) at operator's account tier | low | low | Already paid this cost for `daily` and `adj_factor`; same scale, same rate-limit budget |

## Non-goals

- **No fitness re-tuning** in this change — that's a separate empirical
  question post-extension.
- **No GP parameter changes** — pop/gen/mutation stay at the soft-fitness
  defaults from PR #180's empirical doc.
- **No new operators** — the existing 28-op library is sufficient; the
  expressivity gain comes entirely from the new terminals.
- **No financial-statement ingest** (`income` / `balancesheet`
  endpoints) — those require PIT alignment between report announcement
  date and report period date, which is a much larger workstream.
- **No industry / size cross-sectional buckets** for `cs_*` — that
  would require a new operator family (`cs_industry_rank`, etc.) and
  industry classification metadata, separate change.
- **No multi-frequency data** — daily only, as in v1.
- **No auto-promote of the resulting v1 pool** — D4 manual gate still
  applies. Operator decides.

## Apply-stage execution gate

Before this proposal is applied, the operator should confirm:

1. Tushare account has remaining `daily_basic` quota for ~46k calls.
2. `D:/qlib_data/tushare_raw/` has ~500 MB free for the new endpoint
   parquet files (each per-ticker-per-year is ~2-5 KB; 46k × ~3 KB ≈
   140 MB plus overhead).
3. A 10-14 hour wall-clock budget for the full bake-off.
4. Buy-in that even if the IR threshold doesn't clear, the proposal's
   value still lands (a richer feature universe is independently
   useful for follow-up experiments).
