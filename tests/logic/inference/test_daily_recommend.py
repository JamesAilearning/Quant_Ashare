"""Tests for the daily stock-recommendation inference path.

Two tiers (per AGENTS.md "E2E + synthetic unit twin"):

* **Always-on unit twins** (no qlib, no bundle): date resolution, the
  look-ahead self-guard, and buy-list ranking / topk / tradability /
  reason-labelling. These run in normal CI.
* **RUN_E2E real-bundle tests**: the strengthened look-ahead contract on
  the actual PIT bundle — (a) the as-of-T feature frame has no row dated
  > T, and (b) the normalized feature values for T are IDENTICAL whether
  or not data after T is loaded (proves normalization does not peek at
  the future). Real qlib feature loading is RUN_E2E-gated here exactly
  like the repo's other qlib-feature tests (test_backtest_runner).
"""

from __future__ import annotations

import os
import unittest

import numpy as np
import pandas as pd
import pytest

from src.inference.daily_recommend import (
    DailyRecommendationError,
    RecommendationConfig,
    assert_no_lookahead,
    build_recommendation,
    resolve_dates,
)

_RUN_E2E = os.environ.get("RUN_E2E") == "1"
_PIT_PROVIDER = "D:/qlib_data/my_cn_data_pit"
_PIT_REGISTRY = "D:/qlib_data/tushare_raw/delisted_registry.parquet"
_MODEL = "D:/stock/phase_b_artifacts/alpha158_lgb_pit.pkl"
_FIT_START, _FIT_END = "2018-01-02", "2023-12-20"


# ===========================================================================
# Unit twins — always run (no qlib bundle needed)
# ===========================================================================
class ResolveDatesTests(unittest.TestCase):
    """resolve_dates against a monkeypatched calendar (no real qlib init)."""

    _CAL = ["2025-06-25", "2025-06-26", "2025-06-27", "2025-06-30", "2025-07-01"]

    def test_default_as_of_is_last_day_and_has_no_entry(self) -> None:
        # Default picks the LAST calendar day, which by definition has no
        # T+1 -> explicit error (this is why real runs pass an earlier
        # --as-of). The error names the last day, confirming default = last.
        with self.assertRaisesRegex(DailyRecommendationError, "2025-07-01"):
            resolve_dates(None, calendar=self._CAL)

    def test_explicit_as_of_resolves_next_trading_day(self) -> None:
        t, entry = resolve_dates("2025-06-27", calendar=self._CAL)
        self.assertEqual(t, "2025-06-27")
        self.assertEqual(entry, "2025-06-30")  # skips the weekend gap

    def test_last_day_has_no_entry_date_errors(self) -> None:
        with self.assertRaisesRegex(DailyRecommendationError, "no next trading day"):
            resolve_dates("2025-07-01", calendar=self._CAL)

    def test_non_trading_day_rejected(self) -> None:
        with self.assertRaisesRegex(DailyRecommendationError, "not a trading day"):
            resolve_dates("2025-06-28", calendar=self._CAL)  # Saturday, not in calendar


def _frame_for(dates_instruments: list[tuple[str, str]]) -> pd.DataFrame:
    idx = pd.MultiIndex.from_tuples(
        [(pd.Timestamp(d), i) for d, i in dates_instruments],
        names=["datetime", "instrument"],
    )
    return pd.DataFrame({"feat0": range(len(idx))}, index=idx)


class AssertNoLookaheadTests(unittest.TestCase):
    def test_passes_when_max_equals_as_of(self) -> None:
        frame = _frame_for([("2025-06-30", "SH600000"), ("2025-06-30", "SZ000001")])
        self.assertEqual(assert_no_lookahead(frame, "2025-06-30"),
                         pd.Timestamp("2025-06-30"))

    def test_raises_when_future_row_present(self) -> None:
        frame = _frame_for([("2025-06-30", "SH600000"), ("2025-07-01", "SH600000")])
        with self.assertRaisesRegex(DailyRecommendationError, "LOOK-AHEAD GUARD TRIPPED"):
            assert_no_lookahead(frame, "2025-06-30")

    def test_raises_on_empty_frame(self) -> None:
        empty = _frame_for([]).iloc[0:0]
        with self.assertRaises(DailyRecommendationError):
            assert_no_lookahead(empty, "2025-06-30")


