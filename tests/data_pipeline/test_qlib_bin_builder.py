"""Tests for ``src.data.pit.qlib_bin_builder.QlibBinBuilder``.

Synthetic Tushare-style parquets in tempdirs; build bins; read them
back and assert: NaN-after-delist for delisted tickers, valid data
for active tickers, adj_factor multiplication, volume / money unit
conversion.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.pit.qlib_bin_builder import (  # noqa: E402
    BIN_FEATURE_FIELDS,
    TUSHARE_AMOUNT_KYUAN_TO_YUAN,
    TUSHARE_VOL_LOTS_TO_SHARES,
    QlibBinBuilder,
    QlibBinBuilderError,
)


def _write_active(path: Path, tickers: list[str]) -> None:
    df = pd.DataFrame({
        "ts_code": tickers,
        "list_date": ["20100101"] * len(tickers),
        "list_status": ["L"] * len(tickers),
    })
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)


def _write_registry(path: Path, rows: list[dict]) -> None:
    if rows:
        df = pd.DataFrame(rows)
        df["list_date"] = pd.to_datetime(df["list_date"])
        df["delist_date"] = pd.to_datetime(df["delist_date"])
    else:
        df = pd.DataFrame({
            "ticker": pd.Series([], dtype=str),
            "list_date": pd.Series([], dtype="datetime64[ns]"),
            "delist_date": pd.Series([], dtype="datetime64[ns]"),
            "last_company_name": pd.Series([], dtype=str),
            "delist_reason": pd.Series([], dtype=str),
        })
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)


def _write_daily_year(tushare_dir: Path, year: int, ts_code: str,
                     trade_dates: list[str], close: float = 10.0) -> None:
    df = pd.DataFrame({
        "ts_code": [ts_code] * len(trade_dates),
        "trade_date": trade_dates,
        "open": [close] * len(trade_dates),
        "high": [close * 1.05] * len(trade_dates),
        "low": [close * 0.95] * len(trade_dates),
        "close": [close] * len(trade_dates),
        "vol": [1000.0] * len(trade_dates),
        "amount": [10000.0] * len(trade_dates),
    })
    path = tushare_dir / "daily" / str(year) / f"{ts_code}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)


def _write_adj_factor_year(tushare_dir: Path, year: int, ts_code: str,
                          trade_dates: list[str], factor: float = 1.0) -> None:
    df = pd.DataFrame({
        "ts_code": [ts_code] * len(trade_dates),
        "trade_date": trade_dates,
        "adj_factor": [factor] * len(trade_dates),
    })
    path = tushare_dir / "adj_factor" / str(year) / f"{ts_code}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)


def _read_bin_values(provider_dir: Path, qlib_ticker: str, field: str,
                    calendar: list[str]) -> tuple[int, np.ndarray]:
    """Read a .day.bin and return ``(start_index, values_aligned_to_calendar)``.

    The returned array has length ``len(calendar)``; positions before the
    ticker's run are NaN, and positions after are NaN (since the ticker's
    bin only covers its observed window).
    """
    raw = np.fromfile(
        provider_dir / "features" / qlib_ticker.lower() / f"{field}.day.bin",
        dtype="<f4",
    )
    start = int(raw[0])
    payload = raw[1:]
    out = np.full(len(calendar), np.nan, dtype="float32")
    out[start: start + len(payload)] = payload
    return start, out


class HappyPathTests(unittest.TestCase):

    def test_active_ticker_full_range(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_active(tmp_path / "active_stocks.parquet", ["600519.SH"])
            _write_registry(tmp_path / "registry.parquet", [])
            dates = ["20200102", "20200103", "20200106"]
            _write_daily_year(tmp_path, 2020, "600519.SH", dates, close=100.0)
            _write_adj_factor_year(tmp_path, 2020, "600519.SH", dates, factor=2.0)

            out = tmp_path / "provider"
            QlibBinBuilder(
                tushare_dir=tmp_path,
                delisted_registry_path=tmp_path / "registry.parquet",
                output_dir=out,
            ).build()

            cal = (out / "calendars" / "day.txt").read_text(encoding="utf-8").splitlines()
            self.assertEqual(cal, ["2020-01-02", "2020-01-03", "2020-01-06"])

            _, close_values = _read_bin_values(out, "SH600519", "close", cal)
            # close * adj_factor = 100 * 2 = 200
            self.assertTrue(np.allclose(close_values, [200.0, 200.0, 200.0]))

            _, vol = _read_bin_values(out, "SH600519", "volume", cal)
            # vol (lots) * 100 = 1000 * 100 = 100_000 shares
            self.assertTrue(np.allclose(
                vol, [1000.0 * TUSHARE_VOL_LOTS_TO_SHARES] * 3))

            _, money = _read_bin_values(out, "SH600519", "money", cal)
            # amount (kyuan) * 1000 = 10000 * 1000 = 10_000_000 yuan
            self.assertTrue(np.allclose(
                money, [10000.0 * TUSHARE_AMOUNT_KYUAN_TO_YUAN] * 3))

    def test_delisted_ticker_nan_after_delist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_active(tmp_path / "active_stocks.parquet", ["600519.SH"])
            _write_registry(tmp_path / "registry.parquet", [
                {"ticker": "SH600087", "list_date": "2010-01-01",
                 "delist_date": "2020-01-03"},
            ])
            # Active ticker spans the whole calendar
            active_dates = ["20200102", "20200103", "20200106", "20200107"]
            _write_daily_year(tmp_path, 2020, "600519.SH", active_dates, close=100.0)
            # Delisted ticker has Tushare data for all 4 days (e.g. someone
            # forgot to clip) — the builder MUST clip to delist_date.
            _write_daily_year(tmp_path, 2020, "600087.SH", active_dates, close=50.0)

            out = tmp_path / "provider"
            QlibBinBuilder(
                tushare_dir=tmp_path,
                delisted_registry_path=tmp_path / "registry.parquet",
                output_dir=out,
            ).build()

            cal = (out / "calendars" / "day.txt").read_text(encoding="utf-8").splitlines()
            self.assertEqual(cal, ["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"])

            start, close = _read_bin_values(out, "SH600087", "close", cal)
            # delist_date = 2020-01-03; valid on 01-02 and 01-03, NaN after.
            # close has no adjustment (no adj_factor file written), so raw value.
            self.assertFalse(np.isnan(close[0]))  # 2020-01-02
            self.assertFalse(np.isnan(close[1]))  # 2020-01-03 = delist_date
            self.assertTrue(np.isnan(close[2]))   # 2020-01-06 (after delist)
            self.assertTrue(np.isnan(close[3]))   # 2020-01-07

            # Regression for Phase B smoke finding: the bin MUST extend
            # from start_idx to the LAST calendar position (so qlib reads
            # NaN, not "out of range") for delisted tickers.
            raw = np.fromfile(
                out / "features" / "sh600087" / "close.day.bin",
                dtype="<f4",
            )
            recorded_start = int(raw[0])
            payload_len = len(raw) - 1
            self.assertEqual(recorded_start + payload_len, len(cal),
                             "bin must extend through last calendar date")

    def test_active_ticker_no_nan_in_window(self) -> None:
        """Active tickers MUST NOT get any NaN padding inside their
        observed range (regression for a hypothetical bug where delist
        clipping fires for active tickers too)."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_active(tmp_path / "active_stocks.parquet", ["600519.SH"])
            _write_registry(tmp_path / "registry.parquet", [])
            dates = ["20200102", "20200103", "20200106"]
            _write_daily_year(tmp_path, 2020, "600519.SH", dates, close=100.0)

            out = tmp_path / "provider"
            QlibBinBuilder(
                tushare_dir=tmp_path,
                delisted_registry_path=tmp_path / "registry.parquet",
                output_dir=out,
            ).build()

            cal = (out / "calendars" / "day.txt").read_text(encoding="utf-8").splitlines()
            start, close = _read_bin_values(out, "SH600519", "close", cal)
            valid = close[start: start + 3]
            self.assertFalse(np.isnan(valid).any())

    def test_no_adj_factor_falls_back_to_raw_prices(self) -> None:
        """When no adj_factor parquet exists for a ticker, the builder
        defaults to factor=1.0 and writes raw close prices unchanged."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_active(tmp_path / "active_stocks.parquet", ["600519.SH"])
            _write_registry(tmp_path / "registry.parquet", [])
            dates = ["20200102"]
            _write_daily_year(tmp_path, 2020, "600519.SH", dates, close=42.0)
            # No adj_factor parquet written

            out = tmp_path / "provider"
            QlibBinBuilder(
                tushare_dir=tmp_path,
                delisted_registry_path=tmp_path / "registry.parquet",
                output_dir=out,
            ).build()

            cal = (out / "calendars" / "day.txt").read_text(encoding="utf-8").splitlines()
            _, close = _read_bin_values(out, "SH600519", "close", cal)
            # Raw close, no adjustment
            self.assertAlmostEqual(float(close[0]), 42.0, places=3)

    def test_multi_year_concat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_active(tmp_path / "active_stocks.parquet", ["600519.SH"])
            _write_registry(tmp_path / "registry.parquet", [])
            _write_daily_year(tmp_path, 2019, "600519.SH",
                              ["20191230", "20191231"], close=10.0)
            _write_daily_year(tmp_path, 2020, "600519.SH",
                              ["20200102", "20200103"], close=20.0)

            out = tmp_path / "provider"
            QlibBinBuilder(
                tushare_dir=tmp_path,
                delisted_registry_path=tmp_path / "registry.parquet",
                output_dir=out,
            ).build()

            cal = (out / "calendars" / "day.txt").read_text(encoding="utf-8").splitlines()
            self.assertEqual(cal, [
                "2019-12-30", "2019-12-31", "2020-01-02", "2020-01-03",
            ])


class InstrumentsAllTests(unittest.TestCase):
    """Codex P1 on PR #103: the atomic swap MUST emit
    ``instruments/all.txt`` so a normal ``04 -> 05 -> 06`` flow does
    not lose Phase B.1's universe file."""

    def test_staging_includes_instruments_all_txt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_active(tmp_path / "active_stocks.parquet", ["600519.SH"])
            _write_registry(tmp_path / "registry.parquet", [
                {"ticker": "SH600087", "list_date": "2010-01-01",
                 "delist_date": "2014-06-05"},
            ])
            _write_daily_year(tmp_path, 2020, "600519.SH", ["20200102"], close=10.0)
            _write_daily_year(tmp_path, 2014, "600087.SH", ["20140604"], close=5.0)

            out = tmp_path / "provider"
            QlibBinBuilder(
                tushare_dir=tmp_path,
                delisted_registry_path=tmp_path / "registry.parquet",
                output_dir=out,
            ).build()

            inst = out / "instruments" / "all.txt"
            self.assertTrue(inst.exists(),
                            "B.2 must emit instruments/all.txt in its staging")
            lines = inst.read_text(encoding="utf-8").splitlines()
            tickers = {L.split("\t")[0] for L in lines}
            self.assertIn("SH600519", tickers)
            self.assertIn("SH600087", tickers)
            sh087_line = next(L for L in lines if L.startswith("SH600087"))
            self.assertEqual(sh087_line.split("\t")[2], "2014-06-05")
            sh519_line = next(L for L in lines if L.startswith("SH600519"))
            self.assertEqual(sh519_line.split("\t")[2], "2099-12-31")


