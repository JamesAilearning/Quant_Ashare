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
  (b) an AST diff of the pre-move blob vs the new file(s): class /
      function decorator drift (lost OR added — a new ``@cache`` is a
      behavior change too), changed signatures (incl. keyword-only
      markers and defaults), lost defs/classes, and NEWLY-ADDED broad
      ``except`` handlers (bare / Exception / BaseException) — accounted
      PER ENCLOSING SCOPE, so relocating a catch-all between functions
      is drift, not a wash.

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


def _is_broad_expr(expr: ast.expr) -> bool:
    """Exception / BaseException, bare or ANYWHERE inside a (nested)
    tuple — ``except (ValueError, Exception):`` is a catch-all too
    (codex #364 r8 P2)."""
    if isinstance(expr, ast.Name):
        return expr.id in ("Exception", "BaseException")
    if isinstance(expr, ast.Tuple):
        return any(_is_broad_expr(e) for e in expr.elts)
    return False


def _is_broad_handler(node: ast.ExceptHandler) -> bool:
    return node.type is None or _is_broad_expr(node.type)


def _broad_excepts_by_scope(tree: ast.AST) -> Counter[str]:
    """{enclosing def/class qualname (or "<module>"): broad-handler
    count}. Per-SCOPE accounting (codex #364 r7 P2): an aggregate count
    certifies a swap — `alpha` Exception->TypeError while `beta` goes the
    opposite way keeps the total constant even though `beta` gained a new
    catch-all. Scope names mirror ``_qualified_defs`` qualnames."""
    out: Counter[str] = Counter()

    def walk(node: ast.AST, prefix: str, scope: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.ClassDef)):
                qual = f"{prefix}{child.name}"
                walk(child, f"{qual}.", qual)
            else:
                if (isinstance(child, ast.ExceptHandler)
                        and _is_broad_handler(child)):
                    out[scope] += 1
                walk(child, prefix, scope)

    walk(tree, "", "<module>")
    return out


def compare_module_texts(old_text: str,
                         new_texts: str | list[str],
                         base_texts: list[str | None] | None = None,
                         ) -> list[str]:
    """AST drift findings between the pre-move module and the new file(s).

    Destinations are parsed INDEPENDENTLY and their definitions merged
    (codex #364 r4 P2: concatenating them breaks on a second
    ``from __future__ import annotations``, refusing a perfectly clean
    split). Duplicate qualnames across destinations keep the first
    occurrence (deterministic input order).

    ``base_texts`` aligns with ``new_texts``: the pre-move version of a
    MODIFIED merge destination (None for fresh files). Its broad-except
    handlers are subtracted per enclosing scope, so a merge target's own
    pre-existing handlers do not read as newly added while a genuinely
    new one still does (codex #364 r6). Empty list = clean."""
    if isinstance(new_texts, str):
        new_texts = [new_texts]
    if base_texts is None:
        base_texts = [None] * len(new_texts)
    if len(base_texts) != len(new_texts):
        raise VerifyError("base_texts must align 1:1 with new_texts.")
    try:
        old_tree = ast.parse(old_text)
        new_trees = [ast.parse(t) for t in new_texts]
        base_trees = [ast.parse(b) if b is not None else None
                      for b in base_texts]
    except SyntaxError as exc:
        raise VerifyError(f"cannot parse for AST diff: {exc}") from exc
    old_defs = _qualified_defs(old_tree)
    new_defs: dict[str, ast.AST] = {}
    for tree in new_trees:
        for qual, n in _qualified_defs(tree).items():
            new_defs.setdefault(qual, n)
    findings: list[str] = []
    for qual, old_node in sorted(old_defs.items()):
        new_node = new_defs.get(qual)
        if new_node is None:
            findings.append(f"LOST def/class: {qual}")
            continue
        # exact-list match, order included (codex #364 r5 P1: an ADDED
        # decorator such as @cache changes behavior just like a lost one,
        # and decorator application order matters).
        old_dec, new_dec = _decorators(old_node), _decorators(new_node)
        if old_dec != new_dec:
            findings.append(f"DECORATOR drift on {qual}: "
                            f"{old_dec!r} -> {new_dec!r}")
        old_sig, new_sig = _signature(old_node), _signature(new_node)
        if old_sig is not None and new_sig is not None and old_sig != new_sig:
            findings.append(f"SIGNATURE changed on {qual}: "
                            f"{old_sig!r} -> {new_sig!r}")
    old_scoped = _broad_excepts_by_scope(old_tree)
    new_scoped: Counter[str] = Counter()
    for t, b in zip(new_trees, base_trees, strict=True):
        new_scoped.update(_broad_excepts_by_scope(t))
        if b is not None:
            new_scoped.subtract(_broad_excepts_by_scope(b))
    for scope in sorted(set(old_scoped) | set(new_scoped)):
        if new_scoped[scope] > old_scoped[scope]:
            findings.append(
                f"NEW broad except handler(s) in {scope}: "
                f"{old_scoped[scope]} -> {new_scoped[scope]} "
                f"(bare/Exception/BaseException)")
    return findings


