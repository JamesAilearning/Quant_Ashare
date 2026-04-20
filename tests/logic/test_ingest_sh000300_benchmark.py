"""Regression tests for the SH000300 ingestion script helpers.

The script ``scripts/ingest_sh000300_benchmark.py`` is a one-shot data
ingest, but one helper — ``_needs_leading_newline`` — is worth guarding
with unit tests: the previous implementation used
``read_text().splitlines()[-1].endswith("\\n")``, which was always
``False`` for non-empty files (``splitlines()`` strips terminators),
causing a spurious blank line to be written before every append and
corrupting the qlib instrument registry.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


def _load_ingest_module():
    """Load ``scripts/ingest_sh000300_benchmark.py`` as a module.

    ``scripts/`` is not a Python package, so we go via importlib.
    """
    project_root = Path(__file__).resolve().parents[2]
    script_path = project_root / "scripts" / "ingest_sh000300_benchmark.py"
    spec = importlib.util.spec_from_file_location(
        "ingest_sh000300_benchmark_script", script_path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class NeedsLeadingNewlineTests(unittest.TestCase):
    """Unit tests for ``_needs_leading_newline``."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.module = _load_ingest_module()

    def test_empty_file_does_not_need_leading_newline(self) -> None:
        self.assertFalse(self.module._needs_leading_newline(b""))

    def test_file_ending_in_unix_newline_does_not_need_extra(self) -> None:
        self.assertFalse(
            self.module._needs_leading_newline(b"SH600000\t2020-01-01\t2025-12-31\n")
        )

    def test_file_ending_in_windows_newline_does_not_need_extra(self) -> None:
        self.assertFalse(
            self.module._needs_leading_newline(
                b"SH600000\t2020-01-01\t2025-12-31\r\n"
            )
        )

    def test_file_without_trailing_newline_needs_one(self) -> None:
        """A file whose last byte is NOT a newline should get a separator."""
        self.assertTrue(
            self.module._needs_leading_newline(b"SH600000\t2020-01-01\t2025-12-31")
        )

    def test_file_ending_in_trailing_text_after_newline_needs_one(self) -> None:
        """Pathological: file has text after the last newline (truncated write)."""
        self.assertTrue(
            self.module._needs_leading_newline(
                b"SH600000\t2020-01-01\t2025-12-31\nSH600001\t2021"
            )
        )

    def test_multiple_trailing_newlines_are_still_terminated(self) -> None:
        """Two trailing newlines: still "terminated" — the helper only checks
        whether an explicit separator is needed, not whether existing blanks
        should be collapsed (that's the caller's concern)."""
        self.assertFalse(
            self.module._needs_leading_newline(b"SH600000\t2020\t2025\n\n")
        )

    def test_splitlines_based_check_would_have_been_wrong(self) -> None:
        """Regression guard: the old logic
        ``not text.splitlines()[-1].endswith('\\n')`` returns True for any
        non-empty file because splitlines strips terminators.  The new
        helper must return False when the raw bytes do end in a newline.
        """
        # This exact payload broke the old check:
        payload = b"BJ920992\t2022-10-18\t2026-03-06\r\n"
        old_broken_check = not payload.decode().splitlines()[-1].endswith("\n")
        self.assertTrue(old_broken_check, "sanity: old check would have fired")
        # ...but the new helper correctly says we don't need a separator.
        self.assertFalse(self.module._needs_leading_newline(payload))


if __name__ == "__main__":
    unittest.main()
