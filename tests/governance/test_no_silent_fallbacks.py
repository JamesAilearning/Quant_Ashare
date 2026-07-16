"""Governance: no silent fallbacks in ``src/core`` + ``src/data``
(hardening backlog #4; AGENTS.md "No silent fallback").

The recurring defect: ``except ...: return {} / None / [] / () / ""`` —
an error path that hands the caller an empty-but-plausible value instead
of raising, so corruption flows downstream as "no data". This test AST-
scans every ``except`` handler in the two canonical packages and flags a
``return`` of an EMPTY literal (or bare ``return``/``return None``)
inside the handler, unless the handler carries an explicit escape hatch:

    except FooError:  # fallback-ok: <why an empty result is CORRECT here>
        return []

or the ``return`` line itself carries the marker. The marker forces the
justification to live next to the code, reviewable in every diff.

Scope note (v1, deliberate): the backlog also names "warn-and-continue on
unknown config keys" — that pattern needs semantic context (which loop,
which keys) and is left to LLM review per the backlog's "keep as judgment
review" section; automating it here would be false-positive soup.
"""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_SCAN_ROOTS = ("src/core", "src/data")
_MARKER = "fallback-ok:"


def _is_empty_literal(node: ast.expr | None) -> bool:
    if node is None:
        return True  # bare ``return`` inside except
    if isinstance(node, ast.Constant) and node.value in (None, "", b""):
        return True
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)) and not node.elts:
        return True
    if isinstance(node, ast.Dict) and not node.keys:
        return True
    return False


def _line_has_marker(lines: list[str], lineno: int) -> bool:
    return 0 < lineno <= len(lines) and _MARKER in lines[lineno - 1]


def find_silent_fallbacks(text: str, rel: str) -> list[str]:
    """Offending ``<file>:<line> return <literal>`` strings for one module."""
    tree = ast.parse(text)
    lines = text.splitlines()
    out: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if _line_has_marker(lines, node.lineno):
            continue  # the whole handler is justified
        # Walk ONLY this handler's own scope: a NESTED ExceptHandler owns
        # its own marker and is visited separately by the outer ast.walk —
        # descending into it here would demand a duplicated marker on the
        # return line (codex #364 r2 P2).
        stack: list[ast.AST] = list(node.body)
        while stack:
            sub = stack.pop()
            if isinstance(sub, ast.ExceptHandler):
                continue  # nested handler: judged on its own
            if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef,
                                ast.ClassDef, ast.Lambda)):
                # a helper DEFINED inside the handler has its own return
                # semantics — its `return None` is not the handler's
                # fallback (codex #364 r3 P2).
                continue
            if isinstance(sub, ast.Return):
                if (_is_empty_literal(sub.value)
                        and not _line_has_marker(lines, sub.lineno)):
                    snippet = (lines[sub.lineno - 1].strip()
                               if sub.lineno <= len(lines)
                               else "return <empty>")
                    out.append(f"{rel}:{sub.lineno} {snippet}")
                continue
            stack.extend(ast.iter_child_nodes(sub))
    return out


class NoSilentFallbackTests(unittest.TestCase):
    def test_no_empty_return_inside_except_without_marker(self) -> None:
        offenders: list[str] = []
        for root in _SCAN_ROOTS:
            for py in sorted((_ROOT / root).rglob("*.py")):
                rel = py.relative_to(_ROOT).as_posix()
                offenders.extend(
                    find_silent_fallbacks(py.read_text(encoding="utf-8"),
                                          rel))
        self.assertEqual(
            offenders, [],
            msg=(
                "Silent fallback(s): an except handler returns an empty "
                "value instead of raising — corruption flows downstream as "
                "'no data' (AGENTS.md 'No silent fallback'):\n  "
                + "\n  ".join(offenders)
                + "\n\nEither raise a domain error, or — when an empty "
                "result is genuinely CORRECT — annotate the handler or the "
                "return line with '# fallback-ok: <reason>'."
            ),
        )


class ScannerUnitTests(unittest.TestCase):
    """Guard the scanner itself so a refactor cannot blind the gate."""

    def test_flags_empty_dict_return_in_except(self) -> None:
        code = ("def f():\n"
                "    try:\n"
                "        return work()\n"
                "    except ValueError:\n"
                "        return {}\n")
        hits = find_silent_fallbacks(code, "x.py")
        self.assertEqual(len(hits), 1)
        self.assertIn("x.py:5", hits[0])

    def test_flags_bare_return_and_none_and_empty_list(self) -> None:
        code = ("def f():\n"
                "    try:\n"
                "        pass\n"
                "    except Exception:\n"
                "        return\n"
                "def g():\n"
                "    try:\n"
                "        pass\n"
                "    except Exception:\n"
                "        return None\n"
                "def h():\n"
                "    try:\n"
                "        pass\n"
                "    except Exception:\n"
                "        return []\n")
        self.assertEqual(len(find_silent_fallbacks(code, "x.py")), 3)

    def test_marker_on_handler_or_return_line_exempts(self) -> None:
        code = ("def f():\n"
                "    try:\n"
                "        pass\n"
                "    except KeyError:  # fallback-ok: cache miss = empty\n"
                "        return {}\n"
                "def g():\n"
                "    try:\n"
                "        pass\n"
                "    except KeyError:\n"
                "        return []  # fallback-ok: absent = none found\n")
        self.assertEqual(find_silent_fallbacks(code, "x.py"), [])

    def test_nested_handler_marker_is_honored(self) -> None:
        # codex #364 r2: the inner handler's own marker must suffice — the
        # outer (unmarked) handler must not re-flag the inner return.
        code = ("def f():\n"
                "    try:\n"
                "        pass\n"
                "    except ValueError:\n"
                "        try:\n"
                "            pass\n"
                "        except KeyError:  # fallback-ok: cache miss\n"
                "            return {}\n"
                "        raise\n")
        self.assertEqual(find_silent_fallbacks(code, "x.py"), [])

    def test_nested_unmarked_handler_flagged_exactly_once(self) -> None:
        code = ("def f():\n"
                "    try:\n"
                "        pass\n"
                "    except ValueError:\n"
                "        try:\n"
                "            pass\n"
                "        except KeyError:\n"
                "            return {}\n"
                "        raise\n")
        hits = find_silent_fallbacks(code, "x.py")
        self.assertEqual(len(hits), 1)
        self.assertIn("x.py:8", hits[0])

    def test_helper_defined_inside_handler_is_not_the_handlers_fallback(
            self) -> None:
        # codex #364 r3 P2: a nested def's own `return None` must not be
        # attributed to the enclosing handler; the handler's OWN empty
        # return is still flagged.
        code = ("def f():\n"
                "    try:\n"
                "        pass\n"
                "    except ValueError:\n"
                "        def cb(x):\n"
                "            return None\n"
                "        raise\n")
        self.assertEqual(find_silent_fallbacks(code, "x.py"), [])
        code2 = ("def f():\n"
                 "    try:\n"
                 "        pass\n"
                 "    except ValueError:\n"
                 "        def cb(x):\n"
                 "            return None\n"
                 "        return {}\n")
        hits = find_silent_fallbacks(code2, "x.py")
        self.assertEqual(len(hits), 1)
        self.assertIn("x.py:7", hits[0])

    def test_non_empty_return_in_except_is_fine(self) -> None:
        code = ("def f():\n"
                "    try:\n"
                "        pass\n"
                "    except ValueError as exc:\n"
                "        return {'error': str(exc)}\n")
        self.assertEqual(find_silent_fallbacks(code, "x.py"), [])


if __name__ == "__main__":
    unittest.main()
