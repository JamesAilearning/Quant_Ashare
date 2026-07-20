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
        # check alone misses it. tie_filled_slots = TOPK - n_above = 50 - 40 = 10 -> 10 of
        # the 50 buys are tie-break dependent (20% of the book) -> must be vetoed.
        high = [50.0 + 0.1 * (i + 1) for i in range(40)]   # 40 distinct, > cutoff
        at = [50.0] * 15                                    # 15 tied AT the cutoff
        low = [50.0 - 0.1 * (i + 1) for i in range(245)]    # 245 distinct, < cutoff
        out = _degeneracy_scan(_series({"2025-07-01": high + at + low}))
        self.assertEqual(out["n_degenerate_days"], 1)
        self.assertEqual(out["n_cutoff_straddle_days"], 1)
        self.assertEqual(out["max_tie_filled_slots"], 10)   # the veto basis
        self.assertEqual(out["max_names_tied_at_cutoff"], 15)  # total bubble (reported)
        self.assertEqual(out["min_unique"], 286)            # ratio is healthy -> old miss
        self.assertEqual(out["cutoff_straddle_veto_min"], 10)  # max(round(0.2*50), 2)

    def test_small_cutoff_straddle_is_reported_but_not_vetoed(self) -> None:
        # A 2-slot boundary tie (48 above, 5 tied -> only 2 buys arbitrary) is reported for
        # visibility but below the materiality floor (10) -> not a hard veto.
        high = [50.0 + 0.1 * (i + 1) for i in range(48)]
        at = [50.0] * 5
        low = [50.0 - 0.1 * (i + 1) for i in range(247)]
        out = _degeneracy_scan(_series({"2025-07-01": high + at + low}))
        self.assertEqual(out["n_degenerate_days"], 0)
        self.assertEqual(out["n_cutoff_straddle_days"], 1)
        self.assertEqual(out["max_tie_filled_slots"], 2)    # 50 - 48
        self.assertEqual(out["max_names_tied_at_cutoff"], 5)

    def test_wide_bubble_filling_one_slot_is_not_vetoed(self) -> None:
        # codex P2 on #295: the veto is on TIE-FILLED SLOTS, not total names tied. 49 above
        # + 10 tied means only 1 of 50 buys is tie-break dependent (immaterial) even though
        # 10 names sit on the bubble. The old n_at>=10 basis would WRONGLY veto this; the
        # slot basis (tie_filled_slots = 50 - 49 = 1) correctly does not.
        high = [50.0 + 0.1 * (i + 1) for i in range(49)]
        at = [50.0] * 10
        low = [50.0 - 0.1 * (i + 1) for i in range(241)]
        out = _degeneracy_scan(_series({"2025-07-01": high + at + low}))
        self.assertEqual(out["n_degenerate_days"], 0)        # 1 arbitrary buy -> not material
        self.assertEqual(out["n_cutoff_straddle_days"], 1)   # still reported as a straddle
        self.assertEqual(out["max_tie_filled_slots"], 1)
        self.assertEqual(out["max_names_tied_at_cutoff"], 10)  # wide bubble, reported

    def test_materiality_floor_is_on_tie_filled_slots(self) -> None:
        # Pin the EXACT veto floor (_CUTOFF_STRADDLE_VETO=10) on both sides, on the SLOT
        # basis: a straddle filling 9 slots (n_above=41) is reported but NOT vetoed; one
        # filling exactly 10 (n_above=40) IS vetoed. Guards `>=` against `>` AND the slot
        # basis against a regression back to total-tied-names.
        nine = ([50.0 + 0.1 * (i + 1) for i in range(41)] + [50.0] * 10
                + [50.0 - 0.1 * (i + 1) for i in range(249)])   # tie_filled_slots = 9
        ten = ([50.0 + 0.1 * (i + 1) for i in range(40)] + [50.0] * 11
               + [50.0 - 0.1 * (i + 1) for i in range(249)])    # tie_filled_slots = 10
        out = _degeneracy_scan(_series({"2025-07-01": nine, "2025-07-02": ten}))
        self.assertEqual(out["n_cutoff_straddle_days"], 2)      # both straddle the cutoff
        self.assertEqual(out["n_degenerate_days"], 1)           # only the 10-slot day vetoes
        self.assertEqual(out["max_tie_filled_slots"], 10)
        self.assertEqual(out["max_names_tied_at_cutoff"], 11)


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


class MissingModelGuardTests(unittest.TestCase):
    """codex #387 r6: the DP-1 guard (`--profile csi800_n5` REQUIRES an
    explicit --model) must be exercised, not just documented — a missed
    flag scoring the csi300-era incumbent on csi800 is the exact
    cross-universe pairing DP-1 forbids."""

    def test_non_legacy_profile_without_model_refuses_before_eval(self) -> None:
        from unittest import mock

        import scripts.eval_frozen_model_oos as m

        with mock.patch.object(
            m, "_predictions_over_window",
            side_effect=AssertionError("heavy eval path reached"),
        ) as heavy:
            with self.assertRaises(SystemExit) as ctx:
                m.main(["--profile", "csi800_n5"])
        self.assertIn("REQUIRED for profile", str(ctx.exception))
        heavy.assert_not_called()

    def test_legacy_profile_fills_incumbent_default(self) -> None:
        from unittest import mock

        import scripts.eval_frozen_model_oos as m

        class _Sentinel(Exception):
            pass

        captured: dict[str, str] = {}

        def _stub(args):  # noqa: ANN001 — argparse.Namespace
            captured["model"] = args.model
            raise _Sentinel()

        with mock.patch.object(m, "_predictions_over_window", _stub):
            with self.assertRaises(_Sentinel):
                m.main(["--profile", "csi300_daily"])
        self.assertEqual(
            "D:/stock/phase_b_artifacts/alpha158_lgb_pit.pkl",
            captured["model"],
        )

    def test_non_legacy_profile_with_explicit_model_proceeds(self) -> None:
        from unittest import mock

        import scripts.eval_frozen_model_oos as m

        class _Sentinel(Exception):
            pass

        captured: dict[str, str] = {}

        def _stub(args):  # noqa: ANN001
            captured["model"] = args.model
            captured["instruments"] = args.instruments
            raise _Sentinel()

        with mock.patch.object(m, "_predictions_over_window", _stub):
            with self.assertRaises(_Sentinel):
                m.main(["--profile", "csi800_n5",
                        "--model", "D:/tmp/candidate.pkl"])
        self.assertEqual("D:/tmp/candidate.pkl", captured["model"])
        self.assertEqual("csi800", captured["instruments"])


