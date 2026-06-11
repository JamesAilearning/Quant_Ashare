"""Tests for the embedded snapshot_date contract (P3-5)."""

import sys
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.active_stocks_snapshot import (  # noqa: E402
    SNAPSHOT_DATE_COLUMN,
    SnapshotDateError,
    embedded_snapshot_date,
)


class EmbeddedSnapshotDateTests(unittest.TestCase):

    def test_single_value_roundtrip(self) -> None:
        df = pd.DataFrame({
            "ts_code": ["000001.SZ", "600519.SH"],
            SNAPSHOT_DATE_COLUMN: ["20260610", "20260610"],
        })
        self.assertEqual(embedded_snapshot_date(df), date(2026, 6, 10))

    def test_missing_column_is_old_format_loud(self) -> None:
        df = pd.DataFrame({"ts_code": ["000001.SZ"]})
        with self.assertRaisesRegex(SnapshotDateError, "no embedded"):
            embedded_snapshot_date(df, source="active_stocks.parquet")

    def test_all_null_or_empty_loud(self) -> None:
        with self.assertRaisesRegex(SnapshotDateError, "no value"):
            embedded_snapshot_date(
                pd.DataFrame({SNAPSHOT_DATE_COLUMN: [None, None]}),
            )
        with self.assertRaisesRegex(SnapshotDateError, "no value"):
            embedded_snapshot_date(
                pd.DataFrame({SNAPSHOT_DATE_COLUMN: pd.Series([], dtype=str)}),
            )

    def test_multiple_distinct_values_loud(self) -> None:
        df = pd.DataFrame({SNAPSHOT_DATE_COLUMN: ["20260609", "20260610"]})
        with self.assertRaisesRegex(SnapshotDateError, "distinct"):
            embedded_snapshot_date(df)

    def test_partially_null_column_loud(self) -> None:
        # codex P2: a hand-merged old+new file (some rows stamped, some null)
        # must fail loud — dropna() must not bless the surviving date.
        df = pd.DataFrame({
            "ts_code": ["000001.SZ", "000002.SZ"],
            SNAPSHOT_DATE_COLUMN: ["20260610", None],
        })
        with self.assertRaisesRegex(SnapshotDateError, "null on 1 of 2"):
            embedded_snapshot_date(df)

    def test_non_yyyymmdd_loud(self) -> None:
        df = pd.DataFrame({SNAPSHOT_DATE_COLUMN: ["2026-06-10"]})
        with self.assertRaisesRegex(SnapshotDateError, "not YYYYMMDD"):
            embedded_snapshot_date(df)

    def test_seven_digit_value_loud_not_reinterpreted(self) -> None:
        # codex P2: %Y%m%d would leniently parse '2026061' as 2026-06-01 — the
        # exact-8-digit shape check must fire first, not reinterpret the stamp.
        df = pd.DataFrame({SNAPSHOT_DATE_COLUMN: ["2026061"]})
        with self.assertRaisesRegex(SnapshotDateError, "exactly 8 digits"):
            embedded_snapshot_date(df)


if __name__ == "__main__":
    unittest.main()
