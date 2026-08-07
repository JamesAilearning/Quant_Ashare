"""Governance: ``risk_constraints_mode`` is a narrow, pinned opt-in.

``WARN_AND_CLIP`` exists so a run whose PURPOSE is exporting
out-of-fold predictions is not killed by a post-trade portfolio
violation that has nothing to do with them. Returns computed under a
clipped allocation are NOT the RAISE-validated ones, so the escape
hatch must never become the way official metrics get past the cap:
these pins keep the default at RAISE, reject unknown values on both
engines, and enumerate exactly which tracked presets may declare it.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import yaml

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# The ONLY tracked presets allowed to relax the reaction. Adding a name
# here is a deliberate governance act, reviewed as such.
_ALLOWED_WARN_AND_CLIP = {"pv_incremental_baseline.yaml"}


class RiskConstraintsModeOptIn(unittest.TestCase):
    def test_default_is_raise_on_both_engines(self) -> None:
        # Two engines, one schema: a default that drifted on one side
        # would silently change the other's semantics at the next
        # config copy.
        from src.core.pipeline import PipelineConfig
        from src.core.walk_forward.config import WalkForwardConfig
        for cls in (PipelineConfig, WalkForwardConfig):
            field = cls.__dataclass_fields__["risk_constraints_mode"]
            self.assertEqual("raise", field.default, cls.__name__)

    def test_unknown_mode_is_rejected_on_both_engines(self) -> None:
        from src.core.pipeline import PipelineConfig, PipelineError
        from src.core.walk_forward.config import (
            WalkForwardConfig,
            WalkForwardError,
        )
        with self.assertRaises(PipelineError) as ctx:
            PipelineConfig(provider_uri="x", risk_constraints_mode="clip")
        self.assertIn("risk_constraints_mode", str(ctx.exception))
        with self.assertRaises(WalkForwardError) as ctx2:
            WalkForwardConfig(risk_constraints_mode="clip")
        self.assertIn("risk_constraints_mode", str(ctx2.exception))

    def test_mode_reaches_the_constraints_object(self) -> None:
        # Recording the field without threading it would look exactly
        # like a working opt-in while every fold still aborted.
        import inspect

        from src.core import pipeline
        from src.core.walk_forward import engine
        for mod in (pipeline, engine):
            src = inspect.getsource(mod)
            self.assertIn(
                "mode=RiskConstraintMode(config.risk_constraints_mode)",
                src, mod.__name__)

    def test_only_the_baseline_preset_relaxes_the_reaction(self) -> None:
        # The leak guard: an official-metrics preset that quietly
        # adopted warn_and_clip would publish returns the RAISE
        # validation never saw.
        offenders = []
        for preset in sorted(
                (_PROJECT_ROOT / "config" / "presets").glob("*.yaml")):
            raw = yaml.safe_load(preset.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                continue
            if raw.get("risk_constraints_mode") == "warn_and_clip" \
                    and preset.name not in _ALLOWED_WARN_AND_CLIP:
                offenders.append(preset.name)
        self.assertEqual([], offenders)

    def test_the_baseline_preset_actually_declares_it(self) -> None:
        # The other direction: if the campaign preset loses the opt-in,
        # the run silently goes back to discarding whole quarters of
        # baseline predictions on a 0.01pp drift.
        raw = yaml.safe_load(
            (_PROJECT_ROOT / "config" / "presets"
             / "pv_incremental_baseline.yaml").read_text(encoding="utf-8"))
        self.assertEqual("warn_and_clip", raw["risk_constraints_mode"])
        # And it stays production-equivalent everywhere else.
        self.assertIs(True, raw["risk_constraints_enabled"])
        self.assertEqual("campaign_v1", raw["risk_constraints_calibration"])

    def test_campaign_calibration_limits_are_untouched(self) -> None:
        # The opt-in changes the REACTION, never the limits — relaxing
        # those would be a different (and much larger) decision.
        from src.core.risk_constraints import (
            RiskConstraintMode,
            campaign_risk_constraints_v1,
        )
        c = campaign_risk_constraints_v1()
        self.assertEqual(0.05, c.max_per_name)
        self.assertEqual(1.0, c.max_leverage)
        self.assertIs(RiskConstraintMode.RAISE, c.mode)


if __name__ == "__main__":
    unittest.main()
