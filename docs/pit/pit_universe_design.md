# Point-in-Time Universe — Design Document

> **Goal**: Replace the current "static universe + corrupted data" approach with a Point-in-Time (PIT) universe system. At any historical date `t`, queries return exactly the stocks that were tradable on `t`, with no look-ahead bias and no ticker-reuse contamination.
>
> **Status**: Design document, ready for implementation.
>
> **Effort estimate**: 3-4 weeks (one developer / AI agent) including migration of existing pipeline.
>
> **Blocking**: Factor mining Phase 5 (production integration). Phase 1-4 can proceed in parallel.

---

## 0. Why This Is Bigger Than It Looks

A naïve "universe" in a quant system is just a list of stock codes. A PIT universe is a **time-indexed mapping** from `(date, instrument_id) → metadata`, where:

- The same ticker can refer to different companies in different periods (ticker reuse)
- A stock's listing status (active / suspended / ST / delisted) is part of its history, not its current state
- The universe at date `t` is reconstructable from data available on date `t` — **no future information leaks backward**

This is the difference between a research toy and a production system. Most quant blow-ups can be traced to violations of PIT correctness.

The good news: qlib has primitives that support PIT (per-row date ranges in instruments files). The bad news: most data pipelines (including the current one) don't populate them correctly. We're going to fix that.

---

## 1. Current State (What's Broken)

From the §2 survivorship verification:

```
4 stocks: data extends 1600-2000+ days past delisting (ticker reuse contamination)
3 stocks: missing entirely (pure survivorship bias)
Pattern: Tushare bundle does not differentiate company entities behind reused tickers
```

Two distinct problems:

| Problem | Cause | Effect on factors |
|---------|-------|-------------------|
| **A. Ticker reuse contamination** | `SH600753`'s old data (庞大集团 2011-2019) was overwritten or merged with new data (ST友谊 2019-now) | Factor sees "extreme drawdown then recovery" — actually two unrelated companies |
| **B. Survivorship** | Delisted stocks without code reuse dropped entirely | Factor backtest implicitly bet on "stocks that didn't get delisted" |

Both produce false positive alpha.

---

## 2. Conceptual Model: Ticker vs Entity

The single most important shift in this design:

```
Ticker      = market code (e.g., "SH600753") — REUSABLE, NOT UNIQUE OVER TIME
Entity      = company instance — UNIQUE, ALWAYS

A ticker maps to ONE entity at any point in time, but to MULTIPLE entities over history.
```

### 2.1 Examples

```
Ticker SH600753:
  Entity 1: 庞大集团 (Pang Da Auto)
    listed:   2011-04-25
    delisted: 2019-07-26
    cause:    bankruptcy / financial fraud
  Entity 2: ST友谊 (You Yi Group, post-restructure)
    listed:   2019-11-21 (resumed trading)
    delisted: NULL (active)
  
Ticker SH600268:
  Entity 1: 国电南自 (Guodian NARI)
    listed:   1997-07-14
    delisted: 2019-05-23 (merger / restructure)
  Entity 2: ST南卫 (NARI-Wei, post-restructure)
    listed:   2019-08-22
    delisted: NULL
```

### 2.2 Implications

1. **Joining returns to factors must be entity-aware**: A return computed across the SH600753 ticker reuse gap is meaningless — they're different companies.

2. **Time-series operators (ts_mean, ts_corr, etc.) must respect entity boundaries**: `ts_mean(close, 20)` evaluated on day 5 of Entity 2 must not include Entity 1's last 15 days.

3. **Universe queries are date-parameterized**: `universe(2020-01-01)` returns the set of `entity_id`s tradable on that date, where ticker `SH600753` resolves to Entity 2 (ST友谊).

4. **Backtest must use entity_id internally, ticker for display**: Researcher sees "SH600753" but the engine tracks entity_001 vs entity_002 separately.

---

