"""Governance regression: no publisher under src/data/ calls qlib.init directly.

V1 lesson: "hidden coupling in app runtime initialization" meant qlib
was initialized from multiple places with inconsistent configs. V2
enforces a single canonical initialization entry point at
``src.core.qlib_runtime.init_qlib_canonical``.

This test statically scans ``src/data/`` and asserts:

1. No source file under ``src/data/`` references ``qlib.init(`` or
   ``from qlib import init``.
2. The benchmark artifact publisher imports
   ``is_canonical_qlib_initialized`` from the canonical runtime module.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


FORBIDDEN_INIT_PATTERNS = (
    "qlib.init(",
    "from qlib import init",
)


class NoDirectQlibInitUnderSrcDataTests(unittest.TestCase):
    def test_src_data_has_no_direct_qlib_init(self) -> None:
        data_root = PROJECT_ROOT / "src" / "data"
        offenders: list[str] = []
        for py_file in data_root.rglob("*.py"):
            text = py_file.read_text(encoding="utf-8")
            for forbidden in FORBIDDEN_INIT_PATTERNS:
                if forbidden in text:
                    offenders.append(f"{py_file.relative_to(PROJECT_ROOT)}: {forbidden}")
        self.assertEqual(
            offenders,
            [],
            msg=(
                "Direct qlib.init call leaked into src/data/. "
                "Canonical init must go through src.core.qlib_runtime.init_qlib_canonical. "
                f"Offenders: {offenders}"
            ),
        )

    def test_publisher_uses_canonical_init_guard(self) -> None:
        publisher_file = PROJECT_ROOT / "src" / "data" / "benchmark_artifact_publisher.py"
        self.assertTrue(publisher_file.is_file(), "benchmark_artifact_publisher.py missing")
        text = publisher_file.read_text(encoding="utf-8")
        self.assertIn(
            "from src.core.qlib_runtime import is_canonical_qlib_initialized",
            text,
            msg="publisher must import canonical init guard from src.core.qlib_runtime",
        )


if __name__ == "__main__":
    unittest.main()
