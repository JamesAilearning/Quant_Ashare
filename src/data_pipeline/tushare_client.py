"""Tushare data pipeline client — retry, validate, atomic write.

Wraps the existing :class:`src.data.tushare.client.TushareClient`
with retry-on-rate-limit and post-fetch column validation.

Future phases (entity resolution, universe building) consume Parquet
files written by this module.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import pandas as pd

from src.core.logger import get_logger
from src.data.tushare.client import TushareClient, TushareClientError

_logger = get_logger(__name__)

# ── retry ────────────────────────────────────────────────────────

_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF_S = (1.0, 4.0, 16.0)

# ── output paths ─────────────────────────────────────────────────

_DATA_ROOT = Path("data/raw/tushare")

STOCK_BASIC_FILE = _DATA_ROOT / "all_stocks.parquet"
DELISTED_FILE = _DATA_ROOT / "delisted_stocks.parquet"
NAMECHANGE_DIR = _DATA_ROOT / "namechange"
DAILY_DIR = _DATA_ROOT / "daily"
ADJ_FACTOR_DIR = _DATA_ROOT / "adj_factor"
SUSPEND_FILE = _DATA_ROOT / "suspend.parquet"
INDEX_WEIGHT_DIR = _DATA_ROOT / "index_weight"
_REJECTED_LOG = _DATA_ROOT / "_rejected.log"

# ── required columns for validation ──────────────────────────────

_DAILY_REQUIRED = {"open", "high", "low", "close", "vol", "amount"}
_STOCK_BASIC_REQUIRED = {"ts_code", "name", "list_date", "list_status"}


def _log_rejected(api_name: str, reason: str, details: str = "") -> None:
    _REJECTED_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] {api_name}: {reason}"
    if details:
        entry += f" — {details}"
    with open(_REJECTED_LOG, "a", encoding="utf-8") as f:
        f.write(entry + "\n")
    _logger.warning("REJECTED: %s", entry)


def _atomic_write(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        df.to_parquet(tmp, index=False)
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _call_with_retry(client: TushareClient, api_name: str, **params: Any) -> pd.DataFrame:
    last_exc = None
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            result = client.call(api_name, **params)
        except TushareClientError as exc:
            last_exc = exc
            _log_rejected(api_name, f"TushareClientError (attempt {attempt+1})", str(exc))
            if attempt < _RETRY_ATTEMPTS - 1:
                wait = _RETRY_BACKOFF_S[attempt]
                _logger.info("Retrying %s in %.0fs...", api_name, wait)
                time.sleep(wait)
            continue

        if result is None:
            _logger.info("%s returned None (rate-limited), attempt %d/%d",
                         api_name, attempt + 1, _RETRY_ATTEMPTS)
            if attempt < _RETRY_ATTEMPTS - 1:
                wait = _RETRY_BACKOFF_S[attempt]
                time.sleep(wait)
            continue

        if isinstance(result, pd.DataFrame):
            return result

        _log_rejected(api_name, "unexpected type", str(type(result)))
        if attempt < _RETRY_ATTEMPTS - 1:
            time.sleep(_RETRY_BACKOFF_S[attempt])
            continue
        raise RuntimeError(
            f"{api_name} returned unexpected type {type(result).__name__} "
            f"after {_RETRY_ATTEMPTS} attempts"
        )

    raise RuntimeError(
        f"{api_name} failed after {_RETRY_ATTEMPTS} attempts. "
        f"Last error: {last_exc}"
    )


def _validate_daily(df: pd.DataFrame, ticker: str) -> bool:
    missing = _DAILY_REQUIRED - set(df.columns)
    if missing:
        _log_rejected("daily", f"missing columns {missing}", f"ticker={ticker}")
        return False
    if df.empty:
        _log_rejected("daily", "empty DataFrame", f"ticker={ticker}")
        return False
    return True


def _validate_stock_basic(df: pd.DataFrame) -> bool:
    missing = _STOCK_BASIC_REQUIRED - set(df.columns)
    if missing:
        _log_rejected("stock_basic", f"missing columns {missing}")
        return False
    if df.empty:
        _log_rejected("stock_basic", "empty DataFrame")
        return False
    return True


# ── public fetch helpers ─────────────────────────────────────────

def fetch_all_stocks(client: TushareClient) -> pd.DataFrame:
    """Fetch all stocks (L+P+D list_statuses) and split into active + delisted."""
    active = _call_with_retry(client, "stock_basic",
                               exchange="SSE", list_status="L",
                               fields="ts_code,name,list_date,delist_date,list_status,area,industry")
    # Also fetch SZSE
    szse = _call_with_retry(client, "stock_basic",
                             exchange="SZSE", list_status="L",
                             fields="ts_code,name,list_date,delist_date,list_status,area,industry")
    all_stocks = pd.concat([active, szse], ignore_index=True)
    if not _validate_stock_basic(all_stocks):
        raise RuntimeError("stock_basic validation failed")
    _atomic_write(all_stocks, STOCK_BASIC_FILE)
    _logger.info("all_stocks: %d rows → %s", len(all_stocks), STOCK_BASIC_FILE)
    return all_stocks


def fetch_daily_for_ticker(client: TushareClient, ticker: str,
                            start: str, end: str) -> pd.DataFrame | None:
    """Fetch daily OHLCV for one ticker over a date range."""
    try:
        df = _call_with_retry(client, "daily", ts_code=ticker,
                               start_date=start, end_date=end,
                               fields="ts_code,trade_date,open,high,low,close,vol,amount")
    except RuntimeError:
        _log_rejected("daily", "fetch failed", ticker)
        return None

    if not _validate_daily(df, ticker):
        return None

    year = start[:4] if df.empty else str(df["trade_date"].min())[:4]
    path = DAILY_DIR / year / f"{ticker.replace('.', '')}.parquet"
    _atomic_write(df, path)
    return df


def fetch_namechange(client: TushareClient, ticker: str) -> pd.DataFrame | None:
    """Fetch historical name changes for one ticker."""
    try:
        df = _call_with_retry(client, "namechange", ts_code=ticker)
        path = NAMECHANGE_DIR / f"{ticker.replace('.', '')}.parquet"
        _atomic_write(df, path)
        return df
    except RuntimeError:
        return None


def fetch_adj_factor(client: TushareClient, ticker: str,
                      start: str, end: str) -> pd.DataFrame | None:
    """Fetch adjustment factor for one ticker."""
    try:
        df = _call_with_retry(client, "adj_factor", ts_code=ticker,
                               start_date=start, end_date=end,
                               fields="ts_code,trade_date,adj_factor")
        year = start[:4]
        path = ADJ_FACTOR_DIR / year / f"{ticker.replace('.', '')}.parquet"
        _atomic_write(df, path)
        return df
    except RuntimeError:
        return None


def fetch_suspend_d(client: TushareClient, ticker: str,
                     start: str, end: str) -> pd.DataFrame | None:
    """Fetch daily suspension records for one ticker."""
    try:
        df = _call_with_retry(client, "suspend_d", ts_code=ticker,
                               start_date=start, end_date=end,
                               fields="ts_code,trade_date,suspend_type,suspend_reason")
        return df
    except RuntimeError:
        return None


def fetch_index_weight(client: TushareClient, index_code: str,
                        trade_date: str) -> pd.DataFrame | None:
    """Fetch index constituent weights for one date."""
    try:
        df = _call_with_retry(client, "index_weight",
                               index_code=index_code, trade_date=trade_date,
                               fields="index_code,con_code,trade_date,weight")
        path = INDEX_WEIGHT_DIR / f"{index_code.replace('.', '')}.parquet"
        _atomic_write(df, path)
        return df
    except RuntimeError:
        return None


# ── bulk helpers for the orchestrator ────────────────────────────

def collect_suspend(client: TushareClient, tickers: list[str],
                     start: str, end: str) -> pd.DataFrame:
    """Fetch suspend_d for all tickers and merge into one file."""
    frames = []
    for t in tickers:
        df = fetch_suspend_d(client, t, start, end)
        if df is not None and len(df) > 0:
            frames.append(df)
    if frames:
        merged = pd.concat(frames, ignore_index=True)
        _atomic_write(merged, SUSPEND_FILE)
        _logger.info("suspend: %d rows → %s", len(merged), SUSPEND_FILE)
        return merged
    return pd.DataFrame()
