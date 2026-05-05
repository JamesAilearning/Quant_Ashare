"""Governance tests for runtime-adjacent dependency metadata."""

from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class RuntimeDependencyMetadataTests(unittest.TestCase):
    def test_tushare_extra_is_declared_for_shipped_integration(self) -> None:
        text = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("tushare = [", text)
        self.assertIn('"tushare>=', text)


if __name__ == "__main__":
    unittest.main()