class ConstraintVetoArtifactTests(unittest.TestCase):
    """codex #387 r7: a campaign_v1 RAISE inside the profiled backtest is
    a GUARD VETO — the eval must still write an inspectable artifact
    (constraint_veto recorded, backtest null) and exit 1, never die with
    only a stderr traceback."""

    @staticmethod
    def _stub_ctx(m, backtest_side_effect):  # noqa: ANN001
        from types import SimpleNamespace
        from unittest import mock

        idx = pd.MultiIndex.from_product(
            [pd.to_datetime(["2025-07-01", "2025-07-02"]),
             [f"S{i}" for i in range(5)]],
        )
        preds = pd.Series(range(10), index=idx, dtype=float)
        signal_stub = SimpleNamespace(
            ic_summary={1: {"mean_ic": 0.01, "ir": 0.5,
                            "ic_positive_ratio": 0.6},
                        5: {"mean_ic": 0.02}},
            turnover_stats={"mean_turnover": 0.1},
        )
        return (
            mock.patch.object(
                m, "_predictions_over_window", return_value=preds),
            mock.patch.object(
                m, "_executable_stamps", side_effect=lambda p, a, pr: p),
            mock.patch.object(
                m.SignalAnalyzer, "analyze", return_value=signal_stub),
            mock.patch.object(
                m, "_backtest_metrics", side_effect=backtest_side_effect),
        )

    def test_wrapped_constraint_veto_writes_artifact_and_exits_nonzero(
            self) -> None:
        # codex #387 r8: production raises BacktestRunnerError WRAPPING
        # the RiskConstraintError as __cause__ — the veto path must fire
        # on that exact shape, not just a bare RiskConstraintError.
        import json as _json
        import tempfile

        import scripts.eval_frozen_model_oos as m
        from src.core.backtest_runner import BacktestRunnerError
        from src.core.risk_constraints import RiskConstraintError

        try:
            raise BacktestRunnerError(
                "risk constraints rejected the backtest positions map. "
                "max_per_name 5.04% > 5%"
            ) from RiskConstraintError("max_per_name 5.04% > 5%")
        except BacktestRunnerError as wrapped_exc:
            wrapped = wrapped_exc

        p1, p2, p3, p4 = self._stub_ctx(m, wrapped)
        with tempfile.TemporaryDirectory() as td:
            out_path = Path(td) / "veto.json"
            with p1, p2, p3, p4:
                rc = m.main([
                    "--profile", "csi800_n5",
                    "--model", "D:/tmp/candidate.pkl",
                    "--out", str(out_path),
                ])
            self.assertEqual(1, rc)
            payload = _json.loads(out_path.read_text(encoding="utf-8"))
        self.assertIn("max_per_name", payload["constraint_veto"])
        self.assertIsNone(payload["backtest_excess_with_cost"])
        self.assertEqual("csi800_n5", payload["profile"])

    def test_runner_error_without_constraint_cause_reraises(self) -> None:
        # A BacktestRunnerError with NO RiskConstraintError in its cause
        # chain is tool breakage — it must re-raise, never be recorded
        # as a candidate veto.
        import tempfile

        import scripts.eval_frozen_model_oos as m
        from src.core.backtest_runner import BacktestRunnerError

        p1, p2, p3, p4 = self._stub_ctx(
            m, BacktestRunnerError("benchmark series unavailable"))
        with tempfile.TemporaryDirectory() as td:
            out_path = Path(td) / "never.json"
            with p1, p2, p3, p4:
                with self.assertRaises(BacktestRunnerError):
                    m.main([
                        "--profile", "csi800_n5",
                        "--model", "D:/tmp/candidate.pkl",
                        "--out", str(out_path),
                    ])
            self.assertFalse(out_path.exists())


class ProfileCrossPinTests(unittest.TestCase):
    """codex #387 r1: the pure eval_profiles module hardcodes the legacy
    slippage so governance tests stay off the qlib import path — THIS
    qlib-gated test owns the equality with the replay constant."""

    def test_legacy_profile_slippage_equals_replay_constant(self) -> None:
        from scripts.eval_profiles import EVAL_PROFILES
        from scripts.regen.replay_frozen_baseline import SLIPPAGE_BPS

        self.assertEqual(
            SLIPPAGE_BPS,
            EVAL_PROFILES["csi300_daily"]["slippage_bps"],
        )


if __name__ == "__main__":
    unittest.main()
