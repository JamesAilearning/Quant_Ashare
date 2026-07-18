"""Governance pins for the CSI800 cadence (N5) campaign
(2026-07-17-csi800-cadence-campaign, DP-1..DP-6 signed 2026-07-17).

Coverage matrix (>=1 pin per dimension):
  N5 pair band     — base vs conservative differ EXACTLY in
                     {slippage_bps, output_dir}; band values 5.0/20.0.
  N5 reference     — reference vs N5 base differ EXACTLY in
                     {instruments, benchmark_code,
                     attribution_sleeve_grouping, output_dir}.
  N5-vs-N1 role    — each N5 preset differs from its N1 counterpart
                     EXACTLY in the three cadence fields + output_dir
                     (N1 presets inherit cadence defaults; N5 pins them
                     explicitly so the raw diff is visible).
  resolved cadence — all three N5 presets resolve to the SAME
                     pre-registered 5 / 0 / fold_phase (DP-2; changing N
                     needs a new OpenSpec change and voids results).
  primary criteria — certify's DP-3 numbers (net > 0, gross retention
                     >= 50%, arm divergence <= 5%) and the sidecar
                     schema/mainline ref are pinned against silent
                     drift.
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

CADENCE_FIELDS = {
    "rebalance_cadence_days": 5,
    "rebalance_phase": 0,
    "rebalance_anchor": "fold_phase",
    # R1 (codex #378 r3): rebalance-day constraint scoping is an
    # EXPLICIT preset opt-in — canonical default stays "all_days".
    "risk_constraint_scope": "rebalance_days",
}


def _load(name: str) -> dict:
    return yaml.safe_load((_PRESETS / name).read_text(encoding="utf-8"))


class CadencePresetPins(unittest.TestCase):
    def test_n5_pair_differs_only_in_slippage_and_output_dir(self) -> None:
        base = _load("csi800_cadence5_base.yaml")
        cons = _load("csi800_cadence5_conservative.yaml")
        diff = {k for k in set(base) | set(cons)
                if base.get(k) != cons.get(k)}
        self.assertEqual(
            {"slippage_bps", "output_dir"}, diff,
            f"N5 pair drifted: {sorted(diff)} — the sensitivity band is "
            "void unless slippage_bps (+ per-arm output_dir) is the only "
            "difference.",
        )
        self.assertEqual(5.0, base["slippage_bps"])
        self.assertEqual(20.0, cons["slippage_bps"])
        self.assertNotEqual(base["output_dir"], cons["output_dir"])

    def test_n5_reference_matches_base_except_universe(self) -> None:
        base = _load("csi800_cadence5_base.yaml")
        ref = _load("csi300_cadence5_reference.yaml")
        diff = {k for k in set(base) | set(ref)
                if base.get(k) != ref.get(k)}
        self.assertEqual(
            {"instruments", "benchmark_code", "attribution_sleeve_grouping",
             "output_dir"},
            diff,
            f"N5 reference drifted from the base arm: {sorted(diff)} — "
            "veto-3's baseline must be 同配置 INCLUDING the cadence "
            "fields (spec revision 2026-07-17).",
        )
        self.assertEqual(5.0, ref.get("slippage_bps"))
        self.assertIs(True, ref.get("risk_constraints_enabled"))

    def test_n5_vs_n1_role_diff_is_cadence_plus_output_dir(self) -> None:
        # DP-1: the N=1 control is the #373 campaign (not re-run); its
        # presets stay untouched, so the N5 counterpart may differ ONLY
        # by pinning the three cadence fields + its own output dir.
        for n5_name, n1_name in (
            ("csi800_cadence5_base.yaml", "csi800_campaign_base.yaml"),
            ("csi800_cadence5_conservative.yaml",
             "csi800_campaign_conservative.yaml"),
            ("csi300_cadence5_reference.yaml",
             "csi300_campaign_reference.yaml"),
        ):
            with self.subTest(role=n5_name):
                n5, n1 = _load(n5_name), _load(n1_name)
                diff = {k for k in set(n5) | set(n1)
                        if n5.get(k) != n1.get(k)}
                self.assertEqual(
                    set(CADENCE_FIELDS) | {"output_dir"}, diff,
                    f"{n5_name} vs {n1_name} drifted: {sorted(diff)}",
                )

    def test_resolved_cadence_is_preregistered_for_all_three(self) -> None:
        # DP-2 pre-registration: N=5 / phase=0 / fold_phase, identical
        # across the trio; N is not tunable after results exist.
        for name in ("csi800_cadence5_base.yaml",
                     "csi800_cadence5_conservative.yaml",
                     "csi300_cadence5_reference.yaml"):
            cfg = _load(name)
            for field, expected in CADENCE_FIELDS.items():
                with self.subTest(preset=name, field=field):
                    self.assertEqual(expected, cfg.get(field))

    def test_campaign_trio_shared_invariants(self) -> None:
        for name in ("csi800_cadence5_base.yaml",
                     "csi800_cadence5_conservative.yaml",
                     "csi300_cadence5_reference.yaml"):
            cfg = _load(name)
            with self.subTest(preset=name):
                self.assertEqual("../../config_walk.yaml",
                                 cfg.get("extends"))
                self.assertIs(True, cfg.get("risk_constraints_enabled"))
                self.assertEqual("campaign_v1",
                                 cfg.get("risk_constraints_calibration"))


class PrimaryCriteriaPins(unittest.TestCase):
    def test_dp3_numbers_pinned_in_certify(self) -> None:
        # DP-3 (frozen 2026-07-17): conservative net > 0 AND N5 gross
        # retention >= 50% of N1 (conservative-to-conservative), with a
        # <= 5% base/conservative gross-divergence guard on both pairs.
        # Changing any of these needs a new OpenSpec change.
        from scripts.research import csi800_campaign_certify as certify

        self.assertEqual(0.0, certify.NET_MIN)
        self.assertEqual(0.50, certify.GROSS_RETENTION_MIN)
        self.assertEqual(0.05, certify.ARM_DIVERGENCE_MAX)

    def test_certify_anchoring_constants_pinned(self) -> None:
        # the sole promotion authority anchors to origin/main and emits
        # the v1 sidecar schema — silent drift here would re-route the
        # promotion gate.
        from scripts.research import csi800_campaign_certify as certify

        self.assertEqual("origin/main", certify.MAINLINE_REF)
        self.assertEqual("csi800_cadence_verdict_v1",
                         certify.SIDECAR_SCHEMA)

    def test_pair_schema_is_v3(self) -> None:
        from scripts.research import csi800_campaign_pair_report as pair

        self.assertEqual("csi800_pair_report_v3", pair.SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()
