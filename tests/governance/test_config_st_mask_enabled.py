"""Governance: every shipped backtest config MUST resolve a non-empty
``namechange_path``.

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

    def test_every_shipped_backtest_config_resolves_a_namechange_path(self) -> None:
        """Sweep EVERY shipped root ``config*.yaml`` that is a backtest config
        (single-fold or walk-forward) and assert it resolves — after
        ``extends`` inheritance + ``${VAR:-default}`` expansion — to a
        non-empty ``namechange_path``.

        Both official paths now pass ``require_st_mask=True``, so ANY shipped
        config fed to ``main.py`` / ``run_walk_forward`` without a usable
        ``namechange_path`` would train fully and then RAISE in
        ``BacktestRunner.run``. Codex caught ``config_smoke.yaml`` (standalone,
        no ``extends``) slipping through the single config.yaml pin; this sweep
        closes the class and auto-covers future ``config_walk_n*`` variants.

        Ingest/non-backtest configs (e.g. ``config_tushare.yaml``) carry no
        ``instruments``/``model_type`` and are correctly skipped — they never
        reach ``BacktestRunner.run``.
        """
        offenders: list[str] = []
        checked: list[str] = []
        for path in sorted(_PROJECT_ROOT.glob("config*.yaml")):
            cfg = load_yaml_with_inheritance(path)
            # A backtest config builds PipelineConfig/WalkForwardConfig; both
            # require a universe + a model. Ingest configs have neither.
            is_backtest = "instruments" in cfg and (
                "model_type" in cfg or "feature_handler" in cfg
            )
            if not is_backtest:
                continue
            checked.append(path.name)
            value = cfg.get("namechange_path")
            if not (isinstance(value, str) and value.strip()):
                offenders.append(f"{path.name} -> {value!r}")

        self.assertTrue(
            checked,
            msg="sweep matched no backtest configs — heuristic likely broke.",
        )
        self.assertEqual(
            offenders, [],
            msg=(
                "These shipped backtest configs resolve no usable "
                "namechange_path, so main.py / run_walk_forward would RAISE "
                f"under require_st_mask=True after a full train: {offenders}. "
                "Add an env-defaulted namechange_path (parity with "
                "config.yaml), or extend a config that sets it."
            ),
        )


if __name__ == "__main__":
    unittest.main()
