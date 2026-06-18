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
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.tushare.client import (  # noqa: E402
    KIND_AUTH,
    KIND_ENVIRONMENT,
    KIND_NETWORK,
    KIND_PARAM,
    KIND_RATE_LIMIT,
    KIND_SERVER_ERROR,
    KIND_UNKNOWN,
    TushareClientError,
    classify_tushare_failure,
)
from src.data.tushare.fetcher import (  # noqa: E402
    ENDPOINTS,
    FetchHoleError,
    TushareFetcher,
    TushareFetcherConfig,
    TushareFetcherError,
    _expected_year_file_end,
    _last_trading_day_on_or_before,
    _last_weekday_str,
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

    def __init__(self, call_side_effect, token: str = "test-token-12345",
                 trade_cal_dates: list[str] | None = None) -> None:
        self.token = token

        def _with_trade_cal(api, **p):
            # Serve the trading calendar centrally for every test so the
            # freshness gate's trading-day floor equals the weekday floor here
            # (default: all weekdays are trading days → boundaries unchanged),
            # without each per-endpoint side_effect needing to model trade_cal.
            # ``trade_cal_dates`` overrides with an explicit calendar (e.g. a
            # year whose last weekday is a HOLIDAY) for the regression tests.
            # The calendar call is still recorded on the MagicMock; assertions
            # on the number of DATA pulls use _data_call_count to exclude it.
            if api == "trade_cal":
                if trade_cal_dates is not None:
                    return pd.DataFrame({"cal_date": list(trade_cal_dates)})
                return _all_weekday_cal_df(p["start_date"], p["end_date"])
            return call_side_effect(api, **p)

        self.call = MagicMock(side_effect=_with_trade_cal)


def _all_weekday_cal_df(start: str, end: str) -> pd.DataFrame:
    """A ``trade_cal``-shaped frame: every weekday in ``[start, end]`` as a
    trading day. Keeps the trading-day floor identical to the weekday floor in
    tests while still exercising the real calendar code path."""
    days = []
    d = datetime.strptime(start, "%Y%m%d").date()
    e = datetime.strptime(end, "%Y%m%d").date()
    while d <= e:
        if d.weekday() < 5:
            days.append(d.strftime("%Y%m%d"))
        d += timedelta(days=1)
    return pd.DataFrame({"cal_date": days})


def _data_call_count(client) -> int:
    """``client.call`` invocations excluding the one-shot ``trade_cal`` calendar
    fetch — the count of actual data pulls."""
    return sum(
        1 for c in client.call.call_args_list
        if not (c.args and c.args[0] == "trade_cal")
    )


def _make_client(call_side_effect, token: str = "test-token-12345",
                 trade_cal_dates: list[str] | None = None) -> _FakeClient:
    return _FakeClient(call_side_effect, token=token, trade_cal_dates=trade_cal_dates)


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

    def test_embeds_snapshot_date_in_both_buckets(self) -> None:
        # P3-5: both buckets carry an embedded snapshot_date column stamped with
        # the (injected) fetch date — the staleness guards read THIS, not mtime.
        from src.data.active_stocks_snapshot import embedded_snapshot_date

        client = _make_client(lambda api, **p: _stock_basic_df(p["list_status"]))
        with tempfile.TemporaryDirectory() as tmp:
            cfg = TushareFetcherConfig(
                output_dir=Path(tmp), endpoints=("stock_basic",),
                rate_limit_sleep_ms=0, now=date(2026, 6, 10),
            )
            TushareFetcher(client, cfg).fetch()
            for fname in ("active_stocks.parquet", "delisted_stocks.parquet"):
                df = pd.read_parquet(Path(tmp) / fname)
                self.assertIn("snapshot_date", df.columns)
                self.assertEqual(set(df["snapshot_date"]), {"20260610"})
                self.assertEqual(
                    embedded_snapshot_date(df, source=fname), date(2026, 6, 10),
                )

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
        self.assertEqual(_data_call_count(client), 1)

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
        self.assertEqual(_data_call_count(client), 0)
        r = results[0]
        self.assertEqual(r.files_written, 0)

    def test_dry_run_does_not_create_missing_output_dir(self) -> None:
        """Regression for Codex review on PR #99: dry-run promises no
        filesystem side-effects, so a non-existent output_dir MUST NOT
        be created by fetch(). Previously fetch() unconditionally
        mkdir'd output_dir before checking dry_run.
        """
        client = _make_client(lambda api, **p: _stock_basic_df(p["list_status"]))

        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does_not_exist_yet"
            self.assertFalse(missing.exists())

            cfg = TushareFetcherConfig(
                output_dir=missing, endpoints=("stock_basic",),
                rate_limit_sleep_ms=0, dry_run=True,
            )
            TushareFetcher(client, cfg).fetch()

            # The directory MUST still not exist — dry-run cannot create it.
            self.assertFalse(missing.exists())


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
        self.assertEqual(_data_call_count(client), 1)

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

    def test_one_file_per_index_with_year_chunking(self) -> None:
        """The fetcher chunks index_weight calls by year and concats into
        one parquet per index. Phase A.4 smoke test discovered Tushare's
        per-call row cap silently truncating multi-year ranges, so a
        single (start_date, end_date) call missed most history.
        Regression asserts: (a) per-(index, year) calls happen,
        (b) the concatenated rows all land in one ``{index}.parquet``.
        """
        calls = []

        def side_effect(api, **p):
            calls.append((p["index_code"], p["start_date"], p["end_date"]))
            # Return one row per call; concat should sum across years
            return pd.DataFrame({"index_code": [p["index_code"]],
                                 "con_code": ["600519.SH"],
                                 "trade_date": [f"{p['start_date'][:4]}1231"],
                                 "weight": [1.0]})

        client = _make_client(side_effect)
        with tempfile.TemporaryDirectory() as tmp:
            cfg = TushareFetcherConfig(
                output_dir=Path(tmp), endpoints=("index_weight",),
                indices=("000300.SH", "000905.SH"),
                start_date="20200101", end_date="20221231",  # 3 years
                rate_limit_sleep_ms=0,
            )
            results = TushareFetcher(client, cfg).fetch()
            for idx in ("000300.SH", "000905.SH"):
                path = Path(tmp) / "index_weight" / f"{idx}.parquet"
                self.assertTrue(path.exists())
                df = pd.read_parquet(path)
                # Per-index chunks concatenated: 3 yearly chunks × 1 row each
                self.assertEqual(len(df), 3,
                                 f"{idx}: expected 3 chunks concatenated, got {len(df)}")
                self.assertEqual(set(df["trade_date"]),
                                 {"20201231", "20211231", "20221231"})

        self.assertEqual(results[0].files_written, 2)
        # 2 indices × 3 years = 6 calls
        self.assertEqual(len(calls), 6)
        # Each year called for each index
        year_starts = {(c[0], c[1]) for c in calls}
        self.assertEqual(year_starts, {
            ("000300.SH", "20200101"),
            ("000300.SH", "20210101"),
            ("000300.SH", "20220101"),
            ("000905.SH", "20200101"),
            ("000905.SH", "20210101"),
            ("000905.SH", "20220101"),
        })

    def test_empty_response_writes_empty_parquet_placeholder(self) -> None:
        """If Tushare returns no rows across the whole range (e.g. an
        index code with no historical data), write an empty parquet so
        resume on a subsequent run skips this index instead of redoing
        all the year-chunk calls.
        """
        client = _make_client(
            lambda api, **p: pd.DataFrame(
                columns=["index_code", "con_code", "trade_date", "weight"]
            )
        )
        with tempfile.TemporaryDirectory() as tmp:
            cfg = TushareFetcherConfig(
                output_dir=Path(tmp), endpoints=("index_weight",),
                indices=("000300.SH",),
                start_date="20200101", end_date="20201231",
                rate_limit_sleep_ms=0,
            )
            TushareFetcher(client, cfg).fetch()
            path = Path(tmp) / "index_weight" / "000300.SH.parquet"
            self.assertTrue(path.exists())
            df = pd.read_parquet(path)
            self.assertEqual(len(df), 0)


class RefreshCurrentTests(unittest.TestCase):
    """P3-6a: refresh_current bypasses resume's exists-skip for exactly the
    units a daily update must bring current — and nothing else."""

    @staticmethod
    def _daily_df(api, **p):
        return pd.DataFrame({
            "ts_code": [p["ts_code"]],
            "trade_date": [p["start_date"]],
            "open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0],
            "vol": [0.0], "amount": [0.0],
        })

    def test_stale_final_year_repulled_complete_past_year_skipped(self) -> None:
        # P3-7b: the freshness rule replaces the blind final-year re-pull. A
        # past year COMPLETE through its last weekday is skipped; the final
        # year whose file stops short of the requested end is re-pulled — no
        # refresh_current flag involved for per-ticker endpoints.
        tickers = ["600000.SH", "600001.SH"]
        client = _make_client(self._daily_df)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pd.DataFrame({"ts_code": [tickers[0]]}).to_parquet(
                tmp_path / "active_stocks.parquet", index=False)
            pd.DataFrame({"ts_code": [tickers[1]]}).to_parquet(
                tmp_path / "delisted_stocks.parquet", index=False)
            # 2020 complete through 2020-12-31 (a Thursday — the year's last
            # weekday); 2021 stale at June (yesterday's boundary file).
            for year, max_td in ((2020, "20201231"), (2021, "20210610")):
                d = tmp_path / "daily" / str(year)
                d.mkdir(parents=True)
                for tk in tickers:
                    pd.DataFrame(
                        {"ts_code": [tk], "trade_date": [max_td]}
                    ).to_parquet(d / f"{tk}.parquet", index=False)
            cfg = TushareFetcherConfig(
                output_dir=tmp_path, endpoints=("daily",),
                start_date="20200101", end_date="20211231",
                rate_limit_sleep_ms=0,
            )
            results = TushareFetcher(client, cfg).fetch()
            # Stale final year (2021) re-pulled for both tickers; complete
            # 2020 stays skipped.
            self.assertEqual(results[0].files_written, 2)
            self.assertEqual(results[0].skipped, 2)
            called_years = {p["start_date"][:4] for a, p in
                            [(c.args, c.kwargs) for c in client.call.call_args_list]
                            if not (a and a[0] == "trade_cal")}
            self.assertEqual(called_years, {"2021"})

    def test_repulls_stock_basic_and_aggregates(self) -> None:
        calls: list[str] = []

        def side_effect(api, **p):
            calls.append(api)
            if api == "stock_basic":
                return _stock_basic_df(p["list_status"])
            return pd.DataFrame({"ts_code": ["600000.SH"]})

        client = _make_client(side_effect)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # Yesterday's files all present.
            for fname in ("active_stocks.parquet", "delisted_stocks.parquet",
                          "all_namechanges.parquet", "suspend_d.parquet"):
                pd.DataFrame({"ts_code": ["x"]}).to_parquet(
                    tmp_path / fname, index=False)
            cfg = TushareFetcherConfig(
                output_dir=tmp_path,
                endpoints=("stock_basic", "namechange", "suspend_d"),
                rate_limit_sleep_ms=0, refresh_current=True,
                now=date(2026, 6, 10),
            )
            results = TushareFetcher(client, cfg).fetch()
            self.assertEqual(calls.count("stock_basic"), 2)  # both buckets
            self.assertEqual(calls.count("namechange"), 1)
            self.assertEqual(calls.count("suspend_d"), 1)
            self.assertEqual({r.skipped for r in results}, {0})
            # The refreshed snapshot carries TODAY's embedded stamp (P3-5).
            df = pd.read_parquet(tmp_path / "active_stocks.parquet")
            self.assertEqual(set(df["snapshot_date"]), {"20260610"})

    def test_prior_manifest_hole_forces_retry_across_year_boundary(self) -> None:
        # codex P1: a refresh failure leaves YESTERDAY's file + a manifest
        # hole. After the year rolls over, that unit is no longer end_year, so
        # exists-skip would shadow it forever and the merge would drop its
        # never-re-attempted hole as self-healed. force_retry_units (wired
        # from the prior manifest by the 01 CLI) must re-pull EXACTLY that
        # unit while same-year siblings stay resume-skipped.
        tickers = ["600000.SH", "600001.SH"]
        client = _make_client(self._daily_df)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pd.DataFrame({"ts_code": [tickers[0]]}).to_parquet(
                tmp_path / "active_stocks.parquet", index=False)
            pd.DataFrame({"ts_code": [tickers[1]]}).to_parquet(
                tmp_path / "delisted_stocks.parquet", index=False)
            # Both 2020 files exist and are COMPLETE through the year's last
            # weekday (so the freshness rule alone would skip them — only the
            # force-retry must pierce). 2020 is NOT the final year of this run.
            d = tmp_path / "daily" / "2020"
            d.mkdir(parents=True)
            for tk in tickers:
                pd.DataFrame(
                    {"ts_code": [tk], "trade_date": ["20201231"]}
                ).to_parquet(d / f"{tk}.parquet", index=False)
            cfg = TushareFetcherConfig(
                output_dir=tmp_path, endpoints=("daily",),
                start_date="20200101", end_date="20211231",
                rate_limit_sleep_ms=0,
                force_retry_units=frozenset(
                    {("daily", "ts_code=600000.SH year=2020")},
                ),
            )
            results = TushareFetcher(client, cfg).fetch()
            called_units = {
                (c.kwargs["ts_code"], c.kwargs["start_date"][:4])
                for c in client.call.call_args_list
                if not (c.args and c.args[0] == "trade_cal")
            }
            # The holed 2020 unit was re-attempted; its 2020 sibling was not.
            self.assertIn(("600000.SH", "2020"), called_units)
            self.assertNotIn(("600001.SH", "2020"), called_units)
            # 2021 (no files yet) fetched normally for both.
            self.assertEqual(results[0].skipped, 1)

    def test_index_weight_is_not_refreshed(self) -> None:
        client = _make_client(lambda api, **p: pd.DataFrame())
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            iw = tmp_path / "index_weight"
            iw.mkdir(parents=True)
            pd.DataFrame({"ts_code": ["x"]}).to_parquet(
                iw / "000300.SH.parquet", index=False)
            cfg = TushareFetcherConfig(
                output_dir=tmp_path, endpoints=("index_weight",),
                indices=("000300.SH",),
                rate_limit_sleep_ms=0, refresh_current=True,
            )
            results = TushareFetcher(client, cfg).fetch()
            self.assertEqual(results[0].skipped, 1)  # still resume-skipped
            self.assertEqual(_data_call_count(client), 0)


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
            # Pre-create one ticker's 2020 file, COMPLETE through the year's
            # last weekday, so the freshness rule skips it (P3-7b: a bare
            # placeholder byte-blob would now be treated as unreadable-stale
            # and re-pulled — by design).
            year_dir = tmp_path / "daily" / "2020"
            year_dir.mkdir(parents=True)
            pd.DataFrame(
                {"ts_code": ["600000.SH"], "trade_date": ["20201231"]}
            ).to_parquet(year_dir / "600000.SH.parquet", index=False)

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
        self.assertEqual(_data_call_count(client), 1)


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
        self.assertEqual(_data_call_count(client), 1)

    def test_rate_limit_exhaustion_records_hole_not_abort(self) -> None:
        # P3-4a continue-on-error: an exhausted retryable call no longer aborts
        # the run — it is recorded as a hole and the loop continues. Both
        # stock_basic buckets (L, D) exhaust → 2 holes, fetch() does NOT raise.
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
                fetcher = TushareFetcher(client, cfg)
                results = fetcher.fetch()  # does NOT raise

        self.assertEqual(results[0].files_written, 0)
        self.assertEqual(len(fetcher.holes), 2)
        self.assertTrue(all(h.endpoint == "stock_basic" for h in fetcher.holes))
        self.assertTrue(all(h.reason_class == "transient" for h in fetcher.holes))

    def test_no_sleep_after_final_rate_limit_attempt(self) -> None:
        """Regression for Codex review on PR #99: the final allowed retry
        attempt MUST NOT sleep before surfacing the failure — otherwise an
        exhausted call wastes a full backoff period (~300s) before raising,
        compounding badly inside per-ticker loops. Tested on ``_safe_call``
        directly so a single call's retry/sleep schedule is isolated (P3-4a:
        exhaustion now raises ``FetchHoleError``).
        """
        from src.data.tushare.fetcher import MAX_RATE_LIMIT_RETRIES

        client = _make_client(
            lambda api, **p: (_ for _ in ()).throw(
                TushareClientError("returned None — rate limit exceeded")
            )
        )

        with patch("src.data.tushare.fetcher.time.sleep") as mock_sleep:
            with tempfile.TemporaryDirectory() as tmp:
                cfg = TushareFetcherConfig(
                    output_dir=Path(tmp), rate_limit_sleep_ms=0,  # disable per-call sleep
                )
                fetcher = TushareFetcher(client, cfg)
                with self.assertRaises(FetchHoleError):
                    fetcher._safe_call("daily", ts_code="600000.SH")

        # Sleep called exactly MAX_RATE_LIMIT_RETRIES - 1 times (no sleep after
        # the final attempt). rate_limit_sleep_ms=0 short-circuits the per-call
        # sleep so it contributes 0.
        self.assertEqual(mock_sleep.call_count, MAX_RATE_LIMIT_RETRIES - 1)
        # One network call per attempt.
        self.assertEqual(_data_call_count(client), MAX_RATE_LIMIT_RETRIES)


class RetryableErrorClassificationTests(unittest.TestCase):
    """``_is_retryable_error`` covers rate-limit AND transient
    network / 5xx classes. Audit follow-up after a real
    operator-reported ``HTTPConnectionPool(host='api.waditu.com',
    port=80): ConnectionError`` killed a multi-hour
    ``adj_factor`` pull on the first blip.
    """

    def test_rate_limit_token_still_retried(self) -> None:
        """The original rate-limit tokens MUST still trigger retry —
        regression guard against accidentally narrowing the
        predicate while extending it.
        """
        from src.data.tushare.fetcher import TushareFetcher

        for msg in (
            "RATE limit exceeded",
            "Tushare returned None for stock_basic",
            "返回 None — rate limit",
        ):
            with self.subTest(msg=msg):
                self.assertTrue(
                    TushareFetcher._is_retryable_error(
                        TushareClientError(msg),
                    ),
                    f"rate-limit signal {msg!r} should be retryable",
                )

    def test_connection_error_is_retryable(self) -> None:
        """The user-reported failure mode: requests' adapter raises
        ``ConnectionError`` and the message contains
        ``HTTPConnectionPool(host='api.waditu.com')``. Both
        substrings now trigger retry."""
        from src.data.tushare.fetcher import TushareFetcher

        for msg in (
            "ConnectionError: HTTPConnectionPool(host='api.waditu.com', "
            "port=80): Max retries exceeded with url: /",
            "Connection refused by remote host",
            "ConnectionResetError: connection reset by peer",
        ):
            with self.subTest(msg=msg):
                self.assertTrue(
                    TushareFetcher._is_retryable_error(
                        TushareClientError(msg),
                    ),
                    f"connection-class signal {msg!r} should be retryable",
                )

    def test_timeout_is_retryable(self) -> None:
        from src.data.tushare.fetcher import TushareFetcher

        for msg in (
            "ReadTimeout: HTTPSConnectionPool(...): Read timed out.",
            "ConnectTimeout: HTTPSConnectionPool(...): timeout=5",
            "The read operation timed out",
        ):
            with self.subTest(msg=msg):
                self.assertTrue(
                    TushareFetcher._is_retryable_error(
                        TushareClientError(msg),
                    ),
                )

    def test_5xx_gateway_is_retryable(self) -> None:
        from src.data.tushare.fetcher import TushareFetcher

        for msg in (
            "HTTP 502 Bad Gateway",
            "Server returned 503 Service Unavailable",
            "Gateway Time-out (504)",
        ):
            with self.subTest(msg=msg):
                self.assertTrue(
                    TushareFetcher._is_retryable_error(
                        TushareClientError(msg),
                    ),
                )

    def test_chinese_transient_messages_retryable(self) -> None:
        """Tushare's Pro API sometimes returns Chinese error bodies on
        transient failures. The substring match covers them so a
        misconfigured operator locale doesn't lose the retry."""
        from src.data.tushare.fetcher import TushareFetcher

        for msg in (
            "网络连接异常，请稍后重试",
            "服务异常，请稍后再试",
            "Tushare 服务繁忙，请重试",
        ):
            with self.subTest(msg=msg):
                self.assertTrue(
                    TushareFetcher._is_retryable_error(
                        TushareClientError(msg),
                    ),
                )

    def test_token_and_permission_errors_NOT_retried(self) -> None:
        """Real failures must propagate immediately — wasting 5×60s
        of backoff on a missing-token error is exactly the bad
        operator experience the original retry logic was meant to
        avoid."""
        from src.data.tushare.fetcher import TushareFetcher

        for msg in (
            "Invalid token: please check TUSHARE_TOKEN",
            "Account permission denied for index_classify",
            "Missing required parameter: ts_code",
            "权限不足",
        ):
            with self.subTest(msg=msg):
                self.assertFalse(
                    TushareFetcher._is_retryable_error(
                        TushareClientError(msg),
                    ),
                    f"non-retryable signal {msg!r} must NOT trigger retry",
                )

    def test_connection_error_actually_retries_in_safe_call(self) -> None:
        """End-to-end smoke test: ``_safe_call`` retries a
        ``ConnectionError``-shaped TushareClientError just like a
        rate-limit error. Without this PR, a single transient
        network blip on ``adj_factor`` killed the entire publish
        — the original failure mode reported by the operator."""
        from unittest.mock import MagicMock

        from src.data.tushare.fetcher import (
            MAX_RATE_LIMIT_RETRIES,
            TushareFetcher,
            TushareFetcherConfig,
        )

        # Fail with ConnectionError-style message every attempt;
        # _safe_call should hit MAX_RATE_LIMIT_RETRIES retries before raising
        # FetchHoleError — proving the retry path engages on a network error,
        # not bailing on attempt 1 like the pre-PR behaviour. (P3-4a:
        # exhaustion raises FetchHoleError, which the per-endpoint loop turns
        # into a recorded hole.)
        client = MagicMock()
        client.call.side_effect = TushareClientError(
            "ConnectionError: HTTPConnectionPool(host='api.waditu.com', "
            "port=80): Max retries exceeded"
        )
        with patch("src.data.tushare.fetcher.time.sleep"):
            with tempfile.TemporaryDirectory() as tmp:
                cfg = TushareFetcherConfig(
                    output_dir=Path(tmp), rate_limit_sleep_ms=0,
                )
                fetcher = TushareFetcher(client, cfg)
                with self.assertRaises(FetchHoleError):
                    fetcher._safe_call("adj_factor", ts_code="600000.SH")
        # MAX_RATE_LIMIT_RETRIES attempts — pre-PR this would have
        # been exactly 1 because ConnectionError didn't match
        # ``_is_rate_limit_error``.
        self.assertEqual(_data_call_count(client), MAX_RATE_LIMIT_RETRIES)


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

    def test_holes_do_not_leak_token(self) -> None:
        # The fetcher never holds the token (the client does), and
        # TushareClientError is the client's secrets boundary — so a recorded
        # hole's sanitised last_error can never carry the token (P3-4a).
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
                fetcher = TushareFetcher(client, cfg)
                fetcher.fetch()

        self.assertTrue(fetcher.holes)  # holes were recorded, not aborted
        for h in fetcher.holes:
            self.assertNotIn("super-secret-token", h.last_error)


class ContinueOnErrorTests(unittest.TestCase):
    """P3-4a: a unit that exhausts retryable retries becomes a hole and the run
    continues; a non-retryable error aborts fast (no hole-spamming)."""

    def _prep_stock_basic(self, tmp: Path, tickers: list[str]) -> None:
        df_active = pd.DataFrame({"ts_code": tickers[: len(tickers) // 2 or 1]})
        df_delisted = pd.DataFrame({"ts_code": tickers[len(tickers) // 2 or 1:]})
        df_active.to_parquet(tmp / "active_stocks.parquet", index=False)
        df_delisted.to_parquet(tmp / "delisted_stocks.parquet", index=False)

    def test_per_ticker_hole_continues_to_next_ticker(self) -> None:
        tickers = ["600000.SH", "600001.SH", "600002.SH"]
        bad = "600001.SH"

        def side_effect(api, **p):
            if p.get("ts_code") == bad:
                raise TushareClientError("returned None — rate limit exceeded")
            return pd.DataFrame({
                "ts_code": [p["ts_code"]], "trade_date": ["20200101"],
                "open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0],
                "vol": [0.0], "amount": [0.0],
            })

        client = _make_client(side_effect)
        with patch("src.data.tushare.fetcher.time.sleep"):
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                self._prep_stock_basic(tmp_path, tickers)
                cfg = TushareFetcherConfig(
                    output_dir=tmp_path, endpoints=("daily",),
                    start_date="20200101", end_date="20201231",
                    rate_limit_sleep_ms=0,
                )
                fetcher = TushareFetcher(client, cfg)
                results = fetcher.fetch()  # does NOT raise
                d2020 = tmp_path / "daily" / "2020"
                self.assertTrue((d2020 / "600000.SH.parquet").exists())
                self.assertTrue((d2020 / "600002.SH.parquet").exists())
                self.assertFalse((d2020 / "600001.SH.parquet").exists())

        self.assertEqual(results[0].files_written, 2)
        self.assertEqual(len(fetcher.holes), 1)
        self.assertEqual(fetcher.holes[0].endpoint, "daily")
        self.assertIn("600001.SH", fetcher.holes[0].unit)
        self.assertIn("year=2020", fetcher.holes[0].unit)
        self.assertEqual(fetcher.holes[0].reason_class, "transient")

    def test_non_retryable_aborts_fast_no_hole(self) -> None:
        tickers = ["600000.SH", "600001.SH"]

        def side_effect(api, **p):
            raise TushareClientError("Tushare API 'daily' invalid token / 权限不足")

        client = _make_client(side_effect)
        with patch("src.data.tushare.fetcher.time.sleep"):
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                self._prep_stock_basic(tmp_path, tickers)
                cfg = TushareFetcherConfig(
                    output_dir=tmp_path, endpoints=("daily",),
                    start_date="20200101", end_date="20201231",
                    rate_limit_sleep_ms=0,
                )
                fetcher = TushareFetcher(client, cfg)
                with self.assertRaises(TushareClientError):
                    fetcher.fetch()

        # Hard error aborts fast — NO holes spammed, NO retries.
        self.assertEqual(len(fetcher.holes), 0)
        self.assertEqual(_data_call_count(client), 1)

    def test_holes_accumulate_across_endpoints(self) -> None:
        # ANTI-RESET red line: ONE fetch() run covers every configured endpoint
        # and accumulates EVERY endpoint's holes into one ledger. The per-fetch()
        # reset (`self._holes = []`) happens ONCE before the endpoint loop, so a
        # hole recorded by an early endpoint (namechange, 2nd) must still be
        # present after a later endpoint (daily, 5th) has run — it must NOT be
        # wiped. Without this, 01.main's single end-of-run `.holes` read would
        # silently see only the last endpoint's holes = a partial dump passing
        # as complete.
        tickers = ["600000.SH", "600001.SH"]
        bad_ticker = "600001.SH"

        def side_effect(api, **p):
            # namechange always exhausts; daily exhausts ONLY the bad ticker.
            if api == "namechange":
                raise TushareClientError("returned None — rate limit exceeded")
            if api == "daily" and p.get("ts_code") == bad_ticker:
                raise TushareClientError("returned None — rate limit exceeded")
            return pd.DataFrame({
                "ts_code": [p.get("ts_code", "X")], "trade_date": ["20200101"],
                "open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0],
                "vol": [0.0], "amount": [0.0],
            })

        client = _make_client(side_effect)
        with patch("src.data.tushare.fetcher.time.sleep"):
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                # Pre-seed the universe so `daily` runs without `stock_basic`
                # in the endpoint set (stock_basic and daily can't both hole —
                # a holed stock_basic leaves no universe and daily would abort).
                self._prep_stock_basic(tmp_path, tickers)
                cfg = TushareFetcherConfig(
                    output_dir=tmp_path, endpoints=("namechange", "daily"),
                    start_date="20200101", end_date="20201231",
                    rate_limit_sleep_ms=0,
                )
                fetcher = TushareFetcher(client, cfg)
                fetcher.fetch()  # ONE call spanning both endpoints

        # Both endpoints' holes survived into the single ledger.
        self.assertEqual(len(fetcher.holes), 2)
        self.assertEqual(
            {h.endpoint for h in fetcher.holes}, {"namechange", "daily"}
        )
        # The namechange hole (recorded during the 2nd endpoint) is STILL at
        # index 0 after daily (5th endpoint) ran — not wiped by a reset.
        self.assertEqual(fetcher.holes[0].endpoint, "namechange")
        daily_holes = [h for h in fetcher.holes if h.endpoint == "daily"]
        self.assertEqual(len(daily_holes), 1)
        self.assertIn(bad_ticker, daily_holes[0].unit)

    def test_stock_basic_hole_skips_dependents_not_hard_abort(self) -> None:
        # P1 (codex): a stock_basic hole must NOT hard-abort the dependent
        # per-ticker endpoints via _load_ticker_universe. They skip with a
        # `prerequisite` hole so the run completes-with-holes instead of taking
        # the hard-abort path (which would lose the holes + return exit 1).
        def side_effect(api, **p):
            if api == "stock_basic":
                raise TushareClientError("returned None — rate limit exceeded")
            self.fail(
                f"dependent endpoint {api!r} must not call the API when the "
                "universe is absent"
            )

        client = _make_client(side_effect)
        with patch("src.data.tushare.fetcher.time.sleep"):
            with tempfile.TemporaryDirectory() as tmp:
                cfg = TushareFetcherConfig(
                    output_dir=Path(tmp), endpoints=("stock_basic", "daily"),
                    start_date="20200101", end_date="20201231",
                    rate_limit_sleep_ms=0,
                )
                fetcher = TushareFetcher(client, cfg)
                results = fetcher.fetch()  # must NOT raise

        endpoints_with_holes = {h.endpoint for h in fetcher.holes}
        self.assertIn("stock_basic", endpoints_with_holes)
        self.assertIn("daily", endpoints_with_holes)  # recorded, not aborted
        daily_holes = [h for h in fetcher.holes if h.endpoint == "daily"]
        self.assertEqual(len(daily_holes), 1)
        self.assertEqual(daily_holes[0].reason_class, "prerequisite")
        daily_result = next(r for r in results if r.endpoint == "daily")
        self.assertEqual(daily_result.files_written, 0)

    def test_missing_stock_basic_without_hole_still_hard_aborts(self) -> None:
        # The fix must NOT swallow a genuine usage error: when stock_basic was
        # never fetched (no hole this run), a per-ticker endpoint still hard-
        # aborts so the operator learns they skipped a prerequisite.
        client = _make_client(lambda api, **p: pd.DataFrame())
        with tempfile.TemporaryDirectory() as tmp:
            cfg = TushareFetcherConfig(
                output_dir=Path(tmp), endpoints=("daily",),
                start_date="20200101", end_date="20201231",
                rate_limit_sleep_ms=0,
            )
            fetcher = TushareFetcher(client, cfg)
            with self.assertRaisesRegex(TushareFetcherError, "stock_basic"):
                fetcher.fetch()
        self.assertEqual(len(fetcher.holes), 0)

    def test_index_weight_hole_unit_is_per_index_not_per_year(self) -> None:
        # codex P1 (P3-4b): the index_weight hole unit must be STABLE per-index —
        # it must NOT include the first-failing year (which varies run-to-run),
        # else a re-run that fails at a different year yields a different unit
        # string and the manifest merge drops the prior un-healed hole.
        def side_effect(api, **p):
            raise TushareClientError("returned None — rate limit exceeded")

        client = _make_client(side_effect)
        with patch("src.data.tushare.fetcher.time.sleep"):
            with tempfile.TemporaryDirectory() as tmp:
                cfg = TushareFetcherConfig(
                    output_dir=Path(tmp), endpoints=("index_weight",),
                    indices=("000300.SH",),
                    start_date="20200101", end_date="20231231",
                    rate_limit_sleep_ms=0,
                )
                fetcher = TushareFetcher(client, cfg)
                fetcher.fetch()  # does not raise

        self.assertEqual(len(fetcher.holes), 1)
        self.assertEqual(fetcher.holes[0].endpoint, "index_weight")
        self.assertEqual(fetcher.holes[0].unit, "index=000300.SH")  # no year

    def test_aggregate_hole_unit_is_stable_file_not_range(self) -> None:
        # codex P2 (P3-4b): namechange / suspend_d are SINGLE-file endpoints, so
        # their hole unit must be a stable "file", NOT the run's date range (which
        # varies run-to-run and would make a wider/narrower re-failure look like a
        # different unit so the merge could not match the prior hole).
        def side_effect(api, **p):
            raise TushareClientError("returned None — rate limit exceeded")

        client = _make_client(side_effect)
        with patch("src.data.tushare.fetcher.time.sleep"):
            with tempfile.TemporaryDirectory() as tmp:
                cfg = TushareFetcherConfig(
                    output_dir=Path(tmp), endpoints=("namechange", "suspend_d"),
                    start_date="20200101", end_date="20231231",
                    rate_limit_sleep_ms=0,
                )
                fetcher = TushareFetcher(client, cfg)
                fetcher.fetch()  # does not raise

        by_ep = {h.endpoint: h.unit for h in fetcher.holes}
        self.assertEqual(by_ep, {"namechange": "file", "suspend_d": "file"})  # no range


class CliExitCodeTests(unittest.TestCase):
    """P3-4a: ``01_fetch_tushare.main`` returns non-zero (3) when the fetch
    finished with holes, 0 when clean — so a holey dump is never mistaken for a
    complete one by an orchestrator."""

    @staticmethod
    def _load_cli():
        import importlib.util
        path = PROJECT_ROOT / "scripts" / "data_pipeline" / "01_fetch_tushare.py"
        spec = importlib.util.spec_from_file_location("_fetch01_under_test", path)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_main_returns_3_when_holes(self) -> None:
        mod = self._load_cli()
        client = _make_client(
            lambda api, **p: (_ for _ in ()).throw(
                TushareClientError("returned None — rate limit exceeded")
            )
        )
        with patch("src.data.tushare.fetcher.time.sleep"), \
                patch.object(mod.TushareClient, "from_environment", return_value=client):
            with tempfile.TemporaryDirectory() as tmp:
                rc = mod.main([
                    "--output-dir", tmp, "--endpoints", "stock_basic",
                    "--rate-limit-sleep-ms", "0",
                ])
        self.assertEqual(rc, 3)

    def test_main_returns_0_when_clean(self) -> None:
        mod = self._load_cli()
        client = _make_client(lambda api, **p: _stock_basic_df(p["list_status"]))
        with patch("src.data.tushare.fetcher.time.sleep"), \
                patch.object(mod.TushareClient, "from_environment", return_value=client):
            with tempfile.TemporaryDirectory() as tmp:
                rc = mod.main([
                    "--output-dir", tmp, "--endpoints", "stock_basic",
                    "--rate-limit-sleep-ms", "0",
                ])
        self.assertEqual(rc, 0)

    def test_main_stock_basic_hole_exits_3_not_1(self) -> None:
        # P1 (codex): a stock_basic hole in a run that also has dependent
        # endpoints must exit 3 (completed-with-holes), NOT 1 (hard abort) —
        # the dependent endpoints skip with a prerequisite hole rather than
        # aborting via _load_ticker_universe.
        mod = self._load_cli()

        def side_effect(api, **p):
            if api == "stock_basic":
                raise TushareClientError("returned None — rate limit exceeded")
            return pd.DataFrame()  # daily never actually called (universe absent)

        client = _make_client(side_effect)
        with patch("src.data.tushare.fetcher.time.sleep"), \
                patch.object(mod.TushareClient, "from_environment", return_value=client):
            with tempfile.TemporaryDirectory() as tmp:
                rc = mod.main([
                    "--output-dir", tmp, "--endpoints", "stock_basic,daily",
                    "--rate-limit-sleep-ms", "0",
                ])
        self.assertEqual(rc, 3)


class KindFirstClassificationTests(unittest.TestCase):
    """P3-7: the structured ``kind`` stamped by the client is the PRIMARY
    retryability signal; message substrings are consulted ONLY for errors
    constructed without a kind (legacy / direct constructions).

    The regression these tests pin: ``client.call`` used to append
    "Common causes: rate limit (account tier too low), missing parameter,
    or transient network error." to EVERY wrapped failure, so the
    substring check classified every error — including invalid token /
    missing permission — as retryable, and the P3-4a fast-abort path was
    unreachable in production (the existing tests below construct BARE
    messages, which is exactly the blind spot)."""

    def test_kind_beats_retryable_looking_message(self) -> None:
        # The pre-P3-7 bug shape: an auth failure whose WRAPPED message
        # contains rate-limit / network prose. With a kind present, the
        # message must be ignored entirely.
        exc = TushareClientError(
            "Tushare API 'daily' raised Exception: token无效. Common causes: "
            "rate limit (account tier too low), missing parameter, or "
            "transient network error.",
            kind=KIND_AUTH,
        )
        self.assertFalse(TushareFetcher._is_retryable_error(exc))

    def test_retryable_kinds_retry_regardless_of_message(self) -> None:
        for kind in (KIND_RATE_LIMIT, KIND_NETWORK, KIND_SERVER_ERROR):
            with self.subTest(kind=kind):
                self.assertTrue(
                    TushareFetcher._is_retryable_error(
                        TushareClientError("opaque vendor text", kind=kind)
                    )
                )

    def test_non_retryable_kinds_do_not_retry(self) -> None:
        for kind in (KIND_AUTH, KIND_PARAM, KIND_ENVIRONMENT, KIND_UNKNOWN):
            with self.subTest(kind=kind):
                self.assertFalse(
                    TushareFetcher._is_retryable_error(
                        TushareClientError("opaque vendor text", kind=kind)
                    )
                )

    def test_no_kind_falls_back_to_substrings(self) -> None:
        # Legacy direct constructions (kind=None) keep the original
        # substring semantics on both sides.
        self.assertTrue(
            TushareFetcher._is_retryable_error(
                TushareClientError("returned None — rate limit exceeded")
            )
        )
        self.assertFalse(
            TushareFetcher._is_retryable_error(
                TushareClientError("token无效，请确认设置的token是否正确")
            )
        )

    def test_real_quota_message_with_quanxian_word_is_retryable(self) -> None:
        # Tushare's REAL rate-limit body also contains "权限" — the
        # classifier must rank the specific quota phrase above the auth
        # tokens, or routine quota exhaustion would abort multi-hour runs.
        raw = (
            "Exception: 抱歉，您每分钟最多访问该接口500次，"
            "权限的具体详情访问：https://tushare.pro/document/1?doc_id=108"
        )
        exc = TushareClientError(
            f"Tushare API 'daily' raised {raw}",
            kind=classify_tushare_failure(raw),
        )
        self.assertEqual(exc.kind, KIND_RATE_LIMIT)
        self.assertTrue(TushareFetcher._is_retryable_error(exc))

    def test_real_permission_message_is_not_retryable(self) -> None:
        raw = "Exception: 抱歉，您没有访问该接口的权限"
        exc = TushareClientError(
            f"Tushare API 'index_weight' raised {raw}",
            kind=classify_tushare_failure(raw),
        )
        self.assertEqual(exc.kind, KIND_AUTH)
        self.assertFalse(TushareFetcher._is_retryable_error(exc))


class FastAbortOnNonRetryableTests(unittest.TestCase):
    """P3-7 acceptance: an invalid-token / permission failure aborts the
    WHOLE run on the FIRST call — no retry loop, no backoff sleep, no hole
    recorded (a hard abort is not a hole; 4a original design)."""

    @staticmethod
    def _classified(raw: str) -> TushareClientError:
        # Shape errors exactly as the post-P3-7 client wraps them.
        return TushareClientError(
            f"Tushare API 'namechange' raised {raw}",
            kind=classify_tushare_failure(raw),
        )

    def test_token_error_aborts_run_on_first_call(self) -> None:
        client = _make_client(
            lambda api, **p: (_ for _ in ()).throw(
                self._classified("Exception: token无效，请确认设置的token是否正确")
            )
        )
        with patch("src.data.tushare.fetcher.time.sleep") as mock_sleep:
            with tempfile.TemporaryDirectory() as tmp:
                cfg = TushareFetcherConfig(
                    output_dir=Path(tmp),
                    endpoints=("namechange", "suspend_d"),
                    rate_limit_sleep_ms=0,
                )
                fetcher = TushareFetcher(client, cfg)
                with self.assertRaises(TushareClientError):
                    fetcher.fetch()
        # First call aborts the run: later endpoints never execute, the
        # retry/backoff machinery never engages ("seconds, not minutes").
        self.assertEqual(_data_call_count(client), 1)
        self.assertEqual(len(fetcher.holes), 0)
        mock_sleep.assert_not_called()

    def test_permission_error_aborts_run_on_first_call(self) -> None:
        client = _make_client(
            lambda api, **p: (_ for _ in ()).throw(
                self._classified("Exception: 抱歉，您没有访问该接口的权限")
            )
        )
        with patch("src.data.tushare.fetcher.time.sleep") as mock_sleep:
            with tempfile.TemporaryDirectory() as tmp:
                cfg = TushareFetcherConfig(
                    output_dir=Path(tmp),
                    endpoints=("namechange", "suspend_d"),
                    rate_limit_sleep_ms=0,
                )
                fetcher = TushareFetcher(client, cfg)
                with self.assertRaises(TushareClientError):
                    fetcher.fetch()
        self.assertEqual(_data_call_count(client), 1)
        self.assertEqual(len(fetcher.holes), 0)
        mock_sleep.assert_not_called()

    def test_param_error_aborts_while_rate_limit_still_holes(self) -> None:
        # Contrast pair on the SAME run shape: a param error aborts, a
        # quota error retries to exhaustion and becomes a hole.
        param_client = _make_client(
            lambda api, **p: (_ for _ in ()).throw(
                self._classified("Exception: 抱歉，参数错误，缺少必要的参数")
            )
        )
        quota_client = _make_client(
            lambda api, **p: (_ for _ in ()).throw(
                self._classified(
                    "Exception: 抱歉，您每分钟最多访问该接口500次，权限的具体详情"
                )
            )
        )
        with patch("src.data.tushare.fetcher.time.sleep"):
            with tempfile.TemporaryDirectory() as tmp:
                cfg = TushareFetcherConfig(
                    output_dir=Path(tmp), endpoints=("namechange",),
                    rate_limit_sleep_ms=0,
                )
                aborting = TushareFetcher(param_client, cfg)
                with self.assertRaises(TushareClientError):
                    aborting.fetch()
            with tempfile.TemporaryDirectory() as tmp:
                cfg = TushareFetcherConfig(
                    output_dir=Path(tmp), endpoints=("namechange",),
                    rate_limit_sleep_ms=0,
                )
                holing = TushareFetcher(quota_client, cfg)
                holing.fetch()  # does NOT raise
        self.assertEqual(_data_call_count(param_client), 1)
        self.assertEqual(len(aborting.holes), 0)
        self.assertGreater(_data_call_count(quota_client), 1)  # retried
        self.assertEqual(len(holing.holes), 1)


class BoundaryYearFreshnessTests(unittest.TestCase):
    """P3-7b max(trade_date) freshness rule for per-(ticker, year) files:
    an existing year file is skipped only when its content reaches everything
    this run's range can expect of it; stale/suspicious files are re-pulled
    (one whole-year API call), and a failed re-pull leaves the old file +
    a hole so the next run re-attempts automatically."""

    TICKER = "600000.SH"

    @staticmethod
    def _seed_universe(tmp_path: Path, *, list_date: str | None = "20000101",
                       delist_date: str | None = None) -> None:
        pd.DataFrame({
            "ts_code": [BoundaryYearFreshnessTests.TICKER],
            "list_date": [list_date],
            "delist_date": [delist_date],
        }).to_parquet(tmp_path / "active_stocks.parquet", index=False)
        pd.DataFrame(
            {"ts_code": [], "list_date": [], "delist_date": []}
        ).to_parquet(tmp_path / "delisted_stocks.parquet", index=False)

    @staticmethod
    def _prefill(tmp_path: Path, year: int, dates: list[str]) -> Path:
        d = tmp_path / "daily" / str(year)
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{BoundaryYearFreshnessTests.TICKER}.parquet"
        pd.DataFrame({
            "ts_code": [BoundaryYearFreshnessTests.TICKER] * len(dates),
            "trade_date": dates,
        }).to_parquet(path, index=False)
        return path

    @staticmethod
    def _client_returning(dates: list[str], trade_cal_dates: list[str] | None = None):
        def side_effect(api, **p):
            return pd.DataFrame({
                "ts_code": [p["ts_code"]] * len(dates),
                "trade_date": list(dates),
            })
        return _make_client(side_effect, trade_cal_dates=trade_cal_dates)

    @staticmethod
    def _cfg(tmp_path: Path, start: str, end: str, **kw) -> TushareFetcherConfig:
        return TushareFetcherConfig(
            output_dir=tmp_path, endpoints=("daily",),
            start_date=start, end_date=end, rate_limit_sleep_ms=0, **kw,
        )

    def test_holiday_year_end_complete_file_not_repulled(self) -> None:
        """阶段1 regression, LOOP-level (guards the calendar→freshness-gate
        wiring): a complete year whose last weekday is a market HOLIDAY
        (2018-12-31 Mon closed; the real last bar is 2018-12-28) must NOT trip
        the gate. With the trading-day floor the prefilled complete file is fresh
        → 0 re-pulls, no systemic-shortfall hole. Reverting the calendar wiring
        (or the old weekday floor) expects Dec 31 → re-pulls the complete file
        → this test fails. This is the exact failure that blocked the supervised
        run, exercised through fetch() rather than the pure boundary helper."""
        # 2018 trading days, minus the 2018-12-31 (Mon) holiday.
        cal = [
            d for d in _all_weekday_cal_df("20180101", "20181231")["cal_date"].tolist()
            if d != "20181231"
        ]
        client = self._client_returning(["20181228"], trade_cal_dates=cal)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._seed_universe(tmp_path)
            # Complete 2018 file ending on the real last trading day (Dec 28).
            self._prefill(tmp_path, 2018, ["20180102", "20181228"])
            fetcher = TushareFetcher(client, self._cfg(tmp_path, "20180101", "20181231"))
            fetcher.fetch()
            self.assertEqual(_data_call_count(client), 0)  # complete → not re-pulled
            self.assertEqual(fetcher.holes, ())            # no false systemic-shortfall

    def test_holiday_only_slice_preserves_complete_year_file(self) -> None:
        """Codex P1 (#270): re-running a HOLIDAY-ONLY slice (e.g. --start 20181231
        --end 20181231, a market holiday) against a dir with a COMPLETE 2018 file
        must NOT overwrite it with an empty pull. The slice has no trading day so
        ``_expected_year_file_end`` is None — but the listing window COVERS the
        slice (≠ a pre-listing window-miss), so no empty-placeholder claim applies
        and the existing file (real data for the wider year) is preserved.
        Reverting the window-covers branch routes this through the placeholder
        path: the non-empty file is judged a dirty placeholder, re-pulled, and
        clobbered to empty → 20181228 lost → this test fails."""
        # The single requested day is a holiday → trade_cal(is_open=1) is empty
        # → _get_trading_days() == () → no trading-day boundary for the slice.
        client = self._client_returning([], trade_cal_dates=[])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._seed_universe(tmp_path)  # listed 20000101 → window covers 2018
            path = self._prefill(tmp_path, 2018, ["20180102", "20181228"])
            fetcher = TushareFetcher(
                client, self._cfg(tmp_path, "20181231", "20181231")
            )
            fetcher.fetch()
            self.assertEqual(_data_call_count(client), 0)  # NOT re-pulled
            self.assertEqual(fetcher.holes, ())            # no spurious hole
            df = pd.read_parquet(path)
            self.assertIn("20181228", set(df["trade_date"].astype(str)))  # preserved

    def test_forced_retry_holiday_slice_preserves_complete_year_file(self) -> None:
        """Codex P1 round 5 (#270): the FORCED-retry path (a prior-manifest hole
        turned into a force_retry_unit by 01_fetch_tushare) bypasses the
        ``path.exists() and not force_retry`` freshness scan entirely. A
        holiday-only re-run of such a unit against a COMPLETE file would fetch
        the (empty) holiday range and clobber it — the spurious hole self-heals
        on the next real-range run, so preserve the existing file instead.
        Reverting the forced-path preserve guard re-pulls and clobbers → fails."""
        unit = f"ts_code={self.TICKER} year=2018"
        client = self._client_returning([], trade_cal_dates=[])  # holiday → empty cal
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._seed_universe(tmp_path)  # listed 20000101 → window covers 2018
            path = self._prefill(tmp_path, 2018, ["20180102", "20181228"])
            fetcher = TushareFetcher(
                client,
                self._cfg(
                    tmp_path, "20181231", "20181231",
                    force_retry_units=frozenset({("daily", unit)}),
                ),
            )
            fetcher.fetch()
            self.assertEqual(_data_call_count(client), 0)  # forced unit NOT re-pulled
            df = pd.read_parquet(path)
            self.assertIn("20181228", set(df["trade_date"].astype(str)))  # preserved

    def test_partial_year_file_backfilled_when_range_extends(self) -> None:
        # 半截年文件 + 扩 end_date → 补全 (the original freeze bug).
        client = self._client_returning(["20250630", "20251231"])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._seed_universe(tmp_path)
            path = self._prefill(tmp_path, 2025, ["20250102", "20250630"])
            TushareFetcher(client, self._cfg(tmp_path, "20250101", "20251231")).fetch()
            self.assertEqual(_data_call_count(client), 1)  # whole year, one call
            df = pd.read_parquet(path)
            self.assertEqual(str(df["trade_date"].max()), "20251231")

    def test_complete_boundary_file_skipped(self) -> None:
        # 已完整边界文件 → 跳过 (crash 重跑的 resume 价值). The skip is a
        # VERIFIED one (codex P2 on #240): it must establish coverage, so the
        # first sweep over an already-complete dump does not record an empty
        # manifest that downstream gates reject.
        client = self._client_returning(["20251231"])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._seed_universe(tmp_path)
            self._prefill(tmp_path, 2025, ["20250102", "20251231"])
            results = TushareFetcher(
                client, self._cfg(tmp_path, "20250101", "20251231"),
            ).fetch()
            self.assertEqual(_data_call_count(client), 0)
            self.assertEqual(results[0].skipped, 1)
            self.assertEqual(results[0].units_verified, 1)

    def test_backfill_scans_years_before_prior_coverage_start(self) -> None:
        # codex P1 on #240: with a prior manifest attesting [2020, 2025], a
        # backward backfill to 2018 must SCAN 2018/2019 (outside the attested
        # range) — an end-only watermark would blind-skip them and the merge
        # would then over-claim stale pre-coverage files.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._seed_universe(tmp_path)
            self._prefill(tmp_path, 2018, ["20180102", "20180630"])  # stale!
            complete = {
                2019: "20191231", 2020: "20201231", 2021: "20211231",
                2022: "20221230", 2023: "20231229", 2024: "20241231",
                2025: "20251231",
            }
            for year, floor in complete.items():
                self._prefill(tmp_path, year, [f"{year}0102", floor])
            cfg = self._cfg(
                tmp_path, "20180101", "20251231",
                assume_verified_ranges={"daily": ("20200101", "20251231")},
            )
            client = self._client_returning(["20180102", "20181231"])
            results = TushareFetcher(client, cfg).fetch()
            # Only stale 2018 re-pulled; 2019 (pre-coverage) and 2025 (final
            # year) scanned-and-verified; 2020-2024 blind-skipped on the
            # watermark.
            called_years = {c.kwargs["start_date"][:4]
                            for c in client.call.call_args_list
                            if not (c.args and c.args[0] == "trade_cal")}
            self.assertEqual(called_years, {"2018"})
            self.assertEqual(results[0].files_written, 1)
            self.assertEqual(results[0].units_verified, 2)  # 2019 + 2025
            self.assertEqual(results[0].skipped, 7)  # all non-2018 years

    def test_refetch_failure_keeps_old_file_and_hole_then_retries(self) -> None:
        # 刷新失败 → 旧文件保留 + 记洞；max(trade_date) 规则保证下轮自动重试.
        client = _make_client(
            lambda api, **p: (_ for _ in ()).throw(
                TushareClientError("returned None — rate limit exceeded")
            )
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._seed_universe(tmp_path)
            path = self._prefill(tmp_path, 2025, ["20250102", "20250630"])
            before = path.read_bytes()
            with patch("src.data.tushare.fetcher.time.sleep"):
                fetcher = TushareFetcher(
                    client, self._cfg(tmp_path, "20250101", "20251231"),
                )
                fetcher.fetch()  # exhausts retries → hole, no raise
            self.assertEqual(len(fetcher.holes), 1)
            self.assertEqual(path.read_bytes(), before)  # old file untouched
            first_run_calls = _data_call_count(client)
            self.assertGreater(first_run_calls, 0)
            # Next run: the file is STILL stale → re-attempted automatically,
            # no force-retry bookkeeping needed.
            with patch("src.data.tushare.fetcher.time.sleep"):
                TushareFetcher(
                    client, self._cfg(tmp_path, "20250101", "20251231"),
                ).fetch()
            self.assertGreater(_data_call_count(client), first_run_calls)

    def test_next_day_run_fetches_new_day(self) -> None:
        # “明天再跑”：今天建 2026 文件，明天 end_date+1 → 必须抓到新一天.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._seed_universe(tmp_path)
            day1 = self._client_returning(["20260610"])
            TushareFetcher(day1, self._cfg(tmp_path, "20260101", "20260610")).fetch()
            self.assertEqual(_data_call_count(day1), 1)
            path = tmp_path / "daily" / "2026" / f"{self.TICKER}.parquet"
            self.assertEqual(str(pd.read_parquet(path)["trade_date"].max()), "20260610")
            # Tomorrow (2026-06-11, a Thursday): the file stops one day short.
            day2 = self._client_returning(["20260610", "20260611"])
            TushareFetcher(day2, self._cfg(tmp_path, "20260101", "20260611")).fetch()
            self.assertEqual(_data_call_count(day2), 1)
            self.assertEqual(str(pd.read_parquet(path)["trade_date"].max()), "20260611")
            # Same-day re-run after success: now current → skipped.
            day3 = self._client_returning(["20260611"])
            TushareFetcher(day3, self._cfg(tmp_path, "20260101", "20260611")).fetch()
            self.assertEqual(_data_call_count(day3), 0)

    def test_weekend_end_does_not_repull_friday_complete_file(self) -> None:
        # end on Sunday 2026-06-14 floors to Friday 2026-06-12.
        client = self._client_returning(["20260612"])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._seed_universe(tmp_path)
            self._prefill(tmp_path, 2026, ["20260612"])
            TushareFetcher(client, self._cfg(tmp_path, "20260101", "20260614")).fetch()
            self.assertEqual(_data_call_count(client), 0)

    def test_listing_window_bounds_expectation(self) -> None:
        # Delisted mid-year: a file ending at the delist date is complete —
        # not re-pulled against the year end. (2025-03-10 is a Monday.)
        client = self._client_returning(["20250310"])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._seed_universe(tmp_path, delist_date="20250310")
            self._prefill(tmp_path, 2025, ["20250102", "20250310"])
            TushareFetcher(client, self._cfg(tmp_path, "20250101", "20251231")).fetch()
            self.assertEqual(_data_call_count(client), 0)

    def test_pre_listing_year_empty_placeholder_skipped(self) -> None:
        # Listed 2024: the 2020 empty placeholder is the truthful content.
        client = self._client_returning([])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._seed_universe(tmp_path, list_date="20240115")
            self._prefill(tmp_path, 2020, [])
            TushareFetcher(client, self._cfg(tmp_path, "20200101", "20201231")).fetch()
            self.assertEqual(_data_call_count(client), 0)

    def test_still_short_refetch_warns_loud_but_does_not_hole(self) -> None:
        # codex P1 round 3 on #240: a re-pulled year whose fresh full-year
        # frame STILL ends before the expected boundary (ticker suspended
        # through the slice end / pre-close daily run / delist gap) is the
        # vendor's complete answer: it is written and made LOUD, but never
        # holed — holing would permanently false-positive the build gate for
        # data that does not exist.
        client = self._client_returning(["20250102", "20250630"])  # still short
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._seed_universe(tmp_path)
            self._prefill(tmp_path, 2025, ["20250102", "20250630"])  # stale
            fetcher = TushareFetcher(
                client, self._cfg(tmp_path, "20250101", "20251231"),
            )
            with self.assertLogs("src.data.tushare.fetcher", level="WARNING") as logs:
                results = fetcher.fetch()
        self.assertEqual(_data_call_count(client), 1)  # re-pulled once
        self.assertEqual(results[0].files_written, 1)  # freshest vendor truth
        self.assertEqual(len(fetcher.holes), 0)  # NOT a hole
        self.assertEqual(results[0].units_verified, 0)  # and NOT "verified"
        self.assertTrue(
            any("STILL end before" in line for line in logs.output),
            f"expected the still-short warning in {logs.output}",
        )

    @staticmethod
    def _seed_many(tmp_path: Path, tickers: list[str]) -> None:
        pd.DataFrame({
            "ts_code": tickers,
            "list_date": ["20000101"] * len(tickers),
            "delist_date": [None] * len(tickers),
        }).to_parquet(tmp_path / "active_stocks.parquet", index=False)
        pd.DataFrame(
            {"ts_code": [], "list_date": [], "delist_date": []}
        ).to_parquet(tmp_path / "delisted_stocks.parquet", index=False)

    def test_systemic_shortfall_records_endpoint_hole(self) -> None:
        # Round 5 (拍死选项1): a LARGE share of re-checked re-pulls still
        # short (pre-close run / vendor truncation) is an ENDPOINT hole — the
        # run exits 3 and the build gate refuses — not a warning.
        from src.data.tushare.fetcher import SYSTEMIC_SHORTFALL_MIN_CHECKED
        n = SYSTEMIC_SHORTFALL_MIN_CHECKED + 10
        tickers = [f"{600000 + k}.SH" for k in range(n)]
        client = self._client_returning(["20250102", "20250630"])  # all short
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._seed_many(tmp_path, tickers)
            year_dir = tmp_path / "daily" / "2025"
            year_dir.mkdir(parents=True)
            for tk in tickers:  # every file stale → every unit re-pulled
                pd.DataFrame(
                    {"ts_code": [tk], "trade_date": ["20250630"]}
                ).to_parquet(year_dir / f"{tk}.parquet", index=False)
            fetcher = TushareFetcher(
                client, self._cfg(tmp_path, "20250101", "20251231"),
            )
            results = fetcher.fetch()
        self.assertEqual(results[0].files_written, n)  # data still written
        self.assertEqual(len(fetcher.holes), 1)  # ONE endpoint-level hole
        hole = fetcher.holes[0]
        self.assertEqual(hole.reason_class, "systemic_shortfall")
        self.assertEqual(hole.unit, "systemic-shortfall")
        self.assertIn(f"{n}/{n}", hole.last_error)

    def test_preclose_first_run_trips_systemic_gate(self) -> None:
        # codex P1 round 7: MISSING files fetched fresh get the same
        # post-write re-check — a pre-close FIRST run (no files on disk yet,
        # vendor returns bars only through yesterday) records the systemic
        # endpoint hole instead of a complete manifest through today.
        from src.data.tushare.fetcher import SYSTEMIC_SHORTFALL_MIN_CHECKED
        n = SYSTEMIC_SHORTFALL_MIN_CHECKED + 10
        tickers = [f"{600000 + k}.SH" for k in range(n)]
        # end = "today" 2025-06-11 (a Wednesday); frames stop at yesterday.
        client = self._client_returning(["20250102", "20250610"])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._seed_many(tmp_path, tickers)
            fetcher = TushareFetcher(
                client, self._cfg(tmp_path, "20250101", "20250611"),
            )
            results = fetcher.fetch()
        self.assertEqual(results[0].files_written, n)
        self.assertEqual(len(fetcher.holes), 1)
        self.assertEqual(fetcher.holes[0].reason_class, "systemic_shortfall")
        self.assertEqual(fetcher.holes[0].unit, "systemic-shortfall")

    def test_idiosyncratic_shortfall_stays_warning_below_ratio(self) -> None:
        # A handful of suspended-through-year-end tickers among many healthy
        # re-pulls stays BELOW the systemic ratio: loud warning, zero holes,
        # the daily pipeline keeps flowing (阶段1 red line).
        from src.data.tushare.fetcher import SYSTEMIC_SHORTFALL_MIN_CHECKED
        n = SYSTEMIC_SHORTFALL_MIN_CHECKED + 10
        tickers = [f"{600000 + k}.SH" for k in range(n)]
        suspended = set(tickers[:5])  # ~8% < 20%

        def side_effect(api, **p):
            dates = (
                ["20250102", "20250630"] if p["ts_code"] in suspended
                else ["20250102", "20251231"]
            )
            return pd.DataFrame({
                "ts_code": [p["ts_code"]] * len(dates), "trade_date": dates,
            })

        client = _make_client(side_effect)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._seed_many(tmp_path, tickers)
            year_dir = tmp_path / "daily" / "2025"
            year_dir.mkdir(parents=True)
            for tk in tickers:
                pd.DataFrame(
                    {"ts_code": [tk], "trade_date": ["20250630"]}
                ).to_parquet(year_dir / f"{tk}.parquet", index=False)
            fetcher = TushareFetcher(
                client, self._cfg(tmp_path, "20250101", "20251231"),
            )
            with self.assertLogs("src.data.tushare.fetcher", level="WARNING") as logs:
                fetcher.fetch()
        self.assertEqual(len(fetcher.holes), 0)  # NOT systemic → no hole
        self.assertTrue(
            any("STILL end before" in line for line in logs.output),
            f"expected the idiosyncratic warning in {logs.output}",
        )

    def test_force_retried_still_short_file_also_warns(self) -> None:
        # codex P1 round 4 on #240: a force-retried EXISTING file (prior-
        # manifest hole) bypasses the freshness branch, but its successful
        # retry gets the SAME post-write re-check — a still-short frame
        # surfaces in the aggregate warning instead of silently clearing its
        # hole with no trace.
        client = self._client_returning(["20250102", "20250630"])  # still short
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._seed_universe(tmp_path)
            self._prefill(tmp_path, 2025, ["20250102", "20250630"])
            cfg = self._cfg(
                tmp_path, "20250101", "20251231",
                force_retry_units=frozenset(
                    {("daily", f"ts_code={self.TICKER} year=2025")},
                ),
            )
            fetcher = TushareFetcher(client, cfg)
            with self.assertLogs("src.data.tushare.fetcher", level="WARNING") as logs:
                results = fetcher.fetch()
        self.assertEqual(_data_call_count(client), 1)  # force-retried once
        self.assertEqual(results[0].files_written, 1)
        self.assertEqual(len(fetcher.holes), 0)  # retry succeeded — no hole
        self.assertTrue(
            any("STILL end before" in line for line in logs.output),
            f"expected the still-short warning in {logs.output}",
        )

    def test_dirty_no_data_placeholder_repulled_not_verified(self) -> None:
        # codex P2 round 2 on #240: an expected-no-data placeholder is only
        # "verified" when it is a READABLE parquet with ZERO rows. A corrupt
        # blob — or a file holding rows the listing window says cannot exist —
        # is re-pulled and rewritten as a clean empty placeholder.
        client = self._client_returning([])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._seed_universe(tmp_path, list_date="20240115")  # listed 2024
            year_dir = tmp_path / "daily" / "2020"
            year_dir.mkdir(parents=True)
            path = year_dir / f"{self.TICKER}.parquet"
            path.write_bytes(b"corrupt blob")  # pre-listing year, dirty file
            results = TushareFetcher(
                client, self._cfg(tmp_path, "20200101", "20201231"),
            ).fetch()
            self.assertEqual(_data_call_count(client), 1)  # re-pulled
            self.assertEqual(len(pd.read_parquet(path)), 0)  # clean now
            self.assertEqual(results[0].units_verified, 0)
        # Rows where the window says none can exist → same treatment.
        client2 = self._client_returning([])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._seed_universe(tmp_path, list_date="20240115")
            path = self._prefill(tmp_path, 2020, ["20200315"])
            TushareFetcher(
                client2, self._cfg(tmp_path, "20200101", "20201231"),
            ).fetch()
            self.assertEqual(_data_call_count(client2), 1)
            self.assertEqual(len(pd.read_parquet(path)), 0)

    def test_empty_final_year_placeholder_repulled_when_listing_intersects(self) -> None:
        # Empty placeholder written before a mid-year listing started trading:
        # data is now possible → re-pull.
        client = self._client_returning(["20250701"])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._seed_universe(tmp_path, list_date="20250601")
            path = self._prefill(tmp_path, 2025, [])
            TushareFetcher(client, self._cfg(tmp_path, "20250101", "20251231")).fetch()
            self.assertEqual(_data_call_count(client), 1)
            self.assertEqual(len(pd.read_parquet(path)), 1)

    def test_watermark_skips_past_year_scan_unless_verify_all(self) -> None:
        # A past year attested by the prior manifest's coverage watermark is
        # not re-scanned (its stale content is deliberately trusted);
        # --verify-all-years forces the sweep and finds it.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._seed_universe(tmp_path)
            self._prefill(tmp_path, 2024, ["20240102", "20240630"])  # stale!
            self._prefill(tmp_path, 2025, ["20250102", "20251231"])  # complete
            watermarked = self._cfg(
                tmp_path, "20240101", "20251231",
                assume_verified_ranges={"daily": ("20240101", "20251231")},
            )
            client = self._client_returning(["20241231"])
            TushareFetcher(client, watermarked).fetch()
            self.assertEqual(_data_call_count(client), 0)  # 2024 not scanned
            sweeping = self._cfg(
                tmp_path, "20240101", "20251231",
                assume_verified_ranges={"daily": ("20240101", "20251231")},
                verify_all_years=True,
            )
            client2 = self._client_returning(["20240102", "20241231"])
            TushareFetcher(client2, sweeping).fetch()
            self.assertEqual(_data_call_count(client2), 1)  # 2024 re-pulled
            data_calls = [c for c in client2.call.call_args_list
                          if not (c.args and c.args[0] == "trade_cal")]
            self.assertEqual(data_calls[0].kwargs["start_date"][:4], "2024")


class TradingDayFloorTests(unittest.TestCase):
    """Freshness boundary floors to the actual last TRADING day, not the last
    weekday — fixes the year-end-holiday false systemic-shortfall surfaced by
    the supervised 阶段1 run: 2018-12-31 (Mon) was a market holiday, so the real
    last 2018 bar is 2018-12-28; the weekday floor expected Dec 31 and flagged
    every complete 2018 file as short, tripping the gate for a year that can
    never produce a later bar."""

    # 2018 calendar whose last weekday (Dec 31, Mon) is a HOLIDAY: the real last
    # trading day is Dec 28 (Fri). Sorted (bisect requires it).
    _CAL_2018 = ("20181226", "20181227", "20181228")

    def test_last_trading_day_uses_calendar(self) -> None:
        self.assertEqual(
            _last_trading_day_on_or_before("20181231", self._CAL_2018), "20181228"
        )
        self.assertEqual(
            _last_trading_day_on_or_before("20181228", self._CAL_2018), "20181228"
        )
        self.assertIsNone(
            _last_trading_day_on_or_before("20180101", self._CAL_2018)
        )

    def test_none_calendar_falls_back_to_weekday(self) -> None:
        # No calendar → the documented weekday-floor degradation.
        self.assertEqual(
            _last_trading_day_on_or_before("20181231", None),
            _last_weekday_str("20181231"),
        )
        self.assertEqual(_last_trading_day_on_or_before("20181231", None), "20181231")

    def test_expected_year_end_holiday_aware(self) -> None:
        # THE FIX: a full 2018 slice expects the real last trading day (Dec 28),
        # not the last weekday (Dec 31, a holiday).
        expected = _expected_year_file_end(
            year_start="20180101", year_end="20181231",
            window=(None, None), trading_days=self._CAL_2018,
        )
        self.assertEqual(expected, "20181228")

    def test_expected_year_end_without_calendar_is_weekday(self) -> None:
        # Fallback path documents the legacy (buggy) weekday floor = Dec 31.
        expected = _expected_year_file_end(
            year_start="20180101", year_end="20181231",
            window=(None, None), trading_days=None,
        )
        self.assertEqual(expected, "20181231")

    def test_expected_year_end_delist_cap_floored_to_trading_day(self) -> None:
        expected = _expected_year_file_end(
            year_start="20180101", year_end="20181231",
            window=(None, "20181231"), trading_days=self._CAL_2018,
        )
        self.assertEqual(expected, "20181228")

    def test_malformed_calendar_degrades_whole_to_weekday(self) -> None:
        """Codex P1: a trade_cal with ANY malformed cal_date must degrade the
        WHOLE calendar (_get_trading_days → None → weekday fallback), not become
        a PARTIAL calendar that could be missing a slice's real last trading day,
        under-expect the boundary, and silently skip a stale file."""
        def _cfg(tmp):
            return TushareFetcherConfig(
                output_dir=Path(tmp), endpoints=("daily",),
                start_date="20180101", end_date="20181231", rate_limit_sleep_ms=0,
            )
        # Malformed (a NaN row) → reject the whole calendar.
        bad = _make_client(lambda api, **p: pd.DataFrame(),
                           trade_cal_dates=["20180102", "nan", "20180103"])
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(TushareFetcher(bad, _cfg(tmp))._get_trading_days())
        # Control: a well-formed calendar is accepted (sorted, deduped).
        good = _make_client(lambda api, **p: pd.DataFrame(),
                            trade_cal_dates=["20180103", "20180102", "20180102"])
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                TushareFetcher(good, _cfg(tmp))._get_trading_days(),
                ("20180102", "20180103"),
            )

    def test_empty_calendar_is_valid_no_boundary(self) -> None:
        """Codex P2: an EMPTY trade_cal (a no-trading-day slice, e.g. a range
        that is entirely a holiday) is a VALID empty calendar (()), distinct
        from unavailable (None). An empty calendar yields NO boundary → nothing
        expected, no re-pull, no false shortfall — not a weekday fallback that
        would expect the holiday itself."""
        # Empty calendar → no last trading day → no expected boundary.
        self.assertIsNone(_last_trading_day_on_or_before("20181231", ()))
        self.assertIsNone(_expected_year_file_end(
            year_start="20181231", year_end="20181231",
            window=(None, None), trading_days=(),
        ))
        # _get_trading_days returns () (NOT None) for an empty trade_cal result.
        client = _make_client(lambda api, **p: pd.DataFrame(), trade_cal_dates=[])
        with tempfile.TemporaryDirectory() as tmp:
            cfg = TushareFetcherConfig(
                output_dir=Path(tmp), endpoints=("daily",),
                start_date="20181231", end_date="20181231", rate_limit_sleep_ms=0,
            )
            self.assertEqual(TushareFetcher(client, cfg)._get_trading_days(), ())


if __name__ == "__main__":
    unittest.main()
