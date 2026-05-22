# Design: PIT turnover-rate field

> Spec-only PR. Code lands in the follow-up PRs scoped in `tasks.md`.
> This document is the contract those PRs implement against.

## 1. Tushare endpoint choice

Tushare's `daily_basic` endpoint
(https://tushare.pro/document/2?doc_id=32) exposes per-(ts_code,
trade_date) market-statistic fields including:

| Field | Meaning | Unit |
|-------|---------|------|
| `turnover_rate` | total-shares-based turnover rate | percent (0–100) |
| `turnover_rate_f` | free-float-based turnover rate | percent (0–100) |
| `volume_ratio` | volume relative to 5-day avg | ratio (dimensionless) |
| `pe` / `pe_ttm` | price-earnings (lagging / trailing-12m) | dimensionless |
| `pb`, `ps`, `ps_ttm`, `dv_ratio`, `dv_ttm` | other fundamental ratios | dimensionless / percent |
| `total_share`, `float_share`, `free_share` | share counts | wan-shares (×10 000) |
| `total_mv`, `circ_mv` | market caps | wan-yuan (×10 000) |

v1 pulls only **`turnover_rate`**. The other fields are deferred to
later changes (they all imply downstream grammar / type-system work
on factor mining's side — fundamentals are quarterly with
forward-fill semantics, market caps need separate handling).

### Rate-limit cost

Tushare's `daily_basic` rate limit at the 5000-point Pro tier is the
same as `daily` (~500 calls/min). The Phase A.1 fetcher calls
`daily` once per (year, ticker) for each historical year × the
~5500-ticker universe. Adding `daily_basic` doubles that fetch cost
for a fresh backfill (≈ another 8–12h at the default
`rate_limit_sleep_ms=200`). Operators with smaller Pro tiers should
adjust `--rate-limit-sleep-ms` upward.

Since the fetcher is resume-on-existing-file-skip, incremental
re-runs are cheap.

## 2. Storage layout

Per [src/data/tushare/fetcher.py](src/data/tushare/fetcher.py)'s
pipeline doc:

```
<tushare_dir>/
  daily/{year}/{ticker}.parquet         # already existed
  adj_factor/{year}/{ticker}.parquet    # already existed
  daily_basic/{year}/{ticker}.parquet   # NEW
  active_stocks.parquet
  ...
```

`daily_basic/{year}/{ticker}.parquet` carries columns:
- `ts_code` (string)
- `trade_date` (YYYYMMDD string, same convention as `daily/`)
- `turnover_rate` (float, percent)

Only those three columns are kept; the rest of `daily_basic`'s
response is dropped at staging time so the on-disk size stays small
(~12 bytes per row × ~5500 tickers × ~6000 days ≈ ~400 MB for a
full backfill, comparable to the existing `daily/` cost).

## 3. Bin builder change

[src/data/pit/qlib_bin_builder.py](src/data/pit/qlib_bin_builder.py)'s
existing pattern:

```
_load_ticker_history(tushare_code)   # reads daily/{year}/{ticker}
_load_adj_factor(tushare_code)       # reads adj_factor/{year}/{ticker}
_apply_adjustment(daily, ts_code)    # merges daily + adj_factor + units
```

Extension:

```
_load_daily_basic(tushare_code)      # NEW — reads daily_basic/{year}/{ticker}
_apply_adjustment(...)               # additionally merges daily_basic.turnover_rate
                                     # as a new column 'turn'
```

`BIN_FEATURE_FIELDS` becomes:

```python
BIN_FEATURE_FIELDS: tuple[str, ...] = (
    "open", "high", "low", "close", "volume", "money", "turn",
)
```

The existing `_write_one_ticker_bins` loop already iterates over
`BIN_FEATURE_FIELDS`, so adding `turn` to the tuple writes
`turn.day.bin` for free, with the same NaN-padding-after-delist
behaviour all other fields get via `clip_to_listing_window` +
`reindex(date_slice)`.

## 4. Backwards compatibility

### 4.1 Tushare staging dir without `daily_basic/`

An operator running `qlib_bin_builder` against a staging dir that
predates this change (no `daily_basic/` subtree) is a common case
during the rollout window. The expected behaviour:

- `_load_daily_basic(tushare_code)` returns `None` when the
  `daily_basic/` root does not exist (parallel to `_load_adj_factor`'s
  current behaviour at line 302).
