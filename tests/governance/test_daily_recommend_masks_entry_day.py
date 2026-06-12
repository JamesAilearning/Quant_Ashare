"""Governance: ``daily_recommend.recommend`` MUST key the tradability mask
on the ENTRY day, not the as-of day.

Codex P1 round 4 on PR #241 / audit A1 family.

Why this guard exists
---------------------
The live flow resolves ``(as_of_date=T, entry_date=T+1)`` and
``resolve_dates`` requires the entry session to exist in the bundle
calendar (the default as-of is the second-to-last day), so the entry day's
bars are on disk at decision time. The canonical backtest (PR-C) drops a
T-stamped signal whose EXECUTION day is suspended / one-price-locked; the
live list must apply the same execution-day semantics or live and backtest
diverge on unfillable next-day names — a name tradable on T but suspended
on T+1 would be recommended live yet never filled (and never held in the
backtest). A refactor that "simplifies" the call back to
``(as_of_date, as_of_date)`` would silently restore that divergence while
every existing unit test (which exercises the pure helpers with
pre-computed sets) kept passing.

Detection strategy
------------------
AST-walk ``recommend`` in ``src/inference/daily_recommend.py``, find the
``compute_unavailable_mask`` call, and assert its start/end date arguments
are the ``entry_date`` name. Mirrors
``test_backtest_runner_applies_microstructure_mask.py``.
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


class DailyRecommendMaskEntryDayTests(unittest.TestCase):
    def test_tradability_mask_keyed_on_entry_date(self) -> None:
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
                arg.id, "entry_date",  # type: ignore[union-attr]
                f"the tradability mask's {pos} date must be entry_date "
                "(the day the recommendation fills — execution-day "
                f"semantics, codex P1 round 4 on PR #241); got {arg.id!r}. "
                "Keying it back to as_of_date silently diverges live "
                "recommendations from the lag=1 backtest on names that are "
                "tradable on T but suspended/locked on T+1.",
            )


if __name__ == "__main__":
    unittest.main()
