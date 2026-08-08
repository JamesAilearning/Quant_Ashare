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


def _utf8_env_helpers(tree: ast.Module) -> set[str]:
    """Names of module functions that return an env pinning PYTHONIOENCODING.

    A call site writes ``env=_utf8_child_env()``; the dict lives in the
    helper, so accepting the kwarg blindly would accept any env at all.
    """
    helpers: set[str] = set()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if any(_pins_child_encoder(sub) for sub in ast.walk(node)):
            helpers.add(node.name)
    return helpers


def _pins_child_encoder(node: ast.AST) -> bool:
    """Whether ``node`` is a dict literal mapping PYTHONIOENCODING to UTF-8."""
    if not isinstance(node, ast.Dict):
        return False
    for key, value in zip(node.keys, node.values, strict=True):
        if (isinstance(key, ast.Constant) and key.value == "PYTHONIOENCODING"
                and _is_utf8_literal(value)):
            return True
    return False


def _env_pins_child_encoder(
    env: ast.expr, helpers: set[str], assignments: dict[str, ast.expr],
) -> bool:
    """Whether an ``env=`` argument demonstrably pins the child's encoder.

    Three shapes, all in use in this repo: an inline
    ``{**os.environ, "PYTHONIOENCODING": "utf-8"}``, a call to a module
    helper that returns one, and a local ``env = {...}`` passed by name.
    """
    if isinstance(env, ast.Name):
        resolved = assignments.get(env.id)
        if resolved is not None:
            env = resolved
    if any(_pins_child_encoder(sub) for sub in ast.walk(env)):
        return True
    return (
        isinstance(env, ast.Call)
        and isinstance(env.func, ast.Name)
        and env.func.id in helpers
    )


def _python_child(node: ast.Call, scope_assignments: dict[str, ast.expr]) -> bool | None:
    """Is the spawned command a PYTHON interpreter? ``None`` = unresolvable.

    A python child ENCODES its stdout with the inherited locale, so the
    parent-side pin alone is not enough for these (codex P1 / P2 r4 on
    #410). A ``git`` child needs no env, hence the three-valued answer:
    unresolvable argv fails closed at the call site rather than being
    guessed either way.
    """
    if not node.args:
        return None
    argv = node.args[0]
    if isinstance(argv, ast.Name):
        argv = scope_assignments.get(argv.id)  # type: ignore[assignment]
        if argv is None:
            return None
    if not isinstance(argv, ast.List) or not argv.elts:
        return None
    head = argv.elts[0]
    if isinstance(head, ast.Attribute) and head.attr == "executable":
        return True  # sys.executable
    if isinstance(head, ast.Constant) and isinstance(head.value, str):
        return Path(head.value).stem.lower().startswith("python")
    return None


