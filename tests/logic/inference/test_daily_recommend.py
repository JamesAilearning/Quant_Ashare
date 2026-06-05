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
import pickle
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.inference.daily_recommend import (
    _BUY_LIST_COLUMNS,
    DailyRecommendationError,
    DailyRecommendationResult,
    RecommendationConfig,
    _load_model,
    _scores_to_inst_map,
    _st_snapshot_is_stale,
    _validate_st_snapshot,
    assert_no_lookahead,
    build_recommendation,
    resolve_dates,
    write_outputs,
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

    def test_default_as_of_is_latest_day_with_a_successor(self) -> None:
        # Default picks the latest day that still has a following session
        # (the bundle's last day cannot be a decision day — no T+1 in it).
        t, entry = resolve_dates(None, calendar=self._CAL)
        self.assertEqual(t, "2025-06-30")    # second-to-last
        self.assertEqual(entry, "2025-07-01")  # last day = entry

    def test_default_rejects_single_day_calendar(self) -> None:
        with self.assertRaisesRegex(DailyRecommendationError, "fewer than 2"):
            resolve_dates(None, calendar=["2025-07-01"])

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

    def test_unparseable_as_of_raises_domain_error(self) -> None:
        with self.assertRaisesRegex(DailyRecommendationError, "not a parseable date"):
            resolve_dates("not-a-date", calendar=self._CAL)


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


class ScoresToInstMapTests(unittest.TestCase):
    """The fail-loud instrument->score collapse (single date + unique keys).

    Covers the gap the previous inline ``dict(zip(..., strict=True))`` left:
    ``strict=True`` only checked length parity (which can never differ here),
    so a duplicate instrument or a multi-date index was silently collapsed.
    """

    def _series(self, pairs: list[tuple[str, str, float]]) -> pd.Series:
        """Build a ``(datetime, instrument)``-MultiIndexed score Series."""
        idx = pd.MultiIndex.from_tuples(
            [(pd.Timestamp(d), i) for d, i, _ in pairs],
            names=["datetime", "instrument"],
        )
        return pd.Series([v for *_, v in pairs], index=idx)

    def test_unique_multiindex_tuple_form(self) -> None:
        s = self._series([
            ("2025-06-30", "SH600000", 0.9),
            ("2025-06-30", "SZ000001", 0.8),
        ])
        self.assertEqual(
            _scores_to_inst_map(s), {"SH600000": 0.9, "SZ000001": 0.8},
        )

    def test_unique_flat_index_form(self) -> None:
        # Defensive non-MultiIndex path: each index entry IS the instrument
        # (kept whole, not sliced to its last character).
        s = pd.Series([0.5, 0.4], index=["SH600000", "SZ000001"])
        self.assertEqual(
            _scores_to_inst_map(s), {"SH600000": 0.5, "SZ000001": 0.4},
        )

    def test_empty_series_returns_empty_map(self) -> None:
        # recommend() guards empty upstream; the helper is still empty-safe.
        self.assertEqual(_scores_to_inst_map(pd.Series([], dtype=float)), {})

    def test_duplicate_instruments_raise(self) -> None:
        # Same date, same instrument twice -> dict(zip) would silently drop one.
        s = self._series([
            ("2025-06-30", "SH600000", 0.9),
            ("2025-06-30", "SH600000", 0.1),
        ])
        with self.assertRaisesRegex(
            DailyRecommendationError, "duplicate instruments",
        ):
            _scores_to_inst_map(s)

    def test_multi_date_raises(self) -> None:
        # Two distinct dates -> idx[-1] would alias an instrument across days.
        s = self._series([
            ("2025-06-30", "SH600000", 0.9),
            ("2025-07-01", "SZ000001", 0.8),
        ])
        with self.assertRaisesRegex(
            DailyRecommendationError, "distinct dates",
        ):
            _scores_to_inst_map(s)


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

    def test_negative_topk_rejected(self) -> None:
        with self.assertRaisesRegex(DailyRecommendationError, "topk must be >= 0"):
            build_recommendation(
                score_by_inst={"SH600000": 0.5}, masked_pairs=set(),
                suspended=set(), one_price=set(), name_fn=lambda x: "",
                as_of_date="2025-06-30", entry_date="2025-07-01", topk=-1,
            )

    def test_zero_topk_yields_empty_buy_list(self) -> None:
        picks, frame, _ = build_recommendation(
            score_by_inst={"SH600000": 0.5, "SH600001": 0.4}, masked_pairs=set(),
            suspended=set(), one_price=set(), name_fn=lambda x: "",
            as_of_date="2025-06-30", entry_date="2025-07-01", topk=0,
        )
        self.assertEqual(len(picks), 0)
        self.assertEqual(len(frame), 2)  # audit frame still has all scored rows

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


class StExclusionInBuildRecommendationTests(unittest.TestCase):
    """ST names drop out of the pool BEFORE the Top-K slice (filter-then-K)."""

    def test_st_excluded_from_picks_and_labelled(self) -> None:
        scores = {
            "SH600000": 0.9,   # tradable
            "SZ000004": 0.85,  # *ST -> excluded
            "SH600519": 0.8,   # tradable
            "SZ000078": 0.7,   # ST  -> excluded
            "SH601318": 0.6,   # tradable
        }
        st = {"SZ000004", "SZ000078"}
        picks, frame, n_excl = build_recommendation(
            score_by_inst=scores, masked_pairs=set(), suspended=set(),
            one_price=set(), st_excluded=st, name_fn=lambda x: "",
            as_of_date="2025-06-30", entry_date="2025-07-01", topk=10,
        )
        self.assertEqual([p.stock_code for p in picks],
                         ["SH600000", "SH600519", "SH601318"])  # no ST
        self.assertEqual(n_excl, 2)
        reason = dict(zip(frame.stock_code, frame.unavailable_reason, strict=True))
        self.assertEqual(reason["SZ000004"], "st")
        self.assertEqual(reason["SZ000078"], "st")

    def test_filter_then_take_k_keeps_k_non_st(self) -> None:
        # 6 names, 2 ST interspersed by score; topk=3 must return the 3 highest
        # NON-ST names (K from the non-ST pool, not K minus the ST hits).
        scores = {
            "SZ000004": 1.0,   # *ST (highest) -> excluded
            "SH600000": 0.9,
            "SZ000078": 0.8,   # ST -> excluded
            "SH600519": 0.7,
            "SH601318": 0.6,
            "SH600036": 0.5,
        }
        st = {"SZ000004", "SZ000078"}
        picks, _frame, _ = build_recommendation(
            score_by_inst=scores, masked_pairs=set(), suspended=set(),
            one_price=set(), st_excluded=st, name_fn=lambda x: "",
            as_of_date="2025-06-30", entry_date="2025-07-01", topk=3,
        )
        self.assertEqual(len(picks), 3)
        self.assertEqual([p.stock_code for p in picks],
                         ["SH600000", "SH600519", "SH601318"])

    def test_microstructure_mask_takes_precedence_over_st(self) -> None:
        # Both masked (suspended) AND ST -> the microstructure reason wins.
        scores = {"SZ000004": 0.9}
        picks, frame, n_excl = build_recommendation(
            score_by_inst=scores, masked_pairs={"SZ000004"},
            suspended={"SZ000004"}, one_price=set(), st_excluded={"SZ000004"},
            name_fn=lambda x: "", as_of_date="2025-06-30",
            entry_date="2025-07-01", topk=10,
        )
        self.assertEqual(len(picks), 0)
        self.assertEqual(n_excl, 1)
        reason = dict(zip(frame.stock_code, frame.unavailable_reason, strict=True))
        self.assertEqual(reason["SZ000004"], "suspended")

    def test_fewer_than_k_after_st_filter_returns_available_no_error(self) -> None:
        # topk exceeds the non-ST pool -> return however many remain (no error,
        # no padding). CSI300 won't hit this, but a small universe could.
        scores = {
            "SZ000004": 0.9,   # *ST -> excluded
            "SH600000": 0.8,
            "SZ000078": 0.7,   # ST -> excluded
            "SH600519": 0.6,
        }
        st = {"SZ000004", "SZ000078"}
        picks, _frame, _ = build_recommendation(
            score_by_inst=scores, masked_pairs=set(), suspended=set(),
            one_price=set(), st_excluded=st, name_fn=lambda x: "",
            as_of_date="2025-06-30", entry_date="2025-07-01", topk=5,
        )
        self.assertEqual(len(picks), 2)  # 2 non-ST, not 5, not an error
        self.assertEqual([p.stock_code for p in picks], ["SH600000", "SH600519"])

    def test_no_st_set_is_backward_compatible(self) -> None:
        # Omitting st_excluded keeps the pre-ST behaviour (nothing dropped).
        scores = {"SH600000": 0.9, "SZ000004": 0.8}
        picks, _frame, n_excl = build_recommendation(
            score_by_inst=scores, masked_pairs=set(), suspended=set(),
            one_price=set(), name_fn=lambda x: "", as_of_date="2025-06-30",
            entry_date="2025-07-01", topk=10,
        )
        self.assertEqual([p.stock_code for p in picks], ["SH600000", "SZ000004"])
        self.assertEqual(n_excl, 0)


class StSnapshotStalenessTests(unittest.TestCase):
    """The pure staleness predicate (snapshot date vs as-of, tolerance)."""

    def test_within_tolerance_is_fresh(self) -> None:
        self.assertFalse(_st_snapshot_is_stale(date(2025, 6, 27), "2025-06-30", 7))

    def test_exactly_at_tolerance_is_fresh(self) -> None:
        self.assertFalse(_st_snapshot_is_stale(date(2025, 6, 23), "2025-06-30", 7))

    def test_beyond_tolerance_is_stale(self) -> None:
        self.assertTrue(_st_snapshot_is_stale(date(2025, 6, 22), "2025-06-30", 7))

    def test_newer_snapshot_is_never_stale(self) -> None:
        # Snapshot dated AFTER as-of -> negative age -> not stale here (PR2
        # handles point-in-time history; this guard only catches OLD snapshots).
        self.assertFalse(_st_snapshot_is_stale(date(2025, 7, 15), "2025-06-30", 7))


class ValidateStSnapshotTests(unittest.TestCase):
    """Fail-loud guard on a missing / stale current-ST source."""

    def _config(self, path: str | None, max_age: int = 7) -> RecommendationConfig:
        return RecommendationConfig(
            model_path="m", provider_uri="p", delisted_registry_path="r",
            fit_start=_FIT_START, fit_end=_FIT_END,
            name_source_parquet=path, st_snapshot_max_age_days=max_age,
        )

    def test_none_source_raises(self) -> None:
        with self.assertRaisesRegex(DailyRecommendationError, "requires name_source"):
            _validate_st_snapshot(self._config(None), "2025-06-30")

    def test_missing_file_raises(self) -> None:
        with self.assertRaisesRegex(DailyRecommendationError, "not found"):
            _validate_st_snapshot(
                self._config("D:/no/such/active_xyz.parquet"), "2025-06-30",
            )

    def test_stale_file_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "active.parquet"
            p.write_bytes(b"x")
            old = datetime(2025, 6, 30).timestamp() - 30 * 86400  # 30d stale
            os.utime(p, (old, old))
            with self.assertRaisesRegex(DailyRecommendationError, "stale"):
                _validate_st_snapshot(self._config(str(p)), "2025-06-30")

    def test_fresh_valid_file_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "active.parquet"
            pd.DataFrame({"ts_code": ["000001.SZ"], "name": ["平安银行"]}).to_parquet(p)
            recent = datetime(2025, 6, 29).timestamp()  # 1d before as-of
            os.utime(p, (recent, recent))
            self.assertEqual(
                _validate_st_snapshot(self._config(str(p)), "2025-06-30"), p,
            )

    def test_malformed_schema_raises(self) -> None:
        # Present + fresh but the 'name' column dropped (upstream schema change)
        # -> must NOT pass, else _load_name_map returns {} and ST filtering is
        # silently disabled (Codex P1 on #222).
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "active.parquet"
            pd.DataFrame(
                {"ts_code": ["000001.SZ"], "industry": ["银行"]},
            ).to_parquet(p)
            recent = datetime(2025, 6, 29).timestamp()
            os.utime(p, (recent, recent))
            with self.assertRaisesRegex(
                DailyRecommendationError, "missing required column",
            ):
                _validate_st_snapshot(self._config(str(p)), "2025-06-30")

    def test_empty_snapshot_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "active.parquet"
            pd.DataFrame({"ts_code": [], "name": []}).to_parquet(p)
            recent = datetime(2025, 6, 29).timestamp()
            os.utime(p, (recent, recent))
            with self.assertRaisesRegex(DailyRecommendationError, "zero rows"):
                _validate_st_snapshot(self._config(str(p)), "2025-06-30")


class LoadModelTests(unittest.TestCase):
    def test_missing_path_raises_domain_error(self) -> None:
        with self.assertRaisesRegex(DailyRecommendationError, "not found"):
            _load_model(Path("D:/no/such/model_xyz.pkl"))

    def test_corrupt_pickle_raises_domain_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "bad.pkl"
            p.write_bytes(b"not a valid pickle stream \x00\x01\x02")
            with self.assertRaisesRegex(DailyRecommendationError, "failed to load"):
                _load_model(p)

    def test_non_model_object_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "dict.pkl"
            with p.open("wb") as f:
                pickle.dump({"not": "a model"}, f)
            with self.assertRaisesRegex(DailyRecommendationError, "no .predict"):
                _load_model(p)


class WriteOutputsTests(unittest.TestCase):
    def test_empty_buy_list_csv_still_has_header(self) -> None:
        # Empty picks (e.g. --topk 0 or all masked) must still write a CSV
        # header row so downstream readers don't choke on a column-less file.
        result = DailyRecommendationResult(
            as_of_date="2025-06-30", entry_date="2025-07-01",
            picks=(), n_scored=0, n_masked=0, n_st_excluded=0,
            scored_frame=pd.DataFrame(columns=_BUY_LIST_COLUMNS),
        )
        with tempfile.TemporaryDirectory() as tmp:
            paths = write_outputs(result, tmp)
            header = Path(paths["csv"]).read_text(encoding="utf-8-sig").splitlines()[0]
            self.assertEqual(header.split(","), _BUY_LIST_COLUMNS)


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
