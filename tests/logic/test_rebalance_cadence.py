"""阶段7 (add-rebalance-cadence, Route A signal thinning) — the enabler's
contract surface EXCEPT the qlib-level hold behavior (that lives in the
mini-bundle CONTRACT test, tests/logic/test_rebalance_cadence_contract.py).

Covers: config validation (incl. the operator-mandated N=1∧phase≠0 and
iso_week structural rejections), rebalance-day derivation for both anchors,
the byte-identical default (SAME-OBJECT identity — no filter constructed),
fail-loud empty thinning, fingerprint separation, and the manifest's
named-cause invalidation messaging.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.backtest_runner import BacktestRunner, BacktestRunnerError  # noqa: E402
from src.core.walk_forward._resume import (  # noqa: E402
    compute_config_fingerprint,
    rebalance_cadence_repr,
)
from src.core.walk_forward.config import WalkForwardConfig, WalkForwardError  # noqa: E402


class CadenceConfigValidationTests(unittest.TestCase):
    def test_default_is_daily_fold_phase(self) -> None:
        cfg = WalkForwardConfig()
        self.assertEqual(cfg.rebalance_cadence_days, 1)
        self.assertEqual(cfg.rebalance_phase, 0)
        self.assertEqual(cfg.rebalance_anchor, "fold_phase")

    def test_bad_cadence_rejected(self) -> None:
        for bad in (0, -5, 2.5, True):
            with self.assertRaises(WalkForwardError, msg=f"bad={bad!r}"):
                WalkForwardConfig(rebalance_cadence_days=bad)  # type: ignore[arg-type]

    def test_phase_out_of_range_rejected(self) -> None:
        for n, p in ((5, 5), (5, -1), (5, 2.0), (3, 7)):
            with self.assertRaises(WalkForwardError, msg=f"N={n} p={p!r}"):
                WalkForwardConfig(
                    rebalance_cadence_days=n, rebalance_phase=p,  # type: ignore[arg-type]
                )

    def test_daily_with_phase_is_meaningless_and_rejected(self) -> None:
        # operator small-item 2: N=1 requires phase=0 — never silently pass.
        with self.assertRaisesRegex(WalkForwardError, "meaningless"):
            WalkForwardConfig(rebalance_cadence_days=1, rebalance_phase=1)

    def test_unknown_anchor_rejected(self) -> None:
        with self.assertRaises(WalkForwardError):
            WalkForwardConfig(rebalance_anchor="weekly")

    def test_iso_week_requires_nominal_n5_phase0(self) -> None:
        # N/phase carry no derivational meaning under iso_week — anything
        # but the nominal declaration would be a silently-ignored lie.
        with self.assertRaisesRegex(WalkForwardError, "iso_week"):
            WalkForwardConfig(
                rebalance_anchor="iso_week", rebalance_cadence_days=1,
            )
        with self.assertRaisesRegex(WalkForwardError, "iso_week"):
            WalkForwardConfig(
                rebalance_anchor="iso_week", rebalance_cadence_days=5,
                rebalance_phase=2,
            )
        cfg = WalkForwardConfig(
            rebalance_anchor="iso_week", rebalance_cadence_days=5,
        )
        self.assertEqual(cfg.rebalance_anchor, "iso_week")

    def test_non_daily_cadence_with_lag_gt1_refused(self) -> None:
        # codex P1 #336: thinning precedes the position-based lag restamp,
        # calendar-correct only on a dense daily series — N>1 with lag>1 is
        # refused rather than landing the fill ~N days out.
        with self.assertRaisesRegex(WalkForwardError, "not jointly supported"):
            WalkForwardConfig(
                rebalance_cadence_days=5, signal_to_execution_lag=2,
            )
        # lag=1 with a non-daily cadence is the supported canonical path.
        cfg = WalkForwardConfig(
            rebalance_cadence_days=5, signal_to_execution_lag=1,
        )
        self.assertEqual(cfg.rebalance_cadence_days, 5)


class RunnerBoundaryValidationTests(unittest.TestCase):
    """codex P2 #336: BacktestRunner.run is a public official-metrics entry
    point; direct callers bypass WalkForwardConfig validation, so the runner
    boundary must reject invalid cadence itself (via _validate_cadence)."""

    def test_daily_with_phase_refused_at_boundary(self) -> None:
        with self.assertRaisesRegex(BacktestRunnerError, "meaningless"):
            BacktestRunner._validate_cadence(1, 1, "fold_phase", 1)

    def test_phase_out_of_range_refused(self) -> None:
        with self.assertRaises(BacktestRunnerError):
            BacktestRunner._validate_cadence(5, 5, "fold_phase", 1)

    def test_unknown_anchor_refused(self) -> None:
        with self.assertRaisesRegex(BacktestRunnerError, "rebalance_anchor"):
            BacktestRunner._validate_cadence(5, 0, "weekly", 1)

    def test_iso_week_non_nominal_refused(self) -> None:
        with self.assertRaisesRegex(BacktestRunnerError, "iso_week"):
            BacktestRunner._validate_cadence(3, 0, "iso_week", 1)

    def test_lag_interaction_refused(self) -> None:
        with self.assertRaisesRegex(
            BacktestRunnerError, "not jointly supported",
        ):
            BacktestRunner._validate_cadence(5, 0, "fold_phase", 2)

    def test_default_and_supported_combinations_pass(self) -> None:
        # default daily, non-daily+lag1, iso_week nominal — all accepted.
        BacktestRunner._validate_cadence(1, 0, "fold_phase", 1)
        BacktestRunner._validate_cadence(5, 2, "fold_phase", 1)
        BacktestRunner._validate_cadence(5, 0, "iso_week", 1)


class RebalanceStampDerivationTests(unittest.TestCase):
    @staticmethod
    def _days(spec: list[str]) -> list:
        import pandas as pd

        return [pd.Timestamp(d) for d in spec]

    def test_fold_phase_every_nth_from_phase(self) -> None:
        days = self._days([
            "2021-07-01", "2021-07-02", "2021-07-05", "2021-07-06",
            "2021-07-07", "2021-07-08", "2021-07-09", "2021-07-12",
        ])
        picked = BacktestRunner._rebalance_stamp_dates(
            days, cadence_days=5, phase=0, anchor="fold_phase",
        )
        self.assertEqual(picked, [days[0], days[5]])
        picked = BacktestRunner._rebalance_stamp_dates(
            days, cadence_days=5, phase=2, anchor="fold_phase",
        )
        self.assertEqual(picked, [days[2], days[7]])

    def test_fold_phase_window_shorter_than_cadence(self) -> None:
        days = self._days(["2021-07-01", "2021-07-02", "2021-07-05"])
        self.assertEqual(
            BacktestRunner._rebalance_stamp_dates(
                days, cadence_days=5, phase=0, anchor="fold_phase",
            ),
            [days[0]],
        )
        # phase beyond the window keeps nothing — the caller fails loud.
        self.assertEqual(
            BacktestRunner._rebalance_stamp_dates(
                days, cadence_days=5, phase=4, anchor="fold_phase",
            ),
            [],
        )

    def test_iso_week_first_trading_day_per_week(self) -> None:
        # Week of 2021-07-05 starts Monday; simulate a Monday holiday the
        # NEXT week (2021-07-12 missing) — Tuesday 07-13 becomes that
        # week's first trading day.
        days = self._days([
            "2021-07-01", "2021-07-02",              # ISO week 26 (Thu, Fri)
            "2021-07-05", "2021-07-06", "2021-07-07",  # week 27
            "2021-07-13", "2021-07-14",              # week 28, Monday holiday
        ])
        picked = BacktestRunner._rebalance_stamp_dates(
            days, cadence_days=5, phase=0, anchor="iso_week",
        )
        self.assertEqual(
            picked, self._days(["2021-07-01", "2021-07-05", "2021-07-13"]),
        )

    def test_iso_week_year_boundary(self) -> None:
        # 2021-01-01 (Friday) belongs to ISO week 2020-W53 — it must count
        # as a DIFFERENT week than 2021-01-04 (Monday, 2021-W01).
        days = self._days(["2021-01-01", "2021-01-04", "2021-01-05"])
        picked = BacktestRunner._rebalance_stamp_dates(
            days, cadence_days=5, phase=0, anchor="iso_week",
        )
        self.assertEqual(picked, self._days(["2021-01-01", "2021-01-04"]))


class ThinPredictionsTests(unittest.TestCase):
    @staticmethod
    def _preds(days: list[str]):
        import pandas as pd

        idx = pd.MultiIndex.from_product(
            [pd.to_datetime(days), ["SH600000", "SH600001"]],
            names=["datetime", "instrument"],
        )
        return pd.Series(range(len(idx)), index=idx, dtype=float)

    def test_default_returns_the_same_object(self) -> None:
        # THE identity guarantee: N=1/fold_phase constructs no filter at all
        # — the byte-identical default path is the same object, not a copy.
        preds = self._preds(["2021-07-01", "2021-07-02"])
        out = BacktestRunner._thin_predictions(
            preds, cadence_days=1, phase=0, anchor="fold_phase",
        )
        self.assertIs(out, preds)

    def test_thinning_keeps_only_rebalance_stamps(self) -> None:
        preds = self._preds([
            "2021-07-01", "2021-07-02", "2021-07-05", "2021-07-06",
            "2021-07-07", "2021-07-08",
        ])
        out = BacktestRunner._thin_predictions(
            preds, cadence_days=5, phase=0, anchor="fold_phase",
        )
        kept = sorted(out.index.get_level_values(0).unique().strftime("%Y-%m-%d"))
        self.assertEqual(kept, ["2021-07-01", "2021-07-08"])
        # per-day cross-sections survive intact
        self.assertEqual(len(out), 4)

    def test_empty_thinning_fails_loud(self) -> None:
        preds = self._preds(["2021-07-01", "2021-07-02"])
        with self.assertRaisesRegex(BacktestRunnerError, "ZERO"):
            BacktestRunner._thin_predictions(
                preds, cadence_days=5, phase=3, anchor="fold_phase",
            )


class CadenceDisciplineTests(unittest.TestCase):
    def test_fingerprints_differ_across_cadence(self) -> None:
        base = WalkForwardConfig()
        weekly = WalkForwardConfig(rebalance_cadence_days=5)
        self.assertNotEqual(
            compute_config_fingerprint(base),
            compute_config_fingerprint(weekly),
        )

    def test_cadence_repr_canonical_and_stub_tolerant(self) -> None:
        self.assertEqual(
            rebalance_cadence_repr(WalkForwardConfig(rebalance_cadence_days=5)),
            "N=5,phase=0,anchor=fold_phase",
        )
        self.assertIsNone(rebalance_cadence_repr(object()))

    def test_decide_fold_names_the_cadence_cause(self) -> None:
        from types import SimpleNamespace

        from src.core.walk_forward._resume import ResumeMode, decide_fold

        manifest = SimpleNamespace(
            config_fingerprint="aaaa1111",
            label_horizon_days=1,
            rebalance_cadence="N=1,phase=0,anchor=fold_phase",
            train_period="t", test_period="s", valid_period="v",
        )
        decision = decide_fold(
            fold_index=0,
            train_period="t", test_period="s", valid_period="v",
            config_fingerprint="bbbb2222",
            discovered={0: manifest},  # type: ignore[dict-item]
            resume_mode=ResumeMode.AUTO,
            label_horizon_days=1,
            rebalance_cadence="N=5,phase=0,anchor=fold_phase",
        )
        self.assertFalse(decision.skip)
        self.assertIn("rebalance cadence changed", decision.reason)
        self.assertIn("N=1", decision.reason)
        self.assertIn("N=5", decision.reason)

    def test_decide_fold_names_pre_upgrade_manifest(self) -> None:
        from types import SimpleNamespace

        from src.core.walk_forward._resume import ResumeMode, decide_fold

        manifest = SimpleNamespace(
            config_fingerprint="aaaa1111",
            label_horizon_days=1,
            rebalance_cadence=None,
            train_period="t", test_period="s", valid_period="v",
        )
        decision = decide_fold(
            fold_index=0,
            train_period="t", test_period="s", valid_period="v",
            config_fingerprint="bbbb2222",
            discovered={0: manifest},  # type: ignore[dict-item]
            resume_mode=ResumeMode.AUTO,
            label_horizon_days=1,
            rebalance_cadence="N=5,phase=0,anchor=fold_phase",
        )
        self.assertFalse(decision.skip)
        self.assertIn("predates rebalance-cadence stamping", decision.reason)


if __name__ == "__main__":
    unittest.main()
