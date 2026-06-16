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

from src.data.pit.bundle_integrity import read_bundle_integrity  # noqa: E402
from src.data.pit.qlib_bin_builder import (  # noqa: E402
    BIN_FEATURE_FIELDS,
    TUSHARE_AMOUNT_KYUAN_TO_YUAN,
    TUSHARE_VOL_LOTS_TO_SHARES,
    QlibBinBuilder,
    QlibBinBuilderError,
)
from src.data.tushare.fetch_manifest import (  # noqa: E402
    MANIFEST_FILENAME,
    build_manifest,
    write_manifest,
)
from src.data.tushare.fetcher import FetchHole, TushareFetchResult  # noqa: E402


def _write_active(path: Path, tickers: list[str]) -> None:
    df = pd.DataFrame({
        "ts_code": tickers,
        "list_date": ["20100101"] * len(tickers),
        "list_status": ["L"] * len(tickers),
    })
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    # P3-4c: build() gates on a COMPLETE fetch manifest (no holes AND the required
    # endpoints present). These tests exercise builder LOGIC (not the gate — that
    # has dedicated tests), so seed a hole-free manifest covering the required
    # endpoints alongside the raw dump.
    write_manifest(
        path.parent / MANIFEST_FILENAME,
        build_manifest(
            [TushareFetchResult(e, 1, 0, 0) for e in ("stock_basic", "daily", "adj_factor")],
            (), "20000101", "20251231",
        ),
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


def _write_holey_manifest(tushare_dir: Path) -> None:
    """A fetch manifest that COVERS the required endpoints but records a daily hole
    — so the only incompleteness is the hole, not a missing/empty endpoint."""
    write_manifest(
        tushare_dir / MANIFEST_FILENAME,
        build_manifest(
            [
                TushareFetchResult("stock_basic", 1, 0, 0),
                TushareFetchResult("adj_factor", 1, 0, 0),
                TushareFetchResult("daily", 0, 0, 0),  # holed below
            ],
            (FetchHole(
                endpoint="daily", unit="ts_code=600519.SH year=2020",
                reason_class="transient", attempts=5, last_error="rate limit",
            ),),
            "20000101", "20251231",
        ),
    )


class FetchGateTests(unittest.TestCase):
    """P3-4c Layer 1: build() refuses a holey / missing fetch manifest unless
    allow_holey_fetch, and stamps the bundle's fetch integrity either way."""

    def test_holey_manifest_refuses_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_holey_manifest(tmp_path)
            _write_registry(tmp_path / "registry.parquet", [])
            with self.assertRaisesRegex(QlibBinBuilderError, "INCOMPLETE"):
                QlibBinBuilder(
                    tushare_dir=tmp_path,
                    delisted_registry_path=tmp_path / "registry.parquet",
                    output_dir=tmp_path / "provider",
                ).build()

    def test_missing_manifest_refuses_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_registry(tmp_path / "registry.parquet", [])  # no fetch_manifest.json
            with self.assertRaisesRegex(QlibBinBuilderError, "no fetch_manifest"):
                QlibBinBuilder(
                    tushare_dir=tmp_path,
                    delisted_registry_path=tmp_path / "registry.parquet",
                    output_dir=tmp_path / "provider",
                ).build()

    def test_complete_manifest_builds_and_stamps_clean(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_active(tmp_path / "active_stocks.parquet", ["600519.SH"])  # complete manifest
            _write_daily_year(tmp_path, 2020, "600519.SH", ["20200102", "20200103"])
            _write_registry(tmp_path / "registry.parquet", [])
            out = tmp_path / "provider"
            QlibBinBuilder(
                tushare_dir=tmp_path,
                delisted_registry_path=tmp_path / "registry.parquet",
                output_dir=out,
            ).build()
            integ = read_bundle_integrity(out)
            assert integ is not None
            self.assertFalse(integ.built_from_holey_fetch)
            self.assertEqual(integ.holes, ())
            # PR-G+I: the build path stamps the bundle's content identity into
            # the same _fetch_integrity.json, computed from the promoted bytes.
            from src.data.bundle_manifest import compute_bundle_content_hash
            assert integ.identity is not None
            self.assertEqual(integ.identity.tail_date, "2020-01-03")
            self.assertEqual(integ.identity.calendar_start, "2020-01-02")
            self.assertEqual(integ.identity.calendar_end, "2020-01-03")
            self.assertEqual(integ.identity.instrument_count, 1)
            self.assertEqual(
                integ.identity.content_hash, compute_bundle_content_hash(out),
            )

    def test_holey_build_with_override_stamps_holey(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_active(tmp_path / "active_stocks.parquet", ["600519.SH"])
            _write_holey_manifest(tmp_path)  # OVERWRITE the complete manifest seeded above
            _write_daily_year(tmp_path, 2020, "600519.SH", ["20200102", "20200103"])
            _write_registry(tmp_path / "registry.parquet", [])
            out = tmp_path / "provider"
            QlibBinBuilder(
                tushare_dir=tmp_path,
                delisted_registry_path=tmp_path / "registry.parquet",
                output_dir=out,
                allow_holey_fetch=True,
            ).build()
            integ = read_bundle_integrity(out)
            assert integ is not None
            self.assertTrue(integ.built_from_holey_fetch)
            self.assertEqual(len(integ.holes), 1)
            self.assertEqual(integ.holes[0].endpoint, "daily")

    def test_partial_fetch_missing_required_endpoint_refuses(self) -> None:
        # codex P1: a partial fetch (here only stock_basic) has NO holes but never
        # fetched daily / adj_factor, so "no holes" is NOT "complete" — the gate
        # must require the bundle's endpoints to be present, not just hole-free.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            write_manifest(
                tmp_path / MANIFEST_FILENAME,
                build_manifest(
                    [TushareFetchResult("stock_basic", 2, 0, 0)],  # daily/adj_factor absent
                    (), "20000101", "20251231",
                ),
            )
            _write_registry(tmp_path / "registry.parquet", [])
            with self.assertRaisesRegex(QlibBinBuilderError, "never fetched"):
                QlibBinBuilder(
                    tushare_dir=tmp_path,
                    delisted_registry_path=tmp_path / "registry.parquet",
                    output_dir=tmp_path / "provider",
                ).build()

    def test_empty_coverage_required_endpoint_refuses_build(self) -> None:
        # codex P1 (round-3): a required endpoint recorded with EMPTY coverage —
        # skipped over a pre-existing dump (wrote nothing, holed nothing) — is NOT
        # confirmed fetched even with no holes; the gate must still refuse.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            write_manifest(
                tmp_path / MANIFEST_FILENAME,
                build_manifest(
                    [
                        TushareFetchResult("stock_basic", 1, 0, 0),  # fetched
                        TushareFetchResult("daily", 0, 0, 0),        # skipped => empty cov
                        TushareFetchResult("adj_factor", 0, 0, 0),   # skipped => empty cov
                    ],
                    (), "20000101", "20251231",
                ),
            )
            _write_registry(tmp_path / "registry.parquet", [])
            with self.assertRaisesRegex(QlibBinBuilderError, "empty coverage"):
                QlibBinBuilder(
                    tushare_dir=tmp_path,
                    delisted_registry_path=tmp_path / "registry.parquet",
                    output_dir=tmp_path / "provider",
                ).build()

    def test_corrupt_manifest_refuses_as_builder_error(self) -> None:
        # codex P2 (round-5): a corrupt manifest must surface as QlibBinBuilderError
        # (the 05 CLI's fail-loud path), not an escaping FetchManifestError — and is
        # NOT bypassable by allow_holey_fetch (corruption != partial data).
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / MANIFEST_FILENAME).write_text("{ not json", encoding="utf-8")
            _write_registry(tmp_path / "registry.parquet", [])
            for allow in (False, True):
                with self.assertRaisesRegex(QlibBinBuilderError, "UNREADABLE"):
                    QlibBinBuilder(
                        tushare_dir=tmp_path,
                        delisted_registry_path=tmp_path / "registry.parquet",
                        output_dir=tmp_path / "provider",
                        allow_holey_fetch=allow,
                    ).build()


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
            # allow_holey_fetch bypasses the P3-4c fetch gate (this test does not
            # write active_stocks, hence no seeded manifest) so build() reaches the
            # missing-active_stocks check under test.
            with self.assertRaisesRegex(QlibBinBuilderError, "active_stocks"):
                QlibBinBuilder(
                    tushare_dir=tmp_path,
                    delisted_registry_path=tmp_path / "registry.parquet",
                    output_dir=tmp_path / "provider",
                    allow_holey_fetch=True,
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


class AdjFactorGuardTests(unittest.TestCase):
    """P1-10 / Phase 3 P3-1: a corrupt adj_factor (inf / 0 / negative) must
    fail loud at build time, never silently multiply into the production bins
    (inf -> inf prices, 0 -> zeroed, negative -> sign-flipped). Mirrors the
    publisher's staged-adj validation on the production builder path."""

    @staticmethod
    def _factor_df(factors: list[float]) -> pd.DataFrame:
        """A minimal post-merge frame: trade_date + OHLC + adj_factor, one row
        per factor. Dates are distinct so the error can name the bad one."""
        n = len(factors)
        dates = [f"202001{str(i + 1).zfill(2)}" for i in range(n)]
        return pd.DataFrame({
            "trade_date": dates,
            "open": [10.0] * n, "high": [10.5] * n,
            "low": [9.5] * n, "close": [10.0] * n,
            "adj_factor": factors,
        })

    # --- direct unit tests on the guard (no disk) ---
    def test_clean_factors_pass(self) -> None:
        # All finite + strictly positive -> no raise.
        QlibBinBuilder._validate_adj_factor(
            self._factor_df([1.0, 2.0, 3.5]), "600519.SH",
        )

    def test_inf_factor_flags_only_the_bad_row(self) -> None:
        df = self._factor_df([1.0, float("inf"), 2.0])  # bad row = 20200102
        with self.assertRaises(QlibBinBuilderError) as ctx:
            QlibBinBuilder._validate_adj_factor(df, "600519.SH")
        msg = str(ctx.exception)
        self.assertIn("600519.SH", msg)
        self.assertIn("20200102", msg)   # the corrupt date is named
        self.assertIn("inf", msg)        # ... with its value
        self.assertNotIn("20200101", msg)  # clean rows are NOT flagged
        self.assertNotIn("20200103", msg)

    def test_nan_factor_flags_the_bad_row(self) -> None:
        # A raw NaN in a present row is the case codex P2 (PR #230) caught:
        # ffill().fillna(1.0) would mask it post-merge, so the guard MUST
        # validate the RAW source — which is what _apply_adjustment now does.
        df = self._factor_df([1.0, float("nan"), 2.0])  # bad row = 20200102
        with self.assertRaises(QlibBinBuilderError) as ctx:
            QlibBinBuilder._validate_adj_factor(df, "600519.SH")
        msg = str(ctx.exception)
        self.assertIn("20200102", msg)
        self.assertIn("nan", msg)
        self.assertNotIn("20200101", msg)
        self.assertNotIn("20200103", msg)

    def test_zero_factor_raises_with_date(self) -> None:
        df = self._factor_df([1.0, 0.0])  # bad row = 20200102
        with self.assertRaisesRegex(QlibBinBuilderError, r"finite and > 0"):
            QlibBinBuilder._validate_adj_factor(df, "000001.SZ")
        self.assertIn(
            "20200102",
            str(self._raise_and_capture(self._factor_df([1.0, 0.0]), "000001.SZ")),
        )

    def test_negative_factor_raises_with_ticker_and_value(self) -> None:
        df = self._factor_df([1.0, -1.5])
        with self.assertRaises(QlibBinBuilderError) as ctx:
            QlibBinBuilder._validate_adj_factor(df, "600000.SH")
        msg = str(ctx.exception)
        self.assertIn("600000.SH", msg)
        self.assertIn("-1.5", msg)

    @staticmethod
    def _raise_and_capture(df: pd.DataFrame, code: str) -> QlibBinBuilderError:
        try:
            QlibBinBuilder._validate_adj_factor(df, code)
        except QlibBinBuilderError as exc:
            return exc
        raise AssertionError("expected QlibBinBuilderError")

    # --- integration via build() (guard is actually wired into the pipeline) ---
    def test_build_raises_on_inf_adj_factor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_active(tmp_path / "active_stocks.parquet", ["600519.SH"])
            _write_registry(tmp_path / "registry.parquet", [])
            dates = ["20200102", "20200103"]
            _write_daily_year(tmp_path, 2020, "600519.SH", dates, close=100.0)
            _write_adj_factor_year(
                tmp_path, 2020, "600519.SH", dates, factor=float("inf"),
            )
            with self.assertRaisesRegex(
                QlibBinBuilderError, r"adj_factor must be finite",
            ):
                QlibBinBuilder(
                    tushare_dir=tmp_path,
                    delisted_registry_path=tmp_path / "registry.parquet",
                    output_dir=tmp_path / "provider",
                ).build()

    def test_build_raises_on_negative_adj_factor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_active(tmp_path / "active_stocks.parquet", ["600519.SH"])
            _write_registry(tmp_path / "registry.parquet", [])
            dates = ["20200102"]
            _write_daily_year(tmp_path, 2020, "600519.SH", dates, close=100.0)
            _write_adj_factor_year(
                tmp_path, 2020, "600519.SH", dates, factor=-1.0,
            )
            with self.assertRaisesRegex(QlibBinBuilderError, r"600519\.SH"):
                QlibBinBuilder(
                    tushare_dir=tmp_path,
                    delisted_registry_path=tmp_path / "registry.parquet",
                    output_dir=tmp_path / "provider",
                ).build()

    def test_build_raises_on_nan_adj_factor(self) -> None:
        # Regression for codex P2 on PR #230: a raw NaN factor used to be masked
        # by ffill().fillna(1.0) and silently build UNADJUSTED bins; the
        # raw-source guard now fails loud instead.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_active(tmp_path / "active_stocks.parquet", ["600519.SH"])
            _write_registry(tmp_path / "registry.parquet", [])
            dates = ["20200102", "20200103"]
            _write_daily_year(tmp_path, 2020, "600519.SH", dates, close=100.0)
            _write_adj_factor_year(
                tmp_path, 2020, "600519.SH", dates, factor=float("nan"),
            )
            with self.assertRaisesRegex(
                QlibBinBuilderError, r"adj_factor must be finite",
            ):
                QlibBinBuilder(
                    tushare_dir=tmp_path,
                    delisted_registry_path=tmp_path / "registry.parquet",
                    output_dir=tmp_path / "provider",
                ).build()

    def test_build_passes_with_clean_adj_factor(self) -> None:
        # Positive control: a clean factor still builds and adjusts (100 * 2).
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_active(tmp_path / "active_stocks.parquet", ["600519.SH"])
            _write_registry(tmp_path / "registry.parquet", [])
            dates = ["20200102", "20200103"]
            _write_daily_year(tmp_path, 2020, "600519.SH", dates, close=100.0)
            _write_adj_factor_year(
                tmp_path, 2020, "600519.SH", dates, factor=2.0,
            )
            out = tmp_path / "provider"
            QlibBinBuilder(
                tushare_dir=tmp_path,
                delisted_registry_path=tmp_path / "registry.parquet",
                output_dir=out,
            ).build()
            cal = (out / "calendars" / "day.txt").read_text(
                encoding="utf-8",
            ).splitlines()
            _, close = _read_bin_values(out, "SH600519", "close", cal)
            self.assertTrue(np.allclose(close[:2], [200.0, 200.0]))


class PerYearLoaderPolicyTests(unittest.TestCase):
    """PR-2: the extracted _load_per_year_parquet preserves each loader's
    missing-root policy — daily is MANDATORY (raise), adj_factor/daily_basic
    OPTIONAL (None) — and the concat/dedup/sort tail."""

    def _builder(self, tmp_path: Path) -> QlibBinBuilder:
        return QlibBinBuilder(
            tushare_dir=tmp_path,
            delisted_registry_path=tmp_path / "registry.parquet",
            output_dir=tmp_path / "provider",
        )

    def test_missing_daily_root_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(QlibBinBuilderError, "--endpoints daily"):
                self._builder(Path(tmp))._load_ticker_history("600519.SH")

    def test_missing_adj_factor_root_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(self._builder(Path(tmp))._load_adj_factor("600519.SH"))

    def test_missing_daily_basic_root_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(self._builder(Path(tmp))._load_daily_basic("600519.SH"))

    def test_no_parquet_for_ticker_returns_none(self) -> None:
        # root exists but holds no file for this ticker -> None (not a raise).
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "daily" / "2020").mkdir(parents=True)
            self.assertIsNone(self._builder(tmp_path)._load_ticker_history("600519.SH"))

    def test_concat_dedup_sort_across_years(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            for year, dates in ((2021, ["20211231"]), (2020, ["20200102", "20200102"])):
                d = tmp_path / "daily" / str(year)
                d.mkdir(parents=True)
                pd.DataFrame(
                    {"trade_date": dates, "close": [1.0] * len(dates)},
                ).to_parquet(d / "600519.SH.parquet", index=False)
            df = self._builder(tmp_path)._load_ticker_history("600519.SH")
            assert df is not None
            self.assertEqual(list(df["trade_date"]), ["20200102", "20211231"])


if __name__ == "__main__":
    unittest.main()
