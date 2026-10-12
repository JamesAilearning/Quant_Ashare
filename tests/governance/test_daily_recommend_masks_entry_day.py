"""Governance: candidates may inspect known T bars, never future entry bars.

The approved restore-next-session-candidates contract replaces retrospective
execution confirmation with one unconfirmed candidate path. The backtest's
execution-day mask is unchanged; recommendation must not claim equivalence
to a future fill. Pin both the authoritative mask and the audit-reason query
to the decision date so even already-recorded later bars cannot leak in.
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_TARGET = _PROJECT_ROOT / "src" / "inference" / "daily_recommend.py"


def _find_function(tree: ast.AST, name: str) -> ast.FunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node  # type: ignore[return-value]
    return None


class DailyRecommendMaskAsOfDayTests(unittest.TestCase):
    def test_known_eligibility_mask_keyed_on_as_of_date(self) -> None:
        tree = ast.parse(_TARGET.read_text(encoding="utf-8"))
        fn = _find_function(tree, "recommend")
        self.assertIsNotNone(fn, f"recommend() not found in {_TARGET}")

        calls = [
            node for node in ast.walk(fn)
            if isinstance(node, ast.Call)
            and (
                (isinstance(node.func, ast.Name)
                 and node.func.id == "compute_unavailable_mask")
                or (isinstance(node.func, ast.Attribute)
                    and node.func.attr == "compute_unavailable_mask")
            )
        ]
        self.assertEqual(
            len(calls), 1,
            f"expected exactly one compute_unavailable_mask call inside "
            f"recommend(); found {len(calls)} in {_TARGET}",
        )
        call = calls[0]
        # Signature: compute_unavailable_mask(instruments, start, end, ...).
        date_args = call.args[1:3]
        self.assertEqual(
            len(date_args), 2,
            "compute_unavailable_mask call no longer passes positional "
            "start/end dates; update this governance pin alongside the call.",
        )
        for pos, arg in zip(("start", "end"), date_args, strict=True):
            self.assertIsInstance(
                arg, ast.Name,
                f"{pos} date arg is not a bare name; update this pin "
                "alongside the call.",
            )
            self.assertEqual(
                arg.id, "as_of_date",  # type: ignore[union-attr]
                f"known eligibility {pos} must use as_of_date, not a "
                f"future execution session; got {arg.id!r}",
            )

        reasons = [node for node in ast.walk(fn) if isinstance(node, ast.Call)
                   and isinstance(node.func, ast.Name)
                   and node.func.id == "_per_regime_sets"]
        self.assertEqual(len(reasons), 1)
        self.assertIsInstance(reasons[0].args[2], ast.Name)
        self.assertEqual(reasons[0].args[2].id, "as_of_date")


if __name__ == "__main__":
    unittest.main()
