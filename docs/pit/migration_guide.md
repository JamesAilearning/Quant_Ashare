# PIT Migration Guide

Operator-facing runbook for migrating the qlib trading pipeline from
the legacy (survivorship-biased) data layer to the corrected
Point-in-Time provider produced by Phases A-B.

For the architectural rationale, schema definitions, and design
decisions, read [`pit_universe_design.md`](pit_universe_design.md)
first. This document is the **step-by-step "how do I actually do
this" guide**.

---

## TL;DR

1. **Build** the PIT-corrected qlib provider at a new location
   (`D:/qlib_data/my_cn_data_pit/`). Do NOT touch the legacy provider.
2. **Validate** with `06_validate_pit_data.py`; verdict must be exit
   code 0 or 1 (warnings).
3. **Opt-in** PIT mode in factor mining / training / backtest by
   passing a `pit_provider` kwarg. Default behaviour is unchanged.
4. **Compare** old vs new metrics on a held-out window before
   promoting any model trained on PIT data.

The legacy provider is **never deleted**; both stay queryable
indefinitely.

---

## 1. Build the PIT-corrected provider

### 1.1 Prerequisites

| Item | Requirement |
|---|---|
| `TUSHARE_TOKEN` | Set as env variable (5000-point Pro tier confirmed working) |
| Disk space | ~20-30 GB free at the new provider path |
| Reference YAML | `tests/pit/reference_cases.yaml` already committed (Phase 0.2) |

### 1.2 Pipeline order

The phases are linear — each step writes the inputs that the next
consumes. Total wall-clock: **~12-24 h** dominated by Phase A.1's
Tushare backfill.

```
Phase A.1   Tushare ingestion          ~12-24 h (rate-limited)
   |
Phase A.2   Delisted registry builder  ~10 s
   |
Phase A.4   Index membership resolver  ~5 min (per-index chunked)
   |
Phase B.1   Universe files (instruments/all.txt) — auto-emitted by B.2
   |
Phase B.2   qlib bin builder           ~10 min per 5000 tickers
   |
Phase B.3   Validation suite           ~30 s
```

### 1.3 Step-by-step commands

```bash
# Output locations — keep these out of the legacy paths
export TUSHARE_RAW=D:/qlib_data/tushare_raw
export PIT_PROVIDER=D:/qlib_data/my_cn_data_pit

# A.1 — pull Tushare (12-24h, supports resume on rerun)
python scripts/data_pipeline/01_fetch_tushare.py \
    --output-dir $TUSHARE_RAW \
    --start-date 20000101 --end-date 20251231

# A.2 — build delisted registry (parses reference YAML for validation)
python scripts/data_pipeline/02_build_delisted_registry.py \
    --tushare-dir $TUSHARE_RAW \
    --reference-cases tests/pit/reference_cases.yaml \
    --output $TUSHARE_RAW/delisted_registry.parquet

# A.2 (optional) — apply manual delist overrides
# Only when you've recorded exchange-cited corrections in
# data/manual_delistings.yaml; see PR #107 for the schema.
python scripts/data_pipeline/02_build_delisted_registry.py \
    --tushare-dir $TUSHARE_RAW \
    --reference-cases tests/pit/reference_cases.yaml \
    --manual-overrides data/manual_delistings.yaml \
    --output $TUSHARE_RAW/delisted_registry.parquet

# A.4 — resolve historical index membership (CSI300/500/800)
python scripts/data_pipeline/03_resolve_index_membership.py \
    --tushare-dir $TUSHARE_RAW \
    --output-dir $PIT_PROVIDER
# NOTE: --reference-cases for A.4 currently surfaces stale
# index_membership_cases per PR #102 finding; omit until that YAML
# is corrected in a future user-curated PR.

# B.2 — qlib bin builder (also auto-writes instruments/all.txt;
#       Phase B.1 standalone CLI is optional now)
python scripts/data_pipeline/05_build_qlib_bins.py \
    --tushare-dir $TUSHARE_RAW \
    --delisted-registry $TUSHARE_RAW/delisted_registry.parquet \
    --output-dir $PIT_PROVIDER

# B.3 — validation (exit 0 = clean, 1 = warnings, 2 = failure)
python scripts/data_pipeline/06_validate_pit_data.py \
    --provider-dir $PIT_PROVIDER \
    --delisted-registry $TUSHARE_RAW/delisted_registry.parquet \
    --reference-cases tests/pit/reference_cases.yaml \
    --report-json /tmp/pit_validation.json
```

