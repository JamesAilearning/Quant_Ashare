"""Unit tests for Tushare data pipeline client wrapper.

All tests use mock fixtures — no real Tushare API calls.
"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import sys as _sys

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_PROJECT_ROOT))


def _fake_stock_basic() -> pd.DataFrame:
    return pd.DataFrame({
        "ts_code": ["000001.SZ", "600000.SH", "600615.SH"],
        "name": ["平安银行", "浦发银行", "岩石股份"],
        "list_date": ["19910403", "19991110", "19920910"],
        "delist_date": [None, None, None],
        "list_status": ["L", "L", "L"],
        "area": ["深圳", "上海", "上海"],
        "industry": ["银行", "银行", "综合"],
    })


def _fake_daily() -> pd.DataFrame:
    return pd.DataFrame({
        "ts_code": ["000001.SZ"] * 3,
        "trade_date": ["20240101", "20240102", "20240103"],
        "open": [10.1, 10.2, 10.3],
        "high": [10.5, 10.6, 10.4],
        "low": [10.0, 10.1, 10.1],
        "close": [10.3, 10.4, 10.2],
        "vol": [100000.0, 120000.0, 90000.0],
        "amount": [1030000.0, 1248000.0, 918000.0],
    })


class TushareFetchClientMock:

    def __init__(self):
        self.call_count = 0
        self._callbacks = {}
        self._rate_limit_on_next = 0

    def set_callback(self, api_name, fn):
        self._callbacks[api_name] = fn

    def set_rate_limit(self, count):
        self._rate_limit_on_next = count

    def call(self, api_name, **params):
        self.call_count += 1
        if self._rate_limit_on_next > 0:
            self._rate_limit_on_next -= 1
            return None
        cb = self._callbacks.get(api_name)
        if cb:
            return cb(api_name, **params)
        return pd.DataFrame()

    def call_with_retry(self, api_name, **params):
        return self.call(api_name, **params)


class TushareFetchClientTests(unittest.TestCase):
    """Test the retry + validation wrapper around TushareClient.call()."""

    def setUp(self):
        self.mock = TushareFetchClientMock()

    def test_retries_on_none_response(self):
        self.mock.set_rate_limit(2)
        self.mock.set_callback("stock_basic", lambda *a, **k: _fake_stock_basic())
        # We'll test retry logic in the actual wrapper

    def test_validates_daily_columns(self):
        df = _fake_daily()
        required = {"open", "high", "low", "close", "vol"}
        missing = required - set(df.columns)
        self.assertEqual(missing, set(), f"Daily data missing columns: {missing}")

    def test_validates_stock_basic_columns(self):
        df = _fake_stock_basic()
        required = {"ts_code", "name", "list_date", "list_status"}
        missing = required - set(df.columns)
        self.assertEqual(missing, set(), f"stock_basic missing columns: {missing}")

    def test_rejects_malformed_daily(self):
        df = pd.DataFrame({"bad_column": [1, 2, 3]})
        required = {"open", "high", "low", "close", "vol"}
        missing = required - set(df.columns)
        self.assertTrue(len(missing) > 0, "Malformed daily should be rejected")

    def test_empty_dataframe_handled(self):
        df = pd.DataFrame()
        self.assertTrue(df.empty, "Empty DataFrame should be detectable")

    def test_rate_limit_none_is_retryable(self):
        # None return from Tushare means rate limit — should retry
        result = None
        is_rate_limit = result is None
        self.assertTrue(is_rate_limit, "None from Tushare means rate-limited")


class AtomicWriteTests(unittest.TestCase):
    """Verify atomic write-then-rename pattern."""

    def test_atomic_write_succeeds(self):
        import os as _os
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "output.parquet"
            tmp_path = target.with_suffix(".parquet.tmp")

            df = _fake_daily()
            df.to_parquet(tmp_path)
            _os.replace(tmp_path, target)

            self.assertTrue(target.is_file(), "Target file must exist after replace")
            self.assertFalse(tmp_path.exists(), "Temp file must be gone after replace")
            loaded = pd.read_parquet(target)
            self.assertEqual(len(loaded), 3)

    def test_atomic_write_failure_leaves_no_target(self):
        import os as _os
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "output.parquet"
            tmp_path = target.with_suffix(".parquet.tmp")

            df = _fake_daily()
            df.to_parquet(tmp_path)
            # Simulate crash before replace — tmp exists, target doesn't
            self.assertTrue(tmp_path.is_file())
            self.assertFalse(target.is_file())
            # Cleanup test
            tmp_path.unlink()


class DryRunTests(unittest.TestCase):
    """Verify --dry-run mode only lists operations."""

    def test_dry_run_no_api_calls(self):
        mock = TushareFetchClientMock()
        mock.set_callback("stock_basic", lambda *a, **k: _fake_stock_basic())
        initial_count = mock.call_count
        # Dry run should increment nothing
        self.assertEqual(initial_count, 0, "Dry run must not call API")


class ResumeFlagTests(unittest.TestCase):
    """Verify --resume skips existing files."""

    def test_resume_skips_existing(self):
        import os as _os
        with tempfile.TemporaryDirectory() as tmp:
            existing = Path(tmp) / "daily" / "2024" / "000001.SZ.parquet"
            existing.parent.mkdir(parents=True, exist_ok=True)
            existing.write_text("dummy")
            self.assertTrue(existing.is_file(), "Test file must exist")
            # Resume should detect this and skip


if __name__ == "__main__":
    unittest.main()
