"""Unit tests for the PURE guards of the frozen-model OOS eval tool (④ promotion recon).

Only the degeneracy + concentration helpers are unit-tested here — they are the hard-veto
behavioral guards and are pure (pandas + statistics). The heavy paths (feature build,
predict, backtest) need a real qlib bundle and are exercised by the live eval run, not CI.

The eval module imports qlib-bound modules at top, so this is qlib-gated (CI has qlib).
"""

import sys
import unittest
from pathlib import Path

import pytest

pytest.importorskip("qlib")  # the eval module imports qlib-bound modules at import time

import pandas as pd  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.eval_frozen_model_oos import (  # noqa: E402
    _concentration_stats,
    _degeneracy_scan,
)


def _series(by_date: dict[str, list[float]]) -> pd.Series:
    idx, vals = [], []
    for d, scores in by_date.items():
        for i, s in enumerate(scores):
            idx.append((pd.Timestamp(d), f"inst{i:04d}"))
            vals.append(s)
    return pd.Series(
        vals, index=pd.MultiIndex.from_tuples(idx, names=["datetime", "instrument"])
    )


class DegeneracyScanTests(unittest.TestCase):
    def test_healthy_predictions_have_zero_degenerate_days(self) -> None:
        # 300-name universe, all distinct scores each day -> never degenerate.
        preds = _series({
            "2025-07-01": [0.001 * i for i in range(300)],
            "2025-07-02": [0.002 * i for i in range(300)],
        })
        out = _degeneracy_scan(preds)
        self.assertEqual(out["n_degenerate_days"], 0)
        self.assertEqual(out["min_unique"], 300)

    def test_collapsed_day_is_flagged(self) -> None:
        # day 2 collapses 300 names into 2 score buckets -> topk cutoff in a tie block.
        preds = _series({
            "2025-07-01": [0.001 * i for i in range(300)],   # healthy
            "2025-07-02": [0.5] * 200 + [0.6] * 100,          # 2 unique over 300
        })
        out = _degeneracy_scan(preds)
        self.assertEqual(out["n_degenerate_days"], 1)

    def test_small_universe_all_unique_is_not_flagged(self) -> None:
        # 40 names, all distinct: n_unique(40) <= TOPK(50) but the universe is < TOPK, so
        # the cutoff is not inside a tie block -> healthy, not a false positive.
        preds = _series({"2025-07-01": [0.01 * i for i in range(40)]})
        out = _degeneracy_scan(preds)
        self.assertEqual(out["n_degenerate_days"], 0)

    def test_material_cutoff_straddle_is_flagged_despite_unique_universe(self) -> None:
        # The codex P2 failure mode: scores collapse ONLY around the top-k boundary while
        # the rest of the universe is unique. 40 distinct above + 15 tied AT the cutoff +
        # 245 distinct below -> n_unique=286 (healthy ratio, > TOPK) so the unique-ratio
        # check alone misses it, but the buy list is tie-break dependent -> must be vetoed.
        high = [50.0 + 0.1 * (i + 1) for i in range(40)]   # 40 distinct, > cutoff
        at = [50.0] * 15                                    # 15 tied AT the cutoff
        low = [50.0 - 0.1 * (i + 1) for i in range(245)]    # 245 distinct, < cutoff
        out = _degeneracy_scan(_series({"2025-07-01": high + at + low}))
        self.assertEqual(out["n_degenerate_days"], 1)
        self.assertEqual(out["n_cutoff_straddle_days"], 1)
        self.assertEqual(out["max_names_tied_at_cutoff"], 15)
        self.assertEqual(out["min_unique"], 286)            # ratio is healthy -> old miss
        self.assertEqual(out["cutoff_straddle_veto_min"], 10)  # max(round(0.2*50), 2)

    def test_small_cutoff_straddle_is_reported_but_not_vetoed(self) -> None:
        # A 5-name boundary tie is reported (operator visibility) but below the materiality
        # floor (10) -> not a hard veto, since a handful of tie-break picks is benign.
        high = [50.0 + 0.1 * (i + 1) for i in range(48)]
        at = [50.0] * 5
        low = [50.0 - 0.1 * (i + 1) for i in range(247)]
        out = _degeneracy_scan(_series({"2025-07-01": high + at + low}))
        self.assertEqual(out["n_degenerate_days"], 0)
        self.assertEqual(out["n_cutoff_straddle_days"], 1)
        self.assertEqual(out["max_names_tied_at_cutoff"], 5)

    def test_materiality_floor_is_inclusive_at_the_boundary(self) -> None:
        # Pin the EXACT veto floor (_CUTOFF_STRADDLE_VETO=10) on both sides, so a `>=`->`>`
        # regression cannot slip through: a true straddle of 9 tied at the cutoff is reported
        # but NOT vetoed; exactly 10 IS vetoed. n_above=45 keeps the cutoff strictly inside
        # the tie block in both days (45 < 50 < 45 + n_at).
        nine = ([50.0 + 0.1 * (i + 1) for i in range(45)] + [50.0] * 9
                + [50.0 - 0.1 * (i + 1) for i in range(246)])   # straddle, sub-floor (9)
        ten = ([50.0 + 0.1 * (i + 1) for i in range(45)] + [50.0] * 10
               + [50.0 - 0.1 * (i + 1) for i in range(245)])    # straddle, AT floor (10)
        out = _degeneracy_scan(_series({"2025-07-01": nine, "2025-07-02": ten}))
        self.assertEqual(out["n_cutoff_straddle_days"], 2)      # both straddle the cutoff
        self.assertEqual(out["n_degenerate_days"], 1)           # only the n_at==10 day vetoes
        self.assertEqual(out["max_names_tied_at_cutoff"], 10)


class ConcentrationStatsTests(unittest.TestCase):
    def test_equal_weight_top50_is_diffuse(self) -> None:
        pos = {"2025-07-01": {f"i{j:04d}": 1.0 for j in range(50)}}
        out = _concentration_stats(pos)
        self.assertEqual(out["median_n_holdings"], 50)
        self.assertAlmostEqual(out["median_top10_share"], 0.2)        # 10/50
        self.assertAlmostEqual(out["median_hhi"], 0.02)              # 50 * (1/50)^2
        self.assertAlmostEqual(out["max_single_name_weight"], 0.02)

    def test_skewed_book_is_concentrated(self) -> None:
        pos = {"2025-07-01": {"a": 0.9, "b": 0.05, "c": 0.05}}
        out = _concentration_stats(pos)
        self.assertAlmostEqual(out["max_single_name_weight"], 0.9)
        self.assertGreater(out["median_hhi"], 0.8)

    def test_empty_positions(self) -> None:
        self.assertEqual(_concentration_stats({}), {})
        self.assertEqual(_concentration_stats(None), {})


if __name__ == "__main__":
    unittest.main()
