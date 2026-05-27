"""Governance: the legacy scalar ``stamp_tax_bps`` field MUST NOT
re-appear on any of the public cost-model surfaces or in any shipped
YAML config.

Why this guard exists
---------------------
``stamp_tax_bps`` was replaced by ``stamp_tax_schedule`` (a time-
ordered list) so CN A-share backtests that span the 2023-08-28
reform (0.1% → 0.05% sell-side only) produce the right per-segment
cost rather than a single biased scalar. A future refactor that
re-introduces the scalar would silently revert the fix — the
contract layer still accepts most "looks like a cost" shapes, so
the regression wouldn't fail other tests.

This guard catches the regression at PR review time on three
surfaces:

* ``CanonicalExchangeCostModel`` (the canonical contract)
* ``PipelineConfig`` (the single-fold operator API)
* ``WalkForwardConfig`` (the rolling operator API)
* All ``config*.yaml`` files shipped under the repo root.

Audit P0-4 / openspec/changes/add-stamp-tax-schedule.
"""

from __future__ import annotations

import re
import sys
import unittest
from dataclasses import fields
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.core.canonical_backtest_contract import (  # noqa: E402
    CanonicalExchangeCostModel,
)
from src.core.pipeline import PipelineConfig  # noqa: E402
from src.core.walk_forward.config import WalkForwardConfig  # noqa: E402

# Legacy field / key name. Any re-introduction on the listed
# surfaces fails the corresponding test.
_LEGACY_FIELD = "stamp_tax_bps"

# Shipped YAML configs to sweep. We deliberately do NOT scan
# ``config/presets/`` or any operator-local overrides — those are
# not part of the canonical shipped surface; the governance test
# checks the surfaces an operator points at by default.
_SHIPPED_CONFIG_YAMLS: tuple[str, ...] = (
    "config.yaml",
    "config_walk.yaml",
    "config_smoke.yaml",
    "config_tushare.yaml",
    "config_tushare_qlib_provider.yaml",
    "config_walk_mined.yaml",
    "config_walk_n1.yaml",
    "config_walk_n2.yaml",
    "config_walk_n3.yaml",
    "config_walk_n5.yaml",
)


class CanonicalExchangeCostModelNoLegacyFieldTests(unittest.TestCase):
    def test_no_stamp_tax_bps_field(self) -> None:
        names = {f.name for f in fields(CanonicalExchangeCostModel)}
        self.assertNotIn(
            _LEGACY_FIELD, names,
            msg=(
                f"CanonicalExchangeCostModel.{_LEGACY_FIELD} field is "
                "back — this is the regression guard for audit P0-4. "
                "Stamp tax MUST be carried by ``stamp_tax_schedule`` "
                "so backtests that span 2023-08-28 use the right "
                "per-segment rate. See openspec/changes/"
                "add-stamp-tax-schedule for the design."
            ),
        )

    def test_stamp_tax_schedule_field_is_present(self) -> None:
        names = {f.name for f in fields(CanonicalExchangeCostModel)}
        self.assertIn("stamp_tax_schedule", names)


class PipelineConfigNoLegacyFieldTests(unittest.TestCase):
    def test_no_stamp_tax_bps_field(self) -> None:
        names = {f.name for f in fields(PipelineConfig)}
        self.assertNotIn(
            _LEGACY_FIELD, names,
            msg=(
                f"PipelineConfig.{_LEGACY_FIELD} is back. Replace "
                "with ``stamp_tax_schedule`` per audit P0-4."
            ),
        )

    def test_stamp_tax_schedule_field_is_present(self) -> None:
        names = {f.name for f in fields(PipelineConfig)}
        self.assertIn("stamp_tax_schedule", names)


class WalkForwardConfigNoLegacyFieldTests(unittest.TestCase):
    def test_no_stamp_tax_bps_field(self) -> None:
        names = {f.name for f in fields(WalkForwardConfig)}
        self.assertNotIn(
            _LEGACY_FIELD, names,
            msg=(
                f"WalkForwardConfig.{_LEGACY_FIELD} is back. Replace "
                "with ``stamp_tax_schedule`` per audit P0-4."
            ),
        )

    def test_stamp_tax_schedule_field_is_present(self) -> None:
        names = {f.name for f in fields(WalkForwardConfig)}
        self.assertIn("stamp_tax_schedule", names)


class ShippedYamlsNoLegacyKeyTests(unittest.TestCase):
    """Sweep every YAML file shipped at the repo root for the
    literal top-level key ``stamp_tax_bps``. We match a *top-level*
    occurrence only (regex ``^stamp_tax_bps:``) so a comment inside
    a multi-line string mentioning the key does not trip the test.

    Files listed in ``_SHIPPED_CONFIG_YAMLS`` are inspected if they
    exist; absent files are silently skipped (some configs are
    optional / opt-in).
    """

    def test_no_shipped_yaml_carries_legacy_key(self) -> None:
        offenders: list[str] = []
        pattern = re.compile(rf"^{_LEGACY_FIELD}\s*:", re.MULTILINE)
        for rel in _SHIPPED_CONFIG_YAMLS:
            path = _PROJECT_ROOT / rel
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            if pattern.search(text):
                offenders.append(rel)
        self.assertEqual(
            offenders, [],
            msg=(
                f"Shipped YAML config(s) still carry the legacy "
                f"top-level ``{_LEGACY_FIELD}:`` key:\n  "
                + "\n  ".join(offenders)
                + "\n\nMigrate to ``stamp_tax_schedule`` or omit the "
                "key entirely (the default canonical CN schedule "
                "applies). See openspec/changes/add-stamp-tax-schedule."
            ),
        )


if __name__ == "__main__":
    unittest.main()
