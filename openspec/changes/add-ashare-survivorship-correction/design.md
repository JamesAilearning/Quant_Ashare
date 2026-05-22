# Design: A-share Survivorship Correction

> The long-form design (architecture, pipeline stages, query layer API,
> testing strategy, OpenCode operational workflow) lives at
> `docs/pit/pit_universe_design.md` and was rewritten in this PR. The
> contract-level decisions surfaced into OpenSpec scope are below.

## A-share Reality That Drives This Capability

Per Shanghai and Shenzhen exchange rules and verified market history:

- **No ticker recycling.** A delisted ticker enters a "reserved pool"
  and is not reassigned to a new IPO. There is no documented A-share
  precedent for US-style ticker recycling.
- **Borrow-shell restructure preserves continuity.** A listed company
  may be acquired via reverse merger; the shell keeps its ticker and
  trades continuously while new assets are injected and the company is
  renamed. This is NOT a new entity from a price-series PIT perspective.
- **Old B-share / pre-stock-reform legacy codes** (e.g. `900xxx`) may
  have undergone format conversion but were never "reassigned"; the
  legal entity continuity is preserved.

Consequence: ticker is a stable identifier within the A-share universe
for price-series purposes. The PIT correctness problem reduces to:

1. The local qlib bin contains data for currently-listed tickers but is
   missing or stale for delisted tickers.
2. The local bin's reference list of "what is delisted" was previously
   based on agent-fabricated facts; correcting it requires Tushare
   `stock_basic(list_status='D')` as the source of truth.

## Delisted Registry Schema (`delisted_registry.parquet`)

One row per ticker that has ever delisted. NOT per entity period (no
entity model).

| Column | Type | Notes |
|--------|------|-------|
| `ticker` | string | Market code, e.g. `SH600087` |
| `list_date` | date | First trading day |
| `delist_date` | date | Last trading day (NEVER NULL in this registry) |
| `last_company_name` | string | Display name at time of delisting (may include `(退)` suffix per Tushare convention) |
| `delist_reason` | string | One of: `financial`, `major_violation`, `voluntary`, `par_value`, `restructure_failure`, `other`. Sourced from delist context, not directly from Tushare; may require manual annotation for older entries. |

Invariants:

- `ticker` is unique
- `delist_date >= list_date`
- No currently-active stock (`list_status='L'`) appears in this registry

## NaN-After-Delist Invariant (qlib bin storage)

For any ticker in the delisted registry, the qlib bin storage SHALL
contain NaN values for OHLCV / derived fields on every trading date
strictly after `delist_date`. This is the structural defence against
"stale local bin" — without it, queries on a delisted ticker may
return non-NaN values from a forward-filled snapshot or from a
mis-merged active ticker (the failure mode previously misdiagnosed as
"ticker reuse contamination").

## Borrow-shell Restructure Policy

A borrow-shell event is NOT modelled in the price layer:

- The ticker retains continuous price series.
- The delisted registry does NOT contain the ticker (it was never
  delisted).
- Attribution consumers MAY annotate the restructure date / new asset
  identity via `PURPOSE_ATTRIBUTION` enum in
  `attribution_industry_loader.py`. This is informational, NOT a
  price-series gate.
- Training MUST NOT consume restructure annotations as features
  (already guarded by `PURPOSE_TRAINING` vs `PURPOSE_ATTRIBUTION`
  enum split).

## Adjusted-price PIT Caveat (Unchanged from pit-universe-foundation)

Tushare's `adj_factor` returns today's snapshot, not the historical
as-of-date value. Features SHALL be within-ticker ratios and returns
where the as-of-date `adj_factor` cancels in numerator and denominator.
Absolute adjusted prices SHALL NOT be used as features.

## qlib Operator min_periods Contract (Re-scoped)

The Stage 6.D validation SHALL exercise real qlib operators (`Mean`,
`Ref`, `Corr`) against a delisted ticker on days strictly after
`delist_date`. Window operators MUST return NaN, not partial-window
values. Any qlib operator that silently honours `min_periods < N` is
either wrapped with explicit `min_periods=N` or banned from feature
expressions. The original framing was "across entity boundary" — the
re-scoped framing is "across delist boundary".

## PIT Query Layer (`src/pit/query.py`)

`PITDataProvider` exposes:

- `get_universe(date, universe_name)` — active tickers on `date`
- `get_universe_range(start, end, universe_name)` — daily map
- `get_features(fields, start, end, universe_name, align)` — PIT-aligned panel
- An LRU cache with bounded `cache_max_entries` (default 256)

There is NO `resolve_entity(ticker, date)` method. Ticker is the
stable identifier.

## Legacy Provider Preservation (Unchanged)

Existing `D:/qlib_data/my_cn_data` SHALL NOT be deleted, overwritten,
or retroactively modified. New provider is written to a separate
directory.

## Out-of-scope (PIT) Dimensions

The capability excludes PIT correctness for:

- Entity model / ticker reuse modelling — **excluded by construction**
  (A-share does not have ticker reuse; including it would over-model)
- Industry classification PIT (Shenwan L1/L2) — PHASE-E.1
- Fundamentals PIT (PE / PB / ROE / financial statements) — PHASE-E.2
- Outstanding shares / market cap PIT — PHASE-E.3
- ST / *ST status snapshots within an active listing — PHASE-E.4
- Risk-model factor exposures — deferred indefinitely

## Reference Cases YAML — Coverage Matrix (Replacing ≥10)

The seed YAML is user-curated. The Phase 0.2 minimum is a coverage
matrix, not a count target:

| Dimension | Minimum coverage |
|-----------|------------------|
| Pre-2020 financial delisting era | ≥1 case |
| 2020-2022 \*ST → 退市 mainstream era | ≥1 case |
| 2024+ post-退市新规 strict mandatory delisting | ≥1 case |
| ChiNext / STAR board delisting | ≥1 case |
| Same-day multi-stock delisting batch | ≥1 case |
| Negative control: active stock (e.g. SH600519 贵州茅台) | ≥1 case |
| CSI300 constituent change — enter | ≥1 case |
| CSI300 constituent change — leave | ≥1 case |

= ~8 cases minimum. Agent additions in Phase A.3 require Tushare API
response citation per row (unchanged from pit-universe-foundation
§14.7).
