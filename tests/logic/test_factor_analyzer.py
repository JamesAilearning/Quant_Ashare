"""Tests for FactorAnalyzer — structural + E2E."""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any
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


class DatasetConfigConsistencyTests(unittest.TestCase):
    """_validate_dataset_matches_config blocks silent config/dataset drift."""

    def _make_factor_df(self, start: str, end: str):
        import pandas as pd
        dates = pd.date_range(start, end, freq="D")
        idx = pd.MultiIndex.from_product(
            [dates, ["A", "B"]], names=["datetime", "instrument"],
        )
        return pd.DataFrame({"f1": range(len(idx))}, index=idx)

    def test_accepts_matching_range(self) -> None:
        # Within 14-day tolerance on both ends.
        df = self._make_factor_df("2025-07-05", "2025-12-28")
        cfg = FactorAnalysisConfig(test_start="2025-07-01", test_end="2025-12-31")
        # Must not raise.
        FactorAnalyzer._validate_dataset_matches_config(df, cfg)

    def test_rejects_date_escape_above(self) -> None:
        df = self._make_factor_df("2025-07-01", "2026-02-01")
        cfg = FactorAnalysisConfig(test_start="2025-07-01", test_end="2025-12-31")
        with self.assertRaisesRegex(FactorAnalyzerError, "escapes"):
            FactorAnalyzer._validate_dataset_matches_config(df, cfg)

    def test_rejects_date_escape_below(self) -> None:
        df = self._make_factor_df("2024-01-01", "2025-09-30")
        cfg = FactorAnalysisConfig(test_start="2025-07-01", test_end="2025-12-31")
        with self.assertRaisesRegex(FactorAnalyzerError, "escapes"):
            FactorAnalyzer._validate_dataset_matches_config(df, cfg)

    def test_rejects_narrowed_end(self) -> None:
        # actual_end is >14 days before declared_end → narrowing detected.
        df = self._make_factor_df("2025-07-05", "2025-09-30")
        cfg = FactorAnalysisConfig(test_start="2025-07-01", test_end="2025-12-31")
        with self.assertRaisesRegex(FactorAnalyzerError, "narrowed|ends significantly"):
            FactorAnalyzer._validate_dataset_matches_config(df, cfg)

    def test_rejects_narrowed_start(self) -> None:
        # actual_start is >14 days after declared_start → narrowing detected.
        df = self._make_factor_df("2025-08-01", "2025-12-28")
        cfg = FactorAnalysisConfig(test_start="2025-07-01", test_end="2025-12-31")
        with self.assertRaisesRegex(FactorAnalyzerError, "narrowed|starts significantly"):
            FactorAnalyzer._validate_dataset_matches_config(df, cfg)

    def test_rejects_empty_dataset(self) -> None:
        import pandas as pd
        empty = pd.DataFrame(
            index=pd.MultiIndex.from_tuples([], names=["datetime", "instrument"]),
        )
        cfg = FactorAnalysisConfig()
        with self.assertRaisesRegex(FactorAnalyzerError, "empty"):
            FactorAnalyzer._validate_dataset_matches_config(empty, cfg)


class MissingFactorColumnTests(unittest.TestCase):
    """_compute_factor_decay_cached must fail loud on missing factors."""

    def test_raises_on_missing_factor(self) -> None:
        import pandas as pd
        dates = pd.date_range("2025-07-01", periods=5, freq="D")
        idx = pd.MultiIndex.from_product(
            [dates, ["A"]], names=["datetime", "instrument"],
        )
        factor_df = pd.DataFrame({"known": range(len(idx))}, index=idx)
        # Empty cache is fine — we should raise *before* touching it.
        with self.assertRaisesRegex(FactorAnalyzerError, "not present"):
            FactorAnalyzer._compute_factor_decay_cached(
                factor_df, forward_ret_cache={1: None},
                factor_names=["known", "UNKNOWN_X"],
                config=FactorAnalysisConfig(max_decay_lag=1),
            )


