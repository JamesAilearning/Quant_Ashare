"""Guard CLAUDE.md's repository-layout section against drift.

CLAUDE.md is loaded into the agent's context every session, so a stale layout
actively misleads. PR-N found the ``src/`` layout had silently drifted —
``pit/`` (the PIT query layer the D5 gate protects) and ``inference/`` (the
production daily-recommend path) existed but were undocumented.

This pins the invariant: every SUBSTANTIVE top-level ``src/`` module (one with
real code, not just an ``__init__.py`` placeholder) must be named in CLAUDE.md's
layout. A new module added without a layout entry fails here.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
CLAUDE_MD = PROJECT_ROOT / "CLAUDE.md"


def _substantive_src_modules() -> list[str]:
    """Top-level ``src/`` package dirs that carry real code (a .py file other
    than ``__init__.py``). Empty placeholders (e.g. ``experimental/``) are
    excluded — documenting them would be noise."""
    modules = []
    for child in sorted(SRC.iterdir()):
        if not child.is_dir() or child.name == "__pycache__":
            continue
        if any(p.suffix == ".py" and p.stem != "__init__" for p in child.glob("*.py")):
            modules.append(child.name)
    return modules


def _documented_src_modules(text: str) -> set[str]:
    """Module names from the INDENTED bullets under the 'src/' entry only.

    Scoping to the src/ sub-block matters (Codex P2): a bare 'pit/' search over
    the whole file would also match the 'tests/' layout line, so removing the
    'src/pit/' bullet wouldn't be caught — the exact drift this guards against.
    """
    documented: set[str] = set()
    in_src = False
    for line in text.splitlines():
        if re.match(r"^- `src/`", line):
            in_src = True
            continue
        if in_src:
            if re.match(r"^- `", line):  # next TOP-level bullet → src block ended
                break
            m = re.match(r"^\s+- `([A-Za-z0-9_]+)/`", line)  # indented sub-bullet
            if m:
                documented.add(m.group(1))
    return documented


class ClaudeMdSrcLayoutTests(unittest.TestCase):
    def test_every_substantive_src_module_is_documented(self) -> None:
        text = CLAUDE_MD.read_text(encoding="utf-8")
        modules = _substantive_src_modules()
        self.assertTrue(modules, "expected to discover substantive src/ modules")
        documented = _documented_src_modules(text)
        self.assertTrue(
            documented,
            "could not parse any src/ layout bullets from CLAUDE.md — the "
            "section format may have changed; update this guard.",
        )
        missing = [m for m in modules if m not in documented]
        self.assertEqual(
            missing, [],
            "CLAUDE.md's 'Repository layout' src/ section omits these "
            f"substantive modules: {missing}. Add a bullet for each under the "
            "``- `src/``` entry (the layout is loaded into the agent context "
            "every session — keep it accurate).",
        )


if __name__ == "__main__":
    unittest.main()