## 3. Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                        Tushare API                                │
│  stock_basic · namechange · daily · adj_factor · suspend         │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│           Entity Resolution Layer (NEW)                           │
│  - Detect ticker reuse (gap > 30 days between delist + re-list)  │
│  - Assign unique entity_ids                                       │
│  - Build entity_registry.parquet                                  │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│           PIT Universe Builder (NEW)                              │
│  - Generate instruments/*.txt with (ticker, start, end) per row  │
│  - One row per entity-period                                      │
│  - Universe files: all.txt, csi300.txt, ... per date              │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│           qlib Bin Storage                                        │
│  Existing format, but:                                            │
│  - Features rewritten with NaN gaps at entity boundaries          │
│  - Each entity-period is a contiguous data run                    │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│           PIT Query Layer (NEW)                                   │
│  - get_universe(date) → set of (ticker, entity_id) active on date│
│  - get_features(date_range, universe) → DataFrame                 │
│  - All time-series ops respect entity boundaries                  │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│           Existing pipeline (factor_mining, training, backtest)   │
│  - Drop-in replacement for current data layer                     │
│  - No changes to model_trainer.py, backtester.py logic            │
└──────────────────────────────────────────────────────────────────┘
```

---

## 4. Data Model

### 4.1 Entity Registry (`entity_registry.parquet`)

The master table. One row per entity-period.

```
entity_id      | ticker     | list_date  | delist_date | company_name        | reuse_count
─────────────────────────────────────────────────────────────────────────────────────
ent_000001    | SH600519   | 2001-08-27 | NULL        | 贵州茅台             | 1
ent_000002    | SH600000   | 1999-11-10 | NULL        | 浦发银行             | 1
ent_002847    | SH600753   | 2011-04-25 | 2019-07-26  | 庞大集团             | 1
ent_002848    | SH600753   | 2019-11-21 | NULL        | ST友谊              | 2
ent_002849    | SH600268   | 1997-07-14 | 2019-05-23  | 国电南自             | 1
ent_002850    | SH600268   | 2019-08-22 | NULL        | ST南卫              | 2
ent_004521    | SH600087   | 2011-08-16 | 2019-08-22  | *ST长航             | 1
                                          (no Entity 2 — pure delisting, no reuse)
```

**Schema:**

| Column | Type | Description |
|--------|------|-------------|
| `entity_id` | string | Globally unique, format `ent_NNNNNN` (sortable) |
| `ticker` | string | Market code (NOT unique over time) |
| `list_date` | date | First trading day for this entity |
| `delist_date` | date or NULL | Last trading day; NULL = active |
| `company_name` | string | Display name at end of entity life |
| `reuse_count` | int | 1 = original use of ticker; 2+ = subsequent reuses |

**Invariants:**
- `(ticker, list_date)` is unique
- `(ticker, delist_date)` is unique (NULL counts as "infinity")
- For each ticker, periods do not overlap: `entity_n.delist_date < entity_{n+1}.list_date`
- Gap between `entity_n.delist_date` and `entity_{n+1}.list_date` must be ≥ 30 days (defensive default; can be configured)

### 4.2 PIT Universe Files (`instruments/*.txt`)

qlib's native format, but populated correctly. Each row is one entity-period.

```
# instruments/all.txt
SH600519	2001-08-27	2099-12-31
SH600000	1999-11-10	2099-12-31
SH600753	2011-04-25	2019-07-26
SH600753	2019-11-21	2099-12-31
SH600268	1997-07-14	2019-05-23
SH600268	2019-08-22	2099-12-31
SH600087	2011-08-16	2019-08-22
...
```

**Format:** tab-separated, 3 columns:
```
ticker	start_date	end_date
```

- `end_date = 2099-12-31` for active entities (qlib convention for "still listed")
- One file per universe: `all.txt`, `csi300.txt`, `csi500.txt`, `csi800.txt`, etc.
- Universe membership is also point-in-time: a stock can enter/exit CSI300 over time

**Critical for csi300.txt etc.**: Index membership is itself a time series. `csi300.txt` should look like:

```
SH600519	2018-06-11	2099-12-31    # entered CSI300 on this date
SH600000	2005-04-08	2022-12-12    # left CSI300 on this date
SH600000	2024-06-17	2099-12-31    # re-entered later
```

This catches another bias: backtesting current CSI300 constituents on old data. The right way is to use the constituent set that existed at backtest time.

### 4.3 Feature Storage (qlib bin format)

**Mostly unchanged**, but with one critical rule:

> Within a single `(ticker)` parquet file, rows must have NaN values for dates where no entity is active.

Example for SH600753:
```
date         | open  | high  | low   | close | volume
2011-04-25   | 12.34 | 13.50 | 12.10 | 13.20 | 12345600    ← Entity 1 starts
2011-04-26   | 13.20 | 13.80 | 12.90 | 13.45 | 11234500
...
2019-07-25   | 1.05  | 1.10  | 1.02  | 1.08  | 5678900     ← Entity 1 ends
2019-07-26   | NaN   | NaN   | NaN   | NaN   | NaN         ← Gap (suspended)
2019-07-27   | NaN   | NaN   | NaN   | NaN   | NaN
...                                                          ← (~4 months of NaN)
2019-11-21   | 3.20  | 3.40  | 3.15  | 3.35  | 8901200     ← Entity 2 starts
2019-11-22   | 3.35  | 3.50  | 3.25  | 3.42  | 7234500
...                                                          ← present
```

The NaN gap is what prevents `ts_mean(close, 20)` from crossing entity boundaries. Standard pandas `rolling` treats NaN windows correctly (returns NaN if any required row is NaN, depending on `min_periods` setting).

### 4.3.1 Adjustment factor — known PIT limitation

**Critical caveat**: `adj_factor` from Tushare is itself **not point-in-time**. The value returned today for `2020-01-01` reflects all subsequent splits and dividends through today — not what an investor on `2020-01-01` would have seen. Building features on absolute adjusted prices is therefore PIT-violating by construction.

**Mitigation policy** (this design):

1. **Do not use absolute adjusted prices as features**. Only same-time-window ratios and returns: `close / Ref(close, N)`, `pct_change`, `ts_mean(close)`, etc. — operations where the as-of-date adj_factor cancels because both numerator and denominator are scaled by the same factor when both refer to the same entity period.

2. **Cross-entity ratios are explicitly invalid** even after NaN-gap protection — see §4.3 for the structural defense, but the adj_factor mismatch is a second-order reason cross-entity arithmetic is meaningless.

3. **Document this in the OpenSpec PIT proposal**. Any caller that wants absolute price features (rare; usually only mean-reversion / level-based strategies) MUST trigger a separate ticket to reconstruct historical adj_factor from raw splits + dividends.

4. **Tushare reality check**: As of 2026, Tushare's `adj_factor` endpoint returns today's snapshot. No "as-of-date" snapshot is exposed. Verify before Phase A begins; if Tushare adds historical adj_factor snapshots later, this section gets revised.

### 4.3.2 qlib operator min_periods — boundary enforcement caveat

The NaN-gap-blocks-windows guarantee in §4.3 depends on the time-series operator's `min_periods` behavior:

- pandas `rolling(N).mean()` default: `min_periods=N` → any NaN in the window returns NaN ✓ (correct boundary enforcement)
- pandas `rolling(N, min_periods=1).mean()`: returns the mean of available non-NaN values ✗ (entity 1 data leaks into entity 2 window)

**qlib's own operators** (`Mean($close, N)`, `Ref($close, N)`, `Corr(...)`, etc.) do **not** all share the same defaults. Validation (Stage 6.D) MUST exercise the real qlib operators (not pandas rolling) against a known-reuse ticker to confirm the boundary holds:

```python
# Expected behavior for a ticker with reuse_count >= 2,
# evaluated on day 5 (zero-indexed) of Entity 2:
qlib_mean_20 = D.features([ticker], ['Mean($close, 20)'],
                          start_time=entity2_day0, end_time=entity2_day20)
# qlib_mean_20.iloc[5] MUST be NaN (only 5 of 20 required points exist
# inside Entity 2; the other 15 are inside the NaN gap or Entity 1).
```

If a qlib operator silently uses `min_periods<N`, the NaN-gap defense fails for that operator. Phase B.3 must document which qlib operators are PIT-safe under default settings; for any that are not, the project either (a) wraps them with `min_periods=N` explicitly, or (b) bans them from feature expressions.

### 4.4 Entity-Aware Feature Query

A wrapper around qlib that returns entity-correct data:

```python
def get_features(
    instruments: str,           # universe name, e.g. "csi300"
    fields: list[str],          # ["$close", "Ref($close, -1)"]
    start_date: str,
    end_date: str,
    entity_aware: bool = True,  # NEW
) -> pd.DataFrame:
    """
    Returns a (date, ticker) → fields DataFrame.
    
    When entity_aware=True:
    - Rows are dropped where no entity is active on the given (date, ticker)
    - Time-series operators respect entity boundaries (via NaN gaps)
    - Returned ticker is the ticker (for display), but engine internally knows
      the entity_id via the entity_registry lookup
    """
```

### 4.5 Out-of-scope PIT dimensions (Phase E+ backlog)

This design covers **price / volume / universe membership / entity reuse**. The following PIT dimensions are **explicitly out of scope for Phases A-D** but listed here so the agent does not auto-extend scope and so the project tracks the residual leakage surface.

| Dimension | Current treatment | True PIT requires | Backlog ticket |
|-----------|-------------------|-------------------|----------------|
| **Industry classification** (Shenwan L1/L2) | Today's snapshot, bucketed retroactively. Already handled via `PURPOSE_ATTRIBUTION` vs `PURPOSE_TRAINING` enum in `attribution_industry_loader.py` — attribution allowed to use today's snapshot, training forbidden. | Historical Shenwan reclassification events with effective dates. | PHASE-E.1 |
| **Fundamentals** (PE / PB / ROE / financial statements) | Not currently used as features. | Distinguish "report period" (quarter the data describes) from "publication date" (when the data became public). PIT join uses publication date. | PHASE-E.2 |
| **Outstanding shares / market cap** | Same as adj_factor (§4.3.1) — today's snapshot. | Historical share-count snapshots; needed if market-cap weighting is ever used. | PHASE-E.3 |
| **ST / *ST status, suspension flags** | Partially handled by `suspend_d` ingestion in Stage 1 + delisting in entity resolution. ST transitions within one entity not yet PIT-correct. | Per-day status snapshot via `stock_basic_change` (Tushare). | PHASE-E.4 |
| **Risk model / barra exposures** | Not used. | Historical loading snapshots — out-of-house, no Tushare equivalent. | (deferred indefinitely) |

**Agent rule**: if a task in Phase A-D appears to touch any of the above, stop and confirm with the user. Do not silently extend scope to fix these — they are tracked separately so the project knows where its residual leakage lives.

---

## 5. Pipeline Stages

### Stage 1: Tushare Ingestion

```python
# scripts/data_pipeline/01_fetch_tushare.py

def fetch_tushare_data(
    tushare_token: str,
    start_date: str = "2000-01-01",
    end_date: str = "today",
    output_dir: Path = Path("./data/raw/tushare"),
) -> None:
    """
    Pulls from Tushare:
    
    1. stock_basic (with list_status=L,D,P for all 3 statuses)
       → all_stocks.parquet
    
    2. namechange (历史名称变更)
       → all_namechanges.parquet
    
    3. daily (OHLCV)
       → daily/{year}/{ticker}.parquet
    
    4. adj_factor (复权因子)
       → adj_factor/{year}/{ticker}.parquet
    
    5. suspend_d (停复牌)
       → suspend.parquet
    
    6. index_weight (指数成分股历史 — for csi300, csi500, csi800)
       → index_weight/{index}.parquet
    
    Output: raw Tushare data, no entity resolution yet
    """
```

**Acceptance**: Total raw pull contains:
- ≥ 5000 stocks in `all_stocks`
- ≥ 500 delisted stocks (`list_status='D'`)
- Index weights cover at least 2010-present

### Stage 2: Entity Resolution

```python
# scripts/data_pipeline/02_resolve_entities.py

def resolve_entities(
    tushare_dir: Path,
    output_dir: Path,
    gap_threshold_days: int = 30,
) -> None:
    """
    Build entity_registry.parquet from Tushare data.
    
    Algorithm:
    1. For each ticker, get all (list_date, delist_date) records from stock_basic
       - Active stock: 1 record with delist_date = NULL
       - Delisted stock: 1 record with delist_date set
    
    2. Check namechange history:
       - If a stock has a "completed delisting then re-listed" pattern in
         Tushare, multiple stock_basic entries may exist
       - For tickers with multiple entries: each entry = potential entity
    
    3. Validate gaps:
       - Between Entity N's delist_date and Entity N+1's list_date,
         require gap >= gap_threshold_days
       - If gap < threshold, log warning (might be data error, not reuse)
    
    4. Assign entity_ids in chronological order globally
    
    5. Write entity_registry.parquet
    """
```

**Acceptance**:
- Entity registry has rows for all known historical ticker reuses (manually verify against §2's contaminated stocks)
- `(ticker, list_date)` is unique
- Active stocks all have `delist_date = NULL`

### Stage 3: Index Membership Resolution

```python
# scripts/data_pipeline/03_resolve_index_membership.py

def resolve_index_membership(
    tushare_dir: Path,
    entity_registry_path: Path,
    output_dir: Path,
) -> None:
    """
    Convert Tushare index_weight history into per-date membership sets.
    
    Tushare provides:
        index_weight(index_code='000300.SH', trade_date='20200101')
        → list of constituents on that date
    
    We need to convert this to: when did each stock enter/leave the index?
    
    Algorithm:
    1. For each index (csi300, csi500, csi800):
       - Get membership snapshots at monthly intervals
       - For each (ticker, date) presence/absence, find entry/exit dates
       - Map ticker to entity_id via registry
       - Generate (entity_id, ticker, enter_date, exit_date) records
    
    2. Write per-index membership files:
       data/processed/index_membership/{index}.parquet
    """
```

**Acceptance**:
- CSI300 membership covers 2005-present
- Entry/exit dates are consistent (no overlap, no gaps for active members)
- A spot check: 比亚迪 (SZ002594) entered CSI300 around 2019, verified

### Stage 4: PIT Universe File Generation

```python
# scripts/data_pipeline/04_build_universe_files.py

def build_universe_files(
    entity_registry_path: Path,
    index_membership_dir: Path,
    output_dir: Path,
) -> None:
    """
    Generate qlib instruments/*.txt files in PIT format.
    
    Files generated:
    - all.txt:      every entity-period
    - csi300.txt:   only periods where the entity was in CSI300
    - csi500.txt:   only periods where the entity was in CSI500
    - csi800.txt:   only periods where the entity was in CSI800
    
    For all.txt, one row per (ticker, list_date, delist_date or '2099-12-31').
    For csi*.txt, intersect entity-periods with index membership periods.
    Output rows: (ticker, enter_date, exit_date)
    """
```

**Acceptance**:
- `all.txt` has ≥ 5500 rows (5000+ stocks, some with multiple periods)
- Format matches qlib's expected tab-separated 3-column layout
- Load test: `D.list_instruments(D.instruments('all'), as_list=True)` succeeds

### Stage 5: Feature Bin Generation

```python
# scripts/data_pipeline/05_build_qlib_bins.py

def build_qlib_bins(
    tushare_dir: Path,
    entity_registry_path: Path,
    output_dir: Path,    # qlib provider directory
    backup_dir: Path,    # safety backup
) -> None:
    """
    Build qlib bin format with entity-aware NaN gaps.
    
    For each (ticker):
    1. Look up all entity periods from registry
    2. For each entity period [list_date, delist_date]:
       - Pull OHLCV from Tushare daily data
       - Apply adjustment factors
    3. Concatenate periods with explicit NaN-row gaps between them
       (gap rows: trading days between Entity N's delist and Entity N+1's list)
    4. Write to qlib bin format
    
    Output: D:/qlib_data/my_cn_data_pit/
       ├── calendars/
       ├── features/
       │   ├── sh600000/
       │   ├── sh600519/
       │   ├── sh600753/   ← contains Entity 1 + gap + Entity 2
       │   └── ...
       └── instruments/
           ├── all.txt
           ├── csi300.txt
           └── ...
    
    DO NOT overwrite existing my_cn_data. Write to a NEW directory.
    Caller must explicitly switch the pipeline to use the new provider.
    """
```

**Acceptance**:
- All bins load successfully via `D.features(...)`
- Spot check on a known reuse case (e.g. SH600753):
  - 2019-08-01: NaN (in the gap between entities)
  - 2019-10-01: NaN
  - 2019-11-21: valid data (Entity 2 starts)
- Spot check on a pure delisting (e.g. SH600087):
  - 2019-08-22: valid data (last day)
  - 2019-08-23: NaN
  - 2024-12-31: NaN
- `D.list_instruments(...)` returns instruments active during the queried period only

### Stage 6: Validation

```python
# scripts/data_pipeline/06_validate_pit_data.py

def validate_pit_data(
    provider_uri: Path,
    entity_registry_path: Path,
    output_report: Path,
) -> int:
    """
    Comprehensive PIT data validation.
    
    Tests:
    
    A. Re-run survivorship_verification.py against the new provider
       → MUST get GOOD verdict now
    
    B. Entity boundary check (for each ticker with reuse_count >= 2):
       - Verify NaN gap exists between entity periods
       - Verify NaN gap length is plausible (30-365 days typical)
    
    C. Time-travel sanity check:
       - Query universe at 5 random historical dates
       - Verify no entity in universe has list_date > query_date
       - Verify no entity in universe has delist_date < query_date
    
    D. ts operator boundary check:
       - For a known-reuse ticker, compute ts_mean(close, 20) starting
         from day 1 of Entity 2
       - Verify first 19 values are NaN (no leakage from Entity 1)
    
    E. Index membership check:
       - At date 2018-06-11, CSI300 should include 贵州茅台 (entered on this date)
       - At date 2018-06-10, CSI300 should NOT include 贵州茅台
    
    Exit code: 0 if all pass, 1 if warnings, 2 if failures
    """
```

**Acceptance**: Exit code 0. Report HTML with green checks across the board.

---

## 6. PIT Query Layer

This is the new module that wraps qlib and ensures every query is entity-aware.

```python
# src/pit/query.py

class PITDataProvider:
    """
    Point-in-Time data provider.
    
    Wraps qlib with entity-aware queries. All time-series operations
    are guaranteed to respect entity boundaries via NaN gaps in the
    underlying data.
    """
    
    def __init__(self, provider_uri: str, entity_registry_path: str,
                 cache_max_entries: int = 256):
        qlib.init(provider_uri=provider_uri, region="cn")
        self.registry = pd.read_parquet(entity_registry_path)
        # LRU cache over (universe_name, start_date, end_date, frozenset(fields))
        # eviction policy. Phase C.2 implements this in src/pit/cache.py;
        # do NOT use an unbounded dict — long backtests will OOM otherwise.
        # The 256-entry default is calibrated for ~8GB working set on the
        # full csi300 universe; tune via cache_max_entries.
        self._cache: "LRUCache[CacheKey, pd.DataFrame]" = LRUCache(
            maxsize=cache_max_entries,
        )
    
    def get_universe(
        self,
        date: pd.Timestamp,
        universe_name: str = "all",
    ) -> list[str]:
        """
        Get tickers active on a specific date in a specific universe.
        
        Examples:
            get_universe('2018-01-01', 'csi300')
            → ['SH600519', 'SH600000', ..., 'SZ000001']  (300 tickers)
        """
    
    def get_universe_range(
        self,
        start_date: pd.Timestamp,
        end_date: pd.Timestamp,
        universe_name: str = "all",
    ) -> dict[pd.Timestamp, list[str]]:
        """
        Get universe for each trading date in range.
        """
    
    def get_features(
        self,
        fields: list[str],
        start_date: pd.Timestamp,
        end_date: pd.Timestamp,
        universe_name: str = "all",
        align: str = "universe",  # 'universe' | 'tradable_only'
    ) -> pd.DataFrame:
        """
        Get features, with PIT-correct universe alignment.
        
        align='universe':       Returns full panel, NaN where (date, ticker) 
                               not in universe
        align='tradable_only':  Returns long-format, only rows where (date, ticker)
                               in universe
        """
    
    def resolve_entity(self, ticker: str, date: pd.Timestamp) -> str | None:
        """
        Given a ticker and date, return the entity_id active at that date.
        Returns None if no entity active (delisted period).
        """
```

**Critical guarantee**: Calling `get_features(date_range, universe='csi300')` at any historical date returns exactly what would have been available on that date — no information from the future, no contamination from past ticker reuses.

---

## 7. Migration Plan

### 7.1 Phasing

Migrate incrementally. Don't try to swap everything at once.

```
Week 1: Build new data
  - Stage 1-5: New provider at D:/qlib_data/my_cn_data_pit/
  - Keep existing D:/qlib_data/my_cn_data unchanged
  - Validate (Stage 6)

Week 2: Wrap with PIT query layer
  - Implement src/pit/query.py
  - Smoke tests: query produces same results as old provider for known-good stocks

Week 3: Integrate with factor mining
  - Update factor_mining/evaluator.py to use PITDataProvider
  - Re-run any existing factor mining runs against new data
  - Compare metrics: should see lower IR/Sharpe (correctly!) on contaminated factors

Week 4: Switch primary pipeline
  - Update existing PipelineConfig to default to new provider
  - Existing trained models become "legacy"
  - First full retrain on PIT data
  - Document the change in OpenSpec
```

### 7.2 Safety Net

- Old `my_cn_data` is **never deleted**, only superseded
- Both providers remain queryable indefinitely
- All scripts that touch data require `--confirm-destructive` flag
- All ingestion scripts have `--dry-run` mode

### 7.3 Calibration Step

Once the new data exists, **re-run §2's survivorship verification** against the new provider:

| Before | After |
|--------|-------|
| ❌ BAD: 4 extended, 3 missing | ✅ GOOD: 7/7 properly delisted |

Document the difference in the project journal.

---

## 8. Integration Points (Existing Code)

### 8.1 Factor Mining Module

```python
# src/factor_mining/evaluator.py (Phase 2 of factor mining)

class FactorEvaluator:
    def __init__(self, pit_provider: PITDataProvider, config: EvalConfig):
        self.pit = pit_provider
        ...
    
    def evaluate(self, expression: Expression) -> FactorMetrics:
        # All queries go through PIT provider
        features = self.pit.get_features(
            fields=expression.required_features,
            start_date=self.config.train_start,
            end_date=self.config.train_end,
            universe_name=self.config.universe,
        )
        ...
```

This is the **only** place factor mining touches data. PIT correctness is enforced at the boundary.

### 8.2 Training Pipeline

```python
# src/training/data_loader.py (existing, to be updated)

# OLD:
qlib.init(provider_uri="D:/qlib_data/my_cn_data")
data = D.features(instruments=...)

# NEW:
provider = PITDataProvider(
    provider_uri="D:/qlib_data/my_cn_data_pit",
    entity_registry_path="...entity_registry.parquet",
)
data = provider.get_features(...)
```

The diff is minimal in calling code. The behavior change is large.

### 8.3 Backtest Module

The backtester must:
- Use `get_universe(date)` to know what's tradable each day
- Skip orders on tickers not in universe on that date
- For multi-day positions: detect if a held position's ticker becomes a different entity (rare but catastrophic — should liquidate before delisting)

```python
# src/backtest/runner.py (updated)

for date in trading_dates:
    universe_today = self.pit.get_universe(date, universe_name)
    
    # Liquidate positions in tickers not in universe today
    for ticker in self.portfolio.positions:
        if ticker not in universe_today:
            self.liquidate(ticker, date, reason="delisted_or_left_universe")
    
    # Place new orders only in current universe
    ...
```

---

## 9. Testing Strategy

### 9.1 Unit Tests

```
tests/pit/
├── test_entity_resolution.py     # ticker reuse detection accuracy
├── test_universe_queries.py      # PIT correctness of get_universe
├── test_feature_alignment.py     # NaN gap behavior
├── test_index_membership.py      # CSI300 entry/exit dates
└── test_query_layer.py           # API correctness
```

### 9.2 Reference Cases (manually curated, never auto-generated)

A small file `tests/pit/reference_cases.yaml` with hand-verified facts:

```yaml
ticker_reuse_cases:
  - ticker: SH600753
    entity_1:
      name: 庞大集团
      list_date: 2011-04-25
      delist_date: 2019-07-26
    entity_2:
      name: ST友谊
      list_date: 2019-11-21
      delist_date: null
  - ticker: SH600268
    entity_1:
      name: 国电南自
      list_date: 1997-07-14
      delist_date: 2019-05-23
    entity_2:
      name: ST南卫
      list_date: 2019-08-22
      delist_date: null

pure_delisting_cases:
  - ticker: SH600087
    name: '*ST长航'
    list_date: 2011-08-16
    delist_date: 2019-08-22
    no_reuse: true

index_membership_cases:
  csi300:
    - ticker: SZ002594
      action: enter
      date: 2019-12-13
      reason: "BYD entered CSI300"
    - ticker: SH600015
      action: leave
      date: 2022-06-13
      reason: "华夏银行 dropped from CSI300"
```

Tests assert these facts hold in the data. When Tushare data is refreshed, these tests catch silent regressions.

### 9.3 Property-Based / Parametrized Tests

**Dependency note**: `hypothesis` is not currently a project dependency. Two options for Phase C.3:

- (a) Add `hypothesis>=6.0` to `pyproject.toml` `[project.optional-dependencies].dev` and use `@given` as below.
- (b) Stick with `@parametrize` from `pytest` and a hand-curated set of 50-100 representative dates / tickers. Acceptable for invariants where coverage of the input space is more important than randomized fuzzing.

Recommendation: option (b) for invariant checks (date / ticker space is large but easy to sample structurally); option (a) only if a follow-up phase generates synthetic Tushare-shaped data for negative tests.

```python
# tests/pit/test_invariants.py

# Option (a) — hypothesis:
@given(date=date_strategy(min='2010-01-01', max='2024-12-31'))
def test_universe_no_future_listings(date):
    """Any ticker in universe on date must have list_date <= date."""
    universe = pit.get_universe(date, 'all')
    for ticker in universe:
        entity = pit.resolve_entity(ticker, date)
        assert entity.list_date <= date

# Option (b) — parametrize:
SAMPLED_DATES = [
    "2010-01-04", "2012-06-15", "2015-01-05", "2016-01-04",  # known regime points
    "2018-12-28", "2020-03-19", "2021-02-18", "2024-02-05",
    # ... 50 dates spanning known events: 2015 crash, 2020 COVID, etc.
]

@pytest.mark.parametrize("date", SAMPLED_DATES)
def test_universe_no_future_listings(date):
    universe = pit.get_universe(date, 'all')
    for ticker in universe:
        entity = pit.resolve_entity(ticker, date)
        assert entity.list_date <= pd.Timestamp(date)

@pytest.mark.parametrize("date", SAMPLED_DATES)
def test_universe_no_past_delistings(date):
    universe = pit.get_universe(date, 'all')
    for ticker in universe:
        entity = pit.resolve_entity(ticker, date)
        assert entity.delist_date is None or entity.delist_date > pd.Timestamp(date)

# Critical — this test MUST use real qlib operators, not pandas rolling
# (see §4.3.2). pandas may default to min_periods=N while qlib operators
# may not, leading to false-positive test passes against pandas while
# real qlib leaks across entity boundaries.
@pytest.mark.parametrize("ticker,window", [
    ("SH600753", 20), ("SH600753", 60),  # ticker with reuse_count=2
    ("SH600268", 20), ("SH600268", 60),
])
def test_qlib_ts_mean_does_not_cross_entities(ticker, window):
    """qlib Mean($close, N) at day N-1 of Entity 2 must be NaN."""
    e2_start = pit.registry.query(
        f"ticker=='{ticker}' and reuse_count==2"
    )["list_date"].iloc[0]
    # Day 5 of Entity 2 — only 5 valid points exist inside Entity 2,
    # so a window=20 mean MUST be NaN under proper min_periods handling.
    day_5 = pit.calendar.shift(e2_start, n=5)

    # qlib's actual Mean() operator — NOT pandas rolling — is what the
    # downstream feature builder uses.
    df = D.features([ticker], [f"Mean($close, {window})"],
                    start_time=e2_start, end_time=day_5)
    assert df.iloc[-1].isna().all(), (
        f"qlib Mean($close, {window}) at day 5 of Entity 2 returned "
        f"{df.iloc[-1].values}, expected NaN. Either qlib's operator uses "
        f"min_periods < {window} OR NaN-gap not properly written. "
        f"See §4.3.2."
    )
```

**If `test_qlib_ts_mean_does_not_cross_entities` ever fails**: do not just adjust the assertion. Either (a) qlib's `Mean` operator is silently leaking across the NaN gap — fix by wrapping with explicit `min_periods=N` in qlib's `Expression` layer, or (b) the NaN-gap bin write in Stage 5 is wrong. Both are critical bugs.

---

## 10. Risks & Mitigations

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Tushare delisting data incomplete | High | High | Cross-check with public delisting announcements (Wind, choice); maintain a manual override file `data/manual_delistings.yaml` |
| Tushare namechange data has errors | Medium | Medium | Reference cases in tests catch known cases; manual review of low-confidence detections |
| Some tickers had multiple reuses (3+) | Low | Medium | Algorithm handles N reuses correctly; tests with synthetic 3-reuse cases |
| qlib doesn't respect instruments date ranges at compute time | Low | Critical | Validation Stage 6.D explicitly tests this; if it fails, NaN gaps in bin data are our backup |
| New pipeline produces lower Sharpe than old pipeline | High | Low (this is GOOD news — old was inflated) | Document the difference; communicate to stakeholders that previous "alpha" was partly survivorship |
| Migration breaks existing trained models | High | Medium | Old provider preserved; legacy models can re-run for comparison; full retrain after migration is expected |
| First runs of factor mining on PIT data find "no good factors" | Medium | Low | Expected — old factors were partly fitting noise. Adjust expectations, look for true alpha. |

---

## 11. Tasks Breakdown

### Phase 0: Governance kickoff — ~0.5 day (blocking)

| Task | LOC | Deliverable |
|------|-----|-------------|
| **0.1** OpenSpec proposal | ~100 | `openspec/changes/pit-universe-foundation/proposal.md`, `design.md`, `tasks.md`, `spec.md` skeletons |
| **0.2** User-curated reference cases SEED | ~100 lines | `tests/pit/reference_cases.yaml` seed file with ≥10 hand-verified cases (user provides; agent does NOT generate) |
| **0.3** Tushare access validation | — | One-off script run confirming `TUSHARE_TOKEN` works for `stock_basic` + `namechange` + `index_weight` endpoints; record account tier in proposal |

**Acceptance**: OpenSpec proposal merged. Reference cases YAML committed by the user (not the agent). Tushare token verified.

### Phase A: Foundation (Week 1) — ~7-10 days (revised from earlier 5-day estimate; see realism note below)

| Task | LOC | Deliverable |
|------|-----|-------------|
| **A.1** Tushare ingestion script | ~400 | `scripts/data_pipeline/01_fetch_tushare.py` |
| **A.2** Entity resolution algorithm | ~500 | `scripts/data_pipeline/02_resolve_entities.py` + `tests/pit/test_entity_resolution.py` |
| **A.3** Extend reference cases YAML (agent, with citations) | grow to ~30 cases | Additional rows in `tests/pit/reference_cases.yaml`, each row's PR body MUST cite the Tushare `stock_basic` / `namechange` response that justifies it |
| **A.4** Index membership resolver | ~300 | `scripts/data_pipeline/03_resolve_index_membership.py` |

**Acceptance**: Entity registry contains all 4 ticker-reuse cases identified in §2 and 3 pure-delistings. CSI300 history covers 2005-present.

**Timeline realism note**: The original "5 days" estimate was optimistic. Realistic budget:

- A.1: Tushare API rate limits dominate. Backfill of 5000 stocks × 25 years × 5 endpoints = ~12-24 h wall-clock. Script dev: 1-2 days; data pull: overnight; verification: 0.5 day.
- A.2: First version of entity resolution always misses edge cases (year-of delist+relist, missing delist_date, `list_status='P'`). Expect 2-3 iteration cycles against reference cases. Budget 2-3 days.
- A.3: Each new reference case takes 2-5 min of careful cross-checking (`stock_basic` + `namechange` + manual verification via public news). 20 new cases ≈ 1-2 hours focused work; if agent does this, each row needs a cited Tushare API response in the PR body (§15.2).
- A.4: Tushare `index_weight` rate-limited and patchy for 2005-2010. Budget 1.5-2 days including manual gap-fill via 中证指数 official data if needed.

Total: 7-10 days realistic, longer if Tushare account tier is below "pro".

### Phase B: Data Pipeline (Week 2) — ~5 days

| Task | LOC | Deliverable |
|------|-----|-------------|
| **B.1** Universe file builder | ~250 | `scripts/data_pipeline/04_build_universe_files.py` |
| **B.2** qlib bin builder with NaN gaps | ~600 | `scripts/data_pipeline/05_build_qlib_bins.py` |
| **B.3** PIT validation suite | ~400 | `scripts/data_pipeline/06_validate_pit_data.py` |
| **B.4** Re-run §2 verification | — | Validation passes |

**Acceptance**: New provider at `D:/qlib_data/my_cn_data_pit/` exists. §2 verification returns GOOD. All Stage 6 tests pass.

### Phase C: Query Layer (Week 3) — ~5 days

| Task | LOC | Deliverable |
|------|-----|-------------|
| **C.1** PITDataProvider class | ~400 | `src/pit/query.py` |
| **C.2** Caching layer | ~200 | `src/pit/cache.py` |
| **C.3** Property-based tests | ~300 | `tests/pit/test_invariants.py` |
| **C.4** Spot-check tests against reference cases | ~200 | `tests/pit/test_query_layer.py` |

**Acceptance**: Any query through PITDataProvider is PIT-correct. Property tests pass for 100+ random dates. Reference cases match.

### Phase D: Integration (Week 4) — ~5 days

| Task | LOC | Deliverable |
|------|-----|-------------|
| **D.1** Wire factor_mining to PIT layer | ~150 | `src/factor_mining/evaluator.py` (Phase 2 of factor mining) |
| **D.2** Wire training pipeline | ~100 | Updates to `src/training/` |
| **D.3** Wire backtester | ~200 | Updates to `src/backtest/` |
| **D.4** Migration guide | — | `docs/pit_migration_guide.md` |
| **D.5** Calibration run + benchmark | — | Side-by-side comparison report |

**Acceptance**: A full pipeline run on PIT data completes successfully. Factor mining produces correct (likely lower!) metrics. Backtest correctly avoids trading delisted stocks.

---

## 12. Acceptance Criteria (Overall)

### Functional
- [ ] §2 survivorship verification returns GOOD verdict on new provider
- [ ] Entity registry contains all known ticker-reuse cases from §11 reference data
- [ ] `D.list_instruments(D.instruments('csi300'))` returns date-correct sets
- [ ] `ts_mean(close, 20)` does not cross entity boundaries
- [ ] Backtester refuses to trade in delisted tickers
- [ ] All Tier 1-3 unit tests pass

### Data Quality
- [ ] At least 500 delisted stocks present in registry (Chinese A-share has ~600+ delistings)
- [ ] At least 10 ticker-reuse cases detected (Chinese A-share has dozens)
- [ ] No entity has overlapping periods
- [ ] No entity has delist_date < list_date

### Performance
- [ ] PIT query for full panel (5000 stocks × 5 years) completes in < 30s
- [ ] Caching makes repeat queries < 5s
- [ ] Pipeline run time within 2x of current pipeline (some overhead expected)

### Migration Safety
- [ ] Old provider preserved at `D:/qlib_data/my_cn_data`
- [ ] All destructive scripts have `--dry-run` and `--backup-dir`
- [ ] Migration documented step-by-step with rollback procedure

### Observability
- [ ] Migration validation report generated as HTML
- [ ] Side-by-side metrics comparison (old vs new) saved
- [ ] All ticker-reuse cases logged with manual-review recommendation

---

## 13. Open Questions for User

These need decisions before implementation, even though they're not blockers:

1. **Tushare account level**: Some Tushare endpoints (especially historical index weights) require 2000+ points or pro account. Do you have:
   - [ ] Basic account
   - [ ] 2000+ points
   - [ ] Pro
   
   If basic only, we may need an alternative source for index history (e.g., 中证指数公司 official data).

2. **Manual delisting override**: There will be cases where Tushare's delist_date is wrong or missing. Do you want:
   - [ ] Read-only Tushare (trust whatever it says)
   - [ ] Allow manual override via `data/manual_delistings.yaml` (recommended)

3. **Storage budget**: New provider adds ~20-30GB (mostly duplicated bins with gaps). Confirm disk space available.

4. **Time priority**: This is ~3-4 weeks for one developer. Can it be parallelized with factor mining Phase 1-3? (Yes — they don't depend on each other until factor mining Phase 5.)

5. **Old provider preservation**: should the existing `D:/qlib_data/my_cn_data` be modified in place (e.g. to retroactively NaN-pad delisted tickers) or left untouched? Recommendation: **untouched** — keep the old as a frozen comparison baseline, only consume the new `D:/qlib_data/my_cn_data_pit/`. Any retroactive modification of the legacy provider is out of scope.

---

## 14. What to Tell the Coding Agent When Handing Off

Originally written for DeepSeek; applies to OpenCode, Claude, Codex, or any code-generating agent.

> **Cross-reference (mandatory)**: All rules in `AGENTS.md > Implementation discipline` apply to every PR in this plan in addition to the phase-specific guidance below. In particular: `pytest tests/...` must pass before any "done" claim; mechanical-move PRs (Phase D wiring) must include the whole-file content diff per AGENTS.md §10; no silent fallback; never invent fields, sector names, or company names — grep the producer and cite the API response. §15 below operationalises these into PR-level checklists.

When pasting this document to the agent for implementation:

1. **Start with Phase A only**. Do not let it scope-creep into Phase B before A is done. Each phase is a separate PR.

2. **Phase A.1 (Tushare ingestion) requires a Tushare token**. Provide it as an environment variable, never hardcoded.

3. **Phase A.2 (Entity resolution) is the trickiest part**. The algorithm needs careful thought about edge cases:
   - Stocks that delisted and re-listed in the same year (rare but exists)
   - Stocks where Tushare's `delist_date` is missing but the data clearly ends
   - Stocks where `list_status='P'` (suspended pending decision)
   
   The agent should refer to `tests/pit/reference_cases.yaml` as the source of truth, NOT auto-detect everything.

4. **Phase B.2 (qlib bin builder) involves writing binary qlib format**. The agent should:
   - Read qlib's `dump_bin.py` source for the exact format
   - Use qlib's own utilities where possible (don't reimplement)
   - Write to a NEW directory, never overwrite

5. **Phase C is where most subtle bugs hide**. Tell the agent: "Every test in `tests/pit/test_invariants.py` is sacred. If you change one to make it pass, you're doing it wrong."

6. **Migration in Phase D is high-risk**. Require explicit user confirmation before:
   - Switching pipeline default provider
   - Deleting old data (never automatically — always require flag)
   - Promoting any factor or model trained on contaminated data

7. **Resist the urge to skip the reference cases YAML — AND don't auto-generate it**. AI will want to detect everything algorithmically and will hallucinate plausible-sounding company / sector names (this has happened before in this project — see the post-mortem in `research/sector_alpha_consistency.md` where the agent invented sectors like `通用设备` and `汽车服务` that don't exist in the Shenwan taxonomy).

   Hard rule for `tests/pit/reference_cases.yaml`:
   - The first ≥10 cases (covering known ticker-reuse + pure-delisting examples from §2) MUST be **user-provided** in Phase 0.2, not agent-generated.
   - The agent may add new cases in Phase A.3, but **each new row's PR body MUST cite the Tushare API response** (`stock_basic` row + relevant `namechange` rows) that justifies the entry.
   - PRs with uncited reference rows are rejected by the reviewer.

   Manual curation of ~30 cases takes 2 hours and catches algorithm bugs forever.

---

## 15. OpenCode Operational Workflow

This section makes the agent collaboration model explicit. §1-14 cover **what to build**; §15 covers **how to build it**. Both are required for the project to land cleanly.

### 15.1 Branch & PR mapping

```
One task in §11 → one branch → one PR.

Branch naming:    pit/phase-{0,A,B,C,D}-{N}-{slug}
  e.g.            pit/phase-a-2-entity-resolution
                  pit/phase-d-3-backtester-wiring
PR title prefix:  [PIT-{phase}.{N}] <imperative summary>
  e.g.            [PIT-A.2] Build entity registry from Tushare stock_basic + namechange
                  [PIT-C.1] Implement PITDataProvider class
```

Each phase is a sequence of small PRs. **Phase N+1 cannot start until every Phase N PR is merged AND the phase acceptance checklist (§11 / §12) is fully ticked.** Enforced by user gate, not automation — the agent must wait for the user's explicit Phase-done signal (§15.5).

### 15.2 Per-PR body — required sections

Every PR opened under this plan MUST include these sections in the body. Missing sections = reviewer rejects.

```markdown
## Acceptance (from §11 / §12)
- [ ] <checkbox 1 from the task's acceptance line>
- [ ] <checkbox 2>
- ...

## Pre-push validation
- [ ] `pytest tests/pit/ tests/logic/ tests/governance/` — N passed / 0 failed (paste tail)
- [ ] (Phase D / mechanical moves only) Pre/post AGENTS.md content diff pasted below
- [ ] (Phase A / B data scripts) Sample run output pasted below to prove the script actually executed
- [ ] No `TUSHARE_TOKEN`, secrets, or absolute personal paths in committed files
- [ ] (Phase A.3 only) Per-row Tushare API response citations for any new `reference_cases.yaml` entries

## Mechanical-move diff (Phase D wiring only)
<paste the AGENTS.md §10 whole-file content diff:
  git show <pre-sha>:<old-path> > /tmp/pre.py
  diff <(grep -vE '^(\s*$|\s*#|\s*"""|^import |^from )' /tmp/pre.py | sort) \
       <(cat <new-path-1> <new-path-2> ... | \
          grep -vE '^(\s*$|\s*#|\s*"""|^import |^from )' | sort)
>
Output should be empty or trivially explainable. Any unexpected line is drift that must be reverted or justified.

## Reference case citations (Phase A.3 only)
For each new row added to tests/pit/reference_cases.yaml, paste the
JSON / table response from Tushare (`stock_basic` row + relevant
`namechange` rows) that justifies the row. PRs without citation per row
are rejected.
```

### 15.3 Review loop

```
Author         → OpenCode (build agent in opencode.json)
Reviewer 1     → Human (user) — REQUIRED, final approver
Reviewer 2     → spec-reviewer subagent (opencode.json) — RECOMMENDED for design-heavy
                 tasks: Phase 0.1 OpenSpec proposal, Phase A.2 entity resolution algorithm,
                 Phase B.2 qlib bin builder.
Reviewer 3     → code-reviewer subagent (opencode.json) — RECOMMENDED for high-risk
                 wiring: all Phase D tasks.
```

Subagent reviews are **signals to inform the human reviewer**, not gates. The user has final approval. If the subagent flags a concern that the user accepts as acceptable, the human reviewer documents the rationale in the PR thread and proceeds.

### 15.4 OpenCode self-discipline (in addition to AGENTS.md)

**Inherited from `AGENTS.md > Implementation discipline`** (mandatory across the project):

| AGENTS.md rule | PIT-relevant manifestation |
|----------------|----------------------------|
| #1 Run pytest before claiming done | Push with red tests = PR auto-reject by reviewer |
| #4 Never invent fields | Especially: never invent company names, sector names, ticker codes, or Tushare API response shapes. Grep `reference_cases.yaml` first. |
| #8 No silent fallback | Entity resolution must **raise** on ambiguous reuse, never silently pick the most recent entity |
| #10 Mechanical-move PRs require pre/post diff | Phase D wiring qualifies — show whole-file content diff in PR body |

**PIT-specific additions** (new rules introduced by this plan):

5. **Reference cases YAML is user-curated seed**. Phase 0.2 receives the first ≥10 cases from the user; agent may add more in Phase A.3 only with cited Tushare API response per case (§14 point 7).
6. **Adjusted-price PIT assumption is explicit** (§4.3.1). Do not "fix" `adj_factor` with reconstruction logic without a new dedicated ticket.
7. **NaN-gap depends on qlib operator `min_periods`** (§4.3.2). Validate against real qlib operators, not pandas rolling.
8. **No scope creep into Phase E+ dimensions** (§4.5). If a Phase A-D task appears to touch industry classification PIT, fundamentals PIT, or share-count PIT — stop and confirm with user.

### 15.5 Phase gating signal

The user acknowledges each phase completion by ONE of:

- Writing "**Phase X acceptance ✓**" in the merge commit message of the final PR of that phase, OR
- Adding a line to `openspec/changes/pit-universe-foundation/tasks.md` ticking the phase, OR
- A direct message in the OpenCode session: "Phase X done, proceed to Phase Y"

**The agent MUST NOT start the next phase before observing one of these signals.** If the agent thinks a phase is done but no signal has been issued, the agent waits and asks.

### 15.6 Failure mode handling

| Symptom | Reviewer / agent response |
|---------|---------------------------|
| pytest red on first push | Agent fixes in same PR before re-requesting review. Do NOT open a follow-up PR; rebase the failing commits. |
| Mechanical-move diff (§15.2) shows unexplained drift | Reviewer rejects with `git diff --stat` of unexpected files; agent reverts to true mechanical move OR justifies each line of drift in the PR body. |
| `reference_cases.yaml` expansion without Tushare citation per row | Reviewer rejects; agent removes uncited rows. Existing cited rows may stay. |
| Entity resolution ambiguity (e.g. 3-reuse case with overlapping list_date / delist_date) | Agent raises in algorithm + adds to `data/manual_overrides.yaml`. NEVER pick the most-recent entity silently. |
| Migration step (Phase D) needs to delete old data | NEVER auto-execute. PR documents the deletion; user runs `python scripts/data_pipeline/99_finalize_migration.py --confirm-destructive` manually after offline backup. |
| Tushare API rate-limit exceeded mid-Stage 1 backfill | Agent resumes from checkpoint (cached partial result), not from scratch. `01_fetch_tushare.py` MUST support resume from a `.checkpoint` file. |

### 15.7 Token / credential handling

- `TUSHARE_TOKEN` via environment variable ONLY. Read via `os.environ["TUSHARE_TOKEN"]`.
- `.env.example` documents required variables (no values committed). `.env` is `.gitignore`-d.
- Never embed token in YAML, Python source, commit messages, or PR descriptions.
- CI (if added later) uses GitHub Secrets. Local dev uses `direnv` / `.env` / shell export — agent's choice but **never committed**.
- The OpenCode config `opencode.json` may reference model providers and prompts but MUST NOT contain Tushare or any other data-source credentials.

### 15.8 Definition of done — checklist the user uses to sign each PR

```
[ ] PR body has all sections from §15.2
[ ] Acceptance checkboxes ticked match what the diff actually delivers
[ ] pytest output pasted shows green
[ ] (where applicable) Mechanical-move diff is empty or fully explained
[ ] (where applicable) Reference case citations present per row
[ ] No new files outside the task's stated Files-to-touch list
[ ] No edits to AGENTS.md, opencode.json, or other governance files (unless ticket says so)
[ ] No token / secret / personal path leaked into committed files
```

If any checkbox fails: reviewer comments with the failing item; agent fixes in the SAME PR (do not open a new PR for review-driven fixes — preserves history).

---

## End

When all 4 phases complete:
- Your data layer is professionally PIT-correct
- Factor mining produces metrics you can actually trust
- Survivorship bias and ticker reuse no longer silently inflate results
- The system is now in the same league as professional quant shops on data quality
- Future data sources (futures, options, fundamentals) can be added with the same PIT discipline

This is the most important infrastructure investment in the project. Don't rush it.
