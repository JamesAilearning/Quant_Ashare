"""Governance: ``BacktestRunner.run`` MUST apply the
microstructure mask before constructing qlib's strategy.

Audit P0-3 / openspec/changes/add-microstructure-mask.

Why this guard exists
---------------------
A future refactor that "simplifies" ``BacktestRunner.run`` by
removing the ``compute_unavailable_mask(...)`` call would silently
restore the phantom-fill regime the mask exists to prevent: qlib's
``TopkDropoutStrategy`` picking suspended (volume<=0) or
one-price-locked (high==low) candidates by score, and the executor
reporting fills at the carried-forward / locked price.

The mask integration lives inside a long method whose tests
exercise only end-to-end behaviour against mocked qlib — a
regression that drops the mask call could pass the existing
mocked-qlib tests because the mocks never actually flag any day
as suspended. This governance test catches the regression at PR
review time by AST-grepping the function source for the helper
call.

Detection strategy
------------------
1. Find the ``BacktestRunner`` class node in
   ``src/core/backtest_runner.py``.
2. Inside it, find the ``run`` method.
3. Walk the method body's AST and assert at least one ``Call``
   node whose function name is ``compute_unavailable_mask``.

The test fails with a message naming the file + method when the
call is missing.
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _find_method(tree: ast.AST, class_name: str, method_name: str) -> ast.FunctionDef | None:
    """Return the ``FunctionDef`` node for ``class_name.method_name``
    or ``None`` if missing."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for sub in node.body:
                if (
                    isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and sub.name == method_name
                ):
                    return sub  # type: ignore[return-value]
    return None


def _has_call_to(node: ast.AST, func_name: str) -> bool:
    """True iff ``node``'s subtree contains a ``Call`` whose direct
    function-name (last attribute or bare Name) equals ``func_name``.

    Catches:

    * ``compute_unavailable_mask(...)`` (Name).
    * ``module.compute_unavailable_mask(...)`` (Attribute).
    * ``cls.compute_unavailable_mask(...)`` (Attribute).

    We deliberately do NOT bind to a specific import path so a
    future refactor that moves the helper into a different module
    (or aliases it) still satisfies the guard as long as the
    bare name matches.
    """
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            f = sub.func
            if isinstance(f, ast.Name) and f.id == func_name:
                return True
            if isinstance(f, ast.Attribute) and f.attr == func_name:
                return True
    return False


class BacktestRunnerAppliesMicrostructureMaskTests(unittest.TestCase):
    _SOURCE_PATH = _PROJECT_ROOT / "src" / "core" / "backtest_runner.py"

    def test_run_calls_compute_unavailable_mask(self) -> None:
        text = self._SOURCE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(text)

        run_method = _find_method(tree, "BacktestRunner", "run")
        self.assertIsNotNone(
            run_method,
            "Could not locate ``BacktestRunner.run`` in "
            f"{self._SOURCE_PATH.relative_to(_PROJECT_ROOT)}. The "
            "governance check expects a class ``BacktestRunner`` "
            "with a method ``run`` — if the layout changed, update "
            "this test too.",
        )

        self.assertTrue(
            _has_call_to(run_method, "compute_unavailable_mask"),
            "BacktestRunner.run no longer calls "
            "``compute_unavailable_mask(...)``. The microstructure "
            "mask (audit P0-3) is REQUIRED before constructing "
            "qlib's strategy — otherwise suspended / one-price-"
            "locked candidates land in the strategy by score and "
            "the executor reports phantom fills. Restore the call "
            "OR (if intentionally removing it) update this "
            "governance test in the same PR and reference the "
            "OpenSpec change that retires the requirement.",
        )

    def test_run_calls_apply_mask_to_predictions(self) -> None:
        """The companion check: computing the mask is useless
        without applying it. Both calls must live in ``run``."""
        text = self._SOURCE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(text)
        run_method = _find_method(tree, "BacktestRunner", "run")
        self.assertIsNotNone(run_method)
        self.assertTrue(
            _has_call_to(run_method, "apply_mask_to_predictions"),
            "BacktestRunner.run calls compute_unavailable_mask but "
            "no longer calls ``apply_mask_to_predictions(...)`` — "
            "the mask is computed but never applied to the "
            "predictions Series. Audit P0-3.",
        )

    def test_microstructure_mask_module_exists(self) -> None:
        """Trivial existence guard — protects against a future
        rename that accidentally orphans the module."""
        from src.core import microstructure_mask  # noqa: F401


if __name__ == "__main__":
    unittest.main()
