"""Governance: text-mode subprocess calls pin UTF-8 on BOTH ends of the pipe.

``subprocess.run(..., text=True)`` decodes the child's output with
``locale.getpreferredencoding(False)`` — ``cp936`` (GBK) on a CN Windows
box, ``UTF-8`` on the Linux CI runner. This repo's tracked files, commit
messages and log lines are full of Chinese, so the same call SUCCEEDS in
CI and raises ``UnicodeDecodeError`` locally.

That is not hypothetical: ``scripts/verify_mechanical_move.py`` (the
hardening-backlog #1 gate) crashed on every invocation on a CN Windows
machine — ``git show <ref>:<path>`` returns file content, the reader thread
died mid-decode, and the tool then failed with a confusing
``NoneType.splitlines``. The gate was green in CI and unusable in the local
review loop it was built for.

TWO halves, and both are needed:

* the PARENT must decode as UTF-8 (``encoding="utf-8"``) — git emits UTF-8
  regardless of locale, so the platform default was simply the wrong
  decoder;
* a PYTHON child must ENCODE as UTF-8, which only ``PYTHONIOENCODING`` in
  its environment achieves. Pinning the parent alone converts mojibake
  into a ``UnicodeDecodeError`` — the same crash, relocated.

HOW this gate decides, and why it is shaped this way: proving "this mapping
pins PYTHONIOENCODING" from an AST is a losing game — a later
``**os.environ`` overrides an earlier pin, a helper with a conditional
return falls through to ``None``, a parameter shadows a module constant.
Those are not corner cases, they are the language. So the child-side rule
is SYNTACTIC: a python spawn must pass ``env=utf8_child_env()``, the one
sanctioned constructor in ``src/core/child_env.py``, whose correctness is
proved where it can be — ``tests/logic/test_child_env.py`` spawns a real
child and reads a non-ASCII round trip back.

Scope is ``src/`` + ``scripts/`` — the production and tooling trees whose
behavior must not depend on the operator's locale. ``tests/`` is excluded
deliberately: a test that spawns a subprocess sets its own pipe policy
(several already pass ``encoding``/``errors`` for their own assertions),
and a blanket rule there would fight per-test intent rather than protect a
shipped code path.
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
# ``getoutput`` / ``getstatusoutput`` are ALWAYS text mode and take no
# encoding at all on Python 3.10 (the oldest runtime in CI); they decode
# with the locale, which is the whole bug. There is no pinning spelling to
# recommend, so any use is rejected outright (codex P2 r8 on #410).
_TEXT_ONLY_HELPERS = frozenset({"getoutput", "getstatusoutput"})
_TEXT_FLAGS = frozenset({"text", "universal_newlines"})

# Interpreter flags that make a python child IGNORE ``PYTHON*`` env vars —
# ``-E`` per ``python --help``, and ``-I`` which implies ``-E``. With either
# one, ``PYTHONIOENCODING`` in the child env is dead letter (verified: the
# child emits cp936 bytes), unless UTF-8 mode is forced on the command line.
_ENV_IGNORING_FLAGS = frozenset({"E", "I"})
# Spellings CPython's codec registry normalizes to UTF-8 (aliases differ only
# by case and by '-'/'_' separators).
_UTF8_SPELLINGS = frozenset({"utf8", "utf_8", "u8", "utf"})

_CHILD_ENV_MODULE = "src.core.child_env"
_CHILD_ENV_FUNC = "utf8_child_env"

# Program names known NOT to be a python interpreter. Anything else
# unrecognized stays UNRESOLVED and fails closed, so a launcher this list
# does not know (``py``, ``python3.12``, a venv shim) is never waved through.
_NON_PYTHON_PROGRAMS = frozenset({"git"})
_PYTHON_PROGRAM_PREFIXES = ("python", "py")


def _is_utf8_literal(node: ast.expr) -> bool:
    """Whether ``node`` is a string LITERAL naming the UTF-8 codec.

    A literal is required on purpose: ``encoding=_ENC`` may well be
    "utf-8", but a gate that accepts an unresolvable name cannot tell it
    from ``encoding=locale.getpreferredencoding()``.
    """
    if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
        return False
    return node.value.strip().lower().replace("-", "_") in _UTF8_SPELLINGS


def _sanctioned_env_names(tree: ast.Module) -> tuple[set[str], set[str]]:
    """(bare names, module aliases) bound to the sanctioned constructor.

    Both spellings must be resolved through an actual IMPORT: an attribute
    whose base is not a proven ``src.core.child_env`` alias could be any
    object with a same-named method returning an unpinned environment
    (codex P2 r7 on #410) — the same target-resolution rule the spawner
    check uses.
    """
    bare: set[str] = set()
    modules: set[str] = set()
    package, _, leaf = _CHILD_ENV_MODULE.rpartition(".")
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == _CHILD_ENV_MODULE:
                for alias in node.names:
                    if alias.name == _CHILD_ENV_FUNC:
                        bare.add(alias.asname or alias.name)
            elif node.module == package:  # from src.core import child_env
                for alias in node.names:
                    if alias.name == leaf:
                        modules.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == _CHILD_ENV_MODULE and alias.asname:
                    modules.add(alias.asname)  # import ... as X
    return bare, modules


def _rebound_names(tree: ast.Module) -> set[str]:
    """Names the module rebinds anywhere: parameters, assignments, loop and
    ``with``/``except`` targets, defs and classes.

    An imported name that is ALSO rebound somewhere is no longer proof of
    anything at a call site — ``def launch(utf8_child_env): ...`` makes the
    call use whatever the caller passed (codex P2 r8 on #410). Collected
    module-wide and applied as a fail-closed veto: the remedy is simply not
    to shadow the sanctioned name, and no file here does.
    """
    names: set[str] = set()

    def _add_target(node: ast.expr | None) -> None:
        for sub in ast.walk(node) if node is not None else ():
            if isinstance(sub, ast.Name):
                names.add(sub.id)

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
            args = node.args
            for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs,
                        args.vararg, args.kwarg):
                if arg is not None:
                    names.add(arg.arg)
        elif isinstance(node, ast.Lambda):
            args = node.args
            for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs,
                        args.vararg, args.kwarg):
                if arg is not None:
                    names.add(arg.arg)
        elif isinstance(node, ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                _add_target(target)
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign, ast.For,
                               ast.AsyncFor, ast.comprehension)):
            _add_target(node.target)
        elif isinstance(node, ast.NamedExpr):
            _add_target(node.target)
        elif isinstance(node, ast.withitem):
            _add_target(node.optional_vars)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
    return names


def _env_is_sanctioned(
    env: ast.expr, bare: set[str], modules: set[str],
) -> bool:
    """Whether ``env=`` is a DIRECT call to the sanctioned constructor.

    Not "a mapping that looks pinned", not a name that might hold one, and
    not any attribute that happens to be spelled ``utf8_child_env``: the
    call expression itself, with its target resolved through an import.
    Every weaker form this gate accepted in an earlier revision turned out
    to be forgeable (see the module docstring).
    """
    if not isinstance(env, ast.Call):
        return False
    func = env.func
    if isinstance(func, ast.Name):
        return func.id in bare
    return (  # child_env.utf8_child_env(...)
        isinstance(func, ast.Attribute)
        and func.attr == _CHILD_ENV_FUNC
        and isinstance(func.value, ast.Name)
        and func.value.id in modules
    )


def _program_is_python(head: ast.expr) -> bool | None:
    """True / False / None (unresolvable) for the spawned program."""
    if isinstance(head, ast.Attribute) and head.attr == "executable":
        return True  # sys.executable
    if isinstance(head, ast.Constant) and isinstance(head.value, str):
        stem = Path(head.value).stem.lower()
        if stem in _NON_PYTHON_PROGRAMS:
            return False
        if stem.startswith(_PYTHON_PROGRAM_PREFIXES):
            return True  # python, python3, pythonw, py (Windows launcher)
    return None


def _argv_strings(call: ast.Call) -> list[str] | None:
    """The call's argv as string literals, or ``None`` if not fully literal."""
    if not call.args or not isinstance(call.args[0], ast.List):
        return None
    out: list[str] = []
    for elt in call.args[0].elts:
        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
            out.append(elt.value)
        else:
            out.append("")  # sys.executable and friends: not a flag
    return out


def _child_ignores_env(call: ast.Call) -> bool:
    """Whether the spawned python is told to IGNORE ``PYTHON*`` variables.

    ``-E`` ignores every ``PYTHON*`` env var and ``-I`` implies it, so
    ``PYTHONIOENCODING`` in the child env becomes dead letter and the child
    falls back to the locale encoder — verified against the interpreter
    (codex P2 r8 on #410). ``-X utf8`` / ``-Xutf8`` forces UTF-8 mode on
    the command line and repairs it.
    """
    argv = _argv_strings(call)
    if argv is None:
        return False  # unresolvable argv already fails closed elsewhere
    ignores = any(
        arg.startswith("-") and not arg.startswith("--")
        and set(arg[1:]) & _ENV_IGNORING_FLAGS
        for arg in argv
    )
    if not ignores:
        return False
    forced_utf8 = any(
        arg.replace(" ", "").lower() in ("-xutf8", "utf8")
        and (arg.lower().startswith("-x") or "-X" in argv)
        for arg in argv
    )
    return not forced_utf8


def _python_child(call: ast.Call) -> bool | None:
    """Is the spawned command a PYTHON interpreter? ``None`` = unresolvable.

    Only a LITERAL argv is judged. Name resolution was tried and removed:
    binding a name to its value across scopes, positions and parameters is
    the same losing game as the env analysis, and a wrong answer here is a
    wrong verdict. An argv the gate cannot read literally fails closed.
    """
    if not call.args:
        return None
    argv = call.args[0]
    if not isinstance(argv, ast.List) or not argv.elts:
        return None
    return _program_is_python(argv.elts[0])


def _subprocess_names(
    tree: ast.Module,
) -> tuple[set[str], set[str], set[str]]:
    """(module aliases, bare spawner names, bare text-helper names).

    Resolving the CALL TARGET — not just its trailing attribute — is what
    keeps an unrelated ``renderer.run(text=True)`` or a locally defined
    ``run(text=True)`` from being reported as an unpinned subprocess and
    "fixed" with an ``encoding`` kwarg the API does not accept.
    """
    modules: set[str] = set()
    bare: set[str] = set()
    helpers: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "subprocess":
                    modules.add(alias.asname or "subprocess")
        elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            for alias in node.names:
                if alias.name in _SPAWNERS:
                    bare.add(alias.asname or alias.name)
                elif alias.name in _TEXT_ONLY_HELPERS:
                    helpers.add(alias.asname or alias.name)
    return modules, bare, helpers


def _collect_kwargs(call: ast.Call) -> tuple[dict[str, ast.expr], bool]:
    """(keyword map, opaque_unpacking).

    A LITERAL ``**{...}`` is expanded — it can itself supply ``text=True``,
    so treating any unpacking as "not a text flag" was a hole. Any other
    unpacking is unresolvable and makes the call fail closed.
    """
    kwargs: dict[str, ast.expr] = {}
    opaque = False
    for kw in call.keywords:
        if kw.arg is not None:
            kwargs[kw.arg] = kw.value
        elif isinstance(kw.value, ast.Dict) and all(
            isinstance(k, ast.Constant) and isinstance(k.value, str)
            for k in kw.value.keys
        ):
            for key, value in zip(kw.value.keys, kw.value.values, strict=True):
                kwargs[key.value] = value  # type: ignore[union-attr]
        else:
            opaque = True
    return kwargs, opaque


def offending_lines(source: str) -> list[int]:
    """Line numbers of text-mode ``subprocess`` spawns not pinned to UTF-8.

    Keyword VALUES are inspected, not just their presence, because both
    directions matter: ``text=False`` asks for BYTES (and "fixing" it with
    ``encoding`` would flip the return type), while ``text=True,
    encoding=None`` or ``encoding="cp936"`` would satisfy a name-only check
    while still decoding with the locale or the wrong codec.

    The order follows CPython's own rule,
    ``text_mode = encoding or errors or text or universal_newlines``: a
    CODEC keyword is decisive and OUTRANKS ``text=False``
    (``run(..., text=False, encoding="cp936")`` really does return ``str``),
    so the codec keywords are judged first.

    A text flag whose value is not a literal (``text=want_str``) is SKIPPED
    rather than guessed: the call may legitimately be binary, and the
    emitted advice would then be wrong. Everything else that cannot be
    resolved — opaque ``**kwargs``, a non-literal argv — FAILS CLOSED.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:  # not this gate's job to report
        return []
    modules, bare, text_helpers = _subprocess_names(tree)
    if not modules and not bare and not text_helpers:
        return []
    sanctioned_bare, sanctioned_modules = _sanctioned_env_names(tree)
    # An imported name that is also rebound somewhere proves nothing at a
    # call site (parameter/local shadowing) — drop it, fail closed.
    shadowed = _rebound_names(tree)
    sanctioned_bare -= shadowed
    sanctioned_modules -= shadowed
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

        # subprocess.getoutput / getstatusoutput: always text mode, and no
        # encoding parameter at all on Python 3.10 (oldest CI runtime), so
        # they decode with the locale and there is no pinning spelling to
        # recommend — reject outright (codex P2 r8).
        is_text_helper = (
            isinstance(func, ast.Attribute)
            and func.attr in _TEXT_ONLY_HELPERS
            and isinstance(func.value, ast.Name)
            and func.value.id in modules
        ) or (isinstance(func, ast.Name) and func.id in text_helpers)
        if is_text_helper:
            hits.append(node.lineno)
            continue
        if not is_spawn:
            continue

        kwargs, opaque_unpacking = _collect_kwargs(node)

        # A codec keyword enables text mode only when its VALUE is truthy:
        # CPython evaluates ``encoding or errors or text or
        # universal_newlines``, so an explicit ``encoding=None`` leaves the
        # result as BYTES and must not be reported — following the advice
        # would flip the return type (codex P2 r7 on #410, verified against
        # the interpreter). A non-literal codec value is unresolvable and
        # therefore still counts as text mode (fail closed).
        codecs = [kwargs[k] for k in ("encoding", "errors") if k in kwargs]
        codec_enables_text = any(
            not isinstance(v, ast.Constant) or v.value is not None
            for v in codecs
        )
        if codec_enables_text:
            text_mode = True
        elif opaque_unpacking:
            text_mode = True  # cannot prove binary — fail closed
        else:
            flags = [kwargs[f] for f in _TEXT_FLAGS if f in kwargs]
            if any(not isinstance(v, ast.Constant) for v in flags):
                continue  # dynamic text flag — cannot judge (see docstring)
            text_mode = any(bool(v.value) for v in flags)  # type: ignore[union-attr]
        if not text_mode:
            continue  # bytes: no codec kwarg, no unpacking, no text flag

        if opaque_unpacking or not _is_utf8_literal(
            kwargs.get("encoding", ast.Constant(None))
        ):
            hits.append(node.lineno)
            continue

        # Parent side is right; now the child's own encoder.
        if _python_child(node) is False:
            continue  # git & friends emit UTF-8 regardless of locale
        env = kwargs.get("env")
        if env is None or not _env_is_sanctioned(
            env, sanctioned_bare, sanctioned_modules,
        ) or _child_ignores_env(node):
            hits.append(node.lineno)
    return hits


def _offenders() -> list[str]:
    """``path:line`` for every text-mode spawn that does not pin UTF-8."""
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
                "text-mode subprocess call(s) not pinned to UTF-8:\n  "
                + "\n  ".join(offenders)
                + "\n\ntext=True decodes with the platform default (GBK on a "
                "CN Windows box, UTF-8 in CI), so these succeed in CI and "
                "raise UnicodeDecodeError locally on any non-ASCII output. "
                'Add encoding="utf-8"; if the child is a PYTHON process, '
                "also pass env=utf8_child_env() from src.core.child_env — "
                "the child ENCODES with the inherited locale."
            ),
        )


_PREAMBLE = (
    "import os\n"
    "import subprocess\n"
    "import sys\n"
    "from src.core.child_env import utf8_child_env\n"
)
_PY = 'subprocess.run([sys.executable, "x"], text=True, encoding="utf-8"{extra})'
_INLINE_PIN = ', env={**os.environ, "PYTHONIOENCODING": "utf-8"}'
_REVERSED_PIN = ', env={"PYTHONIOENCODING": "utf-8", **os.environ}'
_CP936_PIN = ', env={**os.environ, "PYTHONIOENCODING": "cp936"}'

# The decision space, as a table. Written after this gate needed six review
# rounds: each round fixed the ONE reported symptom, so the next hole was
# found by the reviewer rather than by the tests. Enumerating the space is
# what makes a future shortcut fail HERE first.
#
# Note the "hand-rolled env" rows: they are REJECTED even when they look
# correct, because the gate requires the sanctioned constructor rather than
# reasoning about mappings (module docstring). The three rows after them are
# exactly the forgeries that beat the old mapping analysis — now moot by
# construction rather than by ever-longer AST rules.
_DECISION_TABLE: tuple[tuple[str, str, bool], ...] = (
    # --- codec keyword semantics (values, not just names) ---
    ("text bare", 'subprocess.run(["git"], text=True)', True),
    ("text + utf-8", 'subprocess.run(["git"], text=True, encoding="utf-8")', False),
    ("text + cp936", 'subprocess.run(["git"], text=True, encoding="cp936")', True),
    ("text + None", 'subprocess.run(["git"], text=True, encoding=None)', True),
    ("binary", 'subprocess.run(["git"])', False),
    ("text=False", 'subprocess.run(["git"], text=False)', False),
    ("text=False + cp936",
     'subprocess.run(["git"], text=False, encoding="cp936")', True),
    ("text=False + utf-8",
     'subprocess.run(["git"], text=False, encoding="utf-8")', False),
    ("errors alone", 'subprocess.run(["git"], errors="replace")', True),
    ("errors + utf-8",
     'subprocess.run(["git"], errors="replace", encoding="utf-8")', False),
    # A codec keyword set to None does NOT enable text mode (verified
    # against the interpreter): flagging it would push the author to add
    # an encoding and flip the call from bytes to str.
    ("encoding=None alone", 'subprocess.run(["git"], encoding=None)', False),
    ("errors=None alone", 'subprocess.run(["git"], errors=None)', False),
    ("both codecs None",
     'subprocess.run(["git"], encoding=None, errors=None)', False),
    ("encoding=None with text=True",
     'subprocess.run(["git"], text=True, encoding=None)', True),
    ("dynamic text flag", 'subprocess.run(["git"], text=want)', False),
    ("non-literal encoding",
     'subprocess.run(["git"], text=True, encoding=ENC)', True),
    ("alias U8", 'subprocess.run(["git"], text=True, encoding="U8")', False),
    ("alias utf_8", 'subprocess.run(["git"], text=True, encoding="utf_8")', False),
    # --- call-target resolution ---
    ("unrelated .run()", "renderer.run(text=True)", False),
    ("locally defined run()",
     "def run(text=False):\n    pass\nrun(text=True)", False),
    ("aliased module", 'import subprocess as sp\nsp.run(["git"], text=True)', True),
    ("from-import", 'from subprocess import run\nrun(["git"], text=True)', True),
    # --- ** unpacking: it can itself supply text=True ---
    ("opaque ** in text mode", 'subprocess.run(["git"], text=True, **o)', True),
    ("opaque ** looking binary", 'subprocess.run(["git"], check=True, **o)', True),
    ("literal ** supplying text", 'subprocess.run(["git"], **{"text": True})', True),
    ("literal ** supplying text + utf-8",
     'subprocess.run(["git"], **{"text": True, "encoding": "utf-8"})', False),
    ("literal ** supplying cp936",
     'subprocess.run(["git"], **{"text": True, "encoding": "cp936"})', True),
    # --- the child encoder: the sanctioned constructor, nothing else ---
    ("python child, no env", _PY.format(extra=""), True),
    ("python child, sanctioned env",
     _PY.format(extra=", env=utf8_child_env()"), False),
    ("python child, sanctioned env with a base",
     _PY.format(extra=", env=utf8_child_env(base)"), False),
    ("python child, hand-rolled inline env", _PY.format(extra=_INLINE_PIN), True),
    ("python child, env via a local name",
     "env = utf8_child_env()\n" + _PY.format(extra=", env=env"), True),
    ("python child, env=os.environ", _PY.format(extra=", env=os.environ"), True),
    # The attribute spelling must resolve through an IMPORT of the module —
    # any object can expose a same-named method returning an unpinned env.
    ("python child, unrelated object with the same method name",
     _PY.format(extra=", env=helper.utf8_child_env()"), True),
    ("python child, imported module alias",
     "from src.core import child_env\n"
     + _PY.format(extra=", env=child_env.utf8_child_env()"), False),
    ("python child, aliased module import",
     "import src.core.child_env as ce\n"
     + _PY.format(extra=", env=ce.utf8_child_env()"), False),
    ("python child, env pinned to cp936", _PY.format(extra=_CP936_PIN), True),
    # the three forgeries that beat the old mapping analysis
    ("python child, pin overridden by later unpacking",
     _PY.format(extra=_REVERSED_PIN), True),
    ("python child, helper that can fall through",
     "def h():\n    if flag:\n        return utf8_child_env()\n"
     + _PY.format(extra=", env=h()"), True),
    ("python child, parameter shadowing a module constant",
     "ENV = utf8_child_env()\ndef f(ENV):\n    " + _PY.format(extra=", env=ENV"),
     True),
    ("python child, sanctioned name shadowed by a parameter",
     "def launch(utf8_child_env):\n    " + _PY.format(extra=", env=utf8_child_env()"),
     True),
    ("python child, sanctioned name shadowed by an assignment",
     "utf8_child_env = make_env\n" + _PY.format(extra=", env=utf8_child_env()"),
     True),
    # -E ignores every PYTHON* var and -I implies it, so the env pin is dead
    # letter (verified: the child emits cp936 bytes) unless -X utf8 forces
    # UTF-8 mode on the command line.
    ("python child with -E ignores the env",
     'subprocess.run([sys.executable, "-E", "x"], text=True,'
     ' encoding="utf-8", env=utf8_child_env())', True),
    ("python child with -I ignores the env",
     'subprocess.run([sys.executable, "-I", "x"], text=True,'
     ' encoding="utf-8", env=utf8_child_env())', True),
    ("python child with a combined -Es cluster",
     'subprocess.run([sys.executable, "-Es", "x"], text=True,'
     ' encoding="utf-8", env=utf8_child_env())', True),
    ("python child with -E but -X utf8 forced",
     'subprocess.run([sys.executable, "-E", "-X", "utf8", "x"], text=True,'
     ' encoding="utf-8", env=utf8_child_env())', False),
    ("python child with -I but -Xutf8 forced",
     'subprocess.run([sys.executable, "-I", "-Xutf8", "x"], text=True,'
     ' encoding="utf-8", env=utf8_child_env())', False),
    # subprocess.getoutput / getstatusoutput decode with the locale and take
    # no encoding on Python 3.10 — no pinning spelling exists, so reject.
    ("getoutput", 'subprocess.getoutput("git log")', True),
    ("getstatusoutput", 'subprocess.getstatusoutput("git log")', True),
    ("getoutput via from-import",
     'from subprocess import getoutput\ngetoutput("git log")', True),
    ("git child needs no env",
     'subprocess.run(["git", "log"], text=True, encoding="utf-8")', False),
    # --- interpreter naming / unknown programs fail closed ---
    ("py launcher", 'subprocess.run(["py", "s.py"], text=True, encoding="utf-8")',
     True),
    ("python3", 'subprocess.run(["python3", "s.py"], text=True, encoding="utf-8")',
     True),
    ("unknown program",
     'subprocess.run(["node", "s.js"], text=True, encoding="utf-8")', True),
    ("unresolvable argv expression",
     'subprocess.run(build(), text=True, encoding="utf-8")', True),
    ("argv from a variable is unresolvable",
     'argv = ["git", "log"]\nsubprocess.run(argv, text=True, encoding="utf-8")',
     True),
)


class DecisionTableTests(unittest.TestCase):
    """Every branch of the gate's decision space, in one place."""

    def test_decision_table(self) -> None:
        for label, body, should_flag in _DECISION_TABLE:
            with self.subTest(case=label):
                flagged = bool(offending_lines(_PREAMBLE + body + "\n"))
                self.assertEqual(flagged, should_flag, label)

    def test_absolute_python_path_is_recognized(self) -> None:
        # Kept out of the table because a Windows path literal fights the
        # table's own quoting; the program-name rule is what matters.
        src = _PREAMBLE + (
            'subprocess.run([r"C:\\Python\\python.exe", "s"],'
            ' text=True, encoding="utf-8")\n'
        )
        self.assertTrue(offending_lines(src))


if __name__ == "__main__":
    unittest.main()
