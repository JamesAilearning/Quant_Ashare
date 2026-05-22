# A-share Survivorship & PIT Universe — Design Document

> **Goal**: Replace the current "static universe + stale data" approach
> with a Point-in-Time (PIT) universe system. At any historical date `t`,
> queries return exactly the stocks that were tradable on `t`, with no
> look-ahead bias and no stale-bin contamination on delisted tickers.
>
> **Status**: Design document, ready for implementation.
>
> **Effort estimate**: 3-4 weeks (one developer / AI agent) including
> migration of existing pipeline.
>
> **Blocking**: Factor mining Phase 5 (production integration). Phases
> 1-4 can proceed in parallel.

---

## 0. Why This Is Bigger Than It Looks

A naïve "universe" in a quant system is just a list of stock codes. A
PIT universe is a **time-indexed mapping** from
`(date, ticker) → metadata`, where:

- A stock's listing status (active / suspended / delisted) is part of its
  history, not its current state.
- The universe at date `t` is reconstructable from data available on
  date `t` — **no future information leaks backward**.
- The local bin SHALL faithfully reflect the historical delist events
  (NaN-after-delist), so time-series operators do not silently consume
  forward-filled values from a stale snapshot.

This is the difference between a research toy and a production system.
Most quant blow-ups can be traced to violations of PIT correctness.

The good news: qlib has primitives that support PIT (per-row date ranges
in instruments files). The bad news: the local bin (built before this
design) does not populate them correctly, and the previous diagnostic
(`scripts/data_quality/verify_survivorship.py`) was anchored on a
hand-curated `KNOWN_DELISTED` list that turned out to be partly
fabricated. We are going to fix both.

### 0.1 Post-mortem: fabricated baseline

The first iteration of this design (commit `e780f65`) was scoped around
"ticker reuse contamination" with `SH600753` (claimed to be 庞大集团 →
ST友谊) as the motivating example. Verification on 2026-05-22 against
Tushare `stock_basic` + `namechange` showed:

- **A-share does not have US-style ticker recycling.** Shanghai and
  Shenzhen exchange rules keep delisted tickers in a "reserved pool";
  the codes are not reassigned to fresh IPOs.
- **`SH600753` was never named 庞大集团.** Tushare's namechange history
  shows the ticker continuously listed since 1996 under names 冰熊股份 →
  东方银星 → 庚星股份 → \*ST海钦. The real 庞大集团 traded under ticker
  `601258`, which is itself now delisted and not reused.
- **Four of seven `KNOWN_DELISTED` entries were active stocks that had
  never delisted**, just renamed through ST cycles. The remaining three
  were real delistings but with `delist_date` off by 1.5-5 years.

The correction landed in PR `add-ashare-survivorship-correction`. The
present document is that PR's design. The lesson is documented in
§14 below and operationalised into `AGENTS.md > Implementation
discipline` rule #4 ("never invent fields").

---

## 1. Current State (What's Actually Broken)

From the (corrected) survivorship verification:

- The local qlib bin at `D:/qlib_data/my_cn_data` is missing or stale
  for the three verified delisted stocks below.
- Borrow-shell restructures (same ticker, new asset, continuous price
  series) are correctly continuous in the bin but were previously
  mis-read as "ticker reuse" — they are not, and require no special
  handling in the price layer.

Three verified delistings (cross-checked against Tushare
`stock_basic(list_status='D')` on 2026-05-22):

| Ticker | delist_date | Post-delist Tushare name | Era |
|--------|-------------|--------------------------|-----|
| `SH600087` | 2014-06-05 | 退市长油 | pre-2020 financial delisting |
| `SH600247` | 2021-03-22 | \*ST成城退 | 2020-2022 \*ST → 退市 mainstream |
| `SZ000023` | 2024-09-02 | \*ST深天退 | 2024+ post-退市新规 strict |

The Phase 0.2 reference cases YAML extends this set per the coverage
matrix in §9.2.

---

## 2. A-share Specifics

### 2.1 No ticker reuse

Per Shanghai and Shenzhen exchange convention as of 2026:

- Delisted tickers enter a "reserved pool" and are NOT reassigned to
  new IPOs.
- Even when a delisted company is fully liquidated, its code remains
  reserved by the exchange.
- B-share to A-share conversions and pre-stock-reform legacy codes
  preserved the legal entity continuity; there is no precedent for
  "ticker recycling" in the US sense.

Consequence: ticker is a stable identifier for price-series purposes
within the A-share universe. The design does NOT need an `entity_id`,
`reuse_count`, or any "ticker → multiple entities" mapping.

### 2.2 Borrow-shell restructure preserves continuity

A listed shell may be acquired via reverse merger, renamed, and have
new assets injected — without delisting. From a price-series PIT
perspective this is the same continuous trading instrument under one
ticker. The legal entity behind the shell may have changed, but the
exchange-traded series is continuous.

Example: `600145` was \*ST新亿; the shell was acquired by 亿阳信通
(借壳), the ticker stayed at `600145`, price trading continued. The
delisted registry does NOT contain `600145`; the price series is one
continuous run.

If a downstream consumer (attribution, research) needs to know that the
underlying entity / sector changed at the restructure date, it MUST use
the `PURPOSE_ATTRIBUTION` enum in `attribution_industry_loader.py`
(already wired). Training (`PURPOSE_TRAINING`) cannot consume these
annotations — this is enforced at the contract layer.

### 2.3 Universe membership is itself time-indexed

A stock's membership in CSI300 / CSI500 / CSI800 changes over time.
Backtesting today's CSI300 constituents on 2015 data is a silent
look-ahead bias. The PIT universe files (§4.2) encode entry/exit dates
per index per ticker.

---

