"""Source-level regression guards for Run History null timestamp rendering."""

from __future__ import annotations

import unittest
from pathlib import Path


class RunHistorySourceTests(unittest.TestCase):
    def test_nullable_timestamps_are_stringified_before_slicing(self) -> None:
        source = Path("web/operator_ui/pages/run_history.py").read_text(encoding="utf-8")

        self.assertIn('str(j.get("started_at") or "")[:19]', source)
        self.assertIn('str(e.get("completed_at") or "")[:19]', source)


if __name__ == "__main__":
    unittest.main()
