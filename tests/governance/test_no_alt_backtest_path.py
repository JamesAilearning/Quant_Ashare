"""Governance regression: canonical backtest path is singular and anchored.

This test protects the V1 lesson: "avoid competing official paths". It
enforces two invariants:

1. ``CANONICAL_OFFICIAL_BACKTEST_PATH`` equals the expected anchor
   ``"qlib.backtest.backtest"``, regardless of whether qlib itself is
   installed in the current environment.
2. No source file under ``src/core/`` references any alternative qlib
   backtest entry point (for example ``qlib.contrib.evaluate.backtest_daily``).

Additionally, when qlib IS importable, the test verifies that the
live ``CANONICAL_OFFICIAL_BACKTEST_CALLABLE`` is the real function.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core import canonical_backtest_contract  # noqa: E402
from src.core.canonical_backtest_contract import (  # noqa: E402
    CANONICAL_OFFICIAL_BACKTEST_CALLABLE,
    CANONICAL_OFFICIAL_BACKTEST_PATH,
    CanonicalBacktestContract,
)


FORBIDDEN_ALT_BACKTEST_REFS = (
    "qlib.contrib.evaluate.backtest_daily",
    "from qlib.contrib.evaluate import backtest_daily",
)


class CanonicalBacktestAnchorTests(unittest.TestCase):
    def test_canonical_path_constant_is_expected_anchor(self) -> None:
        self.assertEqual(CANONICAL_OFFICIAL_BACKTEST_PATH, "qlib.backtest.backtest")

    def test_canonical_path_is_singular(self) -> None:
        self.assertEqual(
            CanonicalBacktestContract.list_official_paths(),
            (CANONICAL_OFFICIAL_BACKTEST_PATH,),
        )

    def test_live_callable_anchor_when_qlib_available(self) -> None:
        if not canonical_backtest_contract._QLIB_BACKTEST_ANCHOR_AVAILABLE:
            self.skipTest("qlib not importable in this environment")
        self.assertIsNotNone(CANONICAL_OFFICIAL_BACKTEST_CALLABLE)
        assert CANONICAL_OFFICIAL_BACKTEST_CALLABLE is not None  # for type-checkers
        self.assertTrue(callable(CANONICAL_OFFICIAL_BACKTEST_CALLABLE))
        self.assertEqual(
            f"{CANONICAL_OFFICIAL_BACKTEST_CALLABLE.__module__}.{CANONICAL_OFFICIAL_BACKTEST_CALLABLE.__name__}",
            "qlib.backtest.backtest",
        )


class NoCompetingBacktestPathTests(unittest.TestCase):
    def test_src_core_has_no_alt_backtest_references(self) -> None:
        core_root = PROJECT_ROOT / "src" / "core"
        offenders: list[str] = []
        for py_file in core_root.rglob("*.py"):
            text = py_file.read_text(encoding="utf-8")
            for forbidden in FORBIDDEN_ALT_BACKTEST_REFS:
                if forbidden in text:
                    offenders.append(f"{py_file.relative_to(PROJECT_ROOT)}: {forbidden}")
        self.assertEqual(
            offenders,
            [],
            msg=(
                "Alternative qlib backtest path leaked into canonical runtime layer. "
                f"Offenders: {offenders}"
            ),
        )


if __name__ == "__main__":
    unittest.main()