- `_apply_adjustment(...)` then sets `out["turn"] = NaN` for the
  whole ticker's panel.
- `_write_one_ticker_bins` writes an all-NaN `turn.day.bin`.
- The bin layout is shape-consistent across tickers (every ticker
  has all 7 `.day.bin` files, even if `turn` is all-NaN).

This means a partial Tushare ingest produces a usable PIT bundle
where `$turn` queries return NaN for tickers / dates without
`daily_basic` data, but `$open` … `$money` are correct. Consumers
that don't ask for `$turn` are unaffected.

### 4.2 Existing 6-field PIT bins on disk

Bins built before this change have no `turn.day.bin` per ticker.
qlib's `D.features(..., ["$turn"])` against such bins raises
`FileNotFoundError`. There is no automatic re-build trigger; the
operator must re-run Phase A.1 (with `daily_basic` endpoint enabled)
and Phase B.2 to upgrade an existing bundle to 7 fields. Documented
explicitly in the migration note (Phase 3 task).

### 4.3 Factor mining FeatureRegistry update

After this PR merges and the bin rebuild is done, factor mining's
`FeatureRegistry` (per `decisions.md` D3) can move `$turn` from `V2`
to `V1` — a single-line change in `src/factor_mining/grammar.py`
once that module exists. This is **not** part of this PR's scope;
flagged here only so the cross-dependency is recorded.

## 5. Validation contract

The new requirement (see `specs/v2-ashare-survivorship-correction/spec.md`)
imposes three scenarios on the bin builder's output:

1. **Turn populated where Tushare provided it** — for an active
   ticker on a date where `daily_basic` returned a row, the bin's
   `turn` value equals Tushare's `turnover_rate` (no unit
   conversion; raw percent is preserved).
2. **Turn NaN past delist** — same NaN-after-delist contract as the
   other 6 fields. The existing PIT post-process mask in
   `PITDataProvider._mask_post_delist` already handles this without
   modification (the mask is field-agnostic).
3. **Turn NaN where source data is missing** — for a (ticker, date)
   inside the listing window where Tushare returned no
   `daily_basic` row (e.g. a suspended trading day with no
   turnover), the bin value SHALL be NaN, not zero. Zero would be
   silently wrong: "no trading happened" is not the same as
   "turnover was exactly zero" for downstream signal computation.

## 6. Tests

The Phase 3 task (`tests/data_pipeline/test_qlib_bin_builder.py`)
gains:

- Happy-path test: synthetic `daily_basic` parquet + synthetic `daily`
  parquet → bin has `turn.day.bin` with values matching the source
  `turnover_rate` column.
- Missing-daily-basic test: synthetic `daily` parquet WITHOUT a
  corresponding `daily_basic` parquet → bin has `turn.day.bin` with
  all-NaN (graceful degradation).
- NaN-after-delist test: a delisted ticker's `turn.day.bin` is
  valid up to `delist_date` and NaN after — same contract as the
  existing OHLCV+money fields.
- Missing-row test: a `daily_basic` parquet with a gap in
  `trade_date` (suspended-trading day) → bin's `turn` is NaN on
  that calendar day, not zero.

The Phase 4 task (`tests/governance/`) gains:

- A regression test pinning `BIN_FEATURE_FIELDS == ("open", "high",
  "low", "close", "volume", "money", "turn")` — changing the bin's
  field set must be a deliberate spec-driven change, not an
  accidental import-order mishap.

## 7. Out of scope (record-only)

- Adding more `daily_basic` fields (pe, pb, total_share, etc.).
  Each is its own grammar / type-system question for factor mining
  and is a separate OpenSpec change.
- Float-adjusted variant `turnover_rate_f`. Researchers can request
  it in a follow-up if v1 `turnover_rate` proves insufficient.
- VWAP / factor / change exposure in PIT bins. They remain
  derivable via qlib expressions (`$money / $volume`,
  cumulative-adj-factor, `Ref($close, 1) / $close - 1`); not
  worth a bin-layer field for v1.
- Backfilling `turn` for historical data the operator has not
  re-pulled. The Phase 3 migration note tells operators to re-run
  Phase A.1 + B.2; there is no in-place upgrade.
- Cross-checks with the `v2-tushare-qlib-provider-bundle` path. That
  capability ships its own publisher and its own field set; whether
  it also exposes `turn` is a parallel design question.
