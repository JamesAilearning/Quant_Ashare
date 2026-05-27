"""Tests for the ``daily_basic`` endpoint in
:class:`src.data.tushare.fetcher.TushareFetcher` (PR #182 — extend the
factor-mining feature universe with fundamentals).

The endpoint mirrors ``daily`` and ``adj_factor``: per-(ticker, year)
parquet files under ``daily_basic/{year}/{ticker}.parquet``, written
atomically, with file-existence resume semantics. The Tushare client
is mocked everywhere — these tests must not touch the network.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.tushare.fetcher import (  # noqa: E402
    ENDPOINTS,
    TushareFetcher,
    TushareFetcherConfig,
    TushareFetcherError,
)

# The exact field list the fetcher passes to Tushare for daily_basic.
# Pinned here so a drift in the fetcher (or in the proposal) is caught
# by a schema test rather than slipping through silently.
EXPECTED_DAILY_BASIC_FIELDS: tuple[str, ...] = (
    "ts_code",
    "trade_date",
    "turnover_rate",
    "pe",
    "pb",
    "ps",
    "ps_ttm",
    "circ_mv",
    "total_mv",
    "float_share",
    "total_share",
)


class _FakeClient:
    """Duck-typed stand-in for :class:`TushareClient` (matches the pattern
    used in ``tests/data_pipeline/test_fetcher.py``)."""

    def __init__(self, call_side_effect, token: str = "test-token-12345") -> None:
        self.token = token
        self.call = MagicMock(side_effect=call_side_effect)


def _make_client(call_side_effect, token: str = "test-token-12345") -> _FakeClient:
    return _FakeClient(call_side_effect, token=token)


def _seed_stock_basic(tmp: Path, tickers: list[str]) -> None:
    """Drop minimal stock_basic parquets so ``_load_ticker_universe`` works.

    The per-(ticker, year) loop refuses to run without these on disk.
    """
    split = max(1, len(tickers) // 2)
    pd.DataFrame({"ts_code": tickers[:split]}).to_parquet(
        tmp / "active_stocks.parquet", index=False,
    )
    pd.DataFrame({"ts_code": tickers[split:]}).to_parquet(
        tmp / "delisted_stocks.parquet", index=False,
    )


def _daily_basic_row(ts_code: str, trade_date: str) -> pd.DataFrame:
    """One synthetic daily_basic row with every required field populated."""
    return pd.DataFrame(
        {
            "ts_code": [ts_code],
            "trade_date": [trade_date],
            "turnover_rate": [1.23],
            "pe": [15.0],
            "pb": [2.0],
            "ps": [3.0],
            "ps_ttm": [3.1],
            "circ_mv": [1.0e9],
            "total_mv": [2.0e9],
            "float_share": [1.0e8],
            "total_share": [2.0e8],
        }
    )


class EndpointRegistrationTests(unittest.TestCase):
    """The endpoint name must be exported in ``ENDPOINTS`` and accepted
    by the same validator the CLI's ``--endpoints`` flag funnels into.
    Together these enforce: ``--endpoints daily_basic`` works end-to-end
    without an extra CLI patch.
    """

    def test_daily_basic_in_endpoints_constant(self) -> None:
        self.assertIn("daily_basic", ENDPOINTS)

    def test_config_accepts_daily_basic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = TushareFetcherConfig(
                output_dir=Path(tmp),
                endpoints=("daily_basic",),
                rate_limit_sleep_ms=0,
            )
        self.assertEqual(cfg.endpoints, ("daily_basic",))

    def test_config_rejects_neighbouring_typo(self) -> None:
        """Regression guard: a near-miss like ``daily_basics`` should still
        be rejected even though ``daily_basic`` is now valid. Confirms the
        allow-list semantics did not relax with the addition.
        """
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(TushareFetcherError, "Unknown endpoint"):
                TushareFetcherConfig(
                    output_dir=Path(tmp),
                    endpoints=("daily_basics",),
                    rate_limit_sleep_ms=0,
                )


class DailyBasicFetchTests(unittest.TestCase):

    def test_writes_per_ticker_per_year_with_expected_schema(self) -> None:
        """Two tickers × two years → four files, each containing the
        eleven daily_basic columns the fetcher requested."""
        tickers = ["600000.SH", "600001.SH"]
        captured_fields: list[str] = []

        def side_effect(api, **params):
            self.assertEqual(api, "daily_basic")
            captured_fields.append(params["fields"])
            return _daily_basic_row(params["ts_code"], f"{params['start_date'][:4]}0701")

        client = _make_client(side_effect)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _seed_stock_basic(tmp_path, tickers)

            cfg = TushareFetcherConfig(
                output_dir=tmp_path,
                endpoints=("daily_basic",),
                start_date="20200101",
                end_date="20211231",
                rate_limit_sleep_ms=0,
            )
            results = TushareFetcher(client, cfg).fetch()

            self.assertEqual(len(results), 1)
            r = results[0]
            self.assertEqual(r.endpoint, "daily_basic")
            # 2 tickers × 2 years = 4 files
            self.assertEqual(r.files_written, 4)
            self.assertEqual(r.skipped, 0)
            self.assertEqual(r.rows_total, 4)

            for year in (2020, 2021):
                for ticker in tickers:
                    path = tmp_path / "daily_basic" / str(year) / f"{ticker}.parquet"
                    self.assertTrue(
                        path.exists(),
                        f"missing daily_basic/{year}/{ticker}.parquet",
                    )
                    df = pd.read_parquet(path)
                    self.assertEqual(
                        tuple(df.columns),
                        EXPECTED_DAILY_BASIC_FIELDS,
                        f"schema drift in daily_basic/{year}/{ticker}.parquet",
                    )

        # Every call requests exactly the canonical field list.
        self.assertEqual(client.call.call_count, 4)
        for fields_arg in captured_fields:
            self.assertEqual(
                tuple(s.strip() for s in fields_arg.split(",")),
                EXPECTED_DAILY_BASIC_FIELDS,
            )

    def test_resume_skips_existing_files(self) -> None:
        """The per-file existence checkpoint must skip already-written
        files — same resume semantics the existing ``daily`` /
        ``adj_factor`` endpoints rely on.
        """
        tickers = ["600000.SH", "600001.SH"]

        client = _make_client(
            lambda api, **p: _daily_basic_row(p["ts_code"], "20200315")
        )

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _seed_stock_basic(tmp_path, tickers)

            # Pre-create one ticker's 2020 file so it gets skipped.
            year_dir = tmp_path / "daily_basic" / "2020"
            year_dir.mkdir(parents=True)
            (year_dir / "600000.SH.parquet").write_bytes(b"placeholder")

            cfg = TushareFetcherConfig(
                output_dir=tmp_path,
                endpoints=("daily_basic",),
                start_date="20200101",
                end_date="20201231",
                rate_limit_sleep_ms=0,
            )
            results = TushareFetcher(client, cfg).fetch()

        # 2 tickers × 1 year = 2 expected; one already on disk → 1 written.
        self.assertEqual(results[0].files_written, 1)
        self.assertEqual(results[0].skipped, 1)
        # Only one network call — for the missing (600001.SH, 2020) pair.
        self.assertEqual(client.call.call_count, 1)

    def test_empty_response_writes_placeholder_by_default(self) -> None:
        """Tushare returns no rows for tickers without daily_basic coverage
        (e.g. just-delisted shells). The fetcher's default
        ``write_empty_placeholders=True`` writes an empty parquet so a
        subsequent rerun skips the call, matching the existing convention
        for ``daily`` / ``adj_factor``.
        """
        tickers = ["600000.SH"]
        empty_df = pd.DataFrame(columns=list(EXPECTED_DAILY_BASIC_FIELDS))

        client = _make_client(lambda api, **p: empty_df)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _seed_stock_basic(tmp_path, tickers)

            cfg = TushareFetcherConfig(
                output_dir=tmp_path,
                endpoints=("daily_basic",),
                start_date="20200101",
                end_date="20201231",
                rate_limit_sleep_ms=0,
            )
            results = TushareFetcher(client, cfg).fetch()

            path = tmp_path / "daily_basic" / "2020" / "600000.SH.parquet"
            self.assertTrue(path.exists(), "empty placeholder should be written")
            df = pd.read_parquet(path)
            self.assertEqual(len(df), 0)
            self.assertEqual(set(df.columns), set(EXPECTED_DAILY_BASIC_FIELDS))

        r = results[0]
        self.assertEqual(r.files_written, 1)
        self.assertEqual(r.rows_total, 0)
        self.assertEqual(r.skipped, 0)

    def test_empty_response_skipped_when_placeholders_disabled(self) -> None:
        """With ``write_empty_placeholders=False`` the fetcher writes nothing
        for empty responses — same opt-out the existing per-ticker-per-year
        loop honours. Covered here to lock in that ``daily_basic`` reuses
        the same code path, not a divergent copy.
        """
        tickers = ["600000.SH"]
        empty_df = pd.DataFrame(columns=list(EXPECTED_DAILY_BASIC_FIELDS))

        client = _make_client(lambda api, **p: empty_df)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _seed_stock_basic(tmp_path, tickers)

            cfg = TushareFetcherConfig(
                output_dir=tmp_path,
                endpoints=("daily_basic",),
                start_date="20200101",
                end_date="20201231",
                rate_limit_sleep_ms=0,
                write_empty_placeholders=False,
            )
            results = TushareFetcher(client, cfg).fetch()

            self.assertFalse(
                (tmp_path / "daily_basic" / "2020" / "600000.SH.parquet").exists(),
                "no file should be written when placeholders are disabled",
            )

        r = results[0]
        self.assertEqual(r.files_written, 0)
        self.assertEqual(r.rows_total, 0)


class DailyBasicRequiresStockBasicTests(unittest.TestCase):
    """The per-(ticker, year) loop refuses to run without
    ``active_stocks.parquet`` / ``delisted_stocks.parquet`` on disk —
    same precondition as the existing endpoints, asserted explicitly so
    a future refactor cannot drop ``daily_basic`` from the gate.
    """

    def test_raises_when_stock_basic_missing(self) -> None:
        client = _make_client(lambda api, **p: pd.DataFrame())
        with tempfile.TemporaryDirectory() as tmp:
            cfg = TushareFetcherConfig(
                output_dir=Path(tmp),
                endpoints=("daily_basic",),
                start_date="20200101",
                end_date="20201231",
                rate_limit_sleep_ms=0,
            )
            with self.assertRaisesRegex(TushareFetcherError, "stock_basic"):
                TushareFetcher(client, cfg).fetch()


if __name__ == "__main__":
    unittest.main()
