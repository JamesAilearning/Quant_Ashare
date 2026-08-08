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
simply the wrong decoder. (A child PYTHON process is the other half of
the problem — it ENCODES with the inherited locale — so those callers
also pass ``PYTHONIOENCODING=utf-8`` in the child env; see
``_utf8_child_env`` next to each such call.)

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


def _subprocess_names(tree: ast.Module) -> tuple[set[str], set[str]]:
    """(module aliases, bare spawner names) that resolve to ``subprocess``.

    Resolving the CALL TARGET — not just its trailing attribute — is what
    keeps an unrelated ``renderer.run(text=True)`` or a locally defined
    ``run(text=True)`` from being reported as an unpinned subprocess and
    "fixed" with an ``encoding`` kwarg the API does not accept (codex P2
    on #410).
    """
    modules: set[str] = set()
    bare: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "subprocess":
                    modules.add(alias.asname or "subprocess")
        elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            for alias in node.names:
                if alias.name in _SPAWNERS:
                    bare.add(alias.asname or alias.name)
    return modules, bare


def offending_lines(source: str) -> list[int]:
    """Line numbers of text-mode ``subprocess`` spawns with no ``encoding``."""
    try:
        tree = ast.parse(source)
    except SyntaxError:  # not this gate's job to report
        return []
    modules, bare = _subprocess_names(tree)
    if not modules and not bare:
        return []
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            is_spawn = (
                func.attr in _SPAWNERS
                and isinstance(func.value, ast.Name)
                and func.value.id in modules
            )
        elif isinstance(func, ast.Name):
            is_spawn = func.id in bare
        else:
            is_spawn = False
        if not is_spawn:
            continue
        kwargs = {kw.arg for kw in node.keywords}
        # ``**kwargs`` forwarding (arg is None) cannot be judged
        # statically — the encoding may live in the caller's dict.
        if None in kwargs:
            continue
        if kwargs & _TEXT_FLAGS and "encoding" not in kwargs:
            hits.append(node.lineno)
    return hits


def _offenders() -> list[str]:
    """``path:line`` for every text-mode spawn that does not pin an encoding."""
    found: list[str] = []
    for tree_name in _TREES:
        for py in sorted((PROJECT_ROOT / tree_name).rglob("*.py")):
            if "__pycache__" in py.parts:
                continue
            rel = py.relative_to(PROJECT_ROOT).as_posix()
            found += [
                f"{rel}:{line}"
                for line in offending_lines(py.read_text(encoding="utf-8"))
            ]
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
                "regardless of locale. If the child is a PYTHON process, also "
                'pass PYTHONIOENCODING=utf-8 in its env (it ENCODES with the '
                "inherited locale)."
            ),
        )


class DetectorSelfTests(unittest.TestCase):
    """Guard the scanner so a refactor cannot silently stop detecting — or
    start flagging calls that are not subprocess spawns at all."""

    _IMPORT = "import subprocess\n"

    def test_flags_unpinned_text_call(self) -> None:
        self.assertTrue(offending_lines(
            self._IMPORT + 'subprocess.run(["git", "log"], text=True)\n'))
        self.assertTrue(offending_lines(
            self._IMPORT
            + 'subprocess.check_output(["git"], universal_newlines=True)\n'))

    def test_accepts_pinned_call(self) -> None:
        self.assertFalse(offending_lines(
            self._IMPORT
            + 'subprocess.run(["git"], text=True, encoding="utf-8")\n'))

    def test_ignores_binary_mode_call(self) -> None:
        # No text flag -> bytes out, the caller decodes deliberately.
        self.assertFalse(offending_lines(self._IMPORT + 'subprocess.run(["git"])\n'))

    def test_ignores_kwargs_forwarding(self) -> None:
        # The encoding may be supplied by the caller's dict; a static
        # verdict here would be a false positive.
        self.assertFalse(offending_lines(
            self._IMPORT + 'subprocess.run(["git"], text=True, **opts)\n'))

    def test_ignores_unrelated_run_attribute(self) -> None:
        # codex P2 #410: a same-named method on an unrelated object is NOT
        # a subprocess spawn, and has no encoding kwarg to add.
        self.assertFalse(offending_lines(
            self._IMPORT + "renderer.run(text=True)\n"))
        self.assertFalse(offending_lines(
            self._IMPORT + "self.pool.Popen(universal_newlines=True)\n"))

    def test_ignores_locally_defined_run(self) -> None:
        self.assertFalse(offending_lines(
            self._IMPORT
            + "def run(text=False):\n    return text\n\nrun(text=True)\n"))

    def test_follows_import_aliases(self) -> None:
        # ``import subprocess as sp`` / ``from subprocess import run`` are
        # real spawns and must still be caught.
        self.assertTrue(offending_lines(
            'import subprocess as sp\nsp.run(["git"], text=True)\n'))
        self.assertTrue(offending_lines(
            'from subprocess import run\nrun(["git"], text=True)\n'))
        self.assertFalse(offending_lines(
            'from subprocess import run\n'
            'run(["git"], text=True, encoding="utf-8")\n'))

    def test_ignores_module_without_subprocess_import(self) -> None:
        self.assertFalse(offending_lines('runner.run(["x"], text=True)\n'))


if __name__ == "__main__":
    unittest.main()
