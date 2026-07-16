"""Mechanical-move drift detector (hardening backlog #1, top ROI).

A "split / rename / extract" PR promises ZERO behavior change, and a green
test suite is necessary but NOT sufficient proof (AGENTS.md: lost
``@dataclass(frozen=True)`` / ``@classmethod`` decorators, dropped WARNING
logs, quietly-added ``except Exception``, swapped keyword-only markers all
pass unchanged tests — walk_forward.py needed FIVE hotfix rounds,
provider_bundle.py broke 17 tests). This script makes the AGENTS.md
verification deterministic and CI-gateable:

  (a) the prescribed WHOLE-FILE filtered content diff (blank / comment /
      pure-docstring-row / import lines removed, remaining lines compared
      as multisets) — decorator rows survive the filter, so a lost
      ``@dataclass`` shows up even though it sits above the class header;
  (b) an AST diff of the pre-move blob vs the new file(s): lost class /
      function decorators, changed signatures (incl. keyword-only
      markers and defaults), lost defs/classes, and NEWLY-ADDED broad
      ``except`` handlers (bare / Exception / BaseException).

Usage — auto mode (rename-detected files against a base ref):

    python scripts/verify_mechanical_move.py --base origin/main

Usage — split mode (one old blob fanned out into several new files):

    python scripts/verify_mechanical_move.py \\
        --old origin/main:src/core/walk_forward.py \\
        --new src/core/walk_forward/engine.py src/core/walk_forward/config.py

Exit 0 = clean mechanical move (paste the printed proof into the PR body);
exit 1 = drift findings (revert them or justify each in the PR body).
"""
from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]

# AGENTS.md filter: blanks, comment rows, pure docstring rows, import rows.
_FILTER = re.compile(r'^(\s*$|\s*#|\s*"""|import |from )')


class VerifyError(RuntimeError):
    """Fail-loud: refuse to certify what cannot be parsed/compared."""


def filtered_lines(text: str) -> Counter[str]:
    """The AGENTS.md whole-file filter, as a line MULTISET (sorted-diff
    equivalent): every functional line survives, including decorator rows."""
    return Counter(line for line in text.splitlines()
                   if not _FILTER.match(line))


def content_diff(old_text: str, new_texts: list[str]) -> tuple[list[str], list[str]]:
    """(lines only in OLD, lines only in NEW) after the filter — the
    AGENTS.md proof. Non-empty either side = behavior/contract drift to
    revert or justify."""
    old = filtered_lines(old_text)
    new: Counter[str] = Counter()
    for t in new_texts:
        new.update(filtered_lines(t))
    only_old = sorted((old - new).elements())
    only_new = sorted((new - old).elements())
    return only_old, only_new


def _qualified_defs(tree: ast.AST) -> dict[str, ast.AST]:
    """{qualname: node} for every class / function, nested included."""
    out: dict[str, ast.AST] = {}

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.ClassDef)):
                qual = f"{prefix}{child.name}"
                out[qual] = child
                walk(child, f"{qual}.")
            else:
                walk(child, prefix)

    walk(tree, "")
    return out


def _decorators(node: ast.AST) -> list[str]:
    return [ast.unparse(d) for d in getattr(node, "decorator_list", [])]


def _signature(node: ast.AST) -> str | None:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return ast.unparse(node.args)
    return None


def _broad_except_count(tree: ast.AST) -> int:
    n = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if node.type is None:
                n += 1
            elif isinstance(node.type, ast.Name) and node.type.id in (
                    "Exception", "BaseException"):
                n += 1
    return n


