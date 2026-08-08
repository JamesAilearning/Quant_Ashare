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

    def test_transient_write_error_is_retried(self) -> None:
        # A multi-hour backfill must not die on one transient system I/O
        # stall (observed 2026-08-08: OSError EINVAL mid-run on a healthy
        # local NVMe, unreproducible in isolation).
        import unittest.mock as mock

        from src.data import _atomic_io
        calls = {"n": 0}
        real = pd.DataFrame.to_parquet

        def flaky(self, path, *a, **kw):  # noqa: ANN001
            calls["n"] += 1
            if calls["n"] < 3:
                raise OSError(22, "Invalid argument")
            return real(self, path, *a, **kw)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.parquet"
            with mock.patch.object(_atomic_io, "_WRITE_BACKOFF_SECONDS", 0),                     mock.patch.object(pd.DataFrame, "to_parquet", flaky):
                atomic_write_parquet(pd.DataFrame({"a": [7]}), path)
            self.assertEqual(3, calls["n"])            # 2 failures, 1 win
            self.assertEqual([7], list(pd.read_parquet(path)["a"]))
            self.assertEqual(list(Path(tmp).glob("*.tmp")), [])

    def test_persistent_write_error_raises_the_original(self) -> None:
        # Retrying must not turn a real, permanent failure into a
        # different exception — the caller has to see the true cause.
        import unittest.mock as mock

        from src.data import _atomic_io
        boom = OSError(22, "Invalid argument")

        def always_fail(self, path, *a, **kw):  # noqa: ANN001
            raise boom

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.parquet"
            with mock.patch.object(_atomic_io, "_WRITE_BACKOFF_SECONDS", 0),                     mock.patch.object(pd.DataFrame, "to_parquet", always_fail):
                with self.assertRaises(OSError) as ctx:
                    atomic_write_parquet(pd.DataFrame({"a": [1]}), path)
            self.assertIs(boom, ctx.exception)
            self.assertFalse(path.exists())            # never half-written
            self.assertEqual(list(Path(tmp).glob("*.tmp")), [])  # swept


if __name__ == "__main__":
    unittest.main()
