"""Governance: pin the public surface of the minimal risk-constraints
module so a future refactor cannot silently remove ``MinimalRiskConstraints``,
mis-default a field, or break the legacy ``RiskConstraintEngine``
fail-closed contract.

Audit P0-1 / openspec/changes/add-minimal-risk-constraints.
"""

from __future__ import annotations

import sys
import unittest
from dataclasses import fields
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


class MinimalRiskConstraintsSurfaceTests(unittest.TestCase):
    def test_minimal_risk_constraints_is_importable(self) -> None:
        from src.core.risk_constraints import MinimalRiskConstraints  # noqa: F401

    def test_risk_constraint_mode_is_importable(self) -> None:
        from src.core.risk_constraints import RiskConstraintMode
        # The two documented modes must exist with the documented
        # values (string values are part of the OpenSpec contract).
        self.assertEqual(RiskConstraintMode.RAISE.value, "raise")
        self.assertEqual(RiskConstraintMode.WARN_AND_CLIP.value, "warn_and_clip")

    def test_violation_and_result_types_are_importable(self) -> None:
        from src.core.risk_constraints import (
            RiskConstraintsApplyResult,
            RiskConstraintViolation,
        )
        # Both must be frozen dataclasses (the OpenSpec design pins
        # immutability so downstream tools can hash / cache them).
        for cls in (RiskConstraintViolation, RiskConstraintsApplyResult):
            self.assertTrue(
                cls.__dataclass_params__.frozen,
                f"{cls.__name__} must be frozen=True",
            )

    def test_four_constraint_fields_with_documented_defaults(self) -> None:
        """The four constraints + their documented defaults are part
        of the public contract. A future change moving any of them
        SHOULD fail this test."""
        from src.core.risk_constraints import (
            MinimalRiskConstraints,
            RiskConstraintMode,
        )
        cfg = MinimalRiskConstraints()
        self.assertEqual(cfg.max_per_name, 0.05)
        self.assertEqual(cfg.max_per_board, 0.40)
        self.assertEqual(cfg.cash_buffer_min, 0.01)
        self.assertEqual(cfg.max_leverage, 1.00)
        self.assertEqual(cfg.mode, RiskConstraintMode.RAISE)
        # And the field names themselves (no rename surprises).
        names = {f.name for f in fields(MinimalRiskConstraints)}
        self.assertIn("max_per_name", names)
        self.assertIn("max_per_board", names)
        self.assertIn("cash_buffer_min", names)
        self.assertIn("max_leverage", names)
        self.assertIn("mode", names)

    def test_minimal_risk_constraints_is_frozen(self) -> None:
        from src.core.risk_constraints import MinimalRiskConstraints
        cfg = MinimalRiskConstraints()
        with self.assertRaises(AttributeError):  # frozen dataclass
            cfg.max_per_name = 0.10  # type: ignore[misc]

    def test_canonical_backtest_output_has_positions_pre_clip(self) -> None:
        """The output dataclass MUST carry the ``positions_pre_clip``
        field so WARN_AND_CLIP callers can compare clipped vs
        original. Audit P0-1."""
        from src.core.canonical_backtest_contract import CanonicalBacktestOutput
        names = {f.name for f in fields(CanonicalBacktestOutput)}
        self.assertIn("positions_pre_clip", names)

    def test_positions_pre_clip_is_in_canonical_output_schema(self) -> None:
        """Codex P2 follow-up on PR #179.

        The schema-level constant ``CANONICAL_OUTPUT_FIELDS`` and
        the contract's ``output_schema()`` accessor MUST list
        ``positions_pre_clip``. Without this, schema-driven
        consumers (UI, JSON validators) can't discover the new
        WARN_AND_CLIP sibling field as part of the official output
        contract — the dataclass and the schema would disagree.
        """
        from src.core.canonical_backtest_contract import (
            CANONICAL_OUTPUT_FIELDS,
            CanonicalBacktestContract,
        )
        self.assertIn("positions_pre_clip", CANONICAL_OUTPUT_FIELDS)
        self.assertIn(
            "positions_pre_clip",
            CanonicalBacktestContract.output_schema(),
        )


class LegacyFailClosedStubGuardTests(unittest.TestCase):
    """The pre-P0-1 ``RiskConstraintEngine`` stub remains in place
    and still fails closed on any call. This is part of the contract
    layer's backwards-compat surface — any code that ever reaches
    the stub today still gets the documented "fails closed" behaviour.
    """

    def test_stub_is_still_importable(self) -> None:
        from src.core.risk_constraints import RiskConstraintEngine  # noqa: F401

    def test_stub_apply_still_raises(self) -> None:
        from src.core.risk_constraints import (
            RiskConstraintEngine,
            RiskConstraintError,
        )
        with self.assertRaises(RiskConstraintError):
            RiskConstraintEngine.apply()

    def test_stub_apply_raises_with_arguments_too(self) -> None:
        """The stub's signature accepts arbitrary args/kwargs; every
        shape must still raise (defensive: no fall-through if a
        future refactor adds branching)."""
        from src.core.risk_constraints import (
            RiskConstraintEngine,
            RiskConstraintError,
        )
        with self.assertRaises(RiskConstraintError):
            RiskConstraintEngine.apply({"date": {"SH600000": 0.5}})
        with self.assertRaises(RiskConstraintError):
            RiskConstraintEngine.apply(None, foo="bar")


if __name__ == "__main__":
    unittest.main()