def compare_module_texts(old_text: str, new_concat: str) -> list[str]:
    """AST drift findings between the pre-move module and the concatenated
    new file(s). Empty list = mechanically clean at the AST level."""
    try:
        old_tree = ast.parse(old_text)
        new_tree = ast.parse(new_concat)
    except SyntaxError as exc:
        raise VerifyError(f"cannot parse for AST diff: {exc}") from exc
    old_defs = _qualified_defs(old_tree)
    new_defs = _qualified_defs(new_tree)
    findings: list[str] = []
    for qual, old_node in sorted(old_defs.items()):
        new_node = new_defs.get(qual)
        if new_node is None:
            findings.append(f"LOST def/class: {qual}")
            continue
        lost_dec = [d for d in _decorators(old_node)
                    if d not in _decorators(new_node)]
        if lost_dec:
            findings.append(f"LOST decorator(s) on {qual}: {lost_dec}")
        old_sig, new_sig = _signature(old_node), _signature(new_node)
        if old_sig is not None and new_sig is not None and old_sig != new_sig:
            findings.append(f"SIGNATURE changed on {qual}: "
                            f"{old_sig!r} -> {new_sig!r}")
    old_broad = _broad_except_count(old_tree)
    new_broad = _broad_except_count(new_tree)
    if new_broad > old_broad:
        findings.append(f"NEW broad except handler(s): {old_broad} -> "
                        f"{new_broad} (bare/Exception/BaseException)")
    return findings


def _git(*args: str) -> str:
    out = subprocess.run(["git", "-C", str(_REPO), *args],
                         capture_output=True, text=True, check=True)
    return out.stdout


def _detect_renames(base: str) -> list[tuple[str, str]]:
    """[(old_path, new_path)] for rename-detected files vs ``base``."""
    raw = _git("diff", "--name-status", "-M50%", base, "HEAD")
    pairs: list[tuple[str, str]] = []
    for line in raw.splitlines():
        parts = line.split("\t")
        if parts and parts[0].startswith("R") and len(parts) == 3:
            pairs.append((parts[1], parts[2]))
    return pairs


def _verify_one(label: str, old_text: str, new_texts: list[str]) -> int:
    only_old, only_new = content_diff(old_text, new_texts)
    ast_findings = compare_module_texts(old_text, "\n".join(new_texts))
    print(f"=== {label} ===")
    if not only_old and not only_new and not ast_findings:
        print("  no diff (mechanically clean)")
        return 0
    for line in only_old:
        print(f"  ONLY-IN-OLD: {line}")
    for line in only_new:
        print(f"  ONLY-IN-NEW: {line}")
    for f in ast_findings:
        print(f"  AST: {f}")
    return 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base", default="origin/main",
                   help="base ref for auto rename detection.")
    p.add_argument("--old", default=None, metavar="REF:PATH",
                   help="split mode: the pre-move blob.")
    p.add_argument("--new", nargs="*", default=None,
                   help="split mode: the new file path(s).")
    args = p.parse_args(argv)

    rc = 0
    if args.old:
        if not args.new:
            raise VerifyError("--old requires --new file(s).")
        ref, _, path = args.old.partition(":")
        if not ref or not path:
            raise VerifyError(f"--old must be REF:PATH; got {args.old!r}.")
        old_text = _git("show", args.old)
        new_texts = [Path(_REPO / n).read_text(encoding="utf-8")
                     for n in args.new]
        rc |= _verify_one(f"{args.old} -> {args.new}", old_text, new_texts)
    else:
        pairs = _detect_renames(args.base)
        if not pairs:
            print(f"no rename-detected files vs {args.base}; nothing to "
                  "verify (splits need --old/--new).")
            return 0
        for old_path, new_path in pairs:
            if not new_path.endswith(".py"):
                continue
            old_text = _git("show", f"{args.base}:{old_path}")
            new_text = (_REPO / new_path).read_text(encoding="utf-8")
            rc |= _verify_one(f"{old_path} -> {new_path}", old_text,
                              [new_text])
    if rc:
        print("\nDRIFT FOUND: revert each line/finding to the pre-move form "
              "or justify it explicitly in the PR body (AGENTS.md).")
    else:
        print("\nmechanical move VERIFIED clean — paste this output into "
              "the PR body as proof.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