### 1.4 Validation acceptance

The Phase B.3 report must show all 6 checks at PASS or WARN:

- **A. Survivorship spot-check** — sample 5 registry tickers; valid
  close on/before delist_date, NaN after. PASS required.
- **B. Delist boundary sweep** — full-registry sweep; both
  truncation (data ends >7d before delist) and extension (NaN-after-
  delist) violations checked. PASS required.
- **C. Time-travel sanity** — universe at 5 sample dates excludes
  pre-listed and post-delisted tickers. PASS required.
- **D. qlib operator `min_periods`** — the §4.3.2 leak check. **May
  FAIL on a raw provider** (qlib's default `min_periods<N` leaks
  past delist); the fix is in the query layer, not the provider.
  Phase C's `PITDataProvider._mask_post_delist` closes the leak.
  Document the FAIL if it appears at this stage.
- **E. Index membership references** — currently expected to WARN
  while `reference_cases.yaml::index_membership_cases` awaits the
  PR #102 follow-up correction.
- **F. Borrow-shell continuity** — currently WARN (no cases in
  reference YAML); add to YAML if you start using borrow-shell
  attribution.

If A / B / C / F FAIL, **stop and investigate** — the provider is
not safe to use. If only D / E fail (known caveats), the provider
is usable through the PIT query layer.

---

## 2. Opt-in PIT mode in the pipeline

The PIT layer is **opt-in by default**. Existing callers stay on
the legacy provider; PIT-aware callers explicitly pass a
`pit_provider`.

### 2.1 Initialize qlib against the PIT provider

```python
from src.core.canonical_backtest_contract import ADJUST_MODE_POST
from src.core.qlib_runtime import QlibRuntimeConfig, init_qlib_canonical
from src.pit.query import PITDataProvider

PIT_PROVIDER = "D:/qlib_data/my_cn_data_pit"
DELISTED_REGISTRY = "D:/qlib_data/tushare_raw/delisted_registry.parquet"

# Step 1 — point qlib at the PIT-corrected provider directory
init_qlib_canonical(QlibRuntimeConfig(
    provider_uri=PIT_PROVIDER,
    region="cn",
    data_adjust_mode=ADJUST_MODE_POST,
))

# Step 2 — construct the PIT provider (reads the registry,
# inherits qlib's already-set singleton state)
pit = PITDataProvider(
    provider_uri=PIT_PROVIDER,
    delisted_registry_path=DELISTED_REGISTRY,
)
```

### 2.2 Factor mining (Phase D.1)

```python
from src.core.factor_analyzer import FactorAnalysisConfig, FactorAnalyzer

result = FactorAnalyzer.analyze(
    FactorAnalysisConfig(test_start="2022-01-01", test_end="2024-12-31"),
    pit_provider=pit,  # NEW: opt-in
)
```

When `pit_provider` is supplied, `_fetch_close_unstacked` routes
through `pit.get_features(instruments=...)` — applying the §4.3.2
post-delist mask and the bounded LRU cache. Default
`pit_provider=None` falls through to direct `qlib.D.features`
preserving legacy behaviour.

### 2.3 Training / feature dataset (Phase D.2)

```python
from src.data.feature_dataset_builder import (
    FeatureDatasetBuilder, FeatureDatasetConfig,
)

result = FeatureDatasetBuilder.build(
    FeatureDatasetConfig(
        instruments="csi300",
        feature_handler="Alpha158",
        train_start="2022-01-01", train_end="2024-06-30",
        valid_start="2024-07-01", valid_end="2024-12-31",
        test_start="2025-01-01",  test_end="2025-12-31",
    ),
    pit_provider=pit,  # NEW: alignment guard, not a swap
)
```

