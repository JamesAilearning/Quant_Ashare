"""Tests for ``QlibBinBuilder`` daily_basic-field emission.

Sub-PR A2 of the ``extend-feature-universe-with-daily-basic`` OpenSpec
change (proposal PR #182). The builder gains six new optional bins
under ``features/<ticker>/`` driven by Tushare's ``daily_basic``
endpoint:

- ``pe.day.bin``, ``pb.day.bin``, ``ps.day.bin``
- ``turnover_rate.day.bin``
- ``circ_mv.day.bin``, ``total_mv.day.bin``

Backward-compatibility invariant: a tushare_dir built before this PR's
data arrived (no ``daily_basic/`` subdir, or an empty subdir for some
tickers) MUST still produce a valid bundle with only the 6 OHLCV bins
per ticker. The 6 daily_basic bins are emitted ONLY for tickers that
have a ``daily_basic/<year>/<ticker>.parquet`` payload.

PIT NaN-after-delist invariant: the new bins inherit the
``delisted_registry.parquet``-driven mask from the existing OHLCV
path, because daily_basic is left-joined into the per-ticker df
BEFORE ``_clip_to_listing_window`` drops post-delist rows.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.pit.qlib_bin_builder import (  # noqa: E402
    BIN_DAILY_BASIC_FIELDS,
    QlibBinBuilder,
)
from src.data.tushare.fetch_manifest import (  # noqa: E402
    MANIFEST_FILENAME,
    build_manifest,
    write_manifest,
)

# ----------------------------------------------------------------------
# Fixture writers (mirror tests/data_pipeline/test_qlib_bin_builder.py)
# ----------------------------------------------------------------------


def _write_active(path: Path, tickers: list[str]) -> None:
    df = pd.DataFrame({
        "ts_code": tickers,
        "list_date": ["20100101"] * len(tickers),
        "list_status": ["L"] * len(tickers),
    })
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    # P3-4c: build() gates on a COMPLETE fetch manifest; seed a hole-free one
    # (empty endpoints => complete) so these builder-logic tests pass the gate.
    write_manifest(
        path.parent / MANIFEST_FILENAME, build_manifest([], (), "20000101", "20251231"),
    )


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


def _write_daily_year(
    tushare_dir: Path, year: int, ts_code: str,
    trade_dates: list[str], close: float = 10.0,
) -> None:
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


def _write_daily_basic_year(
    tushare_dir: Path, year: int, ts_code: str,
    trade_dates: list[str], values: dict[str, list[float]] | None = None,
) -> None:
    """Write a per-(year, ticker) daily_basic parquet.

    ``values`` keys override the default series for each daily_basic
    field; any key not provided uses a deterministic per-index ramp so
    the test can assert exact value parity against the source.
    """
    n = len(trade_dates)
    defaults = {
        "pe": [10.0 + i for i in range(n)],
        "pb": [1.5 + 0.1 * i for i in range(n)],
        "ps": [3.0 + 0.05 * i for i in range(n)],
        "turnover_rate": [0.5 + 0.01 * i for i in range(n)],
        "circ_mv": [1.0e9 + i * 1.0e7 for i in range(n)],
        "total_mv": [2.0e9 + i * 1.0e7 for i in range(n)],
    }
    if values:
        defaults.update(values)
    df = pd.DataFrame({
        "ts_code": [ts_code] * n,
        "trade_date": trade_dates,
        **defaults,
    })
    path = tushare_dir / "daily_basic" / str(year) / f"{ts_code}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)


def _read_bin_values(
    provider_dir: Path, qlib_ticker: str, field: str, calendar: list[str],
) -> tuple[int, np.ndarray]:
    raw = np.fromfile(
        provider_dir / "features" / qlib_ticker.lower() / f"{field}.day.bin",
        dtype="<f4",
    )
    start = int(raw[0])
    payload = raw[1:]
    out = np.full(len(calendar), np.nan, dtype="float32")
    out[start: start + len(payload)] = payload
    return start, out


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------


class DailyBasicEmissionTests(unittest.TestCase):
    """Builder reads daily_basic and emits the 6 new bins."""

    def test_writes_six_daily_basic_bins_for_ticker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_active(tmp_path / "active_stocks.parquet", ["600519.SH"])
            _write_registry(tmp_path / "registry.parquet", [])
            dates = ["20200102", "20200103", "20200106"]
            _write_daily_year(tmp_path, 2020, "600519.SH", dates, close=100.0)
            _write_daily_basic_year(tmp_path, 2020, "600519.SH", dates)

            out = tmp_path / "provider"
            QlibBinBuilder(
                tushare_dir=tmp_path,
                delisted_registry_path=tmp_path / "registry.parquet",
                output_dir=out,
            ).build()

            cal = (out / "calendars" / "day.txt").read_text(
                encoding="utf-8").splitlines()
            self.assertEqual(cal, ["2020-01-02", "2020-01-03", "2020-01-06"])

            feats = out / "features" / "sh600519"
            # All 6 OHLCV bins still exist (unchanged behaviour)
            for field in ("open", "high", "low", "close", "volume", "money"):
                self.assertTrue((feats / f"{field}.day.bin").exists(),
                                f"missing OHLCV bin: {field}.day.bin")
            # All 6 daily_basic bins now exist
            for field in BIN_DAILY_BASIC_FIELDS:
                self.assertTrue((feats / f"{field}.day.bin").exists(),
                                f"missing daily_basic bin: {field}.day.bin")

    def test_daily_basic_bin_start_idx_matches_ohlcv(self) -> None:
        """A daily_basic bin's start_idx aligns with the OHLCV bins for
        the same ticker (both use the ticker's first observed trade
        date, NOT the first daily_basic-only date)."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # Active ticker covers 4 days; another ticker establishes
            # an earlier calendar entry so this ticker's start_idx > 0.
            _write_active(tmp_path / "active_stocks.parquet",
                          ["600519.SH", "000001.SZ"])
            _write_registry(tmp_path / "registry.parquet", [])
            _write_daily_year(tmp_path, 2020, "000001.SZ",
                              ["20200102"], close=10.0)
            ticker_dates = ["20200103", "20200106", "20200107"]
            _write_daily_year(tmp_path, 2020, "600519.SH",
                              ticker_dates, close=100.0)
            _write_daily_basic_year(tmp_path, 2020, "600519.SH", ticker_dates)

            out = tmp_path / "provider"
            QlibBinBuilder(
                tushare_dir=tmp_path,
                delisted_registry_path=tmp_path / "registry.parquet",
                output_dir=out,
            ).build()

            close_raw = np.fromfile(
                out / "features" / "sh600519" / "close.day.bin", dtype="<f4")
            pe_raw = np.fromfile(
                out / "features" / "sh600519" / "pe.day.bin", dtype="<f4")
            # Same start_idx, same payload length.
            self.assertEqual(int(close_raw[0]), int(pe_raw[0]))
            self.assertEqual(len(close_raw), len(pe_raw))
            # start_idx > 0 (other ticker contributed earlier calendar)
            self.assertGreater(int(pe_raw[0]), 0)

    def test_daily_basic_values_match_source(self) -> None:
        """The bin values are exactly the parquet column values modulo
        float32 precision — no scale / unit transform is applied."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_active(tmp_path / "active_stocks.parquet", ["600519.SH"])
            _write_registry(tmp_path / "registry.parquet", [])
            dates = ["20200102", "20200103", "20200106"]
            _write_daily_year(tmp_path, 2020, "600519.SH", dates, close=100.0)
            expected = {
                "pe": [12.34, 56.78, 90.12],
                "pb": [1.10, 2.20, 3.30],
                "ps": [4.40, 5.50, 6.60],
                "turnover_rate": [0.10, 0.20, 0.30],
                "circ_mv": [1.5e9, 1.6e9, 1.7e9],
                "total_mv": [2.5e9, 2.6e9, 2.7e9],
            }
            _write_daily_basic_year(
                tmp_path, 2020, "600519.SH", dates, values=expected,
            )

            out = tmp_path / "provider"
            QlibBinBuilder(
                tushare_dir=tmp_path,
                delisted_registry_path=tmp_path / "registry.parquet",
                output_dir=out,
            ).build()

            cal = (out / "calendars" / "day.txt").read_text(
                encoding="utf-8").splitlines()
            for field, expected_vals in expected.items():
                _, values = _read_bin_values(out, "SH600519", field, cal)
                actual = values[:len(expected_vals)]
                self.assertTrue(
                    np.allclose(actual, expected_vals, rtol=1e-6, atol=1e-3),
                    f"{field}: expected {expected_vals}, got {actual.tolist()}",
                )


class BackwardCompatTests(unittest.TestCase):
    """Bundles built without daily_basic data still work."""

    def test_no_daily_basic_dir_at_all(self) -> None:
        """When ``daily_basic/`` is absent, only the 6 OHLCV bins are
        produced — original behaviour, unchanged."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_active(tmp_path / "active_stocks.parquet", ["600519.SH"])
            _write_registry(tmp_path / "registry.parquet", [])
            dates = ["20200102", "20200103"]
            _write_daily_year(tmp_path, 2020, "600519.SH", dates, close=100.0)
            # NO daily_basic/ dir at all.

            out = tmp_path / "provider"
            QlibBinBuilder(
                tushare_dir=tmp_path,
                delisted_registry_path=tmp_path / "registry.parquet",
                output_dir=out,
            ).build()

            feats = out / "features" / "sh600519"
            # 6 OHLCV bins present
            for field in ("open", "high", "low", "close", "volume", "money"):
                self.assertTrue((feats / f"{field}.day.bin").exists())
            # 6 daily_basic bins absent (NOT all-NaN bins — completely missing)
            for field in BIN_DAILY_BASIC_FIELDS:
                self.assertFalse(
                    (feats / f"{field}.day.bin").exists(),
                    f"daily_basic bin must not be written when source dir "
                    f"is absent: {field}.day.bin",
                )

    def test_per_ticker_missing_daily_basic(self) -> None:
        """When ``daily_basic/`` exists but a specific ticker has no
        parquet under it, that ticker gets only OHLCV bins; other
        tickers with daily_basic data get the full 12 bins. This is
        the mid-rollout case where Phase A.1 backfilled most tickers
        but a few got skipped on rate-limit retry."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_active(tmp_path / "active_stocks.parquet",
                          ["600519.SH", "000001.SZ"])
            _write_registry(tmp_path / "registry.parquet", [])
            dates = ["20200102", "20200103"]
            _write_daily_year(tmp_path, 2020, "600519.SH", dates, close=100.0)
            _write_daily_year(tmp_path, 2020, "000001.SZ", dates, close=20.0)
            # Only 600519 gets daily_basic data; 000001 does not.
            _write_daily_basic_year(tmp_path, 2020, "600519.SH", dates)

            out = tmp_path / "provider"
            QlibBinBuilder(
                tushare_dir=tmp_path,
                delisted_registry_path=tmp_path / "registry.parquet",
                output_dir=out,
            ).build()

            feats_519 = out / "features" / "sh600519"
            feats_001 = out / "features" / "sz000001"

            # 600519: full 12 bins
            for field in BIN_DAILY_BASIC_FIELDS:
                self.assertTrue(
                    (feats_519 / f"{field}.day.bin").exists(),
                    f"SH600519 should have {field}.day.bin",
                )
            # 000001: OHLCV only (no daily_basic data was provided)
            for field in ("open", "high", "low", "close", "volume", "money"):
                self.assertTrue((feats_001 / f"{field}.day.bin").exists())
            for field in BIN_DAILY_BASIC_FIELDS:
                self.assertFalse(
                    (feats_001 / f"{field}.day.bin").exists(),
                    f"SZ000001 has no daily_basic parquet, so {field}.day.bin "
                    "must not be emitted",
                )


class PitNanAfterDelistTests(unittest.TestCase):
    """The PIT NaN-after-delist mask propagates to daily_basic bins
    the same way it does to OHLCV bins."""

    def test_delist_mask_propagates_to_daily_basic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_active(tmp_path / "active_stocks.parquet", ["600519.SH"])
            _write_registry(tmp_path / "registry.parquet", [
                {"ticker": "SH600087", "list_date": "2010-01-01",
                 "delist_date": "2020-01-03"},
            ])
            # Active ticker establishes the full calendar.
            active_dates = ["20200102", "20200103", "20200106", "20200107"]
            _write_daily_year(tmp_path, 2020, "600519.SH",
                              active_dates, close=100.0)
            # Delisted ticker has Tushare data + daily_basic data for
            # ALL 4 days (simulating the case where the upstream
            # endpoint didn't clip at delist_date). The builder MUST
            # clip both the OHLCV AND the daily_basic columns at
            # delist_date.
            _write_daily_year(tmp_path, 2020, "600087.SH",
                              active_dates, close=50.0)
            _write_daily_basic_year(
                tmp_path, 2020, "600087.SH", active_dates,
                values={
                    "pe": [10.0, 11.0, 12.0, 13.0],
                    "pb": [1.0, 1.1, 1.2, 1.3],
                    "ps": [2.0, 2.1, 2.2, 2.3],
                    "turnover_rate": [0.5, 0.6, 0.7, 0.8],
                    "circ_mv": [1.0e9, 1.1e9, 1.2e9, 1.3e9],
                    "total_mv": [2.0e9, 2.1e9, 2.2e9, 2.3e9],
                },
            )

            out = tmp_path / "provider"
            QlibBinBuilder(
                tushare_dir=tmp_path,
                delisted_registry_path=tmp_path / "registry.parquet",
                output_dir=out,
            ).build()

            cal = (out / "calendars" / "day.txt").read_text(
                encoding="utf-8").splitlines()
            self.assertEqual(cal, [
                "2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07",
            ])

            # For every daily_basic field: valid on 01-02 and 01-03,
            # NaN on 01-06 and 01-07 (post-delist).
            for field in BIN_DAILY_BASIC_FIELDS:
                _, values = _read_bin_values(out, "SH600087", field, cal)
                self.assertFalse(np.isnan(values[0]),
                                 f"{field}: 2020-01-02 must be valid")
                self.assertFalse(np.isnan(values[1]),
                                 f"{field}: 2020-01-03 (=delist_date) must be valid")
                self.assertTrue(np.isnan(values[2]),
                                f"{field}: 2020-01-06 (post-delist) must be NaN")
                self.assertTrue(np.isnan(values[3]),
                                f"{field}: 2020-01-07 (post-delist) must be NaN")

            # Regression for the OHLCV invariant: bin extends to the
            # last calendar position even for delisted tickers.
            for field in BIN_DAILY_BASIC_FIELDS:
                raw = np.fromfile(
                    out / "features" / "sh600087" / f"{field}.day.bin",
                    dtype="<f4",
                )
                recorded_start = int(raw[0])
                payload_len = len(raw) - 1
                self.assertEqual(
                    recorded_start + payload_len, len(cal),
                    f"{field}: bin must extend through last calendar date",
                )


class ConstantsTests(unittest.TestCase):

    def test_daily_basic_fields_tuple(self) -> None:
        self.assertEqual(
            BIN_DAILY_BASIC_FIELDS,
            ("pe", "pb", "ps", "turnover_rate", "circ_mv", "total_mv"),
        )


if __name__ == "__main__":
    unittest.main()
