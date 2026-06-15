"""Governance: config.yaml MUST set a non-empty ``namechange_path``.

Why this guard exists (audit E1 / PR-F)
---------------------------------------
The SINGLE-FOLD canonical backtest (``main.py config.yaml`` →
``PipelineConfig`` → ``BacktestRunner.run``) historically ran ST-UNMASKED
because ``config.yaml`` set no ``namechange_path``, while the walk-forward
(``config_walk.yaml``) and live recommend paths exclude ST. That made the
single-fold metrics non-comparable and the universe inconsistent.

PR-F enables the single-fold ST mask via one line — ``namechange_path`` in
``config.yaml`` — and the pipeline passes ``require_st_mask=True`` so a
missing value fails loud at run time. This test pins the YAML line so config
drift (deleting/blanking it) fails at PR-review time rather than silently
reverting the single-fold to an includes-ST universe (the RUN_E2E baseline
drift test is invisible to CI). Mirrors
``test_config_walk_st_mask_enabled.py``.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.core._yaml_loader import load_yaml_with_inheritance  # noqa: E402


class ConfigStMaskEnabledTests(unittest.TestCase):
    def test_config_yaml_sets_non_empty_namechange_path(self) -> None:
        cfg = load_yaml_with_inheritance(_PROJECT_ROOT / "config.yaml")
        self.assertIn(
            "namechange_path", cfg,
            msg=(
                "config.yaml no longer sets namechange_path. The single-fold "
                "ST/*ST exclusion (audit E1 / PR-F) depends on this line; "
                "without it the single-fold backtest runs ST-UNMASKED, "
                "inconsistent with walk-forward + live. Restore it or justify."
            ),
        )
        value = cfg.get("namechange_path")
        self.assertTrue(
            isinstance(value, str) and value.strip(),
            msg=(
                f"config.yaml namechange_path must be a non-empty path; got "
                f"{value!r}. The pipeline passes require_st_mask=True, so a "
                "blank value fails loud — but pin the YAML so drift is caught "
                "at review, not at run time."
            ),
        )


if __name__ == "__main__":
    unittest.main()
