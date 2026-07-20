"""Governance: CSI800 N5 production serving — the two-level binding
chain (2026-07-20-csi800-n5-production-promotion PR-A, DP-5,
codex #385 r1/r2).

The production anchor (iso-week) and the certified winner's anchor
(fold_phase) are DIFFERENT schedules under v2-rebalance-cadence, so
serving must NOT bind to the winner through a whitelist that absorbs
the anchor drift. Instead the chain is pinned in two exact levels:

  level 1: iso_week re-check preset  vs  certified winner preset
           — exact diff == {rebalance_anchor, output_dir}
  level 2: serving params            vs  iso_week re-check preset
           — every semantic key equal; serving-only keys ⊆ whitelist

The anchor difference therefore exists ONLY at level 1, and level 1's
run must pass the pre-registered re-check gate (PR-B) before promotion.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import yaml

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_PRESETS = _PROJECT_ROOT / "config" / "presets"
_SERVING = _PROJECT_ROOT / "config" / "serving" / "csi800_n5_production.yaml"

# Serving-side keys that may exist WITHOUT a counterpart in the walk
# preset chain (pre-registered whitelist — codex #385 r1: the anchor can
# never be whitelisted here; it must agree with the level-1 preset).
SERVING_ONLY_KEYS = {"out_dir"}

# Semantic keys the serving params MUST carry, each equal to the
# iso_week re-check preset's resolved value (preset raw first, falling
# back to the config_walk base it extends).
SEMANTIC_KEYS = (
    "instruments", "benchmark_code", "attribution_sleeve_grouping",
    "risk_constraints_enabled", "risk_constraints_calibration",
    "slippage_bps", "rebalance_cadence_days", "rebalance_phase",
    "rebalance_anchor", "risk_constraint_scope", "topk",
)


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


class LevelOneIsoWeekPresetPin(unittest.TestCase):
    def test_isoweek_preset_differs_only_in_anchor_and_output_dir(self) -> None:
        winner = _load(_PRESETS / "csi800_cadence5_conservative.yaml")
        isoweek = _load(
            _PRESETS / "csi800_cadence5_conservative_isoweek.yaml")
        diff = {k for k in set(winner) | set(isoweek)
                if winner.get(k) != isoweek.get(k)}
        self.assertEqual(
            {"rebalance_anchor", "output_dir"}, diff,
            f"iso_week re-check preset drifted: {sorted(diff)} — the "
            "anchor slice re-checks the CERTIFIED winner; any other "
            "difference voids the re-check (needs a new OpenSpec "
            "change).",
        )
        self.assertEqual("iso_week", isoweek["rebalance_anchor"])
        self.assertEqual("fold_phase", winner["rebalance_anchor"])
        self.assertNotEqual(winner["output_dir"], isoweek["output_dir"])


class LevelTwoServingParamsPin(unittest.TestCase):
    def test_serving_semantic_keys_equal_isoweek_preset(self) -> None:
        serving = _load(_SERVING)
        isoweek = _load(
            _PRESETS / "csi800_cadence5_conservative_isoweek.yaml")
        base = _load(_PROJECT_ROOT / "config_walk.yaml")
        for key in SEMANTIC_KEYS:
            expected = isoweek.get(key, base.get(key))
            with self.subTest(key=key):
                self.assertIn(
                    key, serving,
                    f"serving params missing semantic key {key!r}")
                self.assertEqual(
                    expected, serving[key],
                    f"serving params drifted from the iso_week re-check "
                    f"preset on {key!r} — the binding chain requires "
                    "same-value semantics (needs a new OpenSpec change).",
                )

    def test_serving_only_keys_are_whitelisted(self) -> None:
        serving = _load(_SERVING)
        extras = set(serving) - set(SEMANTIC_KEYS)
        self.assertEqual(
            SERVING_ONLY_KEYS, extras,
            f"serving-side keys drifted: {sorted(extras)} vs whitelist "
            f"{sorted(SERVING_ONLY_KEYS)} — the whitelist is "
            "pre-registered; the rebalance anchor in particular can "
            "NEVER be absorbed here (codex #385 r1).",
        )

    def test_anchor_cannot_be_whitelisted(self) -> None:
        # The scenario pin: rebalance_anchor is a SEMANTIC key and not a
        # serving-only key, so an attempt to move it into the whitelist
        # set is itself a governed change caught by this assertion pair.
        self.assertIn("rebalance_anchor", SEMANTIC_KEYS)
        self.assertNotIn("rebalance_anchor", SERVING_ONLY_KEYS)


class GuardEvalProfilePin(unittest.TestCase):
    def test_csi800_n5_profile_matches_certified_semantics(self) -> None:
        # PR-B (DP-3): the guard-eval csi800_n5 profile IS the certified
        # winner's semantics — every knob equal to the iso_week re-check
        # preset chain. A drifted knob silently changes the promotion
        # gate; pin each value.
        from scripts.eval_frozen_model_oos import resolve_profile

        profile = resolve_profile("csi800_n5")
        isoweek = _load(
            _PRESETS / "csi800_cadence5_conservative_isoweek.yaml")
        for key in ("instruments", "benchmark_code", "slippage_bps",
                    "rebalance_cadence_days", "rebalance_phase",
                    "rebalance_anchor", "risk_constraint_scope"):
            with self.subTest(key=key):
                self.assertEqual(isoweek[key], profile[key])
        self.assertIs(True, profile["campaign_constraints"])

    def test_legacy_profile_is_byte_identical_to_history(self) -> None:
        # The ④ path must stay reproducible: csi300_daily pins the
        # historical knob set exactly (replay constants + SH000300TR +
        # daily + no constraints).
        from scripts.eval_frozen_model_oos import resolve_profile
        from scripts.regen.replay_frozen_baseline import SLIPPAGE_BPS

        profile = resolve_profile("csi300_daily")
        self.assertEqual(
            {"instruments": "csi300", "benchmark_code": "SH000300TR",
             "slippage_bps": SLIPPAGE_BPS, "rebalance_cadence_days": 1,
             "rebalance_phase": 0, "rebalance_anchor": "fold_phase",
             "risk_constraint_scope": "all_days",
             "campaign_constraints": False},
            profile,
        )

    def test_unknown_profile_refused(self) -> None:
        from scripts.eval_frozen_model_oos import resolve_profile

        with self.assertRaises(ValueError):
            resolve_profile("csi800_daily")


class CandidateTrainingPresetPin(unittest.TestCase):
    def test_candidate_windows_mirror_stage4_arithmetic(self) -> None:
        # DP-1 (跑前钉死): the candidate trains on the ④-mirror windows —
        # train/valid untouched by the guard, guard = the committed
        # comparison-origin window (docs/promotion/). Drift = a
        # different gate.
        cand = _load(_PRESETS / "csi800_n5_candidate.yaml")
        self.assertEqual("csi800", cand["instruments"])
        self.assertEqual("SH000906TR", cand["benchmark_code"])
        self.assertEqual("2018-01-02", cand["train_start"])
        self.assertEqual("2024-12-18", cand["train_end"])
        self.assertEqual("2025-01-02", cand["valid_start"])
        self.assertEqual("2025-06-26", cand["valid_end"])
        self.assertEqual("2025-07-01", cand["test_start"])
        self.assertEqual("2026-06-12", cand["test_end"])
        self.assertEqual("gpu", cand["compute_device"])
        # csi800 mandatory guard trio (config-level rule).
        self.assertIs(True, cand["attribution_sleeve_grouping"])
        self.assertIs(True, cand["risk_constraints_enabled"])
        self.assertEqual("campaign_v1",
                         cand["risk_constraints_calibration"])


if __name__ == "__main__":
    unittest.main()
