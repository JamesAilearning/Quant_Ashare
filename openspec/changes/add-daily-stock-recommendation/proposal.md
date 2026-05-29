## Why

The V2 system can now train an effective model on clean PIT data
(Alpha158 + LGB, Phase A measured a single-fold 2025 OOS with-cost
information ratio ≈ 0.36 on csi300), and it can run a canonical
backtest end-to-end. But the target product link —

```
tushare → PIT 清洗 → ML 训练 → (挖因子) → 日频荐股
```

— stops one step short of its endpoint. There is **no code that turns a
trained model into "which stocks to buy today"**. `Pipeline.run`
terminates at "train → predict over a fixed historical test window →
backtest → report". Nothing loads a saved model, scores the *latest*
trading day's cross-section, and emits a ranked buy list. A grep for
`recommend / inference / predict_daily / serve / live` returns zero
production-inference files (confirmed in `docs/code_audit.md` §3, ring 5
= "不存在").

This change adds that final link: a **daily stock-recommendation
inference path** that consumes an already-trained model artifact and
produces a dated, ranked, tradability-filtered stock list.

The single hardest correctness requirement is **no look-ahead bias**:
when recommending for decision-date `T` (run after `T`'s close), the
feature cross-section must be built from data `≤ T` only. Any leak of
`> T` data silently inflates the list's apparent quality and invalidates
the whole recommender. This proposal treats look-ahead prevention as a
first-class, test-enforced contract — not an afterthought.

## What Changes

- **New isolated module `src/inference/daily_recommend.py`** (new
  `src/inference/` package) exposing a pure, testable core:
  * `RecommendationConfig` — frozen dataclass: model artifact path,
    `provider_uri` (PIT), `instruments` (universe), `as_of_date`
    (default = latest PIT trading day), `topk`, Alpha158 training
    window (`fit_start`/`fit_end`) needed to fit processors without
    leakage, output dir.
  * `DailyRecommendationResult` — frozen dataclass: as-of date, entry
    date, ordered list of picks (`rank`, `stock_code`, `stock_name`,
    `predicted_score`, `tradable_flag`, `unavailable_reason`), counts.
  * `recommend(config) -> DailyRecommendationResult` — loads the
    pickled qlib model, builds the **as-of-`T`** Alpha158 feature
    cross-section, calls `model.predict`, applies the existing
    microstructure tradability mask, ranks, truncates to top-K, and
    returns the result.
- **New thin CLI `scripts/daily_recommend.py`** — parses args
  (`--config` or inline flags, `--as-of`, `--topk`, `--out-dir`),
  calls `recommend`, writes `daily_recommendation_<date>.{csv,json}`,
  prints the list. Has an `if __name__ == "__main__"` guard +
  `multiprocessing.freeze_support()` (qlib's Alpha158 uses joblib
  `spawn` workers on Windows — a missing guard fork-bombs, a known
  Phase A trap).
- **New tests** `tests/logic/inference/test_daily_recommend.py`:
  * **Look-ahead guard test (the red line)**: assert the prepared
    feature frame for as-of `T` contains **no datetime `> T`**, on a
    synthetic/fixture panel that has `> T` data available — proving the
    builder cannot reach into the future.
  * Tradability: a stock suspended / one-price-locked on `T` is
    excluded (or flagged) and never appears in the top-K buy list.
  * Ranking: output is sorted by score desc, length ≤ topk, ranks are
    contiguous 1..N.
- **New OpenSpec capability** `v2-daily-stock-recommendation` (spec
  delta in this change).

### Explicitly OUT of scope (do not touch this change)

- The walk-forward embargo P0 regression (Phase A finding) — **not
  touched**.
- `config/factor_mining/default.yaml` dead `features:` key / D3
  decisions.md backfill — **not touched**.
- GP-mined factors as a signal source — recommender uses **Alpha158 +
  LGB only** (GP is empirically unproven, `empirical_results_b_std.md`).
- Order generation, position sizing, live broker integration, T+1
  execution simulation — this change outputs a *list*, not orders.
- ST filtering and forward (T+1) limit-up un-fillability — see
  Known Limitations in design.md; surfaced as flags/TODOs, not faked.
- No edits to `src/core/`, `src/data/`, `Pipeline`, `WalkForwardEngine`,
  or any existing module. The recommender **imports and reuses** them.

## Impact

- Affected specs: **NEW** `v2-daily-stock-recommendation`.
- Affected code: **new files only** — `src/inference/` package,
  `scripts/daily_recommend.py`, `tests/logic/inference/`. No existing
  module is modified.
- Reuses (imports, does not change): `ModelTrainer`/pickle format,
  `FeatureDatasetBuilder` + qlib `Alpha158`, `PITDataProvider`,
  `microstructure_mask.compute_unavailable_mask`, the qlib calendar.
- First time the system produces an end-to-end daily buy list from
  clean PIT data → trained model → ranked, tradability-filtered output.
