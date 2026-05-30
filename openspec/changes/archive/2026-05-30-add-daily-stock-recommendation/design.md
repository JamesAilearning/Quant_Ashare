# Design — Daily Stock Recommendation Inference

## Context

The model is a qlib model (e.g. `LGBModel`) pickled by `ModelTrainer`
(`pickle.dump(model)` + a sidecar). qlib models predict via
`model.predict(dataset, segment=...)` where `dataset` is a `DatasetH`.
So inference is: build a `DatasetH` whose inference segment is the single
as-of date `T`, then `model.predict(dataset, segment="infer")`.

The training label (qlib Alpha158 default) is
`Ref($close, -2) / Ref($close, -1) - 1` — the **T+1 → T+2** return. The
`LABEL_LOOKAHEAD_DAYS = 2` embargo guard corroborates this. Therefore a
score produced from the as-of-`T` cross-section is a prediction of the
**T+1-entry / T+2-exit** return. The recommendation is "buy at the next
session (T+1)". The label itself is never computed or used in inference.

## Decision 1 — Look-ahead bias prevention (the red line)

**Mechanism.** Build the Alpha158 handler with:
- `start_time = fit_start` (training start), `end_time = T` (as-of date)
- `fit_start_time = fit_start`, `fit_end_time = fit_end` (training fit
  window)

Then prepare features for segment `["T", "T"]` and predict.

Why this is leak-free:
1. **Features are all backward-looking.** Alpha158 terminals are
   `Ref($close, k≥0)`, rolling `Mean/Std/Max/...` over trailing windows.
   With `end_time = T`, qlib loads no bar after `T`, so every feature
   value for date `T` is a function of data `≤ T` only.
2. **Normalization processors are fit on the training window, not on
   `T`.** `fit_end_time = fit_end` means Alpha158's infer-processors
   (e.g. z-score) compute their statistics from the training period and
   are merely *applied* to `T`'s raw features. No statistic is learned
   from `T` (and certainly none from `> T`).
3. **The label is forward (`> T`) and is simply never read.** For the
   latest trading day its value is NaN anyway; inference only ever asks
   for `col_set="feature"`.

**Enforcement (test, not trust).** A unit test builds a fixture/synthetic
panel that *contains* data after `T`, runs the as-of-`T` feature build,
and asserts `prepared_features.index.get_level_values("datetime").max()
== T` (zero rows `> T`). If a future refactor lets `> T` data leak into
the cross-section, this test fails. This is the single most important
test in the change.

**Honest limitation we will NOT paper over.** Tradability is filtered
using `T`-day microstructure (suspension / one-price-lock). Whether a
name is *fillable at the T+1 open* (e.g. gaps to limit-up before we can
buy) is genuinely unknowable at decision time `T`. We document this as
an inherent property of any honest end-of-day recommender, not a bug.

## Decision 2 — Reuse, don't rebuild

| Need | Reuse (import only) |
|---|---|
| Load model | `pickle.load` on `ModelTrainer`'s artifact (same format) |
| Build as-of features | qlib `Alpha158` via the same factory contract `FeatureDatasetBuilder` uses |
| PIT universe / cross-section | `PITDataProvider` (`get_universe`) |
| Tradability mask | `microstructure_mask.compute_unavailable_mask(insts, T, T, pit_provider=...)` — suspension + one-price-lock |
| topk | config (default 50, mirrors `PipelineConfig.topk`) |

No existing module is modified.

## Decision 3 — Selection logic: Top-K by score (not TopkDropoutStrategy)

The backtest uses `TopkDropoutStrategy` (topk + n_drop), which is
**stateful** — `n_drop` controls turnover relative to *yesterday's
holdings*. A standalone daily list has no holdings state, so the MVP
output is a clean **Top-K by predicted score** after the tradability
filter. `n_drop` is a portfolio-transition knob, not a list-construction
knob; we note this and leave portfolio-diffing to a future change.

## Decision 4 — Output schema

`daily_recommendation_<YYYY-MM-DD>.csv` and `.json`, columns:
`as_of_date, entry_date, rank, stock_code, stock_name, predicted_score,
tradable_flag, unavailable_reason`.
- `as_of_date` / `entry_date`: the two time points (data cutoff T /
  suggested entry T+1) — both always present.
- `stock_name`: best-effort from the tushare `stock_basic` dump if present
  on disk; otherwise empty with a logged note (names are not in PIT bins).
  Not a hard dependency.
- `tradable_flag` / `unavailable_reason`: from the microstructure mask
  (`""` / `"suspended"` / `"one_price_lock"`). Masked names are excluded
  from the Top-K buy list by default; the full scored frame (with flags)
  is also written for audit.

## Decision 5 — As-of date resolution

Default `as_of_date` = the **latest trading day in the PIT calendar that
still has a following session** (`D.calendar()[-2]` when the calendar ends
at the data cutoff). The last calendar day cannot be a default decision
day because its `T+1` entry session is not in the bundle — so defaulting
to it would make the no-argument CLI always fail. A `--as-of YYYY-MM-DD`
override (must be a real trading day `≤` calendar end, and itself have a
`T+1`) enables historical-day validation runs.

## Known limitations (surfaced, not faked)

- **ST filtering**: not in PIT bins; deferred (TODO flag), not invented.
- **T+1 limit-up un-fillability**: unknowable at `T` (see Decision 1).
- **Single signal**: Alpha158 + LGB only; GP excluded by scope.
- **No order/position sizing**: outputs a list, not orders.
