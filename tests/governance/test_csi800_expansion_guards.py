"""Governance (CI-runnable, no bundle): CSI800 expansion guard pins.

The v2-csi800-expansion-guards contract (openspec change
``2026-07-16-csi800-antiinflation-guards``) freezes the anti-inflation
veto NUMBERS and the sensitivity-band presets BEFORE any campaign
backtest exists. These pins make post-hoc tampering loud:

1. The conservative preset differs from the base csi800 preset in
   EXACTLY ``slippage_bps`` (20.0), and the base preset leaves
   ``slippage_bps`` on the in-code 5.0 default — the DP-2 band.
2. The five veto numbers in the spec text stay byte-identical
   (conservative 20 bps / csi500-dependence 80% / turnover 1.5x /
   risk-constraint defaults 0.05+0.40 / concentration 75%+10%).
3. The pair-report tool's comparison projection whitelist stays the
   explicit run-identity constant (no semantic field smuggled in).
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

import yaml

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_PRESETS = _PROJECT_ROOT / "config" / "presets"
# The spec text lives under openspec/changes/ until /opsx:archive merges
# it into openspec/specs/ — accept either location, require at least one.
_SPEC_CANDIDATES = (
    _PROJECT_ROOT / "openspec" / "specs" / "v2-csi800-expansion-guards"
    / "spec.md",
    _PROJECT_ROOT / "openspec" / "changes"
    / "2026-07-16-csi800-antiinflation-guards" / "specs"
    / "v2-csi800-expansion-guards" / "spec.md",
)

CONSERVATIVE_SLIPPAGE_BPS = 20.0
BASE_SLIPPAGE_BPS = 5.0


def _load(name: str) -> dict:
    return yaml.safe_load((_PRESETS / name).read_text(encoding="utf-8"))


def _spec_text() -> str:
    for p in _SPEC_CANDIDATES:
        if p.is_file():
            return p.read_text(encoding="utf-8")
    raise AssertionError(
        "v2-csi800-expansion-guards spec text not found in either "
        "openspec/specs/ or openspec/changes/ — the veto sheet must exist "
        "BEFORE any campaign run."
    )


class SensitivityBandPresetPins(unittest.TestCase):
    def test_conservative_preset_differs_only_in_slippage(self) -> None:
        base = _load("csi800.yaml")
        cons = _load("csi800_conservative.yaml")
        self.assertNotIn(
            "slippage_bps", base,
            "csi800.yaml must NOT declare slippage_bps — the base band is "
            f"the in-code {BASE_SLIPPAGE_BPS} default; declaring it here "
            "would silently decouple the band from the runtime default.",
        )
        extra = set(cons) - set(base)
        self.assertEqual(
            {"slippage_bps"}, extra,
            f"conservative preset adds {sorted(extra)}; the ONLY allowed "
            "addition is slippage_bps (DP-2 band).",
        )
        for key in base:
            self.assertEqual(
                base[key], cons.get(key),
                f"csi800_conservative.yaml[{key!r}] diverges from "
                "csi800.yaml — the pair must be identical except "
                "slippage_bps or the sensitivity band comparison is void.",
            )
        self.assertEqual(CONSERVATIVE_SLIPPAGE_BPS, cons["slippage_bps"])

    def test_base_band_is_the_incode_default(self) -> None:
        from src.core.pipeline import PipelineConfig
        from src.core.walk_forward.config import WalkForwardConfig
        for cls in (PipelineConfig, WalkForwardConfig):
            self.assertEqual(
                BASE_SLIPPAGE_BPS,
                cls.__dataclass_fields__["slippage_bps"].default,
                f"{cls.__name__}.slippage_bps default moved off "
                f"{BASE_SLIPPAGE_BPS} — the veto sheet's base band and the "
                "runtime default have drifted apart; re-sign DP-2 before "
                "changing either.",
            )


class CampaignWalkForwardPairPins(unittest.TestCase):
    """The walk-forward campaign pair (base/conservative) must stay a
    true sensitivity band: identical except the DP-2 slippage values and
    the per-arm output dirs (run-level pairing is separately proven by
    the pair-report tool from persisted configs)."""

    def test_campaign_pair_differs_only_in_slippage_and_output_dir(
            self) -> None:
        base = _load("csi800_campaign_base.yaml")
        cons = _load("csi800_campaign_conservative.yaml")
        diff = {k for k in set(base) | set(cons)
                if base.get(k) != cons.get(k)}
        self.assertEqual(
            {"slippage_bps", "output_dir"}, diff,
            f"campaign pair drifted: differing keys {sorted(diff)} — the "
            "sensitivity band is void unless slippage_bps (+ per-arm "
            "output_dir) is the only difference.",
        )
        self.assertEqual(BASE_SLIPPAGE_BPS, base["slippage_bps"])
        self.assertEqual(CONSERVATIVE_SLIPPAGE_BPS, cons["slippage_bps"])
        self.assertNotEqual(base["output_dir"], cons["output_dir"])
        for cfg, name in ((base, "base"), (cons, "conservative")):
            self.assertEqual("csi800", cfg.get("instruments"), name)
            self.assertEqual("SH000906TR", cfg.get("benchmark_code"), name)
            self.assertIs(True, cfg.get("attribution_sleeve_grouping"), name)
            self.assertIs(True, cfg.get("risk_constraints_enabled"), name)
            self.assertEqual("../../config_walk.yaml", cfg.get("extends"),
                             name)

    def test_csi300_reference_matches_base_except_universe(self) -> None:
        # codex P1 on #371: veto-3's turnover baseline must be a MATCHED
        # config — the reference differs from the base arm ONLY in the
        # universe/benchmark pair, the (csi300-meaningless) sleeve switch,
        # and its own output dir. risk_constraints stays ON for literal
        # config symmetry (it is post-trade validation and does not alter
        # official turnover; RAISE aborts rather than mutates).
        base = _load("csi800_campaign_base.yaml")
        ref = _load("csi300_campaign_reference.yaml")
        diff = {k for k in set(base) | set(ref)
                if base.get(k) != ref.get(k)}
        self.assertEqual(
            {"instruments", "benchmark_code", "attribution_sleeve_grouping",
             "output_dir"},
            diff,
            f"csi300 reference drifted from the base arm: {sorted(diff)}",
        )
        self.assertIs(True, ref.get("risk_constraints_enabled"))
        self.assertEqual(BASE_SLIPPAGE_BPS, ref.get("slippage_bps"))
        self.assertNotEqual(base["output_dir"], ref["output_dir"])

    def test_reference_resolved_universe_is_pinned(self) -> None:
        # codex P2 on #371 r2: the reference INHERITS universe/benchmark
        # from config_walk.yaml — a raw-mapping diff cannot see a parent
        # drift (e.g. config_walk moving to another universe would turn
        # veto-3 into a csi800-vs-<other> comparison). Pin the RESOLVED
        # values through the same inheritance loader the runner uses.
        from src.core._yaml_loader import load_yaml_with_inheritance
        resolved_ref = load_yaml_with_inheritance(
            _PRESETS / "csi300_campaign_reference.yaml")
        self.assertEqual("csi300", resolved_ref.get("instruments"))
        self.assertEqual("SH000300TR", resolved_ref.get("benchmark_code"))
        for name in ("csi800_campaign_base.yaml",
                     "csi800_campaign_conservative.yaml"):
            resolved = load_yaml_with_inheritance(_PRESETS / name)
            self.assertEqual("csi800", resolved.get("instruments"), name)
            self.assertEqual("SH000906TR", resolved.get("benchmark_code"),
                             name)


class VetoSheetNumberPins(unittest.TestCase):
    """The five DP-4 numbers, pinned as literal spec text — editing any
    of them after campaign data exists must fail HERE first."""

    def test_veto_numbers_pinned(self) -> None:
        text = _spec_text()
        for label, pattern in (
            ("conservative 20 bps", r"conservative\s*=\s*\*{0,2}20\s*bps"),
            ("csi500 dependence 80%", r"≥\s*80%"),
            ("turnover 1.5x", r"1\.5\s*倍"),
            ("max_per_name 0.05", r"max_per_name\s*=\s*0\.05"),
            ("max_per_board 0.40", r"max_per_board\s*=\s*0\.40"),
            ("csi500 weight 75%", r">\s*75%"),
            ("unknown bucket 10%", r">\s*10%"),
        ):
            self.assertIsNotNone(
                re.search(pattern, text),
                f"veto sheet number missing/altered: {label} "
                f"(pattern {pattern!r}) — the DP-4 numbers are frozen; "
                "changing one requires a new OpenSpec change and voids "
                "existing campaign results.",
            )

    def test_conservative_preset_matches_spec_number(self) -> None:
        cons = _load("csi800_conservative.yaml")
        self.assertEqual(
            CONSERVATIVE_SLIPPAGE_BPS, cons["slippage_bps"],
            "preset conservative slippage and the spec's DP-2 value must "
            "be the same number.",
        )


class PairReportProjectionPins(unittest.TestCase):
    def test_required_veto_check_names_pinned(self) -> None:
        from scripts.research.csi800_campaign_pair_report import (
            REQUIRED_VETO_CHECKS,
        )
        self.assertEqual(
            (
                "1_conservative_net_excess",
                "2_csi500_dependence",
                "3_turnover_vs_csi300_ref",
                "4_risk_constraints_recorded",
                "5_midcap_concentration",
            ),
            REQUIRED_VETO_CHECKS,
            "the canonical veto check set changed — eligibility is judged "
            "against these five DP-4 names; renaming/removing one detaches "
            "the checklist from the spec's veto sheet.",
        )

    def test_projection_whitelist_is_run_identity_only(self) -> None:
        from scripts.research.csi800_campaign_pair_report import (
            RUN_IDENTITY_FIELDS,
        )
        self.assertEqual(
            frozenset({"output_dir"}), RUN_IDENTITY_FIELDS,
            "the comparison-projection whitelist changed — it may contain "
            "ONLY run-identity/output-location fields (spec: adding a "
            "semantic field here is a forbidden escape hatch; extending it "
            "legitimately requires updating this pin WITH review).",
        )


if __name__ == "__main__":
    unittest.main()
