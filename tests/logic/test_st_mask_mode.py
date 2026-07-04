"""Tests for ``WalkForwardConfig.st_mask_mode`` — the EXPLICIT ST-off
experiment opt-out (阶段6 label-horizon campaign enabler).

Audit E1 / PR-F made the ST mask mandatory on the official walk-forward path
(``require_st_mask=True``), which also made the runbook-mandated ST-off
isolated experiments (docs/run-comparison-runbook.md) impossible to run.
``st_mask_mode`` restores that capability WITHOUT reopening the silent path:
the opt-out is explicit in the config, rejected when contradictory, and rides
into ``walk_forward_report.json`` via the embedded config.
"""
from __future__ import annotations

import sys
import unittest
from dataclasses import asdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.walk_forward.config import WalkForwardConfig, WalkForwardError  # noqa: E402


class StMaskModeConfigTests(unittest.TestCase):
    def test_default_is_required_and_maps_to_hard_mask(self) -> None:
        # Byte-identical official semantics: default mode is "required" and
        # the engine-facing property demands the mask.
        cfg = WalkForwardConfig()
        self.assertEqual(cfg.st_mask_mode, "required")
        self.assertTrue(cfg.requires_st_mask)

    def test_required_with_namechange_path_unchanged(self) -> None:
        cfg = WalkForwardConfig(namechange_path="D:/data/all_namechanges.parquet")
        self.assertTrue(cfg.requires_st_mask)

    def test_unknown_mode_rejected(self) -> None:
        for bad in ("off", "ON", "", "experiment", None):
            with self.assertRaises(WalkForwardError, msg=f"bad={bad!r}"):
                WalkForwardConfig(st_mask_mode=bad)  # type: ignore[arg-type]

    def test_off_experiment_requires_no_namechange_path(self) -> None:
        # Contradiction: an ST-off experiment carrying ST-mask inputs is a
        # config error, not a preference — one variable at a time.
        with self.assertRaises(WalkForwardError):
            WalkForwardConfig(
                st_mask_mode="off_experiment",
                namechange_path="D:/data/all_namechanges.parquet",
            )

    def test_off_experiment_accepts_absent_or_blank_namechange(self) -> None:
        for blank in (None, "", "   "):
            cfg = WalkForwardConfig(
                st_mask_mode="off_experiment", namechange_path=blank,
            )
            self.assertFalse(cfg.requires_st_mask, msg=f"blank={blank!r}")

    def test_mode_is_stamped_via_config_asdict(self) -> None:
        # The aggregate report embeds ``asdict(config)`` — the mode must ride
        # along so a comparison can PROVE both sides ran the same ST handling.
        cfg = WalkForwardConfig(st_mask_mode="off_experiment")
        self.assertEqual(asdict(cfg)["st_mask_mode"], "off_experiment")


class EngineWiringPinTests(unittest.TestCase):
    def test_engine_passes_mode_derived_require_st_mask(self) -> None:
        # Source-level pin (same style as the qlib-caller whitelist tests):
        # the official backtest call must derive ``require_st_mask`` from the
        # config property — a hardcoded True would break the sanctioned
        # experiment opt-out; a hardcoded False would break audit E1 / PR-F.
        source = (
            PROJECT_ROOT / "src" / "core" / "walk_forward" / "engine.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "require_st_mask=config.requires_st_mask", source,
            msg=(
                "engine.py no longer derives require_st_mask from "
                "WalkForwardConfig.requires_st_mask. The official path must "
                "stay mask-mandatory (audit E1 / PR-F) with st_mask_mode="
                "'off_experiment' as the ONLY sanctioned, config-stamped "
                "exception — restore the wiring or update this pin WITH the "
                "governance story."
            ),
        )
        self.assertNotIn(
            "require_st_mask=True", source,
            msg="engine.py reintroduced a hardcoded require_st_mask=True "
                "alongside the mode-derived wiring — dead or conflicting path.",
        )


if __name__ == "__main__":
    unittest.main()