# Split detection (codex #364 r3 P1): a 1->3+ split leaves every
# destination below git's -M50% similarity, so rename detection alone
# silently certifies exactly the scenario this gate exists for. A deleted
# module whose filtered lines reappear across ADDED files (>= coverage
# threshold) is treated as a split and VERIFIED against their union;
# below the threshold it is a genuine deletion (not a move — reported,
# not failed).
SPLIT_COVERAGE_THRESHOLD = 0.5


def find_split_destinations(old_text: str | Counter[str],
                            added: dict[str, str],
                            min_coverage: float = SPLIT_COVERAGE_THRESHOLD,
                            ) -> list[str]:
    """Added-file names whose filtered lines overlap the deleted module,
    when their UNION covers >= ``min_coverage`` of it; else [] (genuine
    deletion). Accepts either the module text or a precomputed filtered
    Counter (the rename-RESIDUAL case, codex #364 r5 P1). Per-file noise
    floor: >= 2 overlapping lines (a real extracted helper can be that
    small; under-matching is still LOUD — the missed destination's lines
    surface as ONLY-IN-OLD drift — but auto-matching verifies the split
    without operator intervention)."""
    old_lines = (old_text if isinstance(old_text, Counter)
                 else filtered_lines(old_text))
    total = sum(old_lines.values())
    if total == 0:
        return []
    candidates: list[str] = []
    for name, text in sorted(added.items()):
        overlap = sum((old_lines & filtered_lines(text)).values())
        if overlap >= 2:
            candidates.append(name)
    if not candidates:
        return []
    union: Counter[str] = Counter()
    for name in candidates:
        union.update(filtered_lines(added[name]))
    coverage = sum((old_lines & union).values()) / total
    return candidates if coverage >= min_coverage else []


def _git(*args: str) -> str:
    out = subprocess.run(["git", "-C", str(_REPO), *args],
                         capture_output=True, text=True, check=True)
    return out.stdout


def _detect_renames(base: str) -> tuple[list[tuple[str, str]],
                                        list[str], list[str], list[str]]:
    """(rename pairs, deleted .py paths, ADDED .py paths, MODIFIED .py
    paths) vs ``base``. Destination candidates cover both added and
    modified files — an extract/merge move (``D old.py`` +
    ``M existing.py``) must be matched too (codex #364 r4 P2) — and the
    two kinds are kept apart so each MODIFIED destination gets its own
    base version subtracted during verification (codex #364 r6 P1)."""
    raw = _git("diff", "--name-status", "-M50%", base, "HEAD")
    pairs: list[tuple[str, str]] = []
    deleted: list[str] = []
    added: list[str] = []
    modified: list[str] = []
    for line in raw.splitlines():
        parts = line.split("\t")
        if not parts:
            continue
        if parts[0].startswith("R") and len(parts) == 3:
            pairs.append((parts[1], parts[2]))
        elif parts[0] == "D" and len(parts) == 2 and parts[1].endswith(".py"):
            deleted.append(parts[1])
        elif parts[0] == "A" and len(parts) == 2 and parts[1].endswith(".py"):
            added.append(parts[1])
        elif parts[0] == "M" and len(parts) == 2 and parts[1].endswith(".py"):
            modified.append(parts[1])
    return pairs, deleted, added, modified


