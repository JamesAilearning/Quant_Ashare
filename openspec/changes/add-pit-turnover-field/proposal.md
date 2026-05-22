# Add PIT turnover-rate field to qlib bins

## Why

The PIT-corrected qlib bin storage produced by
[`src/data/pit/qlib_bin_builder.py`](src/data/pit/qlib_bin_builder.py)
currently writes 6 fields per ticker (open, high, low, close, volume,
money). Daily turnover rate — A-share's single most important
non-price signal at daily frequency — is **not** in the bins, and
unlike VWAP (`$money / $volume`) it is **not derivable from OHLCV +
volume + money alone**. It requires Tushare's separate `daily_basic`
endpoint (`turnover_rate` field).

The factor-mining Phase 0 work (PR #115, docs/factor_mining/) closed
its feature universe (decisions.md D3) to the 6 fields actually
present in PIT bins, and deferred `$turn` to a v2 follow-up
explicitly because the field requires an ingest change outside
factor mining's scope. This PR delivers that follow-up:

1. Extend the Phase A.1 Tushare ingest
   ([src/data/tushare/fetcher.py](src/data/tushare/fetcher.py),
   [scripts/data_pipeline/01_fetch_tushare.py](scripts/data_pipeline/01_fetch_tushare.py))
   to pull `daily_basic.turnover_rate` per ticker-day.
2. Extend [src/data/pit/qlib_bin_builder.py](src/data/pit/qlib_bin_builder.py)
   to merge the daily_basic dump into per-ticker DataFrames and write
   a `turn.day.bin` aligned to the global calendar, with the existing
   NaN-after-delist contract applied to it.
3. Add the new field to `BIN_FEATURE_FIELDS` (now 7 fields).
4. Update unit tests / docstrings to reflect the 7-field bin layout.

Once shipped, factor mining v1 can extend its terminal registry from
6 fields to 7 by a single line change in `FeatureRegistry`, without
the grammar or operator engine being touched.

## What Changes

- **Add new requirement to `v2-ashare-survivorship-correction`**: the
  PIT qlib bins SHALL include a `turn` field representing daily
  turnover rate (percent of free float traded), populated from
  Tushare's `daily_basic.turnover_rate`, NaN past `delist_date`,
  and NaN where Tushare returned no row for a (ticker, date) cell.
- **Extend `TushareFetcher.ENDPOINTS`** from 6 to 7 to include
  `daily_basic`, with the same per-ticker / per-year parquet layout
  as `daily` and `adj_factor`.
- **Extend `QlibBinBuilder`**: load `daily_basic`, merge
  `turnover_rate` into the per-ticker DataFrame in `_apply_adjustment`
  (or a parallel step), add `"turn"` to `BIN_FEATURE_FIELDS`.
- **Backwards compatibility**: an operator running the bin builder
  against a Tushare staging dir that **lacks** a `daily_basic/`
  subtree SHALL produce bins with `turn.day.bin` containing all-NaN
  (graceful degradation). Existing 6-field bins will still be readable
  by qlib; consumers that don't ask for `$turn` are unaffected.

## Non-Goals

- **`v2-tushare-qlib-provider-bundle` is NOT modified.** That is a
  separate capability for the non-PIT publisher path
  ([src/data/tushare/provider_bundle/](src/data/tushare/provider_bundle/))
  which already writes 9 fields including vwap / factor / change.
  Whether to add `$turn` to that path is a separate question (likely
  yes, but a separate OpenSpec change).
- **No factor-mining grammar changes**. This PR only delivers the bin
  field. Factor mining's grammar update (move `$turn` from `V2` to
  `V1` in `FeatureRegistry`) is a follow-up that lands once factor
  mining is past Phase 1.
- **No retroactive backfill orchestration**. The operator decides
  when to re-run Phase A.1 + Phase B.2 with `daily_basic` enabled;
  this PR does not automate "re-ingest on detect-missing-field".
- **No derived turn-based fields** (e.g. `turn_5d_mean`,
  `turn_zscore`). Those are qlib expressions a consumer composes;
  the bin layer only writes the raw `turn` per (ticker, date).
- **No `turnover_rate_f` (float-adjusted) variant**. Tushare exposes
  both `turnover_rate` and `turnover_rate_f`; v1 picks the simpler
  `turnover_rate` (percent of total shares). Float-adjusted variant
  can be a follow-up if factor research shows it matters.
- **This PR is spec-only.** Implementation lands in follow-up PRs per
  the phase plan in `tasks.md`. Spec-first per AGENTS.md.