This is a **correctness guard, not a data-path swap**: qlib's
`DatasetH` reads features through its opaque handler chain
(Alpha158, etc.) and can't be intercepted without forking qlib. The
guard ensures the canonical qlib config's `provider_uri` matches
`pit._provider_uri` — so when you think you're training on PIT
bins, you actually are.

### 2.4 Backtest (Phase D.3)

```python
from src.core.backtest_runner import BacktestRunner

output = BacktestRunner.run(
    request=request,
    predictions=predictions,
    topk=50, n_drop=5,
    pit_provider=pit,  # NEW: alignment guard + equal-weight baseline routing
)
```

Two layers of protection:

1. **Alignment guard** — raises if `pit_provider` ≠ qlib canonical
   `provider_uri`. Catches "I thought I was running PIT mode" footgun.
2. **Equal-weight baseline PIT routing** — the
   `_compute_equalweight_baseline` close-panel fetch goes through
   `pit.get_features`, so the per-day mean correctly excludes
   delisted tickers instead of consuming forward-filled stale
   values.

The main strategy (`TopkDropoutStrategy` + `BacktestExecutor`) reads
through qlib internally — PIT correctness for the main backtest
relies on `init_qlib_canonical` pointing at the PIT provider, which
the alignment guard enforces.

---

## 3. Calibration: old vs new comparison

**Before promoting any factor or model trained on PIT data**, run
side-by-side metrics against the legacy provider on a held-out
window. Expect lower IR/Sharpe on the PIT-corrected run — that is
the *correct* signal that the old metrics were partly
survivorship-biased.

Recommended comparison workflow:

```bash
# 1. Run factor mining against legacy provider
init_qlib_canonical(provider_uri=LEGACY_DIR, ...)
legacy_result = FactorAnalyzer.analyze(cfg)  # no pit_provider

# 2. Run factor mining against PIT provider
# (in a fresh Python process — qlib singleton can't be re-init'd)
init_qlib_canonical(provider_uri=PIT_DIR, ...)
pit_result = FactorAnalyzer.analyze(cfg, pit_provider=pit)

# 3. Diff: mean IC, IR, top-N factor overlap, decay shape
```

Expected differences:

- **Mean IC / IR**: lower on PIT (5-15% relative drop is normal;
  larger drops mean the legacy alpha was particularly survivorship-
  exposed).
- **Top-N factor overlap**: high (>80%) — survivorship doesn't
  usually change *which* factors work, just by how much.
- **Decay shape**: similar — decay is a within-stock property.

Big surprises (e.g. top-N overlap < 50%, IC sign flips) warrant
manual investigation before any production promotion.

---

## 4. Safety net & rollback

### 4.1 What's preserved

- The legacy provider at `D:/qlib_data/my_cn_data/` is **never
  modified** by any PIT phase. Phase B.2's bin builder writes to a
  new directory via atomic rename; the legacy directory stays
  byte-identical throughout.
- Phase A.1 (`01_fetch_tushare.py`) supports `--dry-run` for
  inspection without filesystem writes. The other Phase A / B
  scripts (A.2 / A.4 / B.2 / B.3) don't currently expose a
  `--dry-run` flag — they're cheap to run end-to-end against a
  test output directory if you want a dry rehearsal.
- Per-file existence checkpointing in Phase A.1 (`daily/` and
  `adj_factor/` per-(year,ticker) parquets, plus the single-file
  endpoints) means resuming an interrupted backfill never re-fetches
  completed files. Resume is automatic on rerun — there is no
  `--resume` flag to set.

### 4.2 Rollback

Reverting to legacy behaviour requires no provider changes:

1. **Codepath**: stop passing `pit_provider` to factor mining /
   training / backtest. Default `pit_provider=None` falls through to
   direct `qlib.data.D` against whatever provider qlib was init'd
   with.
2. **qlib provider**: re-init qlib with
   `provider_uri="D:/qlib_data/my_cn_data"` (the legacy directory).
   The PIT directory remains on disk for later re-use.

Models trained on PIT data may need re-training against the legacy
provider for comparison — they'll have slightly different feature
distributions.

### 4.3 What to do if Phase B.3 validation fails on production data

If the validation suite returns exit code 2 (failure) and Check A or
Check B failed:

