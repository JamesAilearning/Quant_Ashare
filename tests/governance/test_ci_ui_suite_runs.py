"""Pin that CI installs the ``[ui]`` extra so the operator-UI tests RUN.

~15 operator-UI tests (component / page-header / config-run / results-source
helpers + the reportlab PDF-export path) are gated by
``@unittest.skipUnless(_HAS_STREAMLIT, ...)``. CI used to install only
``.[dev]`` (no streamlit), so every one of them silently SKIPPED — a green CI
that proved nothing about the UI layer. PR-M2 added ``[ui]`` to the CI install.

This test does NOT run the UI suite — it pins the **shape** of the CI install so
a future edit that drops the ``ui`` extra (re-hiding the UI tests) fails loudly.
The actual enforcement is CI running the now-unskipped tests.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "test.yml"


class CiInstallsUiExtraTests(unittest.TestCase):
    def _editable_install_line(self) -> str:
        text = WORKFLOW.read_text(encoding="utf-8")
        # The editable install of the repo with its extras, e.g.
        #   pip install -e ".[dev,ui]"
        match = re.search(r'pip install -e "\.\[([^\]]*)\]"', text)
        self.assertIsNotNone(
            match,
            "CI workflow must install the repo editable with extras "
            '(pip install -e ".[...]"). If the form changed, update this pin.',
        )
        assert match is not None  # narrow for mypy
        return match.group(1)

    def test_ci_install_includes_ui_extra(self) -> None:
        extras = {e.strip() for e in self._editable_install_line().split(",")}
        self.assertIn(
            "ui", extras,
            "CI's editable install must include the ``ui`` extra so the "
            "operator-UI tests run instead of silently @skipUnless-ing "
            "(PR-M2). Found extras: " + repr(sorted(extras)),
        )

    def test_ci_install_keeps_dev_extra(self) -> None:
        # ui must be ADDED alongside dev (pytest/ruff/mypy), not replace it.
        extras = {e.strip() for e in self._editable_install_line().split(",")}
        self.assertIn(
            "dev", extras,
            "CI's editable install must still include the ``dev`` extra "
            "(pytest/ruff/mypy). Found extras: " + repr(sorted(extras)),
        )


if __name__ == "__main__":
    unittest.main()
