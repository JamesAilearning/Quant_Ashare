"""Governance tests for risk-constraint canonical boundary.

The legacy ``RiskConstraintEngine`` stub MUST still fail closed on
any call — this is the pre-P0-1 governance contract preserved
unchanged when ``MinimalRiskConstraints`` was introduced. Any code
that ever reaches the stub today gets the documented "fails closed"
behaviour and is guided toward the new engine.

Audit P0-1 / openspec/changes/add-minimal-risk-constraints kept
the stub deliberately rather than deleting it; the test was
updated to match the new pointer message (which now references
``MinimalRiskConstraints`` instead of the old
"not currently implemented" wording).
"""

from __future__ import annotations

import unittest

from src.core.risk_constraints import RiskConstraintEngine, RiskConstraintError


class CoreRiskConstraintBoundaryTests(unittest.TestCase):
    def test_core_risk_constraints_fail_closed(self) -> None:
        """``RiskConstraintEngine.apply(...)`` raises on any input —
        the pre-P0-1 fail-closed surface is preserved.

        After audit P0-1, the error message points operators toward
        the new ``MinimalRiskConstraints`` engine instead of the old
        "not currently implemented" wording. Both indicate the same
        fact (this surface does nothing useful itself) but the new
        message gives the caller a constructive next step.
        """
        with self.assertRaisesRegex(
            RiskConstraintError,
            "MinimalRiskConstraints",
        ):
            RiskConstraintEngine.apply(object())


if __name__ == "__main__":
    unittest.main()