1. **Do not** trust the PIT provider for any factor / model / backtest.
2. Inspect the structured report (`--report-json` output) to find
   which tickers violated the invariant.
3. Common causes:
   - Tushare returned a wrong `delist_date` for a ticker. Add a
     `data/manual_delistings.yaml` override (Phase D.1) citing the
     exchange announcement.
   - Local Tushare cache is stale. Delete the affected parquet
     files (e.g. `<tushare_dir>/daily/2025/*.parquet` for current-
     year data) and rerun `01_fetch_tushare.py` — the fetcher's
     per-file existence check will re-pull only the deleted files
     and skip everything else.
   - The reference YAML disagrees with current Tushare data. Run
     the cross-check in `scripts/data_quality/verify_survivorship.py`
     against the legacy provider first to confirm which side is
     stale.

---

## 5. Known caveats

These are documented in `pit_universe_design.md` (sections
referenced below) and surface as WARN / FAIL in validation. None
block opt-in PIT usage; all are tracked for follow-up work.

| Caveat | Section | Mitigation |
|---|---|---|
| `adj_factor` is today's snapshot, not as-of-date | §4.3.1 | Use within-ticker ratios / returns only; never absolute adjusted prices |
| qlib's `Mean / Ref / Corr` use `min_periods<N` by default → leak past delist | §4.3.2 | Phase C's `PITDataProvider._mask_post_delist` closes the leak at the query layer |
| `index_weight` is monthly-snapshot granularity → can't pin intra-month enter/leave | §4.4 caveat | Phase A.4 resolver tolerates ±35d for reference matching |
| Reference YAML's `index_membership_cases` predate user verification | PR #102 finding | Currently surfaces as WARN; awaits a user-verified follow-up PR before being load-bearing |
| Borrow-shell restructure (e.g. 600145 新亿 → 亿阳信通) is NOT modelled in price layer | §4.6 | Attribution layer concern only; price series is continuous through the restructure |
| Tushare's `delist_date` can be the day AFTER the last actual trade | inline | Phase B.3 Check B uses 7-day tolerance to absorb this |

---

## 6. PIT-correctness contract summary

For agents and reviewers — the load-bearing invariants that any
future PIT change must preserve:

1. **NaN-after-delist in bin storage** — for every delisted
   ticker's qlib bin file, every trading day strictly after
   `delist_date` is NaN. Enforced by Phase B.2; verified by Phase
   B.3 Check A + B.
2. **Post-delist mask at the query layer** — `PITDataProvider`
   masks every `(ticker, date)` where `date > delist_date` to NaN
   before returning, regardless of what qlib's operators computed.
   This closes the qlib `min_periods<N` leak (§4.3.2).
3. **Alignment guard at PIT entry points** — when a `pit_provider`
   is supplied to `FactorAnalyzer` / `FeatureDatasetBuilder` /
   `BacktestRunner`, the canonical qlib config's `provider_uri`
   MUST match. The duplicated guards across three entry points is
   intentional defence-in-depth.
4. **No silent fallback** — when a PIT contract is violated, raise.
   The codebase rejects implicit fallback (AGENTS.md §8); PIT code
   follows the same discipline.
5. **No entity model** — A-share has no ticker reuse (per PR #95).
   Any future PR proposing `entity_id` / `reuse_count` /
   `resolve_entity` for A-share work is rejected on sight.
6. **Reference YAML governance** — Phase 0.2 seed is user-curated.
   Agent additions in later phases require Tushare API citation per
   row (§14.7 hard rule).

---

## 7. Project journal entries to expect

When you complete a PIT migration, record in the journal:

- **Date of the build**: when you ran Phase A.1 (Tushare backfill).
- **Validation report path**: where the Phase B.3
  `--report-json` lives.
- **Side-by-side comparison**: legacy vs PIT mean IC, IR, top-N
  overlap from the calibration step.
- **Any manual overrides applied**: list `data/manual_delistings.yaml`
  rows with their cite URLs.
- **Promotion decision**: which factors / models you signed off for
  production on PIT data.

This is the audit trail the post-mortem will need if a production
result ever surprises you.
