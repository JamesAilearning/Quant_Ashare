"""Tests for ``src.data.tushare.fetcher.TushareFetcher``.

All tests mock :class:`TushareClient` so nothing touches the network.
Verified behaviour:

- Config validation (endpoint names, date format, ordering, sleep >= 0).
- Per-endpoint write paths and atomic-rename semantics.
- Per-file resume (existing parquet is skipped).
- Dry-run writes nothing but still logs.
- Rate-limit retry with backoff; non-rate-limit errors re-raise immediately.
- ``daily`` / ``adj_factor`` refuse to run without stock_basic on disk.
- Token never leaks into errors raised by the fetcher itself.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.tushare.client import TushareClientError  # noqa: E402
from src.data.tushare.fetcher import (  # noqa: E402
    ENDPOINTS,
    TushareFetcher,
    TushareFetcherConfig,
    TushareFetcherError,
)


class _FakeClient:
    """Duck-typed stand-in for :class:`TushareClient`.

    The real client is a frozen dataclass and disallows attribute
    reassignment, so we cannot patch ``.call`` on it directly. The
    fetcher only consumes ``client.call(api, **params)`` — anything
    with that surface works at runtime.

    A non-empty ``token`` is carried so :class:`TokenLeakTests` can
    verify the fetcher does not put it into error messages.
    """

    def __init__(self, call_side_effect, token: str = "test-token-12345") -> None:
        self.token = token
        self.call = MagicMock(side_effect=call_side_effect)


def _make_client(call_side_effect, token: str = "test-token-12345") -> _FakeClient:
    return _FakeClient(call_side_effect, token=token)


def _stock_basic_df(status: str, rows: int = 3) -> pd.DataFrame:
    """Synthetic stock_basic response shaped like Tushare's real output."""
    return pd.DataFrame(
        {
            "ts_code": [f"60000{i}.SH" for i in range(rows)],
            "symbol": [f"60000{i}" for i in range(rows)],
            "name": [f"name_{status}_{i}" for i in range(rows)],
            "area": ["上海"] * rows,
            "industry": ["银行"] * rows,
            "market": ["主板"] * rows,
            "list_date": ["20000101"] * rows,
            "delist_date": ["20220101"] * rows if status == "D" else [None] * rows,
            "list_status": [status] * rows,
            "curr_type": ["CNY"] * rows,
        }
    )


