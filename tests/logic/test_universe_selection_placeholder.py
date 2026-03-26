import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.universe_selection_placeholder import RuntimeUniverseSelectionPlaceholder


class UniverseSelectionPlaceholderTests(unittest.TestCase):
    def test_universe_selection_is_intentionally_unimplemented(self):
        with self.assertRaisesRegex(NotImplementedError, "intentionally unimplemented"):
            RuntimeUniverseSelectionPlaceholder.select()


if __name__ == "__main__":
    unittest.main()