class BuildRecommendationTests(unittest.TestCase):
    def _name(self, inst: str) -> str:
        return {"SH600000": "浦发银行"}.get(inst, "")

    def test_ranking_topk_mask_and_reasons(self) -> None:
        scores = {
            "SH600000": 0.9,   # tradable, top
            "SZ000001": 0.8,   # SUSPENDED -> excluded from picks
            "SH600519": 0.7,   # tradable
            "SZ300750": 0.6,   # ONE-PRICE LOCK -> excluded
            "SH601318": 0.5,   # tradable
        }
        masked = {"SZ000001", "SZ300750"}
        suspended = {"SZ000001"}
        one_price = {"SZ300750"}
        picks, frame, n_masked = build_recommendation(
            score_by_inst=scores, masked_pairs=masked, suspended=suspended,
            one_price=one_price, name_fn=self._name, as_of_date="2025-06-30",
            entry_date="2025-07-01", topk=10,
        )
        # masked names excluded from buy list
        codes = [p.stock_code for p in picks]
        self.assertEqual(codes, ["SH600000", "SH600519", "SH601318"])
        # ranks contiguous 1..N, sorted by score desc
        self.assertEqual([p.rank for p in picks], [1, 2, 3])
        self.assertTrue(picks[0].predicted_score >= picks[1].predicted_score
                        >= picks[2].predicted_score)
        self.assertEqual(n_masked, 2)
        # audit frame carries precise reasons + both time columns
        self.assertEqual(set(frame.columns) >= {"as_of_date", "entry_date",
            "stock_code", "stock_name", "predicted_score", "tradable_flag",
            "unavailable_reason"}, True)
        reason = dict(zip(frame.stock_code, frame.unavailable_reason, strict=True))
        self.assertEqual(reason["SZ000001"], "suspended")
        self.assertEqual(reason["SZ300750"], "one_price_lock")
        self.assertEqual(reason["SH600000"], "")
        # name best-effort
        self.assertEqual(picks[0].stock_name, "浦发银行")

    def test_topk_truncation(self) -> None:
        scores = {f"SH60{i:04d}": 1.0 - i * 0.01 for i in range(20)}
        picks, _frame, _ = build_recommendation(
            score_by_inst=scores, masked_pairs=set(), suspended=set(),
            one_price=set(), name_fn=lambda x: "", as_of_date="2025-06-30",
            entry_date="2025-07-01", topk=5,
        )
        self.assertEqual(len(picks), 5)
        self.assertEqual([p.rank for p in picks], [1, 2, 3, 4, 5])

    def test_stable_sort_preserves_input_order_on_ties(self) -> None:
        # All equal score -> stable sort keeps insertion order (matters for
        # the best_iter=1 model that produces many tied scores).
        scores = {"SH600003": 0.5, "SH600001": 0.5, "SH600002": 0.5}
        picks, _frame, _ = build_recommendation(
            score_by_inst=scores, masked_pairs=set(), suspended=set(),
            one_price=set(), name_fn=lambda x: "", as_of_date="2025-06-30",
            entry_date="2025-07-01", topk=10,
        )
        self.assertEqual([p.stock_code for p in picks],
                         ["SH600003", "SH600001", "SH600002"])