def _literal_assignments(tree: ast.Module) -> dict[str, ast.expr]:
    """``name -> list/dict literal`` for simple assignments in the module.

    Enough to resolve the two shapes this repo uses —
    ``argv = [sys.executable, ...]; run(argv, ...)`` and
    ``env = {...}; run(..., env=env)`` — without a dataflow analysis.
    Anything else stays unresolvable and fails closed. ``argv += [...]``
    must not displace the head-defining assignment, hence ``setdefault``.
    """
    found: dict[str, ast.expr] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, (ast.List, ast.Dict)):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found[target.id] = node.value
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            found.setdefault(node.target.id, node.value)
    return found


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
    helpers = _utf8_env_helpers(tree)
    assignments = _literal_assignments(tree)
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
        forwards_kwargs = None in kwargs

        # CPython: ``text_mode = encoding or errors or text or
        # universal_newlines`` — so a CODEC keyword wins over text=False
        # (``run(..., text=False, encoding="cp936")`` really does return
        # str). Judge the codec keywords FIRST, before any binary-mode
        # exit (codex P2 r3 on #410; verified against the interpreter).
        if "encoding" in kwargs or "errors" in kwargs:
            text_mode = True
        else:
            flags = [kwargs[f] for f in _TEXT_FLAGS if f in kwargs]
            if any(not isinstance(v, ast.Constant) for v in flags):
                continue  # dynamic text flag — cannot judge (see docstring)
            text_mode = any(bool(v.value) for v in flags)  # type: ignore[union-attr]
            if not text_mode and forwards_kwargs:
                continue  # binary as written; ``**opts`` may add a codec
                          # kwarg, but then it supplies the encoding too
        if not text_mode:
            continue  # bytes: no codec kwarg and no truthy text flag

        # Text mode from here on. ``**opts`` FAILS CLOSED (codex P2 r4):
        # a forwarded dict may carry cwd only, and the call would then
        # decode with the locale exactly as before this gate existed.
        if forwards_kwargs or not _is_utf8_literal(
            kwargs.get("encoding", ast.Constant(None))
        ):
            hits.append(node.lineno)
            continue

        # Parent-side pin is right, but a PYTHON child encodes with the
        # locale — the other half of the same bug (codex P2 r4). Require
        # demonstrable env evidence; an unresolvable command fails closed.
        is_python = _python_child(node, assignments)
        if is_python is False:
            continue  # git & friends emit UTF-8 regardless of locale
        env = kwargs.get("env")
        if env is None or not _env_pins_child_encoder(env, helpers, assignments):
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

    def test_kwargs_forwarding_in_text_mode_fails_closed(self) -> None:
        # codex P2 r4 #410: **opts may carry only cwd/timeout, and the call
        # then decodes with the locale exactly as before this gate existed.
        # An unverifiable text-mode spawn is noncompliant, not exempt.
        self.assertTrue(offending_lines(
            self._IMPORT + 'subprocess.run(["git"], text=True, **opts)\n'))
        self.assertTrue(offending_lines(
            self._IMPORT
            + 'subprocess.run(["git"], text=True, encoding="utf-8", **opts)\n'))

    def test_kwargs_forwarding_without_text_mode_is_ignored(self) -> None:
        # Binary as written: **opts could add a codec kwarg, but a dict
        # that supplies `encoding` supplies its VALUE too — nothing here
        # can silently fall back to the locale.
        self.assertFalse(offending_lines(
            self._IMPORT + 'subprocess.run(["git"], check=True, **opts)\n'))

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

    # ---- the CHILD encoder for python spawns (codex P2 r4 #410) ----

    _PY_CALL = ('subprocess.run([sys.executable, "-c", "print(1)"], '
                'text=True, encoding="utf-8"{extra})\n')

    def test_python_child_needs_env_evidence(self) -> None:
        # Parent-side pin alone leaves the child encoding with the locale —
        # the regression this PR fixed can otherwise walk right back in.
        self.assertTrue(offending_lines(
            self._IMPORT + self._PY_CALL.format(extra="")))

    def test_python_child_accepts_inline_env(self) -> None:
        self.assertFalse(offending_lines(
            self._IMPORT + self._PY_CALL.format(
                extra=', env={**os.environ, "PYTHONIOENCODING": "utf-8"}')))

    def test_python_child_accepts_env_helper_and_local(self) -> None:
        helper = (
            "def _utf8_child_env():\n"
            '    return {**os.environ, "PYTHONIOENCODING": "utf-8"}\n\n'
        )
        self.assertFalse(offending_lines(
            self._IMPORT + helper + self._PY_CALL.format(
                extra=", env=_utf8_child_env()")))
        local = 'env = {**os.environ, "PYTHONIOENCODING": "utf-8"}\n'
        self.assertFalse(offending_lines(
            self._IMPORT + local + self._PY_CALL.format(extra=", env=env")))

    def test_python_child_rejects_env_without_the_pin(self) -> None:
        self.assertTrue(offending_lines(
            self._IMPORT + self._PY_CALL.format(
                extra=', env={**os.environ, "TZ": "UTC"}')))
        self.assertTrue(offending_lines(
            self._IMPORT + self._PY_CALL.format(
                extra=', env={**os.environ, "PYTHONIOENCODING": "cp936"}')))

    def test_git_child_needs_no_env(self) -> None:
        # git emits UTF-8 regardless of locale — demanding an env here
        # would imply a dependency that does not exist.
        self.assertFalse(offending_lines(
            self._IMPORT
            + 'subprocess.run(["git", "log"], text=True, encoding="utf-8")\n'))

    def test_python_child_via_resolved_argv_variable(self) -> None:
        # argv = [sys.executable, ...]; run(argv, ...) — the shape
        # rehearse_gate3_prereg_gate.py uses.
        prog = (self._IMPORT
                + 'argv = [sys.executable, "x.py"]\n'
                + 'subprocess.run(argv, text=True, encoding="utf-8")\n')
        self.assertTrue(offending_lines(prog))

    def test_unresolvable_command_fails_closed(self) -> None:
        # Cannot prove the child is not python -> demand the env rather
        # than assume the benign case.
        self.assertTrue(offending_lines(
            self._IMPORT
            + 'subprocess.run(build_argv(), text=True, encoding="utf-8")\n'))

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