## 3. Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                        Tushare API                                │
│  stock_basic · namechange · daily · adj_factor · suspend         │
│  · index_weight                                                   │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│           Delisted Registry Builder (NEW)                         │
│  - Consume stock_basic(list_status='D')                          │
│  - One row per delisted ticker                                    │
│  - Reference cases YAML asserts known-good rows                   │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│           PIT Universe Builder (NEW)                              │
│  - Generate instruments/*.txt with (ticker, start, end) per row  │
│  - One row per ticker; end_date = delist_date or 2099-12-31      │
│  - Universe files: all.txt, csi300.txt, ... time-indexed         │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│           qlib Bin Storage (NEW provider)                         │
│  - For each ticker: OHLCV from list_date to delist_date          │
│  - NaN for trading dates strictly after delist_date              │
│  - Borrow-shell tickers: continuous, no NaN gap                   │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│           PIT Query Layer (NEW)                                   │
│  - get_universe(date) → set of tickers active on date            │
│  - get_features(date_range, universe) → DataFrame                 │
│  - Time-series ops respect delist boundary via NaN-after-delist   │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│           Existing pipeline (factor_mining, training, backtest)   │
│  - Drop-in replacement for current data layer                     │
└──────────────────────────────────────────────────────────────────┘
```

---

## 4. Data Model

### 4.1 Delisted Registry (`delisted_registry.parquet`)

One row per ticker that has ever delisted.

```
ticker      | list_date  | delist_date | last_company_name | delist_reason
─────────────────────────────────────────────────────────────────────────────
SH600087   | 1997-06-12 | 2014-06-05  | 退市长油           | financial
SH600247   | 2000-11-23 | 2021-03-22  | *ST成城退          | financial
SZ000023   | 1993-04-29 | 2024-09-02  | *ST深天退          | major_violation
601258.SH  | 2011-04-25 | 2019-07-26  | 庞大集团           | financial
...
```

**Schema:**

| Column | Type | Description |
|--------|------|-------------|
| `ticker` | string | Market code, e.g. `SH600087` |
| `list_date` | date | First trading day |
| `delist_date` | date | Last trading day (NEVER NULL in this registry) |
| `last_company_name` | string | Display name at delisting (may include `(退)` per Tushare) |
| `delist_reason` | string | `financial` / `major_violation` / `voluntary` / `par_value` / `restructure_failure` / `other` |

**Invariants:**

- `ticker` is unique (no duplicates)
- `delist_date >= list_date`
- No currently-active stock (`list_status='L'`) appears in the registry

Source: Tushare `stock_basic(list_status='D')`. `delist_reason` may
require manual annotation for older delistings (Tushare returns
`delist_date` but not always a structured reason); the registry builder
classifies based on heuristic when explicit reason is unavailable.

### 4.2 PIT Universe Files (`instruments/*.txt`)

qlib's native format. One row per ticker. End date = delist_date for
delisted tickers, `2099-12-31` for active.

```
# instruments/all.txt
SH600519	2001-08-27	2099-12-31    # active: 贵州茅台
SH600000	1999-11-10	2099-12-31    # active: 浦发银行
SH600087	1997-06-12	2014-06-05    # delisted: 退市长油
SH600247	2000-11-23	2021-03-22    # delisted: *ST成城退
SZ000023	1993-04-29	2024-09-02    # delisted: *ST深天退
SH601258	2011-04-25	2019-07-26    # delisted: 庞大集团
...
```

**Format:** tab-separated, 3 columns: `ticker  start_date  end_date`.

**Per-index files** (`csi300.txt`, `csi500.txt`, `csi800.txt`) encode
time-indexed membership. A stock may enter and leave an index multiple
times:

```
SH600519	2018-06-11	2099-12-31    # entered CSI300, still member
SH600000	2005-04-08	2022-12-12    # left CSI300 on this date
SH600000	2024-06-17	2099-12-31    # re-entered later
```

This catches the bias of "backtest today's CSI300 constituents on 2015
data". The right way is to use the constituent set that existed at
backtest time.

### 4.3 Feature Storage (qlib bin format)

**Mostly unchanged**, but with one critical rule:

> For any ticker in the delisted registry, the bin SHALL contain NaN
> for all trading dates strictly after `delist_date`.

Example for `SH600087` (退市长油):

```
date         | open  | high  | low   | close | volume
2014-06-03   | 0.92  | 0.95  | 0.90  | 0.93  | 5678900   ← 倒数第三个交易日
2014-06-04   | 0.93  | 0.94  | 0.90  | 0.91  | 4567800   ← 倒数第二个
2014-06-05   | 0.91  | 0.92  | 0.88  | 0.89  | 3456700   ← 最后一个交易日 = delist_date
2014-06-06   | NaN   | NaN   | NaN   | NaN   | NaN       ← NaN-after-delist
2014-06-09   | NaN   | NaN   | NaN   | NaN   | NaN
...
```

NaN-after-delist prevents `ts_mean(close, 20)` from silently consuming
forward-filled stale values past the delisting. It also prevents the
"data extends 1600-2000+ days past delisting" failure mode that the
original `verify_survivorship.py` flagged.

### 4.3.1 Adjustment factor — known PIT limitation

`adj_factor` from Tushare is itself **not point-in-time**. The value
returned today for `2020-01-01` reflects all subsequent splits and
dividends through today — not what an investor on `2020-01-01` would
have seen. Building features on absolute adjusted prices is therefore
PIT-violating by construction.

**Mitigation policy** (this design):

1. **Do not use absolute adjusted prices as features.** Only within-ticker
   ratios and returns: `close / Ref(close, N)`, `pct_change`,
   `ts_mean(close)`, etc. — operations where the as-of-date `adj_factor`
   cancels because both numerator and denominator share the same scaling.

2. **Cross-delist arithmetic is invalid** regardless of `adj_factor`.
   §4.3's NaN-after-delist is the structural defence; the `adj_factor`
   inconsistency is a second-order reason for the same conclusion.

3. **Document this in the OpenSpec capability.** Any caller that wants
   absolute price features (rare; usually only mean-reversion / level-
   based strategies) MUST trigger a separate ticket to reconstruct
   historical `adj_factor` from raw splits + dividends.

4. **Tushare reality check**: As of 2026, Tushare's `adj_factor`
   endpoint returns today's snapshot. No "as-of-date" snapshot is
   exposed. If Tushare adds historical `adj_factor` snapshots later,
   this section gets revised.

### 4.3.2 qlib operator `min_periods` — delist boundary enforcement

The NaN-after-delist guarantee in §4.3 depends on the time-series
operator's `min_periods` behavior:

- pandas `rolling(N).mean()` default: `min_periods=N` → any NaN in the
  window returns NaN ✓ (correct boundary enforcement)
- pandas `rolling(N, min_periods=1).mean()`: returns the mean of
  available non-NaN values ✗ (window leaks pre-delist values into
  post-delist positions)

**qlib's own operators** (`Mean($close, N)`, `Ref($close, N)`,
`Corr(...)`, etc.) do **not** all share the same defaults. Validation
(Stage 6.D) MUST exercise the real qlib operators (not pandas rolling)
against a delisted ticker:

```python
# Expected behavior for a delisted ticker on day strictly after
# delist_date:
qlib_mean_20 = D.features([ticker], ['Mean($close, 20)'],
                          start_time=delist_date + 1day,
                          end_time=delist_date + 10days)
# Every value MUST be NaN (no valid points exist post-delist).
```

If a qlib operator silently uses `min_periods<N`, the NaN-after-delist
defence fails for that operator. Phase B.3 must document which qlib
operators are PIT-safe under default settings; for any that are not,
the project either (a) wraps them with `min_periods=N` explicitly, or
(b) bans them from feature expressions.

### 4.4 PIT-Aware Feature Query

A wrapper around qlib that returns PIT-correct data:

```python
def get_features(
    instruments: str,           # universe name, e.g. "csi300"
    fields: list[str],          # ["$close", "Ref($close, -1)"]
    start_date: str,
    end_date: str,
    universe_aware: bool = True,  # NEW
) -> pd.DataFrame:
    """
    Returns a (date, ticker) → fields DataFrame.

    When universe_aware=True:
    - Rows are dropped where the ticker is not in the universe on the
      given date (either pre-list or post-delist)
    - Time-series operators respect the delist boundary via the
      NaN-after-delist invariant
    """
```

Note: there is NO `resolve_entity(ticker, date)` method. Ticker is the
stable identifier; the A-share regulator does not recycle tickers.

### 4.5 Out-of-scope PIT dimensions (Phase E+ backlog)

This design covers **price / volume / universe membership / delist
history**. The following PIT dimensions are **explicitly out of scope
for Phases A-D**:

| Dimension | Current treatment | True PIT requires | Backlog ticket |
|-----------|-------------------|-------------------|----------------|
| **Entity model / ticker reuse** | Excluded by construction. A-share has no ticker reuse (§2.1). | — | (will not be opened) |
| **Industry classification** (Shenwan L1/L2) | Today's snapshot, bucketed retroactively. Already handled via `PURPOSE_ATTRIBUTION` vs `PURPOSE_TRAINING` enum in `attribution_industry_loader.py` — attribution allowed to use today's snapshot, training forbidden. | Historical Shenwan reclassification events with effective dates. | PHASE-E.1 |
| **Fundamentals** (PE / PB / ROE / financial statements) | Not currently used as features. | Distinguish "report period" (quarter the data describes) from "publication date" (when the data became public). PIT join uses publication date. | PHASE-E.2 |
| **Outstanding shares / market cap** | Same as `adj_factor` (§4.3.1) — today's snapshot. | Historical share-count snapshots. | PHASE-E.3 |
| **ST / *ST status within an active listing** | Partially handled via `suspend_d` ingestion + delist registry. ST/*ST transitions within one continuous listing are NOT PIT. | Per-day status snapshot via `stock_basic_change` (Tushare). | PHASE-E.4 |
| **Risk model / barra exposures** | Not used. | Historical loading snapshots. | (deferred indefinitely) |

**Agent rule**: if a task in Phase A-D appears to touch any of the
above, stop and confirm with the user. Do not silently extend scope.

### 4.6 Borrow-shell restructure — not modelled in price layer

A-share borrow-shell restructures (reverse-merger asset injection under
the original ticker) preserve price-series continuity. The capability
SHALL NOT inject NaN gaps, split the ticker into multiple "entities",
or otherwise discontinue the price series at the restructure date.

Restructure events MAY be annotated for attribution purposes via the
existing `PURPOSE_ATTRIBUTION` enum in `attribution_industry_loader.py`.
`PURPOSE_TRAINING` consumers cannot access these annotations — this is
already enforced at the contract layer.

Example: `600145` was \*ST新亿 → acquired via 亿阳信通 借壳; the ticker
stayed on `600145`, the price series remained continuous, and the
delisted registry does NOT contain `600145`. Any signal that needs to
know the underlying entity changed at the restructure date is the
attribution layer's responsibility.

---

## 5. Pipeline Stages

### Stage 1: Tushare Ingestion

```python
# scripts/data_pipeline/01_fetch_tushare.py

def fetch_tushare_data(
    tushare_token: str,    # via env, never literal
    start_date: str = "2000-01-01",
    end_date: str = "today",
    output_dir: Path = Path("./data/raw/tushare"),
) -> None:
    """
    Pulls from Tushare:

    1. stock_basic(list_status='L') → active_stocks.parquet
    2. stock_basic(list_status='D') → delisted_stocks.parquet
    3. namechange → all_namechanges.parquet
    4. daily (OHLCV) → daily/{year}/{ticker}.parquet
    5. adj_factor → adj_factor/{year}/{ticker}.parquet
    6. suspend_d → suspend.parquet
    7. index_weight → index_weight/{index}.parquet

    Supports --resume from a .checkpoint file (rate-limit recovery).
    """
```

**Acceptance**:
- ≥5000 stocks in active_stocks
- ≥325 stocks in delisted_stocks (Tushare reports 325 as of 2026-05)
- Index weights cover at least 2010-present

### Stage 2: Delisted Registry Build

```python
# scripts/data_pipeline/02_build_delisted_registry.py

def build_delisted_registry(
    tushare_dir: Path,
    reference_cases_path: Path,
    output_dir: Path,
) -> None:
    """
    Build delisted_registry.parquet from Tushare delisted bucket.

    Algorithm:
    1. Load stock_basic(list_status='D')
    2. For each row: emit (ticker, list_date, delist_date,
       last_company_name, delist_reason)
    3. Assert every row in reference_cases.yaml::pure_delisting_cases
       is present with matching delist_date
    4. Assert no row in reference_cases.yaml::active_control_cases
       appears in the registry
    5. Write delisted_registry.parquet
    """
```

**Acceptance**:
- All Tushare-listed delistings present
- All reference cases match
- No active stock false-flagged

### Stage 3: Index Membership Resolution

```python
# scripts/data_pipeline/03_resolve_index_membership.py

def resolve_index_membership(
    tushare_dir: Path,
    output_dir: Path,
) -> None:
    """
    Convert Tushare index_weight history into per-date membership sets.

    For each index (csi300, csi500, csi800):
    - Get membership snapshots at monthly intervals
    - Derive entry/exit dates per ticker
    - Write per-index membership files
    """
```

**Acceptance**:
- CSI300 history covers 2005-present
- Entry/exit dates non-overlapping for active members
- Reference cases (BYD enter 2019-12-13, 华夏银行 leave 2022-06-13)
  pass spot-checks (verified during baseline-correction work)

### Stage 4: PIT Universe File Generation

```python
# scripts/data_pipeline/04_build_universe_files.py
```

Generates `instruments/*.txt` files. `all.txt` has one row per ticker
(active OR delisted). `csi*.txt` files intersect ticker periods with
index membership periods.

**Acceptance**:
- `all.txt` row count ≈ 5325 (5000 active + 325 delisted)
- Format matches qlib's expected tab-separated 3-column layout
- `D.list_instruments(D.instruments('all'), as_list=True)` succeeds

### Stage 5: Feature Bin Generation

```python
# scripts/data_pipeline/05_build_qlib_bins.py
```

For each ticker, write OHLCV from `list_date` to either
`delist_date` (delisted) or today (active). For delisted tickers,
NaN-pad the bin past `delist_date` so windowing operators terminate
correctly.

**Output**: `D:/qlib_data/my_cn_data_pit/` — DO NOT overwrite the
legacy `D:/qlib_data/my_cn_data` directory.

**Acceptance**:
- All bins load via `D.features(...)`
- Spot-check on `SH600087`:
  - 2014-06-05: valid (last day)
  - 2014-06-06: NaN
  - 2024-12-31: NaN
- Spot-check on borrow-shell ticker `600145`:
  - Price series continuous across the restructure date (no NaN gap)

### Stage 6: Validation

```python
# scripts/data_pipeline/06_validate_pit_data.py
```

Comprehensive PIT validation. Tests:

- **A.** Re-run `scripts/data_quality/verify_survivorship.py` against
  the new provider → MUST get GOOD verdict
- **B.** Delist boundary check: for every ticker in the delisted
  registry, verify the bin has NaN strictly after `delist_date`
- **C.** Time-travel sanity: query universe at 5 random historical
  dates; no ticker has `list_date > query_date` or `delist_date <
  query_date`
- **D.** qlib operator boundary check: for a delisted ticker, compute
  `Mean($close, 20)` starting from `delist_date + 1`; verify all
  returned values are NaN (no leakage from pre-delist days)
- **E.** Index membership check: at 2018-06-11, CSI300 includes 贵州茅台
  (entered that day); at 2018-06-10, CSI300 does NOT include 贵州茅台
- **F.** Borrow-shell continuity: for known borrow-shell tickers, the
  bin has continuous data across the restructure date (no NaN gap)

Exit code: 0 if all pass, 1 if warnings, 2 if failures.

---

## 6. PIT Query Layer

```python
# src/pit/query.py

class PITDataProvider:
    """
    Point-in-Time data provider.

    Wraps qlib with universe-aware queries. All time-series operations
    are guaranteed to respect the delist boundary via NaN-after-delist
    in the underlying bin data.
    """

    def __init__(self, provider_uri: str, delisted_registry_path: str,
                 cache_max_entries: int = 256):
        qlib.init(provider_uri=provider_uri, region="cn")
        self.registry = pd.read_parquet(delisted_registry_path)
        # LRU cache over (universe_name, start_date, end_date,
        # frozenset(fields)). Phase C.2 implements this in
        # src/pit/cache.py; do NOT use an unbounded dict — long
        # backtests will OOM otherwise. The 256-entry default is
        # calibrated for ~8GB working set on the full csi300 universe;
        # tune via cache_max_entries.
        self._cache: "LRUCache[CacheKey, pd.DataFrame]" = LRUCache(
            maxsize=cache_max_entries,
        )

    def get_universe(
        self,
        date: pd.Timestamp,
        universe_name: str = "all",
    ) -> list[str]:
        """Tickers active on `date` in `universe_name`."""

    def get_universe_range(
        self,
        start_date: pd.Timestamp,
        end_date: pd.Timestamp,
        universe_name: str = "all",
    ) -> dict[pd.Timestamp, list[str]]:
        """Per-trading-day universe."""

    def get_features(
        self,
        fields: list[str],
        start_date: pd.Timestamp,
        end_date: pd.Timestamp,
        universe_name: str = "all",
        align: str = "universe",  # 'universe' | 'tradable_only'
    ) -> pd.DataFrame:
        """PIT-aligned panel data."""
```

Note: no `resolve_entity(ticker, date)` method. Ticker is the stable
identifier.

**Critical guarantee**: Calling `get_features(date_range,
universe='csi300')` at any historical date returns exactly what would
have been available on that date — no information from the future, no
stale data from forward-filled post-delist snapshots.

---

## 7. Migration Plan

### 7.1 Phasing

Migrate incrementally:

```
Week 1: Build new data
  - Stage 1-5: New provider at D:/qlib_data/my_cn_data_pit/
  - Keep existing D:/qlib_data/my_cn_data unchanged
  - Validate (Stage 6)

Week 2: Wrap with PIT query layer
  - Implement src/pit/query.py
  - Smoke tests: query produces same results as old provider for
    active, never-delisted, never-restructured tickers

Week 3: Integrate with factor mining
  - Update factor_mining/evaluator.py to use PITDataProvider
  - Re-run any existing factor mining runs against new data
  - Compare metrics: expect lower IR/Sharpe (correctly!) on factors
    that previously consumed forward-filled post-delist values

Week 4: Switch primary pipeline
  - Update PipelineConfig to default to new provider
  - Existing trained models become "legacy"
  - First full retrain on PIT data
  - Document the change in OpenSpec
```

### 7.2 Safety Net

- Old `my_cn_data` is **never deleted**, only superseded.
- Both providers remain queryable indefinitely.
- All scripts that touch data require `--confirm-destructive`.
- All ingestion scripts have `--dry-run` mode.
- Destructive migration steps (e.g. switching the default) need
  explicit user confirmation, not just a script flag.

### 7.3 Calibration Step

Once the new data exists, re-run `verify_survivorship.py` against the
new provider:

| Before | After |
|--------|-------|
| BAD or MISSING on the 3 verified delistings | GOOD: 3/3 properly delisted |

Document the difference in the project journal.

---

## 8. Integration Points (Existing Code)

### 8.1 Factor Mining Module

```python
# src/factor_mining/evaluator.py

class FactorEvaluator:
    def __init__(self, pit_provider: PITDataProvider, config: EvalConfig):
        self.pit = pit_provider
        ...

    def evaluate(self, expression: Expression) -> FactorMetrics:
        features = self.pit.get_features(
            fields=expression.required_features,
            start_date=self.config.train_start,
            end_date=self.config.train_end,
            universe_name=self.config.universe,
        )
        ...
```

PIT correctness is enforced at the data-access boundary.

### 8.2 Training Pipeline

```python
# src/training/data_loader.py

# OLD:
qlib.init(provider_uri="D:/qlib_data/my_cn_data")
data = D.features(instruments=...)

# NEW:
provider = PITDataProvider(
    provider_uri="D:/qlib_data/my_cn_data_pit",
    delisted_registry_path="...delisted_registry.parquet",
)
data = provider.get_features(...)
```

### 8.3 Backtest Module

The backtester:

- Calls `get_universe(date)` to know what is tradable each day
- Skips orders on tickers not in the universe on that date
- For multi-day positions: liquidates a held position on the trading
  day before its `delist_date` (uses the delisted registry as the
  source of truth)

```python
# src/backtest/runner.py

for date in trading_dates:
    universe_today = self.pit.get_universe(date, universe_name)
    for ticker in self.portfolio.positions:
        if ticker not in universe_today:
            self.liquidate(ticker, date,
                          reason="delisted_or_left_universe")
    ...
```

---

## 9. Testing Strategy

### 9.1 Unit Tests

```
tests/pit/
├── test_delisted_registry.py    # registry builder correctness
├── test_universe_queries.py     # PIT correctness of get_universe
├── test_feature_alignment.py    # NaN-after-delist behavior
├── test_index_membership.py     # CSI300 entry/exit dates
├── test_borrow_shell.py         # restructure-day continuity
└── test_query_layer.py          # PITDataProvider API correctness
```

### 9.2 Reference Cases (manually curated, never auto-generated)

`tests/pit/reference_cases.yaml` is the test oracle. The Phase 0.2 seed
SHALL cover the matrix below; aggregate count is a function of
coverage, not a target.

| Dimension | Minimum coverage | Example seed candidate |
|-----------|------------------|------------------------|
| Pre-2020 financial delisting | ≥1 case | `SH600087` 退市长油 (2014-06-05) |
| 2020-2022 \*ST → 退市 mainstream | ≥1 case | `SH600247` \*ST成城退 (2021-03-22) |
| 2024+ post-退市新规 strict | ≥1 case | `SZ000023` \*ST深天退 (2024-09-02) |
| ChiNext / STAR board delisting | ≥1 case | (agent pulls Tushare candidates; user verifies) |
| Same-day multi-stock batch delisting | ≥1 case | (agent pulls Tushare; user verifies) |
| Active stock negative control | ≥1 case | `SH600519` 贵州茅台 |
| CSI300 constituent change — enter | ≥1 case | `SZ002594` BYD enter 2019-12-13 |
| CSI300 constituent change — leave | ≥1 case | `SH600015` 华夏银行 leave 2022-06-13 |

Minimum ≈ 8 cases.

YAML shape (illustrative):

```yaml
pure_delisting_cases:
  - ticker: SH600087
    last_company_name: 退市长油
    list_date: 1997-06-12
    delist_date: 2014-06-05
    delist_reason: financial
    era: pre-2020 financial delisting
    cite_tushare: stock_basic(list_status='D'), 2026-05-22 pull
  # ... (≥3 spanning the 3 delisting eras above, +ChiNext/STAR, +batch)

active_control_cases:
  - ticker: SH600519
    name: 贵州茅台
    list_date: 2001-08-27
    note: must NOT appear in delisted_registry
    # No cite needed: public common knowledge.

index_membership_cases:
  csi300:
    - ticker: SZ002594
      action: enter
      date: 2019-12-13
      note: BYD entered CSI300
      cite_tushare: index_weight(index_code='000300.SH', start_date='20191201', end_date='20191231') — present on 20191231, absent on prior snapshots
    - ticker: SH600015
      action: leave
      date: 2022-06-13
      note: 华夏银行 dropped from CSI300
      cite_tushare: index_weight(...) — present on 20220601 and 20220630 (membership flipped intra-period)

borrow_shell_cases:
  # Documented for reference; tests assert no NaN gap across the date.
  - ticker: SH600145
    restructure_date: 2018-04-26
    note: 新亿 → 亿阳信通 borrow-shell; price series MUST be continuous
```

Tests assert these facts hold in the data. When Tushare data is
refreshed, these tests catch silent regressions.

### 9.3 Parametrized Invariant Tests

```python
# tests/pit/test_invariants.py

SAMPLED_DATES = [
    "2010-01-04", "2012-06-15", "2015-01-05", "2016-01-04",
    "2018-12-28", "2020-03-19", "2021-02-18", "2024-02-05",
    # ... 50 dates spanning known regime events
]

@pytest.mark.parametrize("date", SAMPLED_DATES)
def test_universe_no_future_listings(date):
    universe = pit.get_universe(date, "all")
    for ticker in universe:
        meta = pit.lookup(ticker)
        assert meta.list_date <= pd.Timestamp(date)

@pytest.mark.parametrize("date", SAMPLED_DATES)
def test_universe_no_past_delistings(date):
    universe = pit.get_universe(date, "all")
    delisted = pd.read_parquet(DELISTED_REGISTRY_PATH)
    for ticker in universe:
        match = delisted[delisted["ticker"] == ticker]
        if not match.empty:
            assert match["delist_date"].iloc[0] > pd.Timestamp(date)

# Critical — this test MUST use real qlib operators, not pandas
# rolling (see §4.3.2).
@pytest.mark.parametrize("ticker,delist_date,window", [
    ("SH600087", "2014-06-05", 20),
    ("SH600247", "2021-03-22", 20),
    ("SZ000023", "2024-09-02", 20),
])
def test_qlib_mean_returns_nan_after_delist(ticker, delist_date, window):
    """qlib Mean($close, N) at any day strictly after delist must be NaN."""
    start = pd.Timestamp(delist_date) + pd.Timedelta(days=1)
    end   = start + pd.Timedelta(days=20)
    df = D.features([ticker], [f"Mean($close, {window})"],
                    start_time=start, end_time=end)
    assert df.isna().all().all(), (
        f"qlib Mean($close, {window}) for {ticker} returned non-NaN "
        f"strictly after delist_date={delist_date}. Either qlib's "
        f"operator uses min_periods < {window} OR NaN-after-delist was "
        f"not written. See §4.3.2."
    )
```

**If `test_qlib_mean_returns_nan_after_delist` ever fails**: do NOT
just adjust the assertion. Either (a) qlib's `Mean` operator is
silently consuming forward-filled values — fix by wrapping with
`min_periods=N`, or (b) the NaN-after-delist write in Stage 5 is
broken. Both are critical bugs.

---

## 10. Risks & Mitigations

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Tushare `delist_date` data incomplete / inconsistent for older delistings | Medium | Medium | Cross-check with public delisting announcements (中证指数 official records); maintain `data/manual_delistings.yaml` overrides if needed |
| Manual `delist_reason` annotation drift | Low | Low | Heuristic classification + reference cases YAML pinning the eras that matter |
| qlib does not respect instruments date ranges at compute time | Low | Critical | Stage 6.D explicitly tests this against real qlib operators; if it fails, NaN-after-delist in bin data is the structural backup |
| New pipeline produces lower Sharpe than old | High | Low (this is GOOD news — old was inflated by stale post-delist values) | Document the difference; communicate to stakeholders that previous "alpha" was partly stale-bin reading |
| Migration breaks existing trained models | High | Medium | Old provider preserved; legacy models can re-run for comparison; full retrain after migration is expected |
| Borrow-shell tickers (continuous price series) get mis-classified as delisted | Low | Medium | Reference cases include `borrow_shell_cases:` block (§9.2); Stage 5 acceptance checks borrow-shell continuity |
| New `KNOWN_DELISTED` baseline itself has errors | Low | Medium | Reference cases YAML is the test oracle; Stage 2 acceptance asserts every reference row is in the registry |

---

## 11. Tasks Breakdown

### Phase 0: Governance kickoff — ~0.5 day (blocking)

| Task | Deliverable |
|------|-------------|
| **0.1** OpenSpec proposal | `openspec/changes/add-ashare-survivorship-correction/` (this PR) |
| **0.2** User-curated reference cases SEED | `tests/pit/reference_cases.yaml` covering the matrix in §9.2 (≈8 cases minimum, user provides; agent does NOT generate) |
| **0.3** Tushare access validation | Token confirmed working (5000-point tier, 2026-05-22) |

**Acceptance**: OpenSpec proposal merged. Reference cases YAML committed
by the user. Tushare token verified.

### Phase A: Foundation — ~7-10 days

| Task | LOC | Deliverable |
|------|-----|-------------|
| **A.1** Tushare ingestion script | ~400 | `scripts/data_pipeline/01_fetch_tushare.py` (with `--resume`) |
| **A.2** Delisted registry builder | ~250 | `scripts/data_pipeline/02_build_delisted_registry.py` + `tests/pit/test_delisted_registry.py` (much simpler than the previous "entity resolution" plan) |
| **A.3** Extend reference cases YAML (cited rows) | as needed | Additional rows with per-row Tushare API citation in PR body |
| **A.4** Index membership resolver | ~300 | `scripts/data_pipeline/03_resolve_index_membership.py` |

**Acceptance**: Registry contains all 325 Tushare-listed delistings;
CSI300 history covers 2005-present.

**Timeline realism note**: A.1 Tushare backfill is the long pole.
~12-24h wall-clock at 5000-point tier. Script dev: 1-2 days; data
pull: overnight; verification: 0.5 day.

### Phase B: Data Pipeline — ~5 days

| Task | LOC | Deliverable |
|------|-----|-------------|
| **B.1** Universe file builder | ~200 | `scripts/data_pipeline/04_build_universe_files.py` |
| **B.2** qlib bin builder with NaN-after-delist | ~500 | `scripts/data_pipeline/05_build_qlib_bins.py` |
| **B.3** PIT validation suite | ~400 | `scripts/data_pipeline/06_validate_pit_data.py` |
| **B.4** Re-run corrected `verify_survivorship.py` | — | Verdict GOOD |

**Acceptance**: New provider at `D:/qlib_data/my_cn_data_pit/` exists.
All Stage 6 tests pass.

### Phase C: Query Layer — ~5 days

| Task | LOC | Deliverable |
|------|-----|-------------|
| **C.1** PITDataProvider class | ~350 | `src/pit/query.py` |
| **C.2** Caching layer | ~200 | `src/pit/cache.py` |
| **C.3** Parametrized invariant tests | ~300 | `tests/pit/test_invariants.py` (real qlib operators) |
| **C.4** Reference-case spot-check tests | ~200 | `tests/pit/test_query_layer.py` |

**Acceptance**: Any query through PITDataProvider is PIT-correct.
Parametrized tests pass for 50+ dates. Reference cases match.

### Phase D: Integration — ~5 days

| Task | LOC | Deliverable |
|------|-----|-------------|
| **D.1** Wire factor_mining to PIT layer | ~150 | `src/factor_mining/evaluator.py` |
| **D.2** Wire training pipeline | ~100 | `src/training/` updates |
| **D.3** Wire backtester | ~200 | `src/backtest/` updates |
| **D.4** Migration guide | — | [`docs/pit/migration_guide.md`](migration_guide.md) — operator-facing runbook covering build sequence, opt-in workflows, calibration, rollback, and caveat reference |
| **D.5** Calibration run + benchmark | — | Side-by-side comparison report |

**Acceptance**: Full pipeline run on PIT data completes. Factor mining
produces correct (likely lower) metrics. Backtest correctly avoids
trading delisted tickers.

---

## 12. Acceptance Criteria (Overall)

### Functional
- [ ] `verify_survivorship.py` returns GOOD verdict on new provider
- [ ] Delisted registry contains all Tushare-listed delistings + all
      reference rows
- [ ] No active stock false-flagged as delisted
- [ ] `D.list_instruments(D.instruments('csi300'))` returns date-correct sets
- [ ] qlib `ts_mean(close, 20)` returns NaN strictly after delist_date
- [ ] Borrow-shell tickers have continuous price series (no NaN gap)
- [ ] Backtester refuses to trade in delisted tickers
- [ ] All unit tests pass

### Data Quality
- [ ] ≥300 delisted stocks in registry (Tushare reports 325)
- [ ] No registry row has `delist_date < list_date`
- [ ] No registry row has the ticker also appearing as `list_status='L'`

### Performance
- [ ] PIT query for full panel (5000 stocks × 5 years) < 30s
- [ ] Cached repeat queries < 5s
- [ ] Pipeline run time within 2x of legacy pipeline

### Migration Safety
- [ ] Old provider preserved at `D:/qlib_data/my_cn_data`
- [ ] All destructive scripts have `--dry-run` and `--confirm-destructive`
- [ ] Migration documented with rollback procedure

### Observability
- [ ] Stage 6 validation report generated as HTML
- [ ] Side-by-side metrics comparison (old vs new) saved
- [ ] Borrow-shell tickers logged at registry-build time for review

---

## 13. Open Questions for User

1. **Tushare account level**: 5000-point tier confirmed working
   2026-05-22. Some endpoints (historical index weights, deep history)
   may still hit rate limits during the full backfill.

2. **Manual delist_date override**: For older delistings where Tushare
   may be inconsistent, do we want a `data/manual_delistings.yaml`?
   Recommendation: **yes**, append-only, each entry cited with the
   exchange announcement URL.

3. **Storage budget**: New provider adds ~20-30GB (bins for 325
   delisted stocks across 25 years). Confirm disk space available.

4. **Time priority**: ~3-4 weeks for one developer. Can it be
   parallelized with factor mining Phases 1-3? Yes — they don't depend
   on each other until factor mining Phase 5.

5. **Old provider preservation**: Recommendation: **untouched** —
   frozen comparison baseline, only consume the new
   `D:/qlib_data/my_cn_data_pit/`. Any retroactive modification of the
   legacy provider is out of scope.

---

## 14. What to Tell the Coding Agent When Handing Off

Originally written for DeepSeek; applies to OpenCode, Claude, Codex, or
any code-generating agent.

> **Cross-reference (mandatory)**: All rules in
> `AGENTS.md > Implementation discipline` apply to every PR in this
> plan in addition to the phase-specific guidance below. In particular:
> `pytest tests/...` must pass before any "done" claim; mechanical-move
> PRs (Phase D wiring) must include the whole-file content diff per
> AGENTS.md §10; no silent fallback; **never invent fields, sector
> names, or company names** — grep the producer and cite the API
> response. §15 below operationalises these into PR-level checklists.

When pasting this document to the agent for implementation:

1. **Start with Phase A only**. Do not scope-creep into Phase B before
   A is done. Each phase is a separate PR.

2. **Phase A.1 (Tushare ingestion) requires `TUSHARE_TOKEN`**. Provide
   via env variable, never hardcoded. The token MUST NOT appear in any
   committed file, commit message, or PR description.

3. **Phase A.2 (delisted registry) is structurally simple** (one row
   per delisted ticker, sourced from `stock_basic(list_status='D')`)
   but has data-quality edge cases:
   - `delist_reason` is not always structured in Tushare; heuristic
     classification + manual override for older entries
   - Some older delistings may have `delist_date` empty in Tushare
     but the daily data clearly ends — log and flag for manual review

4. **Phase B.2 (qlib bin builder) involves writing binary qlib format**.
   The agent should:
   - Read qlib's `dump_bin.py` source for the exact format
   - Use qlib's own utilities where possible
   - Write to a NEW directory, never overwrite
   - NaN-pad the trailing rows for delisted tickers

5. **Phase C is where most subtle bugs hide**. Every test in
   `tests/pit/test_invariants.py` is sacred. If a test is changed to
   make it pass, that is a code smell — investigate the underlying
   cause first.

6. **Migration in Phase D is high-risk**. Require explicit user
   confirmation before:
   - Switching pipeline default provider
   - Deleting old data (never automatically — always require flag)
   - Promoting any factor or model trained on stale-bin data

7. **Resist the urge to skip the reference cases YAML — AND don't
   auto-generate it.** Agents have already hallucinated bogus reference
   data in this project. The original PIT design (commit `e780f65`)
   contained a fabricated `SH600753 = 庞大集团 → ST友谊` example that
   propagated into 8 OpenSpec requirements, the `KNOWN_DELISTED` list,
   and §2 of the design doc — all of which had to be rolled back in
   PR `add-ashare-survivorship-correction`. See also the earlier
   `research/sector_alpha_consistency.md` post-mortem where the agent
   invented Shenwan sector names (`通用设备`, `汽车服务`) that don't
   exist.

   **Hard rule for `tests/pit/reference_cases.yaml`:**
   - The Phase 0.2 seed (covering the eras matrix in §9.2) MUST be
     **user-provided**, NOT agent-generated.
   - The agent may add new cases in Phase A.3+, but **each new row's
     PR body MUST cite the Tushare API response** (`stock_basic`,
     `namechange`, or `index_weight` row) that justifies the entry.
   - PRs with uncited reference rows are rejected by the reviewer.

   Manual curation of ~8 cases takes 1-2 hours and catches algorithm
   bugs forever.

---

## 15. OpenCode Operational Workflow

This section makes the agent collaboration model explicit. §1-14 cover
**what to build**; §15 covers **how to build it**. Both are required
for the project to land cleanly.

### 15.1 Branch & PR mapping

```
One task in §11 → one branch → one PR.

Branch naming:    pit/phase-{0,A,B,C,D}-{N}-{slug}
  e.g.            pit/phase-a-2-delisted-registry
                  pit/phase-d-3-backtester-wiring
PR title prefix:  [PIT-{phase}.{N}] <imperative summary>
  e.g.            [PIT-A.2] Build delisted registry from Tushare stock_basic
                  [PIT-C.1] Implement PITDataProvider class
```

Each phase is a sequence of small PRs. **Phase N+1 cannot start until
every Phase N PR is merged AND the phase acceptance checklist (§11 /
§12) is fully ticked.** Enforced by user gate, not automation — the
agent must wait for the user's explicit Phase-done signal (§15.5).

### 15.2 Per-PR body — required sections

Every PR opened under this plan MUST include these sections in the
body. Missing sections = reviewer rejects.

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
- [ ] (Phase A.3+ only) Per-row Tushare API response citations for any new `reference_cases.yaml` entries

## Mechanical-move diff (Phase D wiring only)
<paste the AGENTS.md §10 whole-file content diff:
  git show <pre-sha>:<old-path> > /tmp/pre.py
  diff <(grep -vE '^(\s*$|\s*#|\s*"""|^import |^from )' /tmp/pre.py | sort) \
       <(cat <new-path-1> <new-path-2> ... | \
          grep -vE '^(\s*$|\s*#|\s*"""|^import |^from )' | sort)
>
Output should be empty or trivially explainable.

## Reference case citations (Phase A.3+ only)
For each new row added to tests/pit/reference_cases.yaml, paste the
Tushare API response (`stock_basic` row + relevant `namechange` rows)
that justifies it. PRs without citation per row are rejected.
```

### 15.3 Review loop

```
Author         → OpenCode (build agent in opencode.json), or Claude
                 acting in OpenCode's role when explicitly authorised
Reviewer 1     → Human (user) — REQUIRED, final approver
Reviewer 2     → spec-reviewer subagent (opencode.json) — RECOMMENDED
                 for design-heavy tasks: Phase 0.1 OpenSpec proposal,
                 Phase A.2 registry builder, Phase B.2 bin builder.
Reviewer 3     → code-reviewer subagent (opencode.json) — RECOMMENDED
                 for high-risk wiring: all Phase D tasks.
```

Subagent reviews are **signals to inform the human reviewer**, not
gates. The user has final approval.

### 15.4 OpenCode self-discipline (in addition to AGENTS.md)

**Inherited from `AGENTS.md > Implementation discipline`** (mandatory):

| AGENTS.md rule | PIT-relevant manifestation |
|----------------|----------------------------|
| #1 Run pytest before claiming done | Push with red tests = PR auto-reject |
| #4 Never invent fields | Never invent company names, sector names, ticker codes, or Tushare API response shapes. Grep `reference_cases.yaml` first. Cite Tushare per row. |
| #8 No silent fallback | Registry builder must raise on missing `delist_date`, never silently treat as active |
| #10 Mechanical-move PRs require pre/post diff | Phase D wiring qualifies |

**PIT-specific additions**:

5. **Reference cases YAML is user-curated seed**. Phase 0.2 receives
   the seed from the user per §9.2 coverage matrix; agent may add more
   in Phase A.3+ only with cited Tushare API response per case.
6. **Adjusted-price PIT assumption is explicit** (§4.3.1). Do not
   "fix" `adj_factor` with reconstruction logic without a new ticket.
7. **NaN-after-delist depends on qlib operator `min_periods`**
   (§4.3.2). Validate against real qlib operators, not pandas rolling.
8. **No scope creep into Phase E+ dimensions** (§4.5). If a Phase A-D
   task appears to touch industry classification PIT, fundamentals
   PIT, share-count PIT, or in-listing ST status PIT — stop and
   confirm with user.
9. **No re-introduction of the entity model**. The entity_id /
   reuse_count / NaN-gap-cross-entity / resolve_entity pattern was
   removed in PR `add-ashare-survivorship-correction` for cause. Any
   PR proposing to re-add these concepts to A-share work is rejected
   on sight; if the agent encounters a problem that seems to need
   them, escalate to the user.

### 15.5 Phase gating signal

The user acknowledges each phase completion by ONE of:

- Writing "**Phase X acceptance ✓**" in the merge commit of that
  phase's final PR, OR
- Adding a line to `tasks.md` ticking the phase, OR
- A direct message in the session: "Phase X done, proceed to Phase Y"

**The agent MUST NOT start the next phase before observing one of
these signals.**

### 15.6 Failure mode handling

| Symptom | Reviewer / agent response |
|---------|---------------------------|
| pytest red on first push | Agent fixes in same PR before re-requesting review. Do NOT open a follow-up PR; rebase the failing commits. |
| Mechanical-move diff (§15.2) shows unexplained drift | Reviewer rejects with `git diff --stat`; agent reverts to true mechanical move OR justifies each line. |
| `reference_cases.yaml` expansion without Tushare citation per row | Reviewer rejects; agent removes uncited rows. |
| Registry builder hits ambiguous `delist_date` | Agent raises in algorithm + adds to `data/manual_overrides.yaml`. NEVER silently default. |
| Migration step (Phase D) needs to delete old data | NEVER auto-execute. PR documents the deletion; user runs `python scripts/data_pipeline/99_finalize_migration.py --confirm-destructive` manually after offline backup. |
| Tushare rate-limit mid-Stage 1 backfill | Agent resumes from `.checkpoint`, not from scratch. |

### 15.7 Token / credential handling

- `TUSHARE_TOKEN` via environment variable ONLY. Read via
  `os.environ["TUSHARE_TOKEN"]` or `TushareClient.from_environment()`.
- `.env.example` documents required variables (no values committed).
  `.env` is `.gitignore`-d.
- Never embed token in YAML, Python source, commit messages, or PR
  descriptions.
- CI (if added later) uses GitHub Secrets.
- The OpenCode config `opencode.json` may reference model providers
  but MUST NOT contain Tushare or any other data-source credentials.

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
[ ] No entity_id / reuse_count / resolve_entity field re-introduced
```

If any checkbox fails: reviewer comments with the failing item; agent
fixes in the SAME PR (do not open a new PR for review-driven fixes).

---

## End

When all 4 phases complete:

- Local bin correctly distinguishes delisted from active tickers.
- Factor mining produces metrics not silently inflated by stale-bin
  post-delist values.
- Survivorship bias is bounded and measured (≥325 delisted stocks
  in registry).
- Borrow-shell restructures handled correctly (continuous price
  series).
- Future data sources (futures, options, fundamentals) can be added
  with the same NaN-after-delist discipline.

This is the most important infrastructure investment in the project.
Don't rush it.