# ===========================================================================
# RUN_E2E real-bundle tests — the strengthened look-ahead red line
# ===========================================================================
@pytest.mark.skipif(not _RUN_E2E, reason="needs RUN_E2E=1 + the PIT bundle on disk")
class RealBundleLookaheadTests(unittest.TestCase):
    """Run only with RUN_E2E=1 against D:/qlib_data/my_cn_data_pit.

    Resets the canonical qlib runtime around the test so the process-wide
    qlib singleton does not leak into other tests.
    """

    T = "2025-06-30"     # decision date with plenty of trailing data
    N_FUTURE = 5         # trading days of future data to (not) leak

    def setUp(self) -> None:
        from src.core.qlib_runtime import (
            QlibRuntimeConfig,
            _reset_canonical_qlib_runtime_for_tests,
            init_qlib_canonical,
        )
        _reset_canonical_qlib_runtime_for_tests()
        init_qlib_canonical(QlibRuntimeConfig(
            provider_uri=_PIT_PROVIDER, region="cn",
            data_adjust_mode="post_adjusted",
        ))

    def tearDown(self) -> None:
        from src.core.qlib_runtime import _reset_canonical_qlib_runtime_for_tests
        _reset_canonical_qlib_runtime_for_tests()
        try:  # leave qlib pristine for any downstream test
            from qlib.config import C
            C.registered = False
        except Exception:
            pass

    def _config(self) -> RecommendationConfig:
        return RecommendationConfig(
            model_path=_MODEL, provider_uri=_PIT_PROVIDER,
            delisted_registry_path=_PIT_REGISTRY,
            fit_start=_FIT_START, fit_end=_FIT_END,
            instruments="csi300", as_of_date=self.T, topk=50,
        )

    def _features_with_end(self, end_time: str) -> pd.DataFrame:
        """Alpha158 INFER features over [T, end_time], rows for T only."""
        from qlib.contrib.data.handler import Alpha158
        from qlib.data.dataset import DatasetH
        from qlib.data.dataset.handler import DataHandlerLP
        handler = Alpha158(
            instruments="csi300", start_time=_FIT_START, end_time=end_time,
            fit_start_time=_FIT_START, fit_end_time=_FIT_END,
        )
        ds = DatasetH(handler=handler, segments={"seg": [self.T, end_time]})
        frame = ds.prepare("seg", col_set="feature", data_key=DataHandlerLP.DK_I)
        mask = frame.index.get_level_values("datetime") == pd.Timestamp(self.T)
        return frame[mask].sort_index()

    def test_asof_frame_has_no_future_rows(self) -> None:
        from src.inference.daily_recommend import prepare_asof_features
        frame = prepare_asof_features(self._config(), self.T)
        self.assertFalse(frame.empty)
        max_dt = pd.Timestamp(frame.index.get_level_values("datetime").max())
        self.assertEqual(max_dt, pd.Timestamp(self.T))

    def test_normalization_does_not_peek_at_future(self) -> None:
        """The red line: T's normalized features are identical whether or
        not data after T is loaded into the handler."""
        from dateutil.relativedelta import relativedelta  # noqa: F401
        from qlib.data import D
        cal = [pd.Timestamp(d) for d in D.calendar()]
        future = [d for d in cal if d > pd.Timestamp(self.T)][: self.N_FUTURE]
        self.assertTrue(future, "need trailing trading days after T in the bundle")
        end_future = future[-1].strftime("%Y-%m-%d")

        frame_no_future = self._features_with_end(self.T)         # end_time = T
        frame_with_future = self._features_with_end(end_future)   # end_time = T+N

        # Align to common (instrument) rows + columns, then compare values.
        common_idx = frame_no_future.index.intersection(frame_with_future.index)
        self.assertTrue(len(common_idx) > 0)
        cols = list(frame_no_future.columns)
        a = frame_no_future.loc[common_idx, cols].to_numpy(dtype=float)
        b = frame_with_future.loc[common_idx, cols].to_numpy(dtype=float)
        # NaNs must align identically, finite values must be equal.
        self.assertTrue(np.array_equal(np.isnan(a), np.isnan(b)))
        self.assertTrue(np.allclose(a[~np.isnan(a)], b[~np.isnan(b)], atol=0, rtol=0))


if __name__ == "__main__":
    unittest.main()