def _verify_one(label: str, old_text: str,
                dests: list[tuple[str, str | None]]) -> int:
    """Each destination is ``(new_text, base_text)``: ``base_text`` is
    None for a FRESH file (added / rename target) and the destination's
    own pre-move version for a MODIFIED module (merge). A merge base's
    lines and broad handlers are SUBTRACTED from that destination's
    contribution instead of tolerated wholesale, so the whole-file proof
    stays STRICT for every form (codex #364 r6 P1 — an all-or-nothing
    tolerance let a mixed A+M union smuggle additions into the fresh
    file): exactly the delta added across destinations must reconstruct
    the old module, nothing more, nothing less."""
    old_lines = filtered_lines(old_text)
    contrib: Counter[str] = Counter()
    for new_text, base_text in dests:
        c = filtered_lines(new_text)
        if base_text is not None:
            c = c - filtered_lines(base_text)
        contrib.update(c)
    only_old = sorted((old_lines - contrib).elements())
    only_new = sorted((contrib - old_lines).elements())
    ast_findings = compare_module_texts(old_text,
                                        [t for t, _ in dests],
                                        base_texts=[b for _, b in dests])
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
        # manual mode treats every destination as FRESH (strict whole-
        # file proof) — the documented split usage.
        rc |= _verify_one(f"{args.old} -> {args.new}", old_text,
                          [(t, None) for t in new_texts])
    else:
        pairs, deleted, added, modified = _detect_renames(args.base)
        if not pairs and not deleted:
            print(f"no rename-detected or deleted .py files vs {args.base}; "
                  "nothing to verify.")
            return 0
        added_set = set(added)
        dest_texts = {p: (_REPO / p).read_text(encoding="utf-8")
                      for p in [*added, *modified] if (_REPO / p).is_file()}
        # each MODIFIED destination's own base version is subtracted
        # during verification (codex #364 r6 P1) — fresh files get None.
        base_of = {p: _git("show", f"{args.base}:{p}")
                   for p in modified if p in dest_texts}
        for old_path, new_path in pairs:
            if not new_path.endswith(".py"):
                continue
            old_text = _git("show", f"{args.base}:{old_path}")
            new_text = (_REPO / new_path).read_text(encoding="utf-8")
            # rename+extract (codex #364 r5 P1): git emits `R old.py
            # main.py` + `A helpers.py` when ONE destination clears the
            # -M50% bar — the residual the rename target does not cover
            # must be matched against the other destinations, or a clean
            # split fails as ONLY-IN-OLD (and never reaches the
            # deleted-file handling below).
            residual = filtered_lines(old_text) - filtered_lines(new_text)
            extras = (find_split_destinations(residual, dest_texts)
                      if residual else [])
            if extras:
                kind = ("rename+extract"
                        if all(d in added_set for d in extras)
                        else "rename+merge")
                print(f"({kind} detected: {old_path} -> "
                      f"{[new_path, *extras]})")
                rc |= _verify_one(
                    f"{old_path} -> {[new_path, *extras]} [{kind}]",
                    old_text,
                    [(new_text, None),
                     *((dest_texts[d], base_of.get(d)) for d in extras)])
            else:
                rc |= _verify_one(f"{old_path} -> {new_path}", old_text,
                                  [(new_text, None)])
        # splits fall below -M50% per destination (codex #364 r3 P1):
        # match each DELETED module's filtered lines against ADDED and
        # MODIFIED files (extract/merge moves land in existing modules —
        # codex #364 r4 P2) and verify the reconstructed move; low
        # overlap = genuine deletion (reported, not a move, not failed).
        # The proof stays strict for every form — merge destinations get
        # their own base subtracted, never blanket line tolerance
        # (codex #364 r6 P1).
        for old_path in deleted:
            old_text = _git("show", f"{args.base}:{old_path}")
            dests = find_split_destinations(old_text, dest_texts)
            if dests:
                kind = ("split" if all(d in added_set for d in dests)
                        else "split/merge")
                print(f"({kind} detected: {old_path} -> {dests})")
                rc |= _verify_one(f"{old_path} -> {dests} [{kind}]",
                                  old_text,
                                  [(dest_texts[d], base_of.get(d))
                                   for d in dests])
            else:
                print(f"(deleted, no split destinations found: {old_path} "
                      "— genuine deletion, not verified as a move)")
    if rc:
        print("\nDRIFT FOUND: revert each line/finding to the pre-move form "
              "or justify it explicitly in the PR body (AGENTS.md).")
    else:
        print("\nmechanical move VERIFIED clean — paste this output into "
              "the PR body as proof.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
