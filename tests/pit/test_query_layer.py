"""Tests for ``src.pit.query.PITDataProvider``.

These tests build a tiny synthetic qlib provider by writing the bin
format directly (no Phase B import), then exercise the PIT query
layer end-to-end. Keeping the test self-contained avoids a cross-PR
import dependency while Phase B (PR #103) is still in review.
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

from src.pit.query import (  # noqa: E402
    PITDataProvider,
    PITDataProviderError,
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
        })
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)


def _build_minimal_provider(tmp_path: Path) -> tuple[Path, Path]:
    """Hand-construct a qlib provider with 3 tickers.

    Format matches what Phase B.2's QlibBinBuilder writes, but we
    skip the builder dependency so this test runs before PR #103
    merges. See `src.data.tushare.provider_bundle.publisher
    ._write_qlib_bundle` for the canonical bin layout.

    Tickers:
      - SH600519 (active) — present 2020-01-02 through 2020-01-10
      - SH600087 (delisted 2020-01-06) — valid 2020-01-02..2020-01-06,
        NaN-padded 2020-01-07..2020-01-10
      - SH600247 (delisted 2020-01-08) — valid through 2020-01-08,
        NaN 2020-01-09..2020-01-10

    Returns (provider_dir, registry_path).
    """
    provider = tmp_path / "provider"
    (provider / "calendars").mkdir(parents=True)
    (provider / "instruments").mkdir(parents=True)
    (provider / "features").mkdir(parents=True)

    # Calendar — 7 trading days (skipping weekend 01-04/01-05)
    calendar = ["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07",
                "2020-01-08", "2020-01-09", "2020-01-10"]
    (provider / "calendars" / "day.txt").write_text(
        "\n".join(calendar) + "\n", encoding="utf-8",
    )

    # Instruments file
    (provider / "instruments" / "all.txt").write_text(
        "\n".join([
            "SH600519\t2020-01-02\t2099-12-31",
            "SH600087\t2020-01-02\t2020-01-06",
            "SH600247\t2020-01-02\t2020-01-08",
        ]) + "\n",
        encoding="utf-8",
    )

    def _write_ticker_bin(qlib_ticker: str, valid_through_idx: int,
                         base: float) -> None:
        """Write 6 .day.bin files for one ticker.

        valid_through_idx — last calendar position where the ticker
        has valid data; positions after are NaN.
        """
        feat_dir = provider / "features" / qlib_ticker.lower()
        feat_dir.mkdir(parents=True, exist_ok=True)
        start_idx = 0
        # Bin payload extends through the LAST calendar position
        # (matches Phase B.2 fix per smoke finding)
        n = len(calendar)
        for i, field in enumerate(("open", "high", "low", "close", "volume", "money")):
            payload = np.array(
                [base + i * 0.1] * (valid_through_idx + 1)
                + [np.nan] * (n - valid_through_idx - 1),
                dtype="<f4",
            )
            full = np.hstack([[float(start_idx)], payload]).astype("<f4")
            full.tofile(feat_dir / f"{field}.day.bin")

    _write_ticker_bin("SH600519", valid_through_idx=6, base=100.0)  # active
    _write_ticker_bin("SH600087", valid_through_idx=2, base=50.0)   # delist 2020-01-06
    _write_ticker_bin("SH600247", valid_through_idx=4, base=30.0)   # delist 2020-01-08

    registry_path = tmp_path / "registry.parquet"
    _write_registry(registry_path, [
        {"ticker": "SH600087", "list_date": "2010-01-01",
         "delist_date": "2020-01-06"},
        {"ticker": "SH600247", "list_date": "2010-01-01",
         "delist_date": "2020-01-08"},
    ])
    return provider, registry_path


def _build_tiny_provider(tmp_path: Path) -> tuple[Path, Path]:
    """Back-compat alias retained so tests below read fluently."""
    return _build_minimal_provider(tmp_path)


# ---------------------------------------------------------------------
# Module-level provider — qlib's canonical runtime is a process-singleton
# (src/core/qlib_runtime.py) and refuses re-init with a different
# provider_uri. We build ONE provider for the whole module and share it
# across every test class that needs a working PITDataProvider.
# ---------------------------------------------------------------------

_MODULE_TMP: tempfile.TemporaryDirectory | None = None
_SHARED_PIT: PITDataProvider | None = None


def setUpModule() -> None:  # noqa: N802 — unittest API
    global _MODULE_TMP, _SHARED_PIT
    _MODULE_TMP = tempfile.TemporaryDirectory()
    tmp_path = Path(_MODULE_TMP.name)
    provider, registry = _build_minimal_provider(tmp_path)
    _SHARED_PIT = PITDataProvider(
        provider_uri=provider, delisted_registry_path=registry,
    )


def tearDownModule() -> None:  # noqa: N802 — unittest API
    global _MODULE_TMP, _SHARED_PIT
    _SHARED_PIT = None
    if _MODULE_TMP is not None:
        _MODULE_TMP.cleanup()
        _MODULE_TMP = None


def _pit() -> PITDataProvider:
    if _SHARED_PIT is None:
        raise RuntimeError("setUpModule was not called; module-level fixture missing")
    return _SHARED_PIT


class ConstructionTests(unittest.TestCase):
    """Pre-init failure paths — these tests construct PITDataProvider
    with deliberately broken inputs and assert the correct error
    surfaces BEFORE qlib gets initialised. Critically: the canonical
    qlib runtime is a process-singleton (see src/core/qlib_runtime.py),
    so these tests must NOT successfully initialise qlib — otherwise
    they pin the singleton's provider_uri and break every test that
    needs a DIFFERENT provider_uri.
    """

    def test_missing_registry_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # Don't build the provider — _load_registry runs first and
            # raises, so qlib never initialises.
            with self.assertRaisesRegex(PITDataProviderError, "delisted registry"):
                PITDataProvider(
                    provider_uri=tmp_path / "no_provider_here",
                    delisted_registry_path=tmp_path / "absent.parquet",
                )

    def test_missing_provider_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_registry(tmp_path / "registry.parquet", [])
            with self.assertRaisesRegex(PITDataProviderError, "not a valid qlib provider"):
                PITDataProvider(
                    provider_uri=tmp_path / "no_provider_here",
                    delisted_registry_path=tmp_path / "registry.parquet",
                )

    def test_registry_missing_required_columns_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bad = pd.DataFrame({"wrong_col": ["x"]})
            bad.to_parquet(tmp_path / "bad_registry.parquet", index=False)
            with self.assertRaisesRegex(PITDataProviderError, "missing required columns"):
                PITDataProvider(
                    provider_uri=tmp_path / "no_provider_here",
                    delisted_registry_path=tmp_path / "bad_registry.parquet",
                )


class _UsesSharedProvider:
    """Marker mixin — test classes that need the module-level provider
    inherit this for self-documenting purposes. Access via ``_pit()``.
    """

    @property
    def _pit(self) -> PITDataProvider:
        return _pit()


class PostDelistMaskTests(_UsesSharedProvider, unittest.TestCase):
    """The load-bearing §4.3.2 mitigation — qlib operators that leak
    past delist_date are masked to NaN by the PIT provider's
    post-process pass."""

    def setUp(self) -> None:
        self._pit._cache.clear()

    def test_close_returns_nan_past_delist(self) -> None:
        df = self._pit.get_features(["$close"], "2020-01-02", "2020-01-10")
        sh087 = df.xs("SH600087", level="instrument")
        valid_dates = sh087.dropna().index.strftime("%Y-%m-%d").tolist()
        # SH600087 delist 2020-01-06: 02/03/06 valid, 07+ NaN
        self.assertEqual(valid_dates, ["2020-01-02", "2020-01-03",
                                       "2020-01-06"])

    def test_mean_window_does_not_leak_past_delist(self) -> None:
        """Regression for the Phase B smoke finding: ``Mean($close, 3)``
        on day delist+1 would normally return a partial-window mean
        (qlib default min_periods<N). The PIT provider's mask must
        set it to NaN.
        """
        df = self._pit.get_features(
            ["Mean($close, 3)"], "2020-01-02", "2020-01-10",
        )
        sh087 = df.xs("SH600087", level="instrument")
        post = sh087[sh087.index > pd.Timestamp("2020-01-06")]
        self.assertTrue(
            post.isna().all().all(),
            f"Mean($close, 3) leaked past delist: {post.to_dict()}",
        )

    def test_active_ticker_unaffected(self) -> None:
        df = self._pit.get_features(["$close"], "2020-01-02", "2020-01-10")
        sh519 = df.xs("SH600519", level="instrument")
        self.assertFalse(sh519.isna().any().any(),
                         f"active ticker has unexpected NaN: {sh519}")


class GetUniverseTests(_UsesSharedProvider, unittest.TestCase):

    def test_universe_excludes_post_delisted_tickers(self) -> None:
        # On 2020-01-08: SH600087 already delisted on 2020-01-06,
        # SH600247 delist_date IS 2020-01-08 (still tradable that day),
        # SH600519 active.
        universe = self._pit.get_universe("2020-01-08")
        self.assertIn("SH600519", universe)
        self.assertNotIn("SH600087", universe)
        # SH600247: delist_date == 2020-01-08; per the docstring
        # contract, the delist_date itself is INCLUDED (last valid
        # trading day per Phase B's bin contract).
        self.assertIn("SH600247", universe)

    def test_universe_excludes_day_after_delist(self) -> None:
        # On 2020-01-09: SH600247 was delisted on 2020-01-08, so
        # the next trading day MUST exclude it.
        universe = self._pit.get_universe("2020-01-09")
        self.assertIn("SH600519", universe)
        self.assertNotIn("SH600247", universe)
        self.assertNotIn("SH600087", universe)

    def test_universe_range_calls_per_day(self) -> None:
        ranges = self._pit.get_universe_range("2020-01-02", "2020-01-10")
        # At least 5 trading days returned
        self.assertGreaterEqual(len(ranges), 5)
        for date, tickers in ranges.items():
            self.assertIn("SH600519", tickers,
                          f"{date}: SH600519 missing from active universe")


class CacheBehaviorTests(_UsesSharedProvider, unittest.TestCase):

    def setUp(self) -> None:
        self._pit._cache.clear()

    def test_repeated_query_hits_cache(self) -> None:
        args = (["$close"], "2020-01-02", "2020-01-10")
        df1 = self._pit.get_features(*args)
        df2 = self._pit.get_features(*args)
        pd.testing.assert_frame_equal(df1, df2)
        self.assertIsNot(df1, df2)
        self.assertEqual(len(self._pit._cache), 1)

    def test_field_order_does_not_affect_cache_key(self) -> None:
        self._pit.get_features(["$close", "$open"], "2020-01-02", "2020-01-10")
        self._pit.get_features(["$open", "$close"], "2020-01-02", "2020-01-10")
        # frozenset(fields) normalises ordering -> one cache entry
        self.assertEqual(len(self._pit._cache), 1)


class APIErrorTests(_UsesSharedProvider, unittest.TestCase):

    def test_invalid_align_raises(self) -> None:
        with self.assertRaisesRegex(PITDataProviderError, "unknown align"):
            self._pit.get_features(["$close"], "2020-01-02", "2020-01-10",
                                   align="frobnicate")

    def test_start_after_end_raises(self) -> None:
        with self.assertRaisesRegex(PITDataProviderError, "start.*>.*end"):
            self._pit.get_features(["$close"], "2020-01-10", "2020-01-02")


if __name__ == "__main__":
    unittest.main()
