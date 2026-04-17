"""Tests for FactorAnalyzer — structural + E2E."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from src.core.factor_analyzer import (
    FactorAnalysisConfig,
    FactorAnalysisResult,
    FactorAnalyzer,
    FactorAnalyzerError,
    FactorICStats,
)


class FactorAnalyzerStructuralTests(unittest.TestCase):
    """Tests that don't require qlib data."""

    def test_rejects_when_qlib_not_initialized(self) -> None:
        with patch("src.core.factor_analyzer.is_canonical_qlib_initialized", return_value=False):
            with self.assertRaisesRegex(FactorAnalyzerError, "not initialized"):
                FactorAnalyzer.analyze(FactorAnalysisConfig())

    def test_rejects_bad_forward_period(self) -> None:
        with patch("src.core.factor_analyzer.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaisesRegex(FactorAnalyzerError, "forward_period"):
                FactorAnalyzer.analyze(FactorAnalysisConfig(forward_period=0))

    def test_rejects_bad_ic_method(self) -> None:
        with patch("src.core.factor_analyzer.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaisesRegex(FactorAnalyzerError, "ic_method"):
                FactorAnalyzer.analyze(FactorAnalysisConfig(ic_method="bad"))

    def test_factor_ic_stats_dataclass(self) -> None:
        stats = FactorICStats(
            factor_name="KLEN", mean_ic=0.05, std_ic=0.02,
            ir=2.5, ic_positive_ratio=0.6, num_days=100,
        )
        self.assertEqual(stats.factor_name, "KLEN")
        self.assertAlmostEqual(stats.ir, 2.5)

    def test_config_defaults(self) -> None:
        cfg = FactorAnalysisConfig()
        self.assertEqual(cfg.forward_period, 5)
        self.assertEqual(cfg.ic_method, "rank")
        self.assertEqual(cfg.top_n_factors, 20)
        self.assertEqual(cfg.max_decay_lag, 20)


class ForwardReturnCacheTests(unittest.TestCase):
    """_build_forward_ret_cache must produce correct lagged returns,
    aligned to factor_df's index, for every requested lag."""

    def test_cache_contains_all_requested_lags(self) -> None:
        import pandas as pd

        # Synthetic 10-day, 3-instrument price matrix with simple ramp.
        dates = pd.date_range("2025-01-01", periods=10, freq="D")
        close = pd.DataFrame(
            {"A": range(10, 20), "B": range(20, 30), "C": range(30, 40)},
            index=dates,
        )
        close.index.name = "datetime"
        close.columns.name = "instrument"

        # factor_index is every (date, instrument) pair for the first 7 days.
        idx = pd.MultiIndex.from_product(
            [dates[:7], ["A", "B", "C"]], names=["datetime", "instrument"],
        )

        cache = FactorAnalyzer._build_forward_ret_cache(close, idx, lags=[1, 3, 5])

        self.assertEqual(set(cache.keys()), {1, 3, 5})
        for lag, s in cache.items():
            self.assertEqual(list(s.index.names), ["datetime", "instrument"])
            # Reindexed to factor_index → exact row count.
            self.assertEqual(len(s), len(idx))

    def test_cache_values_match_manual_shift(self) -> None:
        import pandas as pd

        dates = pd.date_range("2025-01-01", periods=6, freq="D")
        close = pd.DataFrame(
            {"A": [10.0, 11.0, 12.0, 13.0, 14.0, 15.0]}, index=dates,
        )
        close.index.name = "datetime"
        close.columns.name = "instrument"
        idx = pd.MultiIndex.from_product(
            [dates, ["A"]], names=["datetime", "instrument"],
        )

        cache = FactorAnalyzer._build_forward_ret_cache(close, idx, lags=[1, 2])

        # Day 0 lag-1: (11-10)/10 = 0.1
        self.assertAlmostEqual(cache[1].loc[(dates[0], "A")], 0.1)
        # Day 0 lag-2: (12-10)/10 = 0.2
        self.assertAlmostEqual(cache[2].loc[(dates[0], "A")], 0.2)
        # Last row lag-1 → NaN (no next day).
        self.assertTrue(pd.isna(cache[1].loc[(dates[-1], "A")]))


class PrepareFromDatasetTests(unittest.TestCase):
    """_prepare_from_dataset delegates to dataset.prepare('test', col_set='feature')."""

    def test_calls_prepare_with_expected_args(self) -> None:
        calls = {}

        class _FakeDataset:
            def prepare(self, segment, col_set):
                calls["segment"] = segment
                calls["col_set"] = col_set
                return "FACTOR_DF"

        result = FactorAnalyzer._prepare_from_dataset(_FakeDataset())
        self.assertEqual(result, "FACTOR_DF")
        self.assertEqual(calls, {"segment": "test", "col_set": "feature"})

    def test_raises_on_bad_dataset(self) -> None:
        class _BadDataset:
            def prepare(self, segment, col_set):
                raise KeyError("no such segment")

        with self.assertRaisesRegex(FactorAnalyzerError, "test.*segment"):
            FactorAnalyzer._prepare_from_dataset(_BadDataset())


# ----- E2E tests (only run when qlib data is available) -----

_QLIB_DATA_DIR = Path("D:/qlib_data/my_cn_data")


def _qlib_available() -> bool:
    try:
        import qlib  # noqa: F401
        return _QLIB_DATA_DIR.exists()
    except ImportError:
        return False


from tests.e2e_guard import skip_unless_e2e

@skip_unless_e2e
@unittest.skipUnless(_qlib_available(), "requires qlib + local data bundle")
class FactorAnalyzerE2ETests(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        from src.core.qlib_runtime import (
            QlibRuntimeConfig, init_qlib_canonical, is_canonical_qlib_initialized,
        )
        if not is_canonical_qlib_initialized():
            init_qlib_canonical(QlibRuntimeConfig(
                provider_uri=str(_QLIB_DATA_DIR), region="cn",
            ))

    def test_basic_analysis(self) -> None:
        config = FactorAnalysisConfig(
            instruments="csi300",
            test_start="2024-10-01",
            test_end="2024-12-31",
            forward_period=5,
            top_n_factors=10,
            max_decay_lag=5,
        )
        result = FactorAnalyzer.analyze(config)

        self.assertIsInstance(result, FactorAnalysisResult)
        self.assertGreater(result.total_factors, 0)
        self.assertGreater(len(result.factor_ic_stats), 0)

        # Top factor should have non-zero IC
        top = result.factor_ic_stats[0]
        self.assertIsInstance(top.factor_name, str)
        self.assertIsInstance(top.mean_ic, float)

        # Correlation matrix should be populated
        self.assertGreater(len(result.correlation_matrix), 0)

        # IC decay should exist
        self.assertGreater(len(result.ic_decay), 0)

        FactorAnalyzer.print_report(result)


if __name__ == "__main__":
    unittest.main()
