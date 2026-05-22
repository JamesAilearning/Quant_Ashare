# Design: PIT Universe Foundation

> The full design (architecture, pipeline stages, query layer API, testing
> strategy, OpenCode operational workflow) lives at
> `docs/pit/pit_universe_design.md`. This file captures the contract-level
> decisions surfaced into OpenSpec scope.

## Entity vs Ticker

- `ticker` is the market code. NOT unique over time — the same code can be
  reused after a delisting + restructure.
- `entity_id` (format `ent_NNNNNN`) is unique per company instance for life.
- The mapping `(ticker, date) → entity_id` is many-to-one at any given date
  and one-to-many over history.

## Entity Registry Schema (`entity_registry.parquet`)

| Column | Type | Notes |
|--------|------|-------|
| `entity_id` | string | Globally unique, sortable |
| `ticker` | string | NOT unique over time |
| `list_date` | date | First trading day for this entity |
| `delist_date` | date or NULL | NULL = active |
| `company_name` | string | Display name at end of entity life |
| `reuse_count` | int | 1 = original; 2+ = reuse |

Invariants enforced by the contract:

- `(ticker, list_date)` unique
- `(ticker, delist_date)` unique (NULL counts as +∞)
- Per ticker, periods do not overlap
- Gap between consecutive entities on the same ticker ≥ 30 days (configurable;
  smaller gaps are logged as warnings and must be cited in the reference
  cases YAML if they are real reuses)

## NaN-Gap Invariant (qlib bin storage)

Within a single ticker's bin column, dates between consecutive entity periods
SHALL be written as NaN. This is the only structural defence that prevents
qlib time-series operators from reading across entity boundaries.

## Adjusted-price PIT Caveat

Tushare's `adj_factor` endpoint returns today's snapshot, not the historical
as-of-date value. Therefore the contract forbids using absolute adjusted
prices as features. Permitted feature shapes are within-entity ratios and
returns where the as-of-date `adj_factor` cancels in numerator and denominator.
Cross-entity arithmetic is invalid regardless of `adj_factor`.

## qlib Operator `min_periods` Contract

The NaN-gap defence is only effective if the time-series operator honours
`min_periods=N` for an N-window. The Stage 6.D validation (Phase B.3 in the
design) SHALL exercise the real qlib `Mean($close, N)`, `Ref(...)`, `Corr(...)`
operators against a known-reuse ticker. Day-N-1 of the second entity MUST
return NaN. Any qlib operator that silently uses `min_periods<N` is either
wrapped with explicit `min_periods=N` in the expression layer or banned from
feature expressions.

## PIT Query Layer (`src/pit/query.py`)

Behavior the capability requires from the query layer:

- `get_universe(date, universe_name)` returns the set of tickers whose entity
  is active on `date` in the given universe. No future-listed entity may
  appear. No past-delisted entity may appear.
- `get_features(fields, start_date, end_date, universe_name, align)` returns
  panel data aligned to the PIT universe; rows for (date, ticker) where no
  entity is active are either dropped (`align='tradable_only'`) or NaN-padded
  (`align='universe'`).
- `resolve_entity(ticker, date)` returns the `entity_id` active at `date`, or
  None if the ticker is in a gap.
- An LRU cache with bounded `cache_max_entries` (default 256) on
  `(universe_name, start_date, end_date, frozenset(fields))`. Unbounded dict
  caches are forbidden — long backtests will OOM otherwise.

## Migration Safety

- Existing `D:/qlib_data/my_cn_data` SHALL NOT be deleted, overwritten, or
  retroactively modified by this work.
- New provider is written to `D:/qlib_data/my_cn_data_pit/` (or equivalent).
- Both providers remain queryable indefinitely as long as disk allows.
- Destructive scripts (e.g. a future `99_finalize_migration.py`) SHALL
  require `--confirm-destructive` and SHALL provide `--dry-run`.

## Out-of-scope PIT Dimensions (Phase E+)

The capability explicitly excludes PIT correctness for: industry
classification (Shenwan L1/L2), fundamentals (PE/PB/ROE/financial statements),
outstanding shares / market cap, ST / *ST status snapshots, and risk-model
factor exposures. Each is tracked as PHASE-E.N in the design doc §4.5.

## Reference Cases YAML Governance

The first ≥10 entries of `tests/pit/reference_cases.yaml` are user-curated
(Phase 0.2). The agent MAY add subsequent rows in Phase A.3, but every new
row's PR body SHALL cite the Tushare API response (`stock_basic` row plus
relevant `namechange` rows) that justifies it. Uncited rows are rejected.
This is a hard rule because the agent previously hallucinated Shenwan L2
sector names (`通用设备`, `汽车服务`) when given freedom to fabricate;
see the post-mortem in `research/sector_alpha_consistency.md`.