class ConfigValidationTests(unittest.TestCase):

    def test_rejects_unknown_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(TushareFetcherError, "Unknown endpoint"):
                TushareFetcherConfig(
                    output_dir=Path(tmp),
                    endpoints=("stock_basic", "not_an_endpoint"),
                )

    def test_rejects_malformed_start_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(TushareFetcherError, "start_date"):
                TushareFetcherConfig(
                    output_dir=Path(tmp), start_date="2000-01-01",  # has dashes
                )

    def test_rejects_malformed_end_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(TushareFetcherError, "end_date"):
                TushareFetcherConfig(
                    output_dir=Path(tmp), end_date="2025",  # wrong length
                )

    def test_rejects_start_after_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(TushareFetcherError, "start_date.*>.*end_date"):
                TushareFetcherConfig(
                    output_dir=Path(tmp),
                    start_date="20251231", end_date="20000101",
                )

    def test_rejects_negative_sleep(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(TushareFetcherError, "rate_limit_sleep_ms"):
                TushareFetcherConfig(
                    output_dir=Path(tmp), rate_limit_sleep_ms=-1,
                )

    def test_default_endpoints_match_endpoints_constant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = TushareFetcherConfig(output_dir=Path(tmp))
        self.assertEqual(cfg.endpoints, ENDPOINTS)


class StockBasicFetchTests(unittest.TestCase):

    def test_writes_both_buckets(self) -> None:
        calls = []

        def side_effect(api, **params):
            calls.append((api, params.get("list_status")))
            return _stock_basic_df(params["list_status"], rows=5)

        client = _make_client(side_effect)

        with tempfile.TemporaryDirectory() as tmp:
            cfg = TushareFetcherConfig(
                output_dir=Path(tmp), endpoints=("stock_basic",),
                rate_limit_sleep_ms=0,
            )
            results = TushareFetcher(client, cfg).fetch()

        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r.endpoint, "stock_basic")
        self.assertEqual(r.files_written, 2)
        self.assertEqual(r.rows_total, 10)
        self.assertEqual(r.skipped, 0)
        self.assertEqual({c[1] for c in calls}, {"L", "D"})

    def test_resume_skips_existing(self) -> None:
        client = _make_client(lambda api, **p: _stock_basic_df(p["list_status"]))

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # Pre-create one of the two output files
            (tmp_path / "active_stocks.parquet").write_bytes(b"placeholder")

            cfg = TushareFetcherConfig(
                output_dir=tmp_path, endpoints=("stock_basic",),
                rate_limit_sleep_ms=0,
            )
            results = TushareFetcher(client, cfg).fetch()

        r = results[0]
        self.assertEqual(r.files_written, 1)  # only delisted_stocks.parquet
        self.assertEqual(r.skipped, 1)
        # Only one network call was made (for the missing bucket).
        self.assertEqual(client.call.call_count, 1)

    def test_dry_run_writes_nothing(self) -> None:
        client = _make_client(lambda api, **p: _stock_basic_df(p["list_status"]))

        with tempfile.TemporaryDirectory() as tmp:
            cfg = TushareFetcherConfig(
                output_dir=Path(tmp), endpoints=("stock_basic",),
                rate_limit_sleep_ms=0, dry_run=True,
            )
            results = TushareFetcher(client, cfg).fetch()

            self.assertEqual(list((Path(tmp)).iterdir()), [])

        # Dry run does NOT call the client at all (would charge rate-limit budget)
        self.assertEqual(client.call.call_count, 0)
        r = results[0]
        self.assertEqual(r.files_written, 0)


class NamechangeAndSuspendDFetchTests(unittest.TestCase):

    def test_namechange_single_call(self) -> None:
        client = _make_client(
            lambda api, **p: pd.DataFrame({"ts_code": ["600000.SH"], "name": ["A"],
                                           "start_date": ["20200101"],
                                           "end_date": [None],
                                           "ann_date": ["20200101"],
                                           "change_reason": ["改名"]})
        )
        with tempfile.TemporaryDirectory() as tmp:
            cfg = TushareFetcherConfig(
                output_dir=Path(tmp), endpoints=("namechange",),
                rate_limit_sleep_ms=0,
            )
            results = TushareFetcher(client, cfg).fetch()
            self.assertTrue((Path(tmp) / "all_namechanges.parquet").exists())
        self.assertEqual(results[0].files_written, 1)
        self.assertEqual(client.call.call_count, 1)

    def test_suspend_d_single_call(self) -> None:
        client = _make_client(
            lambda api, **p: pd.DataFrame({"ts_code": [], "trade_date": [],
                                           "suspend_timing": [], "suspend_type": []})
        )
        with tempfile.TemporaryDirectory() as tmp:
            cfg = TushareFetcherConfig(
                output_dir=Path(tmp), endpoints=("suspend_d",),
                rate_limit_sleep_ms=0,
            )
            results = TushareFetcher(client, cfg).fetch()
        self.assertEqual(results[0].files_written, 1)


