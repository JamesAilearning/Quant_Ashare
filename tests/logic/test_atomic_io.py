"""Unit tests for the shared atomic-write helpers (refactor-audit Tier-1)."""

import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.data._atomic_io import atomic_write_parquet  # noqa: E402


class AtomicWriteParquetTests(unittest.TestCase):

    def test_roundtrip_and_no_tmp_left(self) -> None:
        df = pd.DataFrame({"ts_code": ["600000.SH"], "x": [1.0]})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.parquet"
            atomic_write_parquet(df, path)
            pd.testing.assert_frame_equal(pd.read_parquet(path), df)
            self.assertEqual(list(Path(tmp).glob("*.tmp")), [])  # temp swept

    def test_creates_parent_dirs(self) -> None:
        df = pd.DataFrame({"a": [1]})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "deep" / "out.parquet"
            atomic_write_parquet(df, path)  # parent did not exist
            self.assertTrue(path.exists())

    def test_overwrites_existing_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.parquet"
            atomic_write_parquet(pd.DataFrame({"a": [1]}), path)
            atomic_write_parquet(pd.DataFrame({"a": [2, 3]}), path)
            self.assertEqual(list(pd.read_parquet(path)["a"]), [2, 3])
            self.assertEqual(list(Path(tmp).glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
