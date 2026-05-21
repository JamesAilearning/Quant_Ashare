#!/usr/bin/env python
"""Phase A.1: Tushare data ingestion for PIT universe construction.

Pulls 6 datasets from Tushare:
  1. stock_basic — all stocks (L+P+D list_statuses)
  2. namechange  — historical name changes
  3. daily       — OHLCV (open, high, low, close, volume, amount)
  4. adj_factor  — adjustment factors
  5. suspend_d   — suspension records
  6. index_weight — CSI300, CSI500, CSI800 constituent weights

Output: data/raw/tushare/

Usage:
    python scripts/data_pipeline/01_fetch_tushare.py [--dry-run] [--resume]
                                                     [--start YYYYMMDD] [--end YYYYMMDD]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running from repo root
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pandas as pd

from src.core.logger import get_logger, setup_logging
from src.data.tushare.client import TushareClient
from src.data_pipeline import tushare_client as tc

_logger = get_logger(__name__)

# ── constants ────────────────────────────────────────────────────

DEFAULT_START = "20000101"
DEFAULT_END = "20251231"
INDEX_CODES = {
    "000300.SH": "CSI300",
    "000905.SH": "CSI500",
    "000906.SH": "CSI800",
}


def _year_range(start: str, end: str) -> list[str]:
    return list(range(int(start[:4]), int(end[:4]) + 1))


def _tickers_from_stocks(df: pd.DataFrame) -> list[str]:
    return sorted(df["ts_code"].unique())


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Tushare PIT data ingestion — Phase A.1")
    p.add_argument("--dry-run", action="store_true", help="List what would be fetched, no API calls")
    p.add_argument("--resume", action="store_true", help="Skip already-downloaded files")
    p.add_argument("--start", default=DEFAULT_START, help="Start date YYYYMMDD (default: 20000101)")
    p.add_argument("--end", default=DEFAULT_END, help="End date YYYYMMDD (default: 20251231)")
    args = p.parse_args(argv)

    setup_logging()

    if args.dry_run:
        print("=== DRY RUN — no API calls will be made ===")
        print(f"Would fetch stock_basic (SSE + SZSE) → {tc.STOCK_BASIC_FILE}")
        print(f"Would fetch daily OHLCV per ticker per year ({args.start} to {args.end})")
        print(f"  → {tc.DAILY_DIR}/"+"{year}/{ticker}.parquet")
        print(f"Would fetch adj_factor per ticker per year → {tc.ADJ_FACTOR_DIR}")
        print(f"Would fetch namechange per ticker → {tc.NAMECHANGE_DIR}")
        print(f"Would fetch suspend_d per ticker → {tc.SUSPEND_FILE}")
        print(f"Would fetch index_weight (CSI300/500/800) → {tc.INDEX_WEIGHT_DIR}")
        print(f"\nTotal estimated files: ~5000 stocks × 25 years ≈ 125,000 files (daily)")
        print(f"Estimated disk usage: ~5-8 GB")
        return 0

    _logger.info("Phase A.1 — Tushare PIT data ingestion")
    _logger.info("Range: %s → %s", args.start, args.end)
    _logger.info("Output: %s", tc._DATA_ROOT)

    client = TushareClient.from_environment()

    # ── Step 1: stock_basic ──────────────────────────────────────
    _logger.info("Step 1/6: Fetching stock_basic...")
    all_stocks = tc.fetch_all_stocks(client)
    tickers = _tickers_from_stocks(all_stocks)
    _logger.info("  Active stocks: %d tickers", len(tickers))

    # ── Step 2: daily OHLCV ──────────────────────────────────────
    _logger.info("Step 2/6: Fetching daily OHLCV...")
    years = _year_range(args.start, args.end)
    fetched_daily = 0
    skipped_daily = 0
    for year in years:
        year_start = f"{year}0101"
        year_end = f"{year}1231"
        for ticker in tickers:
            path = tc.DAILY_DIR / str(year) / f"{ticker.replace('.', '')}.parquet"
            if args.resume and path.is_file():
                skipped_daily += 1
                continue
            tc.fetch_daily_for_ticker(client, ticker, year_start, year_end)
            fetched_daily += 1
            if fetched_daily % 500 == 0:
                _logger.info("  daily: %d fetched, %d skipped", fetched_daily, skipped_daily)
    _logger.info("  daily done: %d fetched, %d skipped", fetched_daily, skipped_daily)

    # ── Step 3: adj_factor ───────────────────────────────────────
    _logger.info("Step 3/6: Fetching adj_factor...")
    fetched_adj = 0
    for year in years:
        year_start = f"{year}0101"
        year_end = f"{year}1231"
        for ticker in tickers:
            path = tc.ADJ_FACTOR_DIR / str(year) / f"{ticker.replace('.', '')}.parquet"
            if args.resume and path.is_file():
                continue
            tc.fetch_adj_factor(client, ticker, year_start, year_end)
            fetched_adj += 1
            if fetched_adj % 500 == 0:
                _logger.info("  adj_factor: %d fetched", fetched_adj)
    _logger.info("  adj_factor done: %d fetched", fetched_adj)

    # ── Step 4: namechange ───────────────────────────────────────
    _logger.info("Step 4/6: Fetching namechange...")
    for ticker in tickers:
        path = tc.NAMECHANGE_DIR / f"{ticker.replace('.', '')}.parquet"
        if args.resume and path.is_file():
            continue
        tc.fetch_namechange(client, ticker)
    _logger.info("  namechange done")

    # ── Step 5: suspend_d ────────────────────────────────────────
    _logger.info("Step 5/6: Fetching suspend_d...")
    suspend_file = tc.SUSPEND_FILE
    if not (args.resume and suspend_file.is_file()):
        tc.collect_suspend(client, tickers, args.start, args.end)
        _logger.info("  suspend done")

    # ── Step 6: index_weight ─────────────────────────────────────
    _logger.info("Step 6/6: Fetching index_weight...")
    for idx_code, idx_name in INDEX_CODES.items():
        path = tc.INDEX_WEIGHT_DIR / f"{idx_code.replace('.', '')}.parquet"
        if args.resume and path.is_file():
            _logger.info("  %s: already exists, skipped", idx_name)
            continue
        # Fetch one date per month from 2010-01 to present
        frames = []
        for y in range(2010, int(args.end[:4]) + 1):
            for m in range(1, 13):
                dt = f"{y}{m:02d}01"
                if dt < "20100101" or dt > args.end:
                    continue
                df = tc.fetch_index_weight(client, idx_code, dt)
                if df is not None and len(df) > 0:
                    frames.append(df)
        if frames:
            merged = pd.concat(frames, ignore_index=True)
            tc._atomic_write(merged, path)
            _logger.info("  %s: %d rows → %s", idx_name, len(merged), path)

    _logger.info("=" * 60)
    _logger.info("Phase A.1 COMPLETE")
    _logger.info("  Output: %s", tc._DATA_ROOT)
    _logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
