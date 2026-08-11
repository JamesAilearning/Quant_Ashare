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
import re
import sys
import unittest
from collections import Counter
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
# Short interpreter options that CONSUME a value (attached, ``-Xutf8``, or
# the next argv element, ``-X utf8``) — needed so the value is not mistaken
# for another flag cluster, and so a later script argument spelled ``utf8``
# is not mistaken for the option's value.
_VALUE_TAKING_FLAGS = frozenset({"X", "W"})
# Long options that CONSUME the next argv element. Without this, the value
# ("always") is mistaken for the script name and option parsing stops
# BEFORE a later -E/-I (codex P2 r13 on #410; verified:
# ``--check-hash-based-pycs always -E`` reports ignore_environment=1).
_VALUE_TAKING_LONG_OPTS = frozenset({"--check-hash-based-pycs"})
# The ONLY ``-X`` options that turn UTF-8 mode ON, spelled exactly as CPython
# accepts them. Verified against the interpreter, because two neighbouring
# spellings silently do NOT repair an env-ignoring child (codex P2 r10 on
# #410 for the first, found while checking the class for the second):
#   ``-X utf8=0``  -> UTF-8 mode DISABLED, child emits cp936;
#   ``-X UTF8``    -> ignored, child emits cp936 (``-X`` names are
#                     case-SENSITIVE, so no case folding here);
#   ``-X utf8=2`` / ``utf8=on`` -> the interpreter refuses to start.
# Matching the option NAME and ignoring its value is exactly the class of
# looseness this gate keeps being caught by; this set is compared whole.
_UTF8_MODE_ENABLING_X = frozenset({"utf8", "utf8=1"})
# Spellings CPython's codec registry normalizes to UTF-8 (aliases differ only
# by case and by '-'/'_' separators).
_UTF8_SPELLINGS = frozenset({"utf8", "utf_8", "u8", "utf"})

_CHILD_ENV_MODULE = "src.core.child_env"
_CHILD_ENV_FUNC = "utf8_child_env"

# Program names known NOT to be a python interpreter. Anything else
# unrecognized stays UNRESOLVED and fails closed, so a launcher this list
# does not know (a venv shim, a tool wrapper) is never waved through.
_NON_PYTHON_PROGRAMS = frozenset({"git"})
# Interpreter names are matched EXACTLY, never by prefix (codex P1 r16 on
# #410): ``startswith(("python", "py"))`` also accepted pyright, pytest,
# pyinstaller — native/tool children whose decoding PYTHONIOENCODING does
# not govern. The Windows launcher has exactly two spellings; a CPython
# binary is ``python`` + optional version digits + optional ``w``.
_PY_LAUNCHER_NAMES = frozenset({"py", "pyw"})
_PYTHON_STEM_RE = re.compile(r"python\d*(\.\d+)*w?")


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


def _binding_counts(tree: ast.Module) -> Counter[str]:
    """How many times each name is BOUND anywhere in the module.

    Every binding form counts — imports included (codex P2 r9 on #410: a
    fallback ``except ImportError: from elsewhere import utf8_child_env``
    re-binds the same name and the runtime branch decides which wins), as
    do parameters, assignments, loop / ``with`` / ``except`` targets, defs
    and classes.

    A sanctioned import is trusted only when its name has EXACTLY ONE
    binding site in the file. Anything else — a second import, a parameter,
    an assignment — makes the name prove nothing at the call site, and the
    call fails closed. The remedy is simply not to shadow or re-bind the
    sanctioned name; nothing in this repo does.
    """
    counts: Counter[str] = Counter()

    def _add_target(node: ast.expr | None) -> None:
        for sub in ast.walk(node) if node is not None else ():
            if isinstance(sub, ast.Name):
                counts[sub.id] += 1

    def _add_args(args: ast.arguments) -> None:
        for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs,
                    args.vararg, args.kwarg):
            if arg is not None:
                counts[arg.arg] += 1

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                counts[alias.asname or alias.name.split(".")[0]] += 1
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            counts[node.name] += 1
            _add_args(node.args)
        elif isinstance(node, ast.Lambda):
            _add_args(node.args)
        elif isinstance(node, ast.ClassDef):
            counts[node.name] += 1
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
            counts[node.name] += 1
        # Structural pattern matching binds names too (codex P2 r13):
        # ``case {"env": utf8_child_env}:`` captures into that name, so a
        # sanctioned import shadowed by a match capture must lose its
        # exactly-once status like any other rebinding.
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            counts[node.name] += 1
        elif isinstance(node, ast.MatchMapping) and node.rest:
            counts[node.rest] += 1
    return counts


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