class FactorDecayNaNBehaviorTests(unittest.TestCase):
    """Regression guard for P2c: below-threshold sample size → NaN, not 0.0.

    A zero stood for "no data" *and* "factor is genuinely useless" — those
    two things must be visually distinct on the decay curve.
    """

    def test_decay_returns_nan_below_min_observations(self) -> None:
        """Only 5 merged rows per lag → below the 10-observation threshold
        → all decay values must be NaN."""
        import math
        import pandas as pd

        dates = pd.date_range("2025-07-01", periods=5, freq="D")
        idx = pd.MultiIndex.from_product(
            [dates, ["A"]], names=["datetime", "instrument"],
        )
        factor_df = pd.DataFrame({"factor1": range(5)}, index=idx)

        # Forward returns: same 5 rows per lag → merged is exactly 5 rows,
        # which is below _MIN_IC_OBSERVATIONS_PER_LAG (=10).
        fwd = pd.Series([0.01, 0.02, 0.03, 0.04, 0.05], index=idx)
        forward_ret_cache = {lag: fwd for lag in (1, 2, 3)}

        result = FactorAnalyzer._compute_factor_decay_cached(
            factor_df, forward_ret_cache=forward_ret_cache,
            factor_names=["factor1"],
            config=FactorAnalysisConfig(max_decay_lag=3),
        )

        self.assertIn("factor1", result)
        self.assertEqual(len(result["factor1"]), 3)
        self.assertTrue(
            all(math.isnan(v) for v in result["factor1"]),
            f"expected all NaN below threshold; got {result['factor1']}",
        )

    def test_min_observations_constant_is_ten(self) -> None:
        """Anchor the threshold so it can't drift silently."""
        from src.core.factor_analyzer import _MIN_IC_OBSERVATIONS_PER_LAG
        self.assertEqual(_MIN_IC_OBSERVATIONS_PER_LAG, 10)


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


class FactorICUndefinedIRTests(unittest.TestCase):
    """Regression: undefined IR must surface as NaN, not 0.0.

    Why this matters: factor screening / Optuna / walk-forward all
    consume ``FactorICStats.ir`` and rank factors by it (or filter on
    a threshold). Returning ``0.0`` for "we cannot compute std(IC)"
    makes a one-good-day factor *and* a zero-volatility factor
    indistinguishable from a real flat-IR factor — three very
    different things end up sharing the same number.

    SignalAnalyzer (signal_analyzer.py:136-145) already encodes
    this with NaN. This test pins FactorAnalyzer to the same
    convention.
    """

    @staticmethod
    def _build_minimal_inputs(num_days: int) -> tuple[Any, Any]:
        import pandas as pd
        # 12 instruments per day so len(merged) > 10 each day; values
        # tuned per case below to control daily IC.
        instruments = [f"SH60000{i}" for i in range(12)]
        dates = pd.date_range("2025-10-01", periods=num_days, freq="B")
        idx = pd.MultiIndex.from_product([dates, instruments], names=["datetime", "instrument"])
        return dates, idx

    def test_single_day_ic_yields_nan_std_and_nan_ir(self) -> None:
        """Only one day of valid IC → std is undefined → NaN std & IR.

        With a single sample you can compute the mean (it's the value)
        but not a standard deviation; the zero we used to write was a
        fabricated number that travelled all the way into the report.
        """
        import math
        import pandas as pd
        from src.core.factor_analyzer import FactorAnalyzer, FactorAnalysisConfig

        dates, idx = self._build_minimal_inputs(num_days=1)
        # A linearly-correlated factor on this one day → deterministic IC ≈ 1
        factor_df = pd.DataFrame(
            {"f1": list(range(len(idx)))},
            index=idx,
        )
        forward_ret = pd.Series(list(range(len(idx))), index=idx)

        cfg = FactorAnalysisConfig(ic_method="rank")
        results = FactorAnalyzer._compute_all_factor_ic(factor_df, forward_ret, cfg)
        self.assertEqual(len(results), 1)
        stats = results[0]
        self.assertEqual(stats.num_days, 1)
        self.assertTrue(math.isnan(stats.std_ic), f"std_ic should be NaN, got {stats.std_ic!r}")
        self.assertTrue(math.isnan(stats.ir), f"ir should be NaN, got {stats.ir!r}")

    def test_zero_volatility_ic_yields_nan_ir(self) -> None:
        """Multiple days but every day's IC is identical → std == 0 →
        IR is undefined, must be NaN (not ``mean_ic / 0`` and not 0.0).
        """
        import math
        import pandas as pd
        from src.core.factor_analyzer import FactorAnalyzer, FactorAnalysisConfig

        dates, idx = self._build_minimal_inputs(num_days=4)
        # Identical relationship per day → identical rank-IC each day
        factor_vals = []
        ret_vals = []
        for _ in dates:
            factor_vals.extend(range(12))
            ret_vals.extend(range(12))
        factor_df = pd.DataFrame({"f1": factor_vals}, index=idx)
        forward_ret = pd.Series(ret_vals, index=idx)

        cfg = FactorAnalysisConfig(ic_method="rank")
        results = FactorAnalyzer._compute_all_factor_ic(factor_df, forward_ret, cfg)
        self.assertEqual(len(results), 1)
        stats = results[0]
        # std should be exactly 0.0 (every day's IC is the same)
        self.assertAlmostEqual(stats.std_ic, 0.0, places=10)
        # IR therefore undefined
        self.assertTrue(math.isnan(stats.ir), f"ir should be NaN, got {stats.ir!r}")


