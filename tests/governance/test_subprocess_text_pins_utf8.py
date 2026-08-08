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
# Spellings CPython's codec registry normalizes to UTF-8 (aliases differ only
# by case and by '-'/'_' separators).
_UTF8_SPELLINGS = frozenset({"utf8", "utf_8", "u8", "utf"})


def _is_utf8_literal(node: ast.expr) -> bool:
    """Whether ``node`` is a string LITERAL naming the UTF-8 codec.

    A literal is required on purpose: ``encoding=_ENC`` may well be
    "utf-8", but a governance gate that accepts an unresolvable name
    cannot tell it from ``encoding=locale.getpreferredencoding()``. The
    repo has no such call today, and a literal is also what makes the
    rule greppable.
    """
    if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
        return False
    return node.value.strip().lower().replace("-", "_") in _UTF8_SPELLINGS


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
    """Line numbers of text-mode ``subprocess`` spawns not pinned to UTF-8.

    Keyword VALUES are inspected, not just their presence (codex P2 on
    #410), because both directions matter:

    * ``text=False`` / ``universal_newlines=False`` asks for BYTES — the
      locale never decodes anything, and "fixing" it by adding
      ``encoding`` would flip the return type (any of ``encoding`` /
      ``errors`` implies text mode);
    * ``text=True, encoding=None`` and ``encoding="cp936"`` would satisfy
      a name-only check while still decoding with the locale or the wrong
      codec — exactly the regression this gate exists to prevent.

    The order follows CPython's own rule,
    ``text_mode = encoding or errors or text or universal_newlines``: a
    CODEC keyword is decisive and OUTRANKS ``text=False``
    (``run(..., text=False, encoding="cp936")`` really does return
    ``str``), so the codec keywords are judged first and the binary-mode
    exit is only reached when neither is present.

    A text flag whose value is not a literal (``text=want_str``) is
    SKIPPED rather than guessed: the call may legitimately be binary, and
    the emitted advice would then be wrong.
    """
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
        kwargs = {kw.arg: kw.value for kw in node.keywords}
        # ``**kwargs`` forwarding (arg is None) cannot be judged
        # statically — the encoding may live in the caller's dict.
        if None in kwargs:
            continue

        # CPython: ``text_mode = encoding or errors or text or
        # universal_newlines`` — so a CODEC keyword wins over text=False
        # (``run(..., text=False, encoding="cp936")`` really does return
        # str). Judge the codec keywords FIRST, before any binary-mode
        # exit (codex P2 r3 on #410; verified against the interpreter).
        if "encoding" in kwargs or "errors" in kwargs:
            if not _is_utf8_literal(kwargs.get("encoding", ast.Constant(None))):
                hits.append(node.lineno)
            continue

        flags = [kwargs[f] for f in _TEXT_FLAGS if f in kwargs]
        if any(not isinstance(v, ast.Constant) for v in flags):
            continue  # dynamic text flag — cannot judge, see the docstring
        if not any(bool(v.value) for v in flags):  # type: ignore[union-attr]
            continue  # bytes: no codec kwarg and no truthy text flag
        hits.append(node.lineno)  # text mode with no encoding at all
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

    # ---- keyword VALUES, not just names (codex P2 #410 r2) ----

    def test_ignores_explicit_binary_request(self) -> None:
        # text=False asks for BYTES; adding encoding would flip the return
        # type, so the advice must not be emitted here.
        self.assertFalse(offending_lines(
            self._IMPORT + 'subprocess.run(["git"], text=False)\n'))
        self.assertFalse(offending_lines(
            self._IMPORT
            + 'subprocess.run(["git"], universal_newlines=False)\n'))

    def test_flags_encoding_none(self) -> None:
        # Passing None is the locale default spelled out — a name-only
        # check would have let it through.
        self.assertTrue(offending_lines(
            self._IMPORT
            + 'subprocess.run(["git"], text=True, encoding=None)\n'))

    def test_flags_non_utf8_codec(self) -> None:
        self.assertTrue(offending_lines(
            self._IMPORT
            + 'subprocess.run(["git"], text=True, encoding="cp936")\n'))

    def test_accepts_utf8_alias_spellings(self) -> None:
        for spelling in ('"utf-8"', '"UTF-8"', '"utf8"', '"utf_8"', '"U8"'):
            self.assertFalse(
                offending_lines(
                    self._IMPORT
                    + f'subprocess.run(["git"], text=True, encoding={spelling})\n'),
                spelling,
            )

    def test_flags_non_literal_encoding(self) -> None:
        # encoding=_ENC may be "utf-8" — or locale.getpreferredencoding().
        # A gate that cannot tell must not pass it.
        self.assertTrue(offending_lines(
            self._IMPORT
            + 'subprocess.run(["git"], text=True, encoding=_ENC)\n'))

    def test_flags_errors_kwarg_without_encoding(self) -> None:
        # errors="replace" alone switches the spawn to TEXT mode decoded
        # with the locale — the same bug without a text flag in sight.
        self.assertTrue(offending_lines(
            self._IMPORT
            + 'subprocess.run(["git"], errors="replace")\n'))

    def test_ignores_dynamic_text_flag(self) -> None:
        # Unresolvable: the call may legitimately be binary, and the
        # advice would then be wrong (see offending_lines docstring).
        self.assertFalse(offending_lines(
            self._IMPORT + 'subprocess.run(["git"], text=want_str)\n'))

    # ---- codec kwargs OUTRANK text=False (codex P2 r3 #410) ----
    # Verified against the interpreter: CPython computes
    # ``text_mode = encoding or errors or text or universal_newlines``,
    # so these combinations really do return str.

    def test_codec_kwarg_beats_explicit_binary_flag(self) -> None:
        self.assertTrue(offending_lines(
            self._IMPORT
            + 'subprocess.run(["git"], text=False, encoding="cp936")\n'))
        self.assertTrue(offending_lines(
            self._IMPORT
            + 'subprocess.run(["git"], universal_newlines=False,'
              ' errors="replace")\n'))
        self.assertTrue(offending_lines(
            self._IMPORT
            + 'subprocess.run(["git"], text=False, encoding=None,'
              ' errors="replace")\n'))

    def test_utf8_with_binary_flag_still_passes(self) -> None:
        # The combination is odd, but it decodes as UTF-8 — which is all
        # this gate is about; flagging it would be a false positive.
        self.assertFalse(offending_lines(
            self._IMPORT
            + 'subprocess.run(["git"], text=False, encoding="utf-8")\n'))

    def test_dynamic_text_flag_with_codec_kwarg_is_judged(self) -> None:
        # The dynamic flag is irrelevant once a codec kwarg is present:
        # text mode is guaranteed either way, so the codec must be UTF-8.
        self.assertTrue(offending_lines(
            self._IMPORT
            + 'subprocess.run(["git"], text=want_str, encoding="cp936")\n'))
        self.assertFalse(offending_lines(
            self._IMPORT
            + 'subprocess.run(["git"], text=want_str, encoding="utf-8")\n'))


if __name__ == "__main__":
    unittest.main()
