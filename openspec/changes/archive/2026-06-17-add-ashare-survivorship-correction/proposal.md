# Add A-share Survivorship Correction (replacing PIT Universe Foundation)

## Why

The `pit-universe-foundation` proposal landed in commit `e780f65` was built
on two assumptions that Tushare verification on 2026-05-22 disproved:

1. **A-share has ticker reuse**, motivating an `entity_id` / `reuse_count` /
   NaN-gap-cross-entity / `resolve_entity` model.
2. **`SH600753` was 庞大集团 then ST友谊**, used as the design's primary
   ticker-reuse example throughout §2 / §4 / §9.

Both are false:

- **A-share regulator does not recycle tickers.** Shanghai and Shenzhen
  exchanges keep delisted tickers in a "reserved pool" indefinitely. There
  is no documented case of a delisted ticker being reassigned to a fresh
  IPO. Borrow-shell restructure preserves the ticker, injects new assets
  under the same code, and continues trading without a gap — it is NOT
  ticker reuse.
- **`SH600753` was never named 庞大集团.** Tushare `namechange` shows the
  ticker continuously listed since 1996 under names 冰熊股份 → 东方银星 →
  庚星股份 → *ST海钦. The real 庞大集团 (real ticker `601258`) was a 2011
  IPO that delisted in 2019 and whose code has not been reassigned. The
  600753 example was fabricated by a prior agent.
- The original `scripts/data_quality/verify_survivorship.py::KNOWN_DELISTED`
  list contained 7 entries; cross-check shows 4 of them
  (`SH600615`, `SH600753`, `SZ000010`, `SH600268`) are still actively
  listed and were never delisted, and the remaining 3 had delist_date wrong
  by 1.5-5 years.

The previous capability therefore over-models the actual A-share problem.
The true concerns are simpler:

- **Survivorship bias**: delisted stocks dropped entirely from the local bin.
- **Stale local bin**: delisted stocks with data extending past `delist_date`
  because the reference list incorrectly excluded them from delisting.
- **Borrow-shell continuity**: same ticker, new asset, continuous price
  series — an attribution-layer concern (see `PURPOSE_ATTRIBUTION` in
  `attribution_industry_loader.py`), NOT a price-series PIT concern.

## What Changes

- **Remove `pit-universe-foundation` change** entirely (it had not been
  archived to `openspec/specs/`, so no MODIFIED/REMOVED requirements
  delta is needed — the change folder is deleted).
- **Add `v2-ashare-survivorship-correction` capability** with 9 requirements
  scoped to the actual A-share problem: delisted registry, NaN-after-delist,
  adj_factor caveat (kept), qlib operator min_periods boundary (kept,
  re-scoped), PIT query layer (without `resolve_entity`), bounded LRU
  cache (kept), legacy provider preservation (kept), borrow-shell
  non-modelling, out-of-scope dimensions (now includes "entity model"),
  reference cases governance.
- **Rewrite `docs/pit/pit_universe_design.md`** to drop the entity model,
  document the A-share-specific reality, and replace the fabricated
  examples with verified Tushare data.
- **Add `scripts/data_quality/verify_survivorship.py`** with a corrected
  `KNOWN_DELISTED` list (3 verified entries, citing Tushare
  `stock_basic(list_status='D')` for delist_date and the
  post-delisting display name).
- **Replace the `≥10 reference cases` rule** with a coverage matrix
  (delisting era × control); minimum ~8 cases.

## Non-Goals

- No entity model. No `entity_id`, `reuse_count`, NaN-gap-cross-entity,
  or `resolve_entity` API.
- No modelling of borrow-shell restructure in the price layer. Attribution
  consumers may annotate restructure events separately.
- No `tests/pit/reference_cases.yaml` seed in this PR — it remains user-
  curated in a follow-up Phase 0.2 PR per the revised coverage matrix.
- No ingestion script, registry builder, or PIT query implementation in
  this PR — those are Phase A onwards.
- No edits to the existing `v2-universe-data-contract` (which governs
  artifact validation; complementary to this capability).
