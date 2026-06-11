"""Governance: the 数据检视 page SHALL be read-only over the production bundle.

P3-6b / U3: the UI's ingest path was retired; the inspector page replaces it
and must NEVER grow a write path or a bundle-building path. Source-level scan
(same style as test_no_train_on_ui_inspection_bundle): any write-side
filesystem API or builder/fetcher import appearing in the page source is a
contract violation, regardless of whether it is currently reachable.
"""

import re
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PAGE = PROJECT_ROOT / "web" / "operator_ui" / "pages" / "data_inspect.py"

# Write-side APIs that must not appear in a read-only page. Word-boundary
# regexes so e.g. "unlink" in a comment about the contract still trips —
# the page shouldn't even talk its way around the contract.
_WRITE_APIS = (
    r"\.to_parquet\(", r"\.to_csv\(", r"\.write_text\(", r"\.write_bytes\(",
    r"open\([^)]*['\"][wax]", r"\.mkdir\(", r"\.rename\(", r"\.replace\(",
    r"rmtree\(", r"\.unlink\(", r"shutil\.", r"os\.remove", r"os\.makedirs",
)

# Build/ingest machinery the THIN inspector must not reach for.
_FORBIDDEN_IMPORTS = (
    "qlib_bin_builder", "QlibBinBuilder",
    "TushareFetcher", "src.data.tushare.fetcher",
    "daily_update", "bundle_swap",
)


class DataInspectReadOnlyTests(unittest.TestCase):

    def setUp(self) -> None:
        self.source = PAGE.read_text(encoding="utf-8")

    def test_page_exists_and_declares_inspection_copy(self) -> None:
        # The operator-facing copy must say it INSPECTS production data —
        # the U3 promise that the UI no longer makes bundles.
        self.assertIn("检视生产", self.source)
        self.assertIn("只读", self.source)

    def test_no_write_side_filesystem_api(self) -> None:
        for pattern in _WRITE_APIS:
            self.assertIsNone(
                re.search(pattern, self.source),
                f"read-only inspector page contains write API {pattern!r}",
            )

    def test_no_builder_or_fetcher_import(self) -> None:
        # Scan IMPORT lines only: prose may mention the pipeline by name
        # (e.g. "bundles are made by daily_update"), but importing any build /
        # ingest machinery is the violation.
        import_lines = [
            ln for ln in self.source.splitlines()
            if re.match(r"\s*(import|from)\s", ln)
        ]
        for name in _FORBIDDEN_IMPORTS:
            for ln in import_lines:
                self.assertNotIn(
                    name, ln,
                    f"thin inspector page must not import {name!r} — the UI "
                    f"does not build or ingest bundles (U3). Line: {ln!r}",
                )

    def test_page_is_registered_in_navigation(self) -> None:
        app = (PROJECT_ROOT / "web" / "operator_ui" / "app.py").read_text(
            encoding="utf-8",
        )
        self.assertIn("data_inspect.py", app)


if __name__ == "__main__":
    unittest.main()