def _sys_module_names(tree: ast.Module) -> set[str]:
    """Names bound to the ``sys`` MODULE by import.

    ``X.executable`` proves a python child only when ``X`` really is the
    ``sys`` module — any object can expose an ``executable`` attribute
    naming a native binary (codex P2 r12 on #410). Same target-resolution
    rule as the spawner and env-helper checks: resolved through an import,
    never by the attribute's spelling.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "sys":
                    names.add(alias.asname or "sys")
    return names


def _program_is_python(head: ast.expr, sys_names: set[str]) -> bool | None:
    """True / False / None (unresolvable) for the spawned program."""
    if isinstance(head, ast.Attribute) and head.attr == "executable":
        if isinstance(head.value, ast.Name) and head.value.id in sys_names:
            return True  # a resolved sys.executable
        return None  # someone ELSE's .executable — fail closed
    if isinstance(head, ast.Constant) and isinstance(head.value, str):
        stem = Path(head.value).stem.lower()
        if stem in _NON_PYTHON_PROGRAMS:
            return False
        if stem in _PY_LAUNCHER_NAMES or _PYTHON_STEM_RE.fullmatch(stem):
            return True  # python / python3.12 / pythonw / py|pyw launcher
    return None


def _argv_strings(call: ast.Call) -> list[str | None] | None:
    """The call's argv elements, classified for option scanning.

    Per element: a literal string stays itself; EVERYTHING else becomes
    ``None`` — unresolvable. A ``*args`` splice can carry any number of
    flags and a bare name can hold one (codex P2 r14 on #410), but so
    can ANY other single expression: with ``flag = "-E"``, the call
    ``[sys.executable, str(flag), "x.py"]`` hands CPython the
    env-ignoring option itself, so the old "one opaque value ends the
    option region like a script name" rule was an accept built on an
    unprovable claim (codex P2 r18). Only a literal non-option element
    or an explicit ``--`` proves the boundary — spell a dynamic script
    path as ``[sys.executable, "--", str(path), ...]`` (verified: ``--``
    ends CPython option parsing; ``python -- -E`` opens a FILE named
    ``-E``, while ``-E`` before ``--`` stays active).
    """
    if not call.args or not isinstance(call.args[0], ast.List):
        return None
    return [
        elt.value
        if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
        else None
        for elt in call.args[0].elts
    ]


def _interpreter_options(
    argv: list[str | None],
) -> tuple[set[str], list[str], bool]:
    """(short option letters, ``-X`` option values) of a python argv.

    Parsed the way CPython parses: options end at ``-c`` / ``-m`` / ``--``
    or the script name, and value-taking short options (``-X``, ``-W``)
    consume the rest of their cluster if attached (``-Xutf8``) or the NEXT
    element otherwise (``-X utf8``). Both rules matter — a trailing script
    argument that happens to read ``utf8`` must NOT count as a UTF-8 pin
    (codex P2 r9 on #410), and ``-Es`` must still yield the ``E``.
    """
    letters: set[str] = set()
    x_values: list[str] = []
    unresolvable = False
    i = 1  # argv[0] is the interpreter itself
    while i < len(argv):
        arg = argv[i]
        if arg is None:
            # A *splice or a variable in the OPTION REGION: it can contain
            # -E / -I, so the options cannot be known (codex P2 r14).
            unresolvable = True
            break
        if not arg.startswith("-") or arg in ("-", "--"):
            break  # the script name (or an explicit end of options)
        if arg in ("-c", "-m"):
            break  # everything after belongs to the command / module
        if arg.startswith("--"):
            # A value-taking long option consumes the NEXT element too —
            # otherwise its value is mistaken for the script name and
            # parsing stops before a later -E/-I (codex P2 r13).
            if arg in _VALUE_TAKING_LONG_OPTS:
                i += 2
            else:
                i += 1
            continue
        cluster = arg[1:]
        for pos, letter in enumerate(cluster):
            if letter in _VALUE_TAKING_FLAGS:
                attached = cluster[pos + 1:]
                if attached:
                    if letter == "X":
                        x_values.append(attached)
                elif i + 1 < len(argv):
                    nxt = argv[i + 1]
                    if nxt is None:
                        # A DYNAMIC value for a value-taking option (-X
                        # mode): the option cannot be known — unresolvable,
                        # not a crash (codex P2 r15).
                        unresolvable = True
                        i = len(argv)
                        break
                    if letter == "X":
                        x_values.append(nxt)
                    i += 1
                break  # the rest of the cluster was this option's value
            letters.add(letter)
        i += 1
    return letters, x_values, unresolvable


def _child_ignores_env(call: ast.Call) -> bool:
    """Whether the spawned python is told to IGNORE ``PYTHON*`` variables.

    ``-E`` ignores every ``PYTHON*`` env var and ``-I`` implies it, so
    ``PYTHONIOENCODING`` in the child env becomes dead letter and the child
    falls back to the locale encoder — verified against the interpreter
    (codex P2 r8 on #410). ``-X utf8`` forces UTF-8 mode on the command
    line and repairs it, but only when ``utf8`` is the option PAIRED with
    ``-X`` (codex P2 r9).
    """
    argv = _argv_strings(call)
    if argv is None:
        return False  # unresolvable argv already fails closed elsewhere
    letters, x_values, unresolvable = _interpreter_options(argv)
    if unresolvable:
        return True  # hidden flags possible — fail closed
    if not letters & _ENV_IGNORING_FLAGS:
        return False
    # Repeated ``-X utf8`` options: CPython honors the FIRST occurrence
    # (verified: ``-X utf8=0 -X utf8=1`` -> utf8_mode=0, and the reverse
    # -> 1), so ``any()`` over the values was wrong (codex P2 r13). Only
    # the first utf8-named option decides.
    for value in x_values:
        if value == "utf8" or value.startswith("utf8="):
            return value not in _UTF8_MODE_ENABLING_X
    return True  # env-ignoring flag with no utf8 option at all


def _python_child(call: ast.Call, sys_names: set[str]) -> bool | None:
    """Is the spawned command a PYTHON interpreter? ``None`` = unresolvable.

    Only a LITERAL argv is judged. Name resolution was tried and removed:
    binding a name to its value across scopes, positions and parameters is
    the same losing game as the env analysis, and a wrong answer here is a
    wrong verdict. An argv the gate cannot read literally fails closed.

    An ``executable=`` OVERRIDES the program (Popen executes it and argv[0]
    becomes mere display), so when present it — not argv[0] — is what gets
    judged (codex P2 r14 on #410), and it counts however it arrives:
    keyword or Popen's THIRD positional argument (codex r16). A literal
    ``executable=None`` is CPython's own no-override spelling and falls
    through to argv[0]. Under a truthy (or unresolvable) ``shell=`` the
    argv is a shell command line, not a program vector — nothing about the
    child is provable. And more than three positional arguments would let
    later Popen parameters (``shell`` among them, at position nine) arrive
    positionally unmodeled — fail closed rather than model them.
    """
    for kw in call.keywords:
        if kw.arg == "shell":
            if not (isinstance(kw.value, ast.Constant) and not kw.value.value):
                return None  # truthy or unresolvable shell — fail closed
    if len(call.args) > 3:
        return None  # positions past executable are unmodeled — fail closed
    executable: ast.expr | None = call.args[2] if len(call.args) == 3 else None
    for kw in call.keywords:
        if kw.arg == "executable":
            executable = kw.value
    if executable is not None and not (
        isinstance(executable, ast.Constant) and executable.value is None
    ):
        return _program_is_python(executable, sys_names)
    if not call.args:
        return None
    argv = call.args[0]
    if not isinstance(argv, ast.List) or not argv.elts:
        return None
    return _program_is_python(argv.elts[0], sys_names)


def _subprocess_names(
    tree: ast.Module,
) -> tuple[set[str], set[str], set[str]]:
    """(module aliases, bare spawner names, bare text-helper names).

    Resolving the CALL TARGET — not just its trailing attribute — is what
    keeps an unrelated ``renderer.run(text=True)`` or a locally defined
    ``run(text=True)`` from being reported as an unpinned subprocess and
    "fixed" with an ``encoding`` kwarg the API does not accept.

    Import bindings are the roots; plain ``Name = Name`` assignments then
    propagate them to a fixpoint (codex P2 r19 on #410: ``sp =
    subprocess`` followed by ``sp.run(...)`` spawned unseen). Rebinding
    an alias later does not un-flag it — the gate is a refuse-list, so
    over-approximating aliases only ever fails closed.
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
    grew = True
    while grew:
        grew = False
        for node in ast.walk(tree):
            value = getattr(node, "value", None)
            if not (isinstance(node, (ast.Assign, ast.AnnAssign))
                    and isinstance(value, ast.Name)):
                continue
            targets = (node.targets if isinstance(node, ast.Assign)
                       else [node.target])
            names = {t.id for t in targets if isinstance(t, ast.Name)}
            for pool in (modules, bare, helpers):
                if value.id in pool and not names <= pool:
                    pool.update(names)
                    grew = True
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

    THE INVARIANT, and the reason this function keeps its shape: every path
    that ACCEPTS a call must be provably safe, and everything else fails
    closed. Exactly four acceptances exist —

    1. the call is not a subprocess spawn at all;
    2. provably binary: no codec keyword with a truthy value, no unpacking,
       and every text flag a falsy LITERAL;
    3. a proven non-python child (``git``) with a UTF-8 literal encoding —
       git emits UTF-8 whatever the locale;
    4. a proven PYTHON child with a UTF-8 literal encoding, the sanctioned
       ``env=utf8_child_env()``, and no ``-E`` / ``-I`` to ignore it.

    Everything else — an opaque ``**kwargs``, a non-literal argv or an
    unknown program, a dynamic text flag, a non-literal encoding, a
    hand-rolled env, a shadowed sanctioned name — is REFUSED. Two of these
    used to be exceptions ("skip the dynamic flag", "let the env repair an
    unresolved child") and both turned into holes, so the rule is now
    uniform: unresolvable means refused, never assumed.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:  # not this gate's job to report
        return []
    modules, bare, text_helpers = _subprocess_names(tree)
    if not modules and not bare and not text_helpers:
        return []
    sanctioned_bare, sanctioned_modules = _sanctioned_env_names(tree)
    # A sanctioned name is trusted only when the file binds it EXACTLY
    # once — a second import, a parameter or an assignment means the call
    # site cannot know which object it gets, so fail closed.
    counts = _binding_counts(tree)
    sanctioned_bare = {n for n in sanctioned_bare if counts[n] == 1}
    sanctioned_modules = {n for n in sanctioned_modules if counts[n] == 1}
    # Same exactly-once rule for the sys module itself: a shadowed ``sys``
    # cannot prove ``sys.executable`` is a python interpreter.
    sys_names = {n for n in _sys_module_names(tree) if counts[n] == 1}
    hits: list[int] = []
    # A module reference FORWARDED anywhere else — a call argument, an
    # attribute/subscript target, a container literal, a return value —
    # escapes this file-local analysis entirely: the receiver can spawn
    # through it unseen (codex P2 r19 on #410). Exactly two uses are
    # resolvable: the base of an attribute access (``subprocess.run``,
    # ``subprocess.PIPE``) and a plain Name-to-Name alias (tracked in
    # ``_subprocess_names``). Everything else fails closed.
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Name) and node.id in modules
                and isinstance(node.ctx, ast.Load)):
            continue
        parent = parents.get(node)
        if isinstance(parent, ast.Attribute) and parent.value is node:
            continue
        if (isinstance(parent, ast.Assign) and parent.value is node
                and len(parent.targets) == 1
                and isinstance(parent.targets[0], ast.Name)):
            continue
        if (isinstance(parent, ast.AnnAssign) and parent.value is node
                and isinstance(parent.target, ast.Name)):
            continue
        hits.append(node.lineno)
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

        # Popen's optional parameters can arrive POSITIONALLY too — env is
        # position 10 and universal_newlines position 11 (codex P2 r17 on
        # #410: a positional universal_newlines=True with no text keyword
        # made the call look provably binary and skipped the gate). The
        # keyword-based analysis below models nothing past ``executable``
        # (position 2), and a call-level ``*splat`` can smuggle any number
        # of positions — both make the call unresolvable, so they fail
        # closed BEFORE the binary-mode conclusion, not after it.
        if len(node.args) > 3 or any(
            isinstance(arg, ast.Starred) for arg in node.args
        ):
            hits.append(node.lineno)
            continue

        # A codec keyword enables text mode only when its VALUE is truthy:
        # CPython evaluates ``encoding or errors or text or
        # universal_newlines``, so an explicit ``encoding=None`` leaves the
        # result as BYTES and must not be reported — following the advice
        # would flip the return type (codex P2 r7 on #410, verified against
        # the interpreter). A non-literal codec value is unresolvable and
        # therefore still counts as text mode (fail closed).
        codecs = [kwargs[k] for k in ("encoding", "errors") if k in kwargs]
        # TRUTHINESS, not is-not-None (codex P2 r15; verified): CPython's
        # ``encoding or errors or text or ...`` treats EVERY falsy literal
        # as binary — encoding="" and errors="" return bytes just like
        # None. A non-literal stays unresolvable (counts as text mode).
        codec_enables_text = any(
            not isinstance(v, ast.Constant) or bool(v.value)
            for v in codecs
        )
        if codec_enables_text:
            text_mode = True
        elif opaque_unpacking:
            text_mode = True  # cannot prove binary — fail closed
        else:
            flags = [kwargs[f] for f in _TEXT_FLAGS if f in kwargs]
            if any(not isinstance(v, ast.Constant) for v in flags):
                # A DYNAMIC text flag cannot be proven binary, and if it is
                # true at runtime the call decodes with the locale — the
                # very bug. Skipping it was the one place this gate failed
                # OPEN (codex P2 r11 on #410); it now fails closed like
                # every other unresolvable input. The remedy is a literal
                # flag, not an added encoding (that would flip a genuinely
                # binary call to text) — see the failure message.
                hits.append(node.lineno)
                continue
            text_mode = any(bool(v.value) for v in flags)  # type: ignore[union-attr]
        if not text_mode:
            continue  # bytes: no codec kwarg, no unpacking, no text flag

        if opaque_unpacking or not _is_utf8_literal(
            kwargs.get("encoding", ast.Constant(None))
        ):
            hits.append(node.lineno)
            continue

        # Parent side is right; now the child's own encoder.
        child = _python_child(node, sys_names)
        if child is False:
            continue  # git & friends emit UTF-8 regardless of locale
        if child is None:
            # UNRESOLVED child (a non-literal argv, ``shell=True``, an
            # unknown program): PYTHONIOENCODING cannot control a native
            # child's encoder, so the env helper does not repair it and
            # must not be accepted as if it did (codex P2 r11 on #410).
            # Only a PROVEN python child is repairable.
            hits.append(node.lineno)
            continue
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
    # EVERY falsy literal is binary (verified: encoding="" returns bytes),
    # so flagging it would flip the return type — same rule as None.
    ("encoding empty-string alone",
     'subprocess.run(["git"], encoding="")', False),
    ("errors empty-string alone", 'subprocess.run(["git"], errors="")', False),
    ("text=True with empty encoding",
     'subprocess.run(["git"], text=True, encoding="")', True),
    ("both codecs None",
     'subprocess.run(["git"], encoding=None, errors=None)', False),
    ("encoding=None with text=True",
     'subprocess.run(["git"], text=True, encoding=None)', True),
    # A dynamic flag cannot be proven binary; if it is true at runtime the
    # call decodes with the locale, so it is refused rather than skipped.
    ("dynamic text flag", 'subprocess.run(["git"], text=want)', True),
    ("dynamic universal_newlines",
     'subprocess.run(["git"], universal_newlines=want)', True),
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
    # ``-X`` consumes the option NEXT to it: a different -X option plus a
    # later script argument spelled "utf8" is NOT a UTF-8 pin.
    ("python child, -X dev with a trailing utf8 argument",
     'subprocess.run([sys.executable, "-E", "-X", "dev", "-c", code, "utf8"],'
     ' text=True, encoding="utf-8", env=utf8_child_env())', True),
    ("python child, utf8 only after -c",
     'subprocess.run([sys.executable, "-I", "-c", code, "-X", "utf8"],'
     ' text=True, encoding="utf-8", env=utf8_child_env())', True),
    ("python child, -X utf8=1 accepted",
     'subprocess.run([sys.executable, "-E", "-X", "utf8=1", "x"], text=True,'
     ' encoding="utf-8", env=utf8_child_env())', False),
    # ``-X utf8[=0|1]``: the VALUE decides, and the option name is
    # case-SENSITIVE — both spellings below leave the child on cp936
    # (verified against the interpreter).
    ("python child, -X utf8=0 disables UTF-8 mode",
     'subprocess.run([sys.executable, "-E", "-X", "utf8=0", "x"], text=True,'
     ' encoding="utf-8", env=utf8_child_env())', True),
    ("python child, -X UTF8 is not the option name",
     'subprocess.run([sys.executable, "-E", "-X", "UTF8", "x"], text=True,'
     ' encoding="utf-8", env=utf8_child_env())', True),
    ("python child, -Xutf8=0 attached form",
     'subprocess.run([sys.executable, "-I", "-Xutf8=0", "x"], text=True,'
     ' encoding="utf-8", env=utf8_child_env())', True),
    # Repeated -X utf8: CPython honors the FIRST occurrence (verified).
    ("python child, -X utf8=0 then utf8=1 stays disabled",
     'subprocess.run([sys.executable, "-E", "-X", "utf8=0", "-X", "utf8=1",'
     ' "x"], text=True, encoding="utf-8", env=utf8_child_env())', True),
    ("python child, -X utf8=1 then utf8=0 stays enabled",
     'subprocess.run([sys.executable, "-E", "-X", "utf8=1", "-X", "utf8=0",'
     ' "x"], text=True, encoding="utf-8", env=utf8_child_env())', False),
    # A value-taking LONG option must not end option parsing (its value is
    # not the script name): the -E after it still ignores the env.
    ("python child, long option value before -E",
     'subprocess.run([sys.executable, "--check-hash-based-pycs", "always",'
     ' "-E", "x"], text=True, encoding="utf-8", env=utf8_child_env())', True),
    # A match-pattern capture shadows the sanctioned name (3.10+).
    ("python child, sanctioned name captured by a match pattern",
     "match cfg:\n    case {\"env\": utf8_child_env}:\n        pass\n"
     + _PY.format(extra=", env=utf8_child_env()"), True),
    # A *splice or a variable in the OPTION REGION can hide -E/-I — the
    # options cannot be known, so the call fails closed; past the script
    # boundary (an opaque single value like str(path)) it is harmless.
    ("python child, starred args in the option region",
     'args = ["-E", "x.py"]\n'
     'subprocess.run([sys.executable, *args], text=True, encoding="utf-8",'
     " env=utf8_child_env())", True),
    ("python child, variable in the option region",
     'subprocess.run([sys.executable, flag, "x.py"], text=True,'
     ' encoding="utf-8", env=utf8_child_env())', True),
    # An OPAQUE expression is no script boundary: str(flag) can evaluate
    # to "-E" just as well as to a path (codex P2 r18) — only a literal
    # non-option or an explicit "--" proves where options end.
    ("opaque expression in the option region fails closed",
     'subprocess.run([sys.executable, str(script), *args], text=True,'
     ' encoding="utf-8", env=utf8_child_env())', True),
    ("opaque value can BE the env-ignoring flag",
     'flag = "-E"\n'
     'subprocess.run([sys.executable, str(flag), "x.py"], text=True,'
     ' encoding="utf-8", env=utf8_child_env())', True),
    ("explicit -- proves the boundary for a dynamic script",
     'subprocess.run([sys.executable, "--", str(script), *args],'
     ' text=True, encoding="utf-8", env=utf8_child_env())', False),
    ("literal script boundary keeps later opaque args harmless",
     'subprocess.run([sys.executable, "x.py", str(arg)], text=True,'
     ' encoding="utf-8", env=utf8_child_env())', False),
    # A DYNAMIC value for a value-taking option: the option set cannot be
    # known — unresolvable and refused, never an AttributeError crash.
    ("python child, dynamic separated -X value",
     'subprocess.run([sys.executable, "-E", "-X", mode, "x.py"], text=True,'
     ' encoding="utf-8", env=utf8_child_env())', True),
    # executable= OVERRIDES the program: argv[0] becomes display only.
    ("python argv but executable=node",
     'subprocess.run([sys.executable, "x"], executable="node", text=True,'
     ' encoding="utf-8", env=utf8_child_env())', True),
    ("node argv but executable=sys.executable",
     'subprocess.run(["node", "x"], executable=sys.executable, text=True,'
     ' encoding="utf-8", env=utf8_child_env())', False),
    ("executable from a variable is unresolvable",
     'subprocess.run([sys.executable, "x"], executable=exe, text=True,'
     ' encoding="utf-8", env=utf8_child_env())', True),
    # executable can arrive as Popen's THIRD positional just as well.
    ("positional executable overrides a python argv",
     'subprocess.Popen([sys.executable, "x"], -1, "node", text=True,'
     ' encoding="utf-8", env=utf8_child_env())', True),
    ("positional executable=sys.executable proves python",
     'subprocess.Popen(["node", "x"], -1, sys.executable, text=True,'
     ' encoding="utf-8", env=utf8_child_env())', False),
    ("literal executable=None is no override",
     'subprocess.run([sys.executable, "x"], executable=None, text=True,'
     ' encoding="utf-8", env=utf8_child_env())', False),
    ("positions past executable are unmodeled",
     'subprocess.Popen([sys.executable, "x"], -1, None, None, text=True,'
     ' encoding="utf-8", env=utf8_child_env())', True),
    # ...even with NO text keyword at all: universal_newlines is position
    # 11 and env position 10, so a long positional call is never provably
    # binary (codex P2 r17 on #410).
    ("positional universal_newlines with positional env",
     "subprocess.Popen([sys.executable, \"x\"], -1, None, None, None,"
     " None, None, True, False, None, utf8_child_env(), True)", True),
    ("four positionals look binary but fail closed",
     "subprocess.Popen([sys.executable, \"x\"], -1, None, None)", True),
    ("call-level splat can smuggle positional text flags",
     "subprocess.run(*cmd)", True),
    ("three positionals with no text keyword stay provably binary",
     'subprocess.Popen(["git", "log"], -1, None)', False),
    # a truthy shell= makes argv a shell command line, not a program vector
    ("list argv with shell=True even fully pinned",
     'subprocess.run([sys.executable, "x"], shell=True, text=True,'
     ' encoding="utf-8", env=utf8_child_env())', True),
    ("shell from a variable is unresolvable",
     'subprocess.run([sys.executable, "x"], shell=flag, text=True,'
     ' encoding="utf-8", env=utf8_child_env())', True),
    ("shell=False stays a program vector",
     'subprocess.run([sys.executable, "x"], shell=False, text=True,'
     ' encoding="utf-8", env=utf8_child_env())', False),
    # interpreter names match exactly — "py" the prefix proves nothing
    ("py-prefixed native tool even fully pinned",
     'subprocess.run(["pyright", "x"], text=True,'
     ' encoding="utf-8", env=utf8_child_env())', True),
    ("pytest is not an interpreter either",
     'subprocess.run(["pytest", "-q"], text=True,'
     ' encoding="utf-8", env=utf8_child_env())', True),
    ("versioned windows interpreter still proves python",
     'subprocess.run(["python3.12", "s.py"], text=True,'
     ' encoding="utf-8", env=utf8_child_env())', False),
    ("pyw launcher still proves python",
     'subprocess.run(["pyw", "s.py"], text=True,'
     ' encoding="utf-8", env=utf8_child_env())', False),
    # a competing / fallback import re-binds the sanctioned name
    ("python child, sanctioned name also imported elsewhere",
     "from fallback import utf8_child_env\n"
     + _PY.format(extra=", env=utf8_child_env()"), True),
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
    # ``X.executable`` proves python only when X is the imported sys module
    # — any object can expose an .executable naming a native binary.
    ("someone else's .executable even fully pinned",
     'subprocess.run([native_tool.executable, "x"], text=True,'
     ' encoding="utf-8", env=utf8_child_env())', True),
    ("aliased sys module still proves python",
     "import sys as _s\n"
     'subprocess.run([_s.executable, "x"], text=True, encoding="utf-8",'
     " env=utf8_child_env())", False),
    ("shadowed sys cannot prove python",
     "def f(sys):\n    "
     'subprocess.run([sys.executable, "x"], text=True, encoding="utf-8",'
     " env=utf8_child_env())", True),
    # --- interpreter naming / unknown programs fail closed ---
    ("py launcher", 'subprocess.run(["py", "s.py"], text=True, encoding="utf-8")',
     True),
    ("python3", 'subprocess.run(["python3", "s.py"], text=True, encoding="utf-8")',
     True),
    ("unknown program",
     'subprocess.run(["node", "s.js"], text=True, encoding="utf-8")', True),
    # PYTHONIOENCODING cannot control a NATIVE child's encoder, so the env
    # helper does not repair an unresolved child and must not excuse it.
    ("unknown program even with the sanctioned env",
     'subprocess.run(["node", "s.js"], text=True, encoding="utf-8",'
     " env=utf8_child_env())", True),
    ("shell=True command with the sanctioned env",
     'subprocess.run("git log", shell=True, text=True, encoding="utf-8",'
     " env=utf8_child_env())", True),
    ("variable argv even with the sanctioned env",
     "argv = [sys.executable, 'x']\n"
     'subprocess.run(argv, text=True, encoding="utf-8", env=utf8_child_env())',
     True),
    ("unresolvable argv expression",
     'subprocess.run(build(), text=True, encoding="utf-8")', True),
    ("argv from a variable is unresolvable",
     'argv = ["git", "log"]\nsubprocess.run(argv, text=True, encoding="utf-8")',
     True),
    # an ordinary assignment alias must not launder the module reference
    ("assigned alias spawns are recognized",
     "sp = subprocess\n"
     'sp.run(["git"], text=True)', True),
    ("alias-of-alias spawns are recognized",
     "sp = subprocess\nsp2 = sp\n"
     'sp2.run(["git"], text=True)', True),
    ("aliased spawn accepted when properly pinned",
     "sp = subprocess\n"
     'sp.run(["git", "log"], text=True, encoding="utf-8")', False),
    # ...and a module reference forwarded OUT of the file-local analysis
    # (call argument, attribute target) fails closed at the forwarding.
    ("module forwarded as a call argument fails closed",
     "helper(subprocess)", True),
    ("module stored onto an attribute fails closed",
     "obj.sp = subprocess", True),
    ("module attribute constants stay usable",
     'subprocess.run(["git", "log"], stdout=subprocess.PIPE, text=True,'
     ' encoding="utf-8")', False),
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
