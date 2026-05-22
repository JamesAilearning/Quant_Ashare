"""Tests for ``src.data.pit.universe_files.UniverseFilesBuilder``."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.pit.universe_files import (  # noqa: E402
    QLIB_OPEN_END_DATE,
    UniverseFilesBuilder,
    UniverseFilesError,
)


def _write_active(path: Path, tickers: list[tuple[str, str]]) -> None:
    """``tickers`` is list of ``(ts_code, list_date_yyyymmdd)``."""
    df = pd.DataFrame({
        "ts_code": [t[0] for t in tickers],
        "list_date": [t[1] for t in tickers],
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
        # Empty registry parquet must still carry the schema so the
        # loader's required-column check passes.
        df = pd.DataFrame({
            "ticker": pd.Series([], dtype=str),
            "list_date": pd.Series([], dtype="datetime64[ns]"),
            "delist_date": pd.Series([], dtype="datetime64[ns]"),
            "last_company_name": pd.Series([], dtype=str),
            "delist_reason": pd.Series([], dtype=str),
        })
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)


class UniverseFilesBuilderTests(unittest.TestCase):

    def test_builds_all_txt_with_active_and_delisted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_active(tmp_path / "active_stocks.parquet", [
                ("600519.SH", "20010827"),
                ("000001.SZ", "19910403"),
            ])
            _write_registry(tmp_path / "registry.parquet", [
                {"ticker": "SH600087", "list_date": "1997-06-12",
                 "delist_date": "2014-06-05"},
            ])

            result = UniverseFilesBuilder(
                tushare_dir=tmp_path,
                delisted_registry_path=tmp_path / "registry.parquet",
                output_dir=tmp_path,
            ).build()

            self.assertEqual(result.active_count, 2)
            self.assertEqual(result.delisted_count, 1)
            self.assertEqual(result.total_rows, 3)

            lines = (tmp_path / "instruments" / "all.txt").read_text(encoding="utf-8").splitlines()
            self.assertEqual(sorted(lines), [
                "SH600087\t1997-06-12\t2014-06-05",
                f"SH600519\t2001-08-27\t{QLIB_OPEN_END_DATE}",
                f"SZ000001\t1991-04-03\t{QLIB_OPEN_END_DATE}",
            ])

    def test_overlap_active_and_delisted_raises(self) -> None:
        """If the same ticker shows up in both buckets — the failure mode
        the corrected verify_survivorship.py was built to detect — the
        builder MUST raise rather than silently emit two rows.
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_active(tmp_path / "active_stocks.parquet", [
                ("600087.SH", "19970612"),  # SAME ticker as delisted
            ])
            _write_registry(tmp_path / "registry.parquet", [
                {"ticker": "SH600087", "list_date": "1997-06-12",
                 "delist_date": "2014-06-05"},
            ])
            with self.assertRaisesRegex(UniverseFilesError, "BOTH active and delisted"):
                UniverseFilesBuilder(
                    tushare_dir=tmp_path,
                    delisted_registry_path=tmp_path / "registry.parquet",
                    output_dir=tmp_path,
                ).build()

    def test_missing_active_parquet_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_registry(tmp_path / "registry.parquet", [])
            with self.assertRaisesRegex(UniverseFilesError, "active_stocks"):
                UniverseFilesBuilder(
                    tushare_dir=tmp_path,
                    delisted_registry_path=tmp_path / "registry.parquet",
                    output_dir=tmp_path,
                ).build()

    def test_missing_registry_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_active(tmp_path / "active_stocks.parquet",
                          [("600519.SH", "20010827")])
            with self.assertRaisesRegex(UniverseFilesError, "delisted_registry|registry"):
                UniverseFilesBuilder(
                    tushare_dir=tmp_path,
                    delisted_registry_path=tmp_path / "absent.parquet",
                    output_dir=tmp_path,
                ).build()

    def test_unparseable_active_list_date_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_active(tmp_path / "active_stocks.parquet", [
                ("600519.SH", "rotten"),  # bad date
            ])
            _write_registry(tmp_path / "registry.parquet", [])
            with self.assertRaisesRegex(UniverseFilesError, "active stocks row"):
                UniverseFilesBuilder(
                    tushare_dir=tmp_path,
                    delisted_registry_path=tmp_path / "registry.parquet",
                    output_dir=tmp_path,
                ).build()

    def test_no_tmp_file_left_after_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_active(tmp_path / "active_stocks.parquet",
                          [("600519.SH", "20010827")])
            _write_registry(tmp_path / "registry.parquet", [])
            UniverseFilesBuilder(
                tushare_dir=tmp_path,
                delisted_registry_path=tmp_path / "registry.parquet",
                output_dir=tmp_path,
            ).build()
            tmp_files = list((tmp_path / "instruments").glob("*.tmp"))
        self.assertEqual(tmp_files, [])

    def test_output_sorted_by_ticker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_active(tmp_path / "active_stocks.parquet", [
                ("600519.SH", "20010827"),
                ("000001.SZ", "19910403"),
                ("300750.SZ", "20180611"),
            ])
            _write_registry(tmp_path / "registry.parquet", [
                {"ticker": "SH600087", "list_date": "1997-06-12",
                 "delist_date": "2014-06-05"},
            ])

            UniverseFilesBuilder(
                tushare_dir=tmp_path,
                delisted_registry_path=tmp_path / "registry.parquet",
                output_dir=tmp_path,
            ).build()
            lines = (tmp_path / "instruments" / "all.txt").read_text(encoding="utf-8").splitlines()
            tickers = [L.split("\t")[0] for L in lines]
            self.assertEqual(tickers, sorted(tickers))


if __name__ == "__main__":
    unittest.main()
