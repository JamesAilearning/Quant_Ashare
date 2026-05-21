# Phase A.1 — Tushare Data Ingestion

Pulls raw Tushare data needed for Point-in-Time (PIT) universe construction.

## Quick start

```bash
# Preview what will be fetched (no API calls):
python scripts/data_pipeline/01_fetch_tushare.py --dry-run

# Full fetch (requires TUSHARE_TOKEN env var):
python scripts/data_pipeline/01_fetch_tushare.py

# Resume from interruption (skips already-downloaded files):
python scripts/data_pipeline/01_fetch_tushare.py --resume

# Custom date range:
python scripts/data_pipeline/01_fetch_tushare.py --start 20100101 --end 20241231
```

## Environment

Requires `TUSHARE_TOKEN` environment variable set to a valid Tushare Pro API token.

```bash
export TUSHARE_TOKEN="your_token_here"
# or on Windows:
set TUSHARE_TOKEN=your_token_here
```

## Output structure

```
data/raw/tushare/
├── all_stocks.parquet       # All stocks (SSE + SZSE, list_status L/P/D)
├── delisted_stocks.parquet   # Stocks with delist_date set
├── _rejected.log             # Rejected fetches with reasons
├── daily/
│   └── {year}/
│       └── {ticker}.parquet  # OHLCV per ticker per year
├── adj_factor/
│   └── {year}/
│       └── {ticker}.parquet  # Adjustment factors
├── namechange/
│   └── {ticker}.parquet      # Historical name changes
├── suspend.parquet           # All suspension records (merged)
└── index_weight/
    ├── 000300SH.parquet       # CSI300 constituent weights
    ├── 000905SH.parquet       # CSI500 constituent weights
    └── 000906SH.parquet       # CSI800 constituent weights
```

## Expected runtime

| Item | Count | Per-item | Total |
|------|-------|----------|-------|
| stock_basic | 2 calls | <1s | <2s |
| daily (per ticker/year) | ~5,000 × 25 | ~0.5s | ~17 hours |
| adj_factor (per ticker/year) | ~5,000 × 25 | ~0.3s | ~10 hours |
| namechange (per ticker) | ~5,000 | ~0.5s | ~40 min |
| suspend_d (per ticker) | ~5,000 | ~0.3s | ~25 min |
| index_weight (per index/month) | 3 × 180 | ~0.3s | ~3 min |

**Total: ~28 hours** with default 5000 stocks × 25 years.

Use `--resume` to continue after interruptions. Daily and adj_factor are the dominant costs.

## Tests

```bash
pytest tests/data_pipeline/ -v
```

All tests use mock fixtures — no real Tushare API calls.
