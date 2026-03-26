import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class BoundarySkeletonTests(unittest.TestCase):
    def test_research_factor_lab_readme_marks_non_production(self):
        text = (PROJECT_ROOT / "research" / "factor_lab" / "README.md").read_text(encoding="utf-8")
        self.assertIn("non-production", text.lower())
        self.assertIn("non-canonical", text.lower())


if __name__ == "__main__":
    unittest.main()