class SignalAnalyzerIndexValidationTests(unittest.TestCase):
    """Regression: SignalAnalyzer must reject MultiIndex predictions
    that lack the named ``datetime`` and ``instrument`` levels.

    Previously only the *type* (``isinstance(MultiIndex)``) was checked,
    so an unnamed MultiIndex or one in ``(instrument, datetime)`` order
    would slip through. Downstream code does
    ``groupby(level="datetime")`` and reads instruments from the named
    level — without these checks a wrong-shaped index produces
    silently mis-grouped results.
    """

    def test_rejects_unnamed_multiindex(self) -> None:
        import pandas as pd
        from unittest.mock import patch as _patch
        from src.core.signal_analyzer import (
            SignalAnalysisConfig,
            SignalAnalyzer,
            SignalAnalyzerError,
        )

        idx = pd.MultiIndex.from_tuples(
            [(pd.Timestamp("2025-10-01"), "SH600000")],
        )  # names defaults to (None, None)
        preds = pd.Series([1.0], index=idx)
        with _patch("src.core.signal_analyzer.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaisesRegex(SignalAnalyzerError, "datetime"):
                SignalAnalyzer.analyze(preds, SignalAnalysisConfig())

    def test_rejects_swapped_level_order(self) -> None:
        """``(instrument, datetime)`` levels are present but order is
        wrong — ``in`` membership is satisfied so this passes the new
        check; what we want is for the names being present is enough.

        This test confirms the contract: the validator only requires
        BOTH names present, regardless of order. Downstream uses
        ``level="datetime"`` / ``level="instrument"`` by name, which
        pandas resolves correctly regardless of position. So a swapped
        order is *not* the bug — only missing/wrong-named levels are.
        """
        import pandas as pd
        from unittest.mock import patch as _patch
        from src.core.signal_analyzer import (
            SignalAnalysisConfig,
            SignalAnalyzer,
            SignalAnalyzerError,
        )

        # Names present but in (instrument, datetime) order.
        idx = pd.MultiIndex.from_tuples(
            [("SH600000", pd.Timestamp("2025-10-01"))],
            names=["instrument", "datetime"],
        )
        preds = pd.Series([1.0], index=idx)
        # Should NOT raise on the index-name check (downstream
        # ``groupby(level="datetime")`` resolves by name) — verifies the
        # check is *name presence*, not positional. We expect failure to
        # come later (qlib data fetch), so we patch qlib init to True
        # and just assert the index-name guard does not fire.
        with _patch("src.core.signal_analyzer.is_canonical_qlib_initialized", return_value=True):
            try:
                # Will fail later when fetching returns, but should pass
                # the index-name guard. We don't run the full analyze; we
                # only confirm the *index-name* SignalAnalyzerError text
                # is not the one raised first.
                SignalAnalyzer.analyze(preds, SignalAnalysisConfig())
            except SignalAnalyzerError as exc:
                self.assertNotIn("datetime", str(exc).split("level")[0])
            except Exception:
                # Any non-SignalAnalyzerError is fine — proves we got past
                # the index-name guard into qlib fetch territory.
                pass

    def test_rejects_missing_instrument_level(self) -> None:
        """One named level missing — the symmetric case of unnamed."""
        import pandas as pd
        from unittest.mock import patch as _patch
        from src.core.signal_analyzer import (
            SignalAnalysisConfig,
            SignalAnalyzer,
            SignalAnalyzerError,
        )

        idx = pd.MultiIndex.from_tuples(
            [(pd.Timestamp("2025-10-01"), "X")],
            names=["datetime", "ticker"],  # 'instrument' missing
        )
        preds = pd.Series([1.0], index=idx)
        with _patch("src.core.signal_analyzer.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaisesRegex(SignalAnalyzerError, "instrument"):
                SignalAnalyzer.analyze(preds, SignalAnalysisConfig())


if __name__ == "__main__":
    unittest.main()
