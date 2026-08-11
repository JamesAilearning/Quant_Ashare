"""Governance for the factor-mining config templates (external finding #1).

The documented real-PIT flow is "fill the two paths in default.yaml and
run it". The code default for ``data.mode`` is ``"synthetic"``, so before
default.yaml declared ``mode: pit`` explicitly, that flow silently mined
on a random synthetic panel — and the empty-path fail-closed check never
ran, because it lives on the PIT branch. These tests pin the contract so
the template cannot regress:

* default.yaml IS the real-PIT template: it parses to ``mode: pit``;
* running it UNFILLED fails loud (the documented empty-path refusal
  actually triggers, instead of being bypassed by synthetic mode);
* smoke.yaml stays the explicit synthetic example;
* every tracked factor-mining config declares ``mode`` explicitly — the
  dataclass default never decides what a TEMPLATE mines on.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.factor_mining.miner import build_panel, load_config  # noqa: E402

_CONFIG_DIR = PROJECT_ROOT / "config" / "factor_mining"


class DefaultConfigModeTests(unittest.TestCase):
    def test_default_yaml_parses_to_pit_mode(self) -> None:
        config = load_config(_CONFIG_DIR / "default.yaml")
        self.assertEqual(
            config.data.mode, "pit",
            "default.yaml is the documented real-PIT template; if it does "
            "not declare mode: pit, the code default (synthetic) silently "
            "mines on a random panel after the operator fills in the paths.",
        )

    def test_default_yaml_unfilled_fails_loud(self) -> None:
        # The OPERATOR-FILL paths ship empty; running the template as-is
        # must hit the documented empty-path refusal, not synthetic data.
        config = load_config(_CONFIG_DIR / "default.yaml")
        self.assertEqual(config.data.pit_provider_uri, "")
        with self.assertRaisesRegex(ValueError, "pit_provider_uri"):
            build_panel(config)

    def test_smoke_yaml_stays_synthetic(self) -> None:
        config = load_config(_CONFIG_DIR / "smoke.yaml")
        self.assertEqual(config.data.mode, "synthetic")

    def test_every_tracked_config_declares_mode_explicitly(self) -> None:
        # A template whose mode comes from the dataclass default is exactly
        # the trap this finding closed — require the declaration.
        missing = []
        for path in sorted(_CONFIG_DIR.glob("*.yaml")):
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            data = raw.get("data") or {}
            if "mode" not in data:
                missing.append(path.name)
        self.assertEqual(
            missing, [],
            msg=(
                "factor-mining config(s) without an explicit data.mode: "
                f"{missing} — the code default (synthetic) must never decide "
                "what a tracked template mines on."
            ),
        )


if __name__ == "__main__":
    unittest.main()