class FailureTests(unittest.TestCase):

    def test_missing_active_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_registry(tmp_path / "registry.parquet", [])
            with self.assertRaisesRegex(QlibBinBuilderError, "active_stocks"):
                QlibBinBuilder(
                    tushare_dir=tmp_path,
                    delisted_registry_path=tmp_path / "registry.parquet",
                    output_dir=tmp_path / "provider",
                ).build()

    def test_missing_registry_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_active(tmp_path / "active_stocks.parquet", ["600519.SH"])
            with self.assertRaisesRegex(QlibBinBuilderError, "delisted|registry"):
                QlibBinBuilder(
                    tushare_dir=tmp_path,
                    delisted_registry_path=tmp_path / "absent.parquet",
                    output_dir=tmp_path / "provider",
                ).build()

    def test_no_daily_data_anywhere_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_active(tmp_path / "active_stocks.parquet", ["600519.SH"])
            _write_registry(tmp_path / "registry.parquet", [])
            # No daily/ dir at all
            with self.assertRaisesRegex(QlibBinBuilderError,
                                        "daily|No ticker produced"):
                QlibBinBuilder(
                    tushare_dir=tmp_path,
                    delisted_registry_path=tmp_path / "registry.parquet",
                    output_dir=tmp_path / "provider",
                ).build()


class AtomicWriteTests(unittest.TestCase):

    def test_no_staging_dir_left_after_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_active(tmp_path / "active_stocks.parquet", ["600519.SH"])
            _write_registry(tmp_path / "registry.parquet", [])
            _write_daily_year(tmp_path, 2020, "600519.SH", ["20200102"], close=10.0)

            out = tmp_path / "provider"
            QlibBinBuilder(
                tushare_dir=tmp_path,
                delisted_registry_path=tmp_path / "registry.parquet",
                output_dir=out,
            ).build()
            # No .tmp or .bak siblings should remain
            siblings = [p for p in out.parent.iterdir()
                       if p.name.startswith(".") and "provider" in p.name]
            self.assertEqual(siblings, [])


class ConstantsTests(unittest.TestCase):

    def test_feature_fields(self) -> None:
        self.assertEqual(BIN_FEATURE_FIELDS,
                        ("open", "high", "low", "close", "volume", "money"))

    def test_tushare_unit_conversions(self) -> None:
        self.assertEqual(TUSHARE_VOL_LOTS_TO_SHARES, 100)
        self.assertEqual(TUSHARE_AMOUNT_KYUAN_TO_YUAN, 1000)


if __name__ == "__main__":
    unittest.main()