class IndexWeightFetchTests(unittest.TestCase):

    def test_one_file_per_index(self) -> None:
        seen = []

        def side_effect(api, **p):
            seen.append(p["index_code"])
            return pd.DataFrame({"index_code": [p["index_code"]],
                                 "con_code": ["600519.SH"],
                                 "trade_date": ["20200101"],
                                 "weight": [1.0]})

        client = _make_client(side_effect)
        with tempfile.TemporaryDirectory() as tmp:
            cfg = TushareFetcherConfig(
                output_dir=Path(tmp), endpoints=("index_weight",),
                indices=("000300.SH", "000905.SH"),
                rate_limit_sleep_ms=0,
            )
            results = TushareFetcher(client, cfg).fetch()
            self.assertTrue((Path(tmp) / "index_weight" / "000300.SH.parquet").exists())
            self.assertTrue((Path(tmp) / "index_weight" / "000905.SH.parquet").exists())

        self.assertEqual(results[0].files_written, 2)
        self.assertEqual(sorted(seen), ["000300.SH", "000905.SH"])


class DailyAndAdjFactorTests(unittest.TestCase):

    def _prep_stock_basic(self, tmp: Path, tickers: list[str]) -> None:
        """Drop in a minimal stock_basic pair so _load_ticker_universe works."""
        df_active = pd.DataFrame({"ts_code": tickers[: len(tickers) // 2 or 1]})
        df_delisted = pd.DataFrame({"ts_code": tickers[len(tickers) // 2 or 1:]})
        df_active.to_parquet(tmp / "active_stocks.parquet", index=False)
        df_delisted.to_parquet(tmp / "delisted_stocks.parquet", index=False)

    def test_daily_loops_per_ticker_per_year(self) -> None:
        tickers = ["600000.SH", "600001.SH", "600002.SH"]
        client = _make_client(
            lambda api, **p: pd.DataFrame({
                "ts_code": [p["ts_code"]],
                "trade_date": [f"{int(p['start_date']) + 1}"],
                "open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0],
                "vol": [0.0], "amount": [0.0],
            })
        )

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._prep_stock_basic(tmp_path, tickers)

            cfg = TushareFetcherConfig(
                output_dir=tmp_path, endpoints=("daily",),
                start_date="20200101", end_date="20211231",
                rate_limit_sleep_ms=0,
            )
            results = TushareFetcher(client, cfg).fetch()

            # 3 tickers × 2 years = 6 files
            self.assertEqual(results[0].files_written, 6)
            for year in (2020, 2021):
                for ticker in tickers:
                    self.assertTrue(
                        (tmp_path / "daily" / str(year) / f"{ticker}.parquet").exists(),
                        f"missing daily/{year}/{ticker}.parquet",
                    )

    def test_daily_requires_stock_basic_first(self) -> None:
        client = _make_client(lambda api, **p: pd.DataFrame())
        with tempfile.TemporaryDirectory() as tmp:
            cfg = TushareFetcherConfig(
                output_dir=Path(tmp), endpoints=("daily",),
                start_date="20200101", end_date="20201231",
                rate_limit_sleep_ms=0,
            )
            with self.assertRaisesRegex(TushareFetcherError, "stock_basic"):
                TushareFetcher(client, cfg).fetch()

    def test_daily_resumes_existing_files(self) -> None:
        tickers = ["600000.SH", "600001.SH"]

        def side_effect(api, **p):
            return pd.DataFrame({
                "ts_code": [p["ts_code"]],
                "trade_date": ["20200101"],
                "open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0],
                "vol": [0.0], "amount": [0.0],
            })

        client = _make_client(side_effect)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._prep_stock_basic(tmp_path, tickers)
            # Pre-create one ticker's 2020 file so it gets skipped
            year_dir = tmp_path / "daily" / "2020"
            year_dir.mkdir(parents=True)
            (year_dir / "600000.SH.parquet").write_bytes(b"placeholder")

            cfg = TushareFetcherConfig(
                output_dir=tmp_path, endpoints=("daily",),
                start_date="20200101", end_date="20201231",
                rate_limit_sleep_ms=0,
            )
            results = TushareFetcher(client, cfg).fetch()

        # 2 tickers × 1 year = 2 expected; 1 already on disk; so 1 written
        self.assertEqual(results[0].files_written, 1)
        self.assertEqual(results[0].skipped, 1)
        # Only one call: for the missing (600001.SH, 2020) pair
        self.assertEqual(client.call.call_count, 1)


class RateLimitTests(unittest.TestCase):

    def test_retries_on_rate_limit_then_succeeds(self) -> None:
        attempts = {"n": 0}

        def side_effect(api, **p):
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise TushareClientError(
                    f"Tushare API '{api}' returned None. This typically means "
                    "rate-limit / insufficient account points."
                )
            return _stock_basic_df(p["list_status"])

        client = _make_client(side_effect)

        # Patch time.sleep so the test doesn't actually wait 60s + 120s
        with patch("src.data.tushare.fetcher.time.sleep"):
            with tempfile.TemporaryDirectory() as tmp:
                cfg = TushareFetcherConfig(
                    output_dir=Path(tmp), endpoints=("stock_basic",),
                    rate_limit_sleep_ms=0,
                )
                results = TushareFetcher(client, cfg).fetch()

        # 2 buckets × 3 attempts to first success = 6 (we only count attempts
        # for the first bucket; second bucket starts after success of first
        # so attempt counter would be 6, but we just check 2 files written)
        self.assertEqual(results[0].files_written, 2)

    def test_non_rate_limit_error_reraises_immediately(self) -> None:
        client = _make_client(
            lambda api, **p: (_ for _ in ()).throw(
                TushareClientError(f"Tushare {api} no callable named foo")
            )
        )

        with patch("src.data.tushare.fetcher.time.sleep"):
            with tempfile.TemporaryDirectory() as tmp:
                cfg = TushareFetcherConfig(
                    output_dir=Path(tmp), endpoints=("stock_basic",),
                    rate_limit_sleep_ms=0,
                )
                with self.assertRaises(TushareClientError):
                    TushareFetcher(client, cfg).fetch()

        # No retries — exactly one call, then re-raise
        self.assertEqual(client.call.call_count, 1)

    def test_rate_limit_exhaustion_raises_fetcher_error(self) -> None:
        client = _make_client(
            lambda api, **p: (_ for _ in ()).throw(
                TushareClientError("returned None — rate limit exceeded")
            )
        )

        with patch("src.data.tushare.fetcher.time.sleep"):
            with tempfile.TemporaryDirectory() as tmp:
                cfg = TushareFetcherConfig(
                    output_dir=Path(tmp), endpoints=("stock_basic",),
                    rate_limit_sleep_ms=0,
                )
                with self.assertRaisesRegex(TushareFetcherError, "rate limit"):
                    TushareFetcher(client, cfg).fetch()


class AtomicWriteTests(unittest.TestCase):

    def test_no_tmp_file_left_after_success(self) -> None:
        client = _make_client(lambda api, **p: _stock_basic_df(p["list_status"]))

        with tempfile.TemporaryDirectory() as tmp:
            cfg = TushareFetcherConfig(
                output_dir=Path(tmp), endpoints=("stock_basic",),
                rate_limit_sleep_ms=0,
            )
            TushareFetcher(client, cfg).fetch()
            tmp_files = list(Path(tmp).glob("**/*.tmp"))
        self.assertEqual(tmp_files, [])


class TokenLeakTests(unittest.TestCase):

    def test_fetcher_error_does_not_leak_token(self) -> None:
        client = _make_client(
            lambda api, **p: (_ for _ in ()).throw(
                TushareClientError("returned None — rate limit")
            ),
            token="super-secret-token-DO-NOT-LEAK",
        )

        with patch("src.data.tushare.fetcher.time.sleep"):
            with tempfile.TemporaryDirectory() as tmp:
                cfg = TushareFetcherConfig(
                    output_dir=Path(tmp), endpoints=("stock_basic",),
                    rate_limit_sleep_ms=0,
                )
                try:
                    TushareFetcher(client, cfg).fetch()
                except TushareFetcherError as exc:
                    self.assertNotIn("super-secret-token", str(exc))
                else:
                    self.fail("Expected TushareFetcherError")


if __name__ == "__main__":
    unittest.main()
