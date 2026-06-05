"""Governance: config_walk.yaml MUST set a non-empty ``namechange_path``.

Why this guard exists
---------------------
The canonical walk-forward backtest's PIT historical-ST exclusion (C2-d PR2) is
enabled by exactly ONE line — ``namechange_path`` in ``config_walk.yaml`` (which
the C1 baseline config extends). ``BacktestRunner.run`` treats a missing
``namechange_path`` as "ST mask disabled" (a backward-compatible WARN, so unit
callers don't break). That convenience is also a footgun: if this line is ever
deleted or blanked, the canonical WF would silently run **ST-unmasked** with
only a WARN, and the next baseline regeneration would quietly drift back to an
includes-ST universe. The drift test is RUN_E2E-gated, so CI would NOT catch it.

A load-bearing guarantee that CI can't see needs a test. This pins it so config
drift fails at PR-review time instead.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.core._yaml_loader import load_yaml_with_inheritance  # noqa: E402


class ConfigWalkStMaskEnabledTests(unittest.TestCase):
    def test_config_walk_sets_non_empty_namechange_path(self) -> None:
        cfg = load_yaml_with_inheritance(_PROJECT_ROOT / "config_walk.yaml")
        self.assertIn(
            "namechange_path", cfg,
            msg=(
                "config_walk.yaml no longer sets namechange_path. The canonical "
                "walk-forward ST/*ST exclusion (C2-d PR2) depends on this line; "
                "without it the WF runs ST-UNMASKED (only a WARN) and the next "
                "RUN_E2E baseline regen drifts back to an includes-ST universe. "
                "Restore namechange_path or justify the removal."
            ),
        )
        value = cfg.get("namechange_path")
        self.assertTrue(
            isinstance(value, str) and value.strip(),
            msg=(
                f"config_walk.yaml namechange_path must be a non-empty path; "
                f"got {value!r}. A None/blank value disables the canonical WF ST "
                "mask silently (CI is blind to it — the drift test is "
                "RUN_E2E-gated)."
            ),
        )


if __name__ == "__main__":
    unittest.main()
