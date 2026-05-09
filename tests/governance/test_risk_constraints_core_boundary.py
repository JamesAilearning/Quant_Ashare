"""Governance tests for risk-constraint canonical boundary."""

from __future__ import annotations

import unittest

from src.core.risk_constraints import RiskConstraintEngine, RiskConstraintError


class CoreRiskConstraintBoundaryTests(unittest.TestCase):
    def test_core_risk_constraints_fail_closed(self) -> None:
        with self.assertRaisesRegex(
            RiskConstraintError,
            "not currently implemented",
        ):
            RiskConstraintEngine.apply(object())


if __name__ == "__main__":
    unittest.main()
