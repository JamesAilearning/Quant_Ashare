"""Governance pins for the pv_incremental_v1 freeze (PV-DP-1..8).

The frozen plan is the signed artifact — these pins make any drift a
red test instead of a silent re-registration.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import yaml

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_PLAN = yaml.safe_load(
    (_PROJECT_ROOT / "docs" / "prereg" / "pv_incremental.yaml").read_text(
        encoding="utf-8"))


class PvIncrementalFreezePins(unittest.TestCase):
    def test_protocol_identity(self) -> None:
        self.assertEqual("pv_incremental_v1", _PLAN["protocol_id"])
        self.assertIs(False, _PLAN["holdout_unblinded"])

    def test_operator_whitelist_is_the_registry_verbatim(self) -> None:
        # PV-DP-4: exactly the baseline 28, zero extensions, no ts_cov.
        from src.factor_mining.grammar import REGISTRY

        self.assertEqual(sorted(REGISTRY.names()),
                         sorted(_PLAN["operators"]))
        self.assertEqual(28, len(_PLAN["operators"]))
        self.assertNotIn("ts_cov", _PLAN["operators"])
        self.assertIs(True, _PLAN["ts_cov_excluded"])

    def test_gp_depth_pins_match_engine_defaults(self) -> None:
        from src.factor_mining.gp_engine import GPConfig

        cfg = GPConfig()
        self.assertEqual(cfg.max_depth, _PLAN["gp"]["max_depth"])
        self.assertEqual(cfg.min_depth, _PLAN["gp"]["min_depth"])

    def test_fields_are_the_signed_seven(self) -> None:
        self.assertEqual(
            ["open", "high", "low", "close", "volume", "money",
             "turnover_rate"], _PLAN["fields"])

    def test_windows_are_the_signed_values(self) -> None:
        w = _PLAN["windows"]
        self.assertEqual(
            ("2018-01-01", "2022-12-31", "2023-01-01", "2024-12-31",
             2025, "2026-01-01"),
            (w["is_start"], w["is_end"], w["oos_start"], w["oos_end"],
             w["holdout_year"], w["forbidden_from"]))

    def test_fwer_mechanism_pins(self) -> None:
        f = _PLAN["fwer"]
        # codex #399 r10: sidedness is FROZEN — survival is the
        # one-sided event t >= threshold, so the null statistic is
        # the signed max-t (Gate-4A-sourced), never |t|.
        self.assertEqual("one_sided_max_t", f["statistic"])
        self.assertEqual("full_batch_block_bootstrap_max_statistic",
                         f["method"])
        self.assertEqual(2.85, f["hard_floor_t"])
        self.assertEqual(0.95, f["quantile"])
        self.assertEqual(120, f["per_trial_min_n_days"])
        self.assertEqual("reject_iff", f["tri_state"]["clean_negative"])
        # codex #399 r1: significant-but-non-incremental is a DISTINCT
        # state routed to the operator — never laundered into
        # clean_negative's reject_iff.
        self.assertEqual("operator_decision",
                         f["tri_state"]["significant_non_incremental"])

    def test_orthogonality_coverage_pin(self) -> None:
        self.assertEqual(
            0.95,
            _PLAN["fitness"]["orthogonality"]["min_coverage_of_ic_days"])

    def test_implementation_pr_decisions_pinned(self) -> None:
        # The three口径 the operator signed for the implementation PR
        # (PV-DP-3 left them to it). Frozen here so a later edit is a
        # deliberate protocol change, not a silent drift.
        orth = _PLAN["fitness"]["orthogonality"]
        base = _PLAN["fitness"]["baseline"]
        # ① IS coverage: penalise only days the baseline covers — the
        # baseline keeps the production fold geometry rather than
        # manufacturing earlier folds.
        self.assertEqual("penalize_covered_days_only",
                         orth["is_coverage_policy"])
        # ② production-equivalent warm ensemble, not single-model.
        self.assertEqual(3, base["ensemble_window"])
        # ③ GP breeding signal uses |rank-IC| (direction belongs to the
        # expression; the one-sided FWER threshold culls negatives).
        self.assertEqual("abs_rank_ic", _PLAN["fitness"]["ic_term"])
        # Sacred-invariant defence: the baseline run's hard tail must
        # stop before the blinded holdout year.
        self.assertEqual("2024-12-31", base["overall_end"])
        self.assertEqual(_PLAN["windows"]["oos_end"], base["overall_end"])

    def test_baseline_preset_matches_frozen_tail(self) -> None:
        # The preset that drives the baseline run must pin the frozen
        # tail: the parent config_walk.yaml default (2025-12-31) would
        # train into and predict the holdout year.
        preset = yaml.safe_load(
            (_PROJECT_ROOT / "config" / "presets"
             / "pv_incremental_baseline.yaml").read_text(encoding="utf-8"))
        self.assertEqual(_PLAN["fitness"]["baseline"]["overall_end"],
                         preset["overall_end"])
        self.assertEqual(_PLAN["universe"]["instruments"],
                         preset["instruments"])

    def test_universe_and_scope(self) -> None:
        self.assertEqual("csi800", _PLAN["universe"]["instruments"])
        self.assertIs(False, _PLAN["universe"]["ex_financials"])
        self.assertIs(
            False, _PLAN["promotion"]["production_wiring_in_scope"])


if __name__ == "__main__":
    unittest.main()
