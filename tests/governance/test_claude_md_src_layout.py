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


class ClaudeMdSrcLayoutTests(unittest.TestCase):
    def test_every_substantive_src_module_is_documented(self) -> None:
        text = CLAUDE_MD.read_text(encoding="utf-8")
        modules = _substantive_src_modules()
        self.assertTrue(modules, "expected to discover substantive src/ modules")
        missing = [m for m in modules if f"`{m}/`" not in text]
        self.assertEqual(
            missing, [],
            "CLAUDE.md's 'Repository layout' section omits these substantive "
            f"src/ modules: {missing}. Add a bullet for each (the layout is "
            "loaded into the agent context every session — keep it accurate).",
        )


if __name__ == "__main__":
    unittest.main()
