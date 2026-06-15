"""Tests for ``src.data.pit.benchmark_index_ingest`` (PR-E).

Synthetic tushare ``index_daily`` frames written into a temp bundle dir;
read the bins back and assert calendar alignment, close-only handling, and
idempotent registry update. No qlib, no network.
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

from src.data.pit.benchmark_index_ingest import (  # noqa: E402
    BenchmarkIngestError,
    ingest_benchmark_index,
)

# A 6-day synthetic bundle calendar.
_CAL = [
    "2025-01-02", "2025-01-03", "2025-01-06",
    "2025-01-07", "2025-01-08", "2025-01-09",
]


def _bundle(tmp: Path, extra_all: list[str] | None = None) -> Path:
    """Minimal bundle skeleton: calendar + a seeded instruments/all.txt."""
    (tmp / "calendars").mkdir(parents=True)
    (tmp / "calendars" / "day.txt").write_text(
        "\n".join(_CAL) + "\n", encoding="utf-8",
    )
    (tmp / "instruments").mkdir(parents=True)
    rows = ["SH600000\t2018-01-02\t2025-12-31"] + (extra_all or [])
    (tmp / "instruments" / "all.txt").write_text(
        "\n".join(rows) + "\n", encoding="utf-8",
    )
    return tmp


def _read_bin(provider: Path, inst: str, field: str) -> tuple[int, np.ndarray]:
    raw = np.fromfile(
        provider / "features" / inst.lower() / f"{field}.day.bin", dtype="<f4",
    )
    return int(raw[0]), raw[1:]


def _to_yyyymmdd(iso_dates: list[str]) -> list[str]:
    return [d.replace("-", "") for d in iso_dates]


class FullOhlcIngestTests(unittest.TestCase):
    def test_price_index_writes_all_fields_aligned(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            prov = _bundle(Path(t))
            # Starts on the 2nd calendar day (start_index == 1).
            df = pd.DataFrame({
                "ts_code": ["000300.SH"] * 5,
                "trade_date": _to_yyyymmdd(_CAL[1:]),
                "open": [10.0, 11.0, 12.0, 13.0, 14.0],
                "high": [10.5, 11.5, 12.5, 13.5, 14.5],
                "low": [9.5, 10.5, 11.5, 12.5, 13.5],
                "close": [10.2, 11.2, 12.2, 13.2, 14.2],
                "vol": [100.0, 200.0, 300.0, 400.0, 500.0],
            })
            res = ingest_benchmark_index(
                df, instrument_code="SH000300", provider_dir=prov,
            )
            self.assertEqual(res.first_date, "2025-01-03")
            self.assertEqual(res.last_date, "2025-01-09")
            self.assertEqual(res.n_trading_days, 5)
            self.assertEqual(res.n_gap_days, 0)
            self.assertFalse(res.ohlc_degenerate)

            start, close = _read_bin(prov, "SH000300", "close")
            self.assertEqual(start, 1)
            np.testing.assert_allclose(close, [10.2, 11.2, 12.2, 13.2, 14.2], rtol=1e-5)
            _, high = _read_bin(prov, "SH000300", "high")
            np.testing.assert_allclose(high, [10.5, 11.5, 12.5, 13.5, 14.5], rtol=1e-5)
            # No factor bin is written (equities carry none; benchmark read
            # path never adjusts by factor) — self-review symmetry fix.
            self.assertFalse(
                (prov / "features" / "sh000300" / "factor.day.bin").exists(),
            )

            # Benchmark goes to benchmark.txt, NOT all.txt (training universe
            # must stay clean — codex P1 on #243).
            bench_txt = (prov / "instruments" / "benchmark.txt").read_text().splitlines()
            self.assertIn("SH000300\t2025-01-03\t2025-01-09", bench_txt)
            all_txt = (prov / "instruments" / "all.txt").read_text().splitlines()
            self.assertNotIn("SH000300\t2025-01-03\t2025-01-09", all_txt)
            self.assertEqual(all_txt, ["SH600000\t2018-01-02\t2025-12-31"])


class CloseOnlyIngestTests(unittest.TestCase):
    def test_total_return_close_only_falls_back_ohlc_to_close(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            prov = _bundle(Path(t))
            # H00300-shape: only close; open/high/low/vol are None.
            df = pd.DataFrame({
                "ts_code": ["H00300.CSI"] * 6,
                "trade_date": _to_yyyymmdd(_CAL),
                "open": [None] * 6,
                "high": [None] * 6,
                "low": [None] * 6,
                "close": [100.0, 101.0, 102.0, 103.0, 104.0, 105.0],
                "vol": [None] * 6,
            })
            res = ingest_benchmark_index(
                df, instrument_code="SH000300TR", provider_dir=prov,
            )
            self.assertTrue(res.ohlc_degenerate)
            self.assertEqual(res.n_trading_days, 6)
            start, close = _read_bin(prov, "SH000300TR", "close")
            self.assertEqual(start, 0)
            np.testing.assert_allclose(close, [100, 101, 102, 103, 104, 105], rtol=1e-5)
            # OHLC fall back to close exactly.
            for field in ("open", "high", "low"):
                _, vals = _read_bin(prov, "SH000300TR", field)
                np.testing.assert_allclose(vals, close, rtol=1e-6)
            # volume is NaN (no fallback).
            _, vol = _read_bin(prov, "SH000300TR", "volume")
            self.assertTrue(np.isnan(vol).all())


class GapAlignmentTests(unittest.TestCase):
    def test_missing_calendar_day_is_forward_filled_not_nan(self) -> None:
        # P1 (self-review): a NaN benchmark close makes qlib fabricate a 0%
        # return on the gap day AND drop the true cross-gap move on the
        # recovery day (Ref pulls the NaN). Forward-filling the level fixes
        # both: gap day = true 0%, recovery day = real move.
        with tempfile.TemporaryDirectory() as t:
            prov = _bundle(Path(t))
            # Publishes all days EXCEPT 2025-01-07 (calendar index 3).
            pub = [d for d in _CAL if d != "2025-01-07"]
            closes = [100.0, 101.0, 102.0, 104.0, 105.0]  # for the 5 pub days
            df = pd.DataFrame({
                "trade_date": _to_yyyymmdd(pub), "close": closes,
            })
            res = ingest_benchmark_index(
                df, instrument_code="SH000300", provider_dir=prov,
            )
            self.assertEqual(res.n_trading_days, 6)
            self.assertEqual(res.n_gap_days, 1)  # diagnostic count unchanged
            _, close = _read_bin(prov, "SH000300", "close")
            # No NaN anywhere; the gap (index 3) carries the prior level
            # (102.0 from 2025-01-06), and the recovery day (index 4) is the
            # real 104.0 — so close[4]/close[3]-1 recovers the true move.
            self.assertFalse(np.isnan(close).any())
            np.testing.assert_allclose(close[3], 102.0, rtol=1e-5)
            np.testing.assert_allclose(close[4], 104.0, rtol=1e-5)
            # Gap-day return is exactly 0%; recovery-day carries the move.
            self.assertAlmostEqual(float(close[3] / close[2] - 1), 0.0, places=5)
            self.assertAlmostEqual(
                float(close[4] / close[3] - 1), 104.0 / 102.0 - 1, places=5,
            )


class TrailingLagTests(unittest.TestCase):
    def test_index_lagging_calendar_tail_ends_at_last_published_not_filled(self) -> None:
        # codex P1 on #243: when index_daily lags the calendar tail (hasn't
        # printed the last days), the series must END at the last published
        # date — NOT ffill fabricated closes through calendar[-1] and
        # register the instrument over days it never published.
        with tempfile.TemporaryDirectory() as t:
            prov = _bundle(Path(t))
            # Calendar has 6 days; index publishes only through 2025-01-07
            # (calendar index 3) — the last 2 days (01-08, 01-09) are absent.
            pub = _CAL[:4]
            df = pd.DataFrame({
                "trade_date": _to_yyyymmdd(pub), "close": [10.0, 11.0, 12.0, 13.0],
            })
            res = ingest_benchmark_index(
                df, instrument_code="SH000300", provider_dir=prov,
            )
            self.assertEqual(res.last_date, "2025-01-07")
            self.assertEqual(res.n_trading_days, 4)  # NOT 6 — no trailing fab
            self.assertEqual(res.n_gap_days, 0)
            _, close = _read_bin(prov, "SH000300", "close")
            self.assertEqual(len(close), 4)
            np.testing.assert_allclose(close, [10.0, 11.0, 12.0, 13.0], rtol=1e-5)
            # Registry span (in benchmark.txt) ends at the real last published date.
            lines = (prov / "instruments" / "benchmark.txt").read_text().splitlines()
            self.assertIn("SH000300\t2025-01-02\t2025-01-07", lines)


class RegistryIdempotencyTests(unittest.TestCase):
    def test_reingest_replaces_not_duplicates_and_updates_span(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            prov = _bundle(Path(t))
            narrow = pd.DataFrame({
                "trade_date": _to_yyyymmdd(_CAL[2:4]),
                "close": [1.0, 2.0],
            })
            ingest_benchmark_index(
                narrow, instrument_code="SH000300", provider_dir=prov,
            )
            wide = pd.DataFrame({
                "trade_date": _to_yyyymmdd(_CAL),
                "close": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
            })
            ingest_benchmark_index(
                wide, instrument_code="SH000300", provider_dir=prov,
            )
            lines = (prov / "instruments" / "benchmark.txt").read_text().splitlines()
            bench = [ln for ln in lines if ln.startswith("SH000300\t")]
            self.assertEqual(len(bench), 1, f"duplicated registry rows: {bench}")
            self.assertEqual(bench[0], "SH000300\t2025-01-02\t2025-01-09")
            # The equity all.txt is never touched by benchmark ingest.
            all_txt = (prov / "instruments" / "all.txt").read_text().splitlines()
            self.assertEqual(all_txt, ["SH600000\t2018-01-02\t2025-12-31"])


class LegacyAllTxtScrubTests(unittest.TestCase):
    def test_legacy_benchmark_row_scrubbed_from_all_txt(self) -> None:
        # codex P2 on #243: a bundle whose all.txt still has the retired
        # xlsx script's SH000300 row must have it REMOVED on ingest (the
        # benchmark belongs only in benchmark.txt). A benchmark code is never
        # a real equity, so the scrub only undoes legacy contamination.
        with tempfile.TemporaryDirectory() as t:
            prov = Path(t)
            (prov / "calendars").mkdir(parents=True)
            (prov / "calendars" / "day.txt").write_text(
                "\n".join(_CAL) + "\n", encoding="utf-8",
            )
            (prov / "instruments").mkdir(parents=True)
            # Legacy all.txt with the benchmark contaminating the universe.
            (prov / "instruments" / "all.txt").write_text(
                "SH600000\t2018-01-02\t2025-12-31\n"
                "SH000300\t2005-04-08\t2026-03-10\n",
                encoding="utf-8",
            )
            df = pd.DataFrame({
                "trade_date": _to_yyyymmdd(_CAL), "close": [1.0, 2, 3, 4, 5, 6],
            })
            ingest_benchmark_index(df, instrument_code="SH000300", provider_dir=prov)
            all_txt = (prov / "instruments" / "all.txt").read_text().splitlines()
            self.assertEqual(all_txt, ["SH600000\t2018-01-02\t2025-12-31"])
            bench_txt = (prov / "instruments" / "benchmark.txt").read_text()
            self.assertIn("SH000300\t", bench_txt)


class ErrorPathTests(unittest.TestCase):
    def test_missing_close_column_fails_loud(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            prov = _bundle(Path(t))
            df = pd.DataFrame({"trade_date": _to_yyyymmdd(_CAL), "open": [1.0] * 6})
            with self.assertRaisesRegex(BenchmarkIngestError, "trade_date.*close|close"):
                ingest_benchmark_index(df, instrument_code="SH000300", provider_dir=prov)

    def test_all_nan_close_fails_loud(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            prov = _bundle(Path(t))
            df = pd.DataFrame({"trade_date": _to_yyyymmdd(_CAL), "close": [None] * 6})
            # All-NaN is a superset of the published-null-close check.
            with self.assertRaisesRegex(BenchmarkIngestError, "null / non-numeric close"):
                ingest_benchmark_index(df, instrument_code="SH000300", provider_dir=prov)

    def test_published_row_with_null_close_fails_loud(self) -> None:
        # codex P2 on #243: a PUBLISHED row with null/non-numeric close is a
        # corrupt source, not a calendar gap — fail loud, don't ffill a
        # fabricated 0% benchmark return. (A calendar gap = a date ABSENT
        # from the source, which legitimately ffills; see GapAlignmentTests.)
        with tempfile.TemporaryDirectory() as t:
            prov = _bundle(Path(t))
            df = pd.DataFrame({
                "trade_date": _to_yyyymmdd(_CAL),
                "close": [100.0, 101.0, None, 103.0, 104.0, 105.0],  # row present, close null
            })
            with self.assertRaisesRegex(BenchmarkIngestError, "null / non-numeric close"):
                ingest_benchmark_index(df, instrument_code="SH000300", provider_dir=prov)

    def test_dates_outside_calendar_fail_loud(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            prov = _bundle(Path(t))
            df = pd.DataFrame({
                "trade_date": ["20200101", "20200102"], "close": [1.0, 2.0],
            })
            with self.assertRaisesRegex(BenchmarkIngestError, "calendar"):
                ingest_benchmark_index(df, instrument_code="SH000300", provider_dir=prov)

    def test_missing_calendar_fails_loud(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            prov = Path(t)  # no calendar/instruments seeded
            df = pd.DataFrame({"trade_date": _to_yyyymmdd(_CAL), "close": [1.0] * 6})
            with self.assertRaisesRegex(BenchmarkIngestError, "calendar"):
                ingest_benchmark_index(df, instrument_code="SH000300", provider_dir=prov)

    def test_malformed_trade_date_raises_typed_error(self) -> None:
        # codex P2 on #243: a malformed trade_date is a source-contract
        # violation that must surface as BenchmarkIngestError (so the CLI
        # maps it to a stage exit), not a bare ValueError escaping main().
        with tempfile.TemporaryDirectory() as t:
            prov = _bundle(Path(t))
            df = pd.DataFrame({"trade_date": ["not-a-date", "2025XX02"],
                               "close": [1.0, 2.0]})
            with self.assertRaisesRegex(BenchmarkIngestError, "unparseable trade_date"):
                ingest_benchmark_index(df, instrument_code="SH000300", provider_dir=prov)

    def test_duplicate_dates_fail_loud(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            prov = _bundle(Path(t))
            df = pd.DataFrame({
                "trade_date": ["20250102", "20250102"], "close": [1.0, 2.0],
            })
            with self.assertRaisesRegex(BenchmarkIngestError, "duplicate"):
                ingest_benchmark_index(df, instrument_code="SH000300", provider_dir=prov)


if __name__ == "__main__":
    unittest.main()
