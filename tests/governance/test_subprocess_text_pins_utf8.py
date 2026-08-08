"""Governance: text-mode subprocess calls pin UTF-8 explicitly.

``subprocess.run(..., text=True)`` decodes the child's output with
``locale.getpreferredencoding(False)`` — ``cp936`` (GBK) on a CN Windows
box, ``UTF-8`` on the Linux CI runner. This repo's tracked files, commit
messages and log lines are full of Chinese, so the same call SUCCEEDS in
CI and raises ``UnicodeDecodeError`` locally.

That is not hypothetical: ``scripts/verify_mechanical_move.py`` (the
hardening-backlog #1 gate) crashed on every invocation on a CN Windows
machine — ``git show <ref>:<path>`` returns file content, the reader
thread died mid-decode, and the tool then failed with a confusing
``NoneType.splitlines``. The gate was green in CI and unusable in the
local review loop it was built for.

Pinning ``encoding="utf-8"`` is a pure correction, never a workaround:
git emits UTF-8 regardless of the OS locale, so the platform default was
simply the wrong decoder.

Scope is ``src/`` + ``scripts/`` — the production and tooling trees whose
behavior must not depend on the operator's locale. ``tests/`` is excluded
deliberately: a test that spawns a subprocess sets its own pipe policy
(several already pass ``encoding``/``errors`` for their own assertions),
and a blanket rule there would fight per-test intent rather than protect
a shipped code path.
"""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_TREES = ("src", "scripts")
_SPAWNERS = frozenset({"run", "check_output", "Popen", "call", "check_call"})
_TEXT_FLAGS = frozenset({"text", "universal_newlines"})


def _offenders() -> list[str]:
    """``path:line`` for every text-mode spawn that does not pin an encoding."""
    found: list[str] = []
    for tree in _TREES:
        for py in sorted((PROJECT_ROOT / tree).rglob("*.py")):
            if "__pycache__" in py.parts:
                continue
            try:
                tree_ast = ast.parse(py.read_text(encoding="utf-8"))
            except SyntaxError:  # not this gate's job to report
                continue
            for node in ast.walk(tree_ast):
                if not isinstance(node, ast.Call):
                    continue
                name = (
                    node.func.attr if isinstance(node.func, ast.Attribute)
                    else node.func.id if isinstance(node.func, ast.Name)
                    else None
                )
                if name not in _SPAWNERS:
                    continue
                kwargs = {kw.arg for kw in node.keywords}
                # ``**kwargs`` forwarding (arg is None) cannot be judged
                # statically — the encoding may live in the caller's dict.
                if None in kwargs:
                    continue
                if kwargs & _TEXT_FLAGS and "encoding" not in kwargs:
                    rel = py.relative_to(PROJECT_ROOT).as_posix()
                    found.append(f"{rel}:{node.lineno}")
    return found


class SubprocessEncodingPinTests(unittest.TestCase):
    def test_no_text_mode_spawn_without_explicit_encoding(self) -> None:
        offenders = _offenders()
        self.assertEqual(
            offenders, [],
            msg=(
                "text-mode subprocess call(s) without an explicit encoding:\n  "
                + "\n  ".join(offenders)
                + "\n\ntext=True decodes with the platform default (GBK on a "
                "CN Windows box, UTF-8 in CI), so these succeed in CI and "
                "raise UnicodeDecodeError locally on any non-ASCII output. "
                'Add encoding="utf-8" — git and this repo emit UTF-8 '
                "regardless of locale."
            ),
        )


class DetectorSelfTests(unittest.TestCase):
    """Guard the AST scanner so a refactor cannot silently stop detecting."""

    @staticmethod
    def _scan(source: str) -> bool:
        """True iff ``source`` contains an unpinned text-mode spawn."""
        found = False
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call):
                continue
            name = (
                node.func.attr if isinstance(node.func, ast.Attribute)
                else node.func.id if isinstance(node.func, ast.Name)
                else None
            )
            if name not in _SPAWNERS:
                continue
            kwargs = {kw.arg for kw in node.keywords}
            if None in kwargs:
                continue
            if kwargs & _TEXT_FLAGS and "encoding" not in kwargs:
                found = True
        return found

    def test_flags_unpinned_text_call(self) -> None:
        self.assertTrue(self._scan(
            'subprocess.run(["git", "log"], text=True)\n'))
        self.assertTrue(self._scan(
            'subprocess.check_output(["git"], universal_newlines=True)\n'))

    def test_accepts_pinned_call(self) -> None:
        self.assertFalse(self._scan(
            'subprocess.run(["git"], text=True, encoding="utf-8")\n'))

    def test_ignores_binary_mode_call(self) -> None:
        # No text flag -> bytes out, the caller decodes deliberately.
        self.assertFalse(self._scan('subprocess.run(["git"])\n'))

    def test_ignores_kwargs_forwarding(self) -> None:
        # The encoding may be supplied by the caller's dict; a static
        # verdict here would be a false positive.
        self.assertFalse(self._scan(
            'subprocess.run(["git"], text=True, **opts)\n'))


if __name__ == "__main__":
    unittest.main()
