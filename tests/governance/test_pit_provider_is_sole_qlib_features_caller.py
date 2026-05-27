"""Governance: ``PITDataProvider`` is the sole intended caller of
``qlib.data.D.features`` inside ``src/``.

Why this guard exists
---------------------
Direct ``D.features(...)`` calls bypass the §4.3.2 post-delist mask
that lives inside ``src/pit/query.py:_mask_post_delist``. The mask
sets every ``(ticker, date)`` position past ``delist_date`` to NaN —
qlib's default operator semantics let stale / forward-filled values
slip through window operators (e.g. ``Mean($close, 20)``), so any
non-PIT consumer that reads close prices can silently absorb
post-delist data into IC, attribution, or backtest baselines.

Three orthogonal protections layered together
---------------------------------------------
A NEW direct caller must fail at LEAST one of these three tests, so
silently slipping a bypass past CI requires defeating all three:

1. **File allowlist (``PIT_FEATURES_BYPASS_ALLOWLIST``)**: a file
   not on the list calling ``D.features(...)`` fails
   ``test_no_unexpected_callers_outside_allowlist``. The list also
   carries per-file exact counts, so removing the approved call
   without removing the entry (= stale allowlist) fails
   ``test_no_stale_allowlist_entries``.

2. **Per-call marker (``pit-bypass-ok``)**: every approved call must
   live inside a function whose source span contains the literal
   string ``pit-bypass-ok`` (in a comment, docstring, or string).
   A refactor that swaps the reviewed call with a different
   direct-qlib read inside the SAME file would pass the count check
   but fail ``test_every_call_has_pit_bypass_ok_marker_in_enclosing_function``
   if the new call lands in a function that has no marker. Codex
   P2 follow-up on PR #177.

3. **Alias-aware scanner**: the scanner counts ``D.features(...)``
   calls whose receiver resolves to ``qlib.data.D`` — even when the
   import was aliased (``from qlib.data import D as QD; QD.features``)
   or accessed through the dotted chain (``import qlib.data;
   qlib.data.D.features``). The pre-#177-followup version only
   matched the literal name ``D``, so any of those alias patterns
   would have silently bypassed the guard. Codex P2 follow-up.

How to add a new entry
----------------------
1. Add a WARN log next to the new ``D.features(...)`` call referencing
   ``Audit P0-6``. Mirror the WARN copy in
   ``backtest_runner._compute_equalweight_baseline``.
2. Add the file to ``PIT_FEATURES_BYPASS_ALLOWLIST`` with a
   justification comment and the exact call count.
3. Add the literal string ``pit-bypass-ok`` somewhere inside each
   enclosing function (a comment is fine). One marker per function
   covers all calls within that function.

Audit P0-6.
"""

from __future__ import annotations

import ast
import unittest
from dataclasses import dataclass
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Marker substring that must appear in the enclosing function source
# for every approved ``D.features(...)`` call.
_BYPASS_MARKER = "pit-bypass-ok"


# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------
# Each entry: ``relative-path-from-repo-root: expected-D.features-count``.
# The justification belongs in the comment block above each entry —
# keep it as code-adjacent prose so a future contributor reading the
# allowlist knows why the bypass is OK without digging through git
# history.
PIT_FEATURES_BYPASS_ALLOWLIST: dict[str, int] = {
    # PITDataProvider is the PIT layer itself. ``_fetch_qlib_features``
    # MUST call D.features to get raw bytes that ``_mask_post_delist``
    # then masks. Routing this call through ``PITDataProvider`` would
    # be infinite recursion.
    "src/pit/query.py": 1,

    # ``pit_validator`` validates the on-disk bin contents BEFORE the
    # post-delist mask is applied. Routing through ``PITDataProvider``
    # would apply the very mask the validator is checking for, and
    # the validator would tautologically "pass". The validator MUST
    # bypass PIT.
    "src/data/pit/pit_validator.py": 5,

    # ``benchmark_artifact_publisher`` is a PUBLISHER — it fetches
    # benchmark index closes (e.g. SH000300) to write to a CSV
    # artifact. It does not consume features for downstream
    # computation, and the post-delist mask is irrelevant for an
    # index that does not delist.
    "src/data/benchmark_artifact_publisher.py": 1,

    # Has ``pit_provider`` opt-in. The ``D.features`` call only fires
    # when the caller did NOT pass a provider; a WARN log
    # ("Audit P0-6") makes the bypass observable. TODO listed in the
    # source: thread ``pit_provider`` through ``Pipeline`` /
    # ``WalkForwardEngine`` and retire this legacy branch.
    "src/core/backtest_runner.py": 1,

    # Same pattern as ``backtest_runner``: ``pit_provider`` opt-in
    # plus WARN-on-fallback. Same TODO follow-up.
    "src/core/factor_analyzer.py": 1,

    # No ``pit_provider`` opt-in yet — the public ``analyze`` method
    # would need a contract change. WARN log added at the call site
    # so operators reading attribution reports see the bypass on
    # every run. TODO(P0-6 follow-up): thread ``pit_provider``
    # through ``PerformanceAttribution.analyze``.
    "src/core/performance_attribution.py": 1,

    # Same as ``performance_attribution`` — no opt-in yet, WARN-only.
    # TODO(P0-6 follow-up): thread ``pit_provider`` through
    # ``SignalAnalyzer.analyze``.
    "src/core/signal_analyzer.py": 1,
}


# ---------------------------------------------------------------------------
# Scanner — alias-aware, marker-aware
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _CallSite:
    """One direct ``D.features(...)`` call discovered in the AST."""

    lineno: int
    enclosing_function_name: str
    enclosing_function_lineno: int
    enclosing_function_end_lineno: int


def _is_qlib_data_d_chain(node: ast.AST) -> bool:
    """True iff ``node`` is the AST expression ``qlib.data.D``.

    Catches the explicit-chain bypass attempt
    ``import qlib; qlib.data.D.features(...)`` — the canonical form
    ``from qlib.data import D`` is detected via the alias map instead.
    """
    if not (isinstance(node, ast.Attribute) and node.attr == "D"):
        return False
    if not (isinstance(node.value, ast.Attribute) and node.value.attr == "data"):
        return False
    if not (isinstance(node.value.value, ast.Name) and node.value.value.id == "qlib"):
        return False
    return True


def _collect_qlib_d_aliases(tree: ast.AST) -> set[str]:
    """Return all local names that bind to ``qlib.data.D`` in this file.

    Covers:

    * ``from qlib.data import D``                    → adds ``"D"``
    * ``from qlib.data import D as QD``              → adds ``"QD"``
    * ``from qlib.data import D as QD, anything``    → adds ``"QD"``

    Does NOT cover (yet, by design — separate ``import qlib.data``
    detection handles those):

    * ``import qlib.data`` + ``qlib.data.D.features(...)``
      — caught via :func:`_is_qlib_data_d_chain` on the call's
      receiver expression.
    * Local re-assignments like ``my_d = D; my_d.features(...)``
      — a deliberate re-bind that loses static traceability is rare
      and would already require code-review attention.
    """
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "qlib.data":
            for alias in node.names:
                if alias.name == "D":
                    aliases.add(alias.asname or alias.name)
    return aliases


def _enclosing_function(
    tree: ast.AST, call_node: ast.Call,
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Return the innermost function definition that contains
    ``call_node``, or ``None`` if the call is at module scope.
    """
    best: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    target_line = call_node.lineno
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.end_lineno is None:
            continue
        if node.lineno <= target_line <= node.end_lineno:
            # Innermost = highest start lineno among enclosing scopes.
            if best is None or node.lineno > best.lineno:
                best = node
    return best


def _find_d_features_call_sites(text: str) -> list[_CallSite]:
    """Return every direct ``D.features(...)`` call site in *text*,
    with its enclosing-function metadata.

    Matches calls whose receiver expression is either:

    * a ``Name`` whose id is in the file's qlib.data.D alias set, OR
    * the literal AST chain ``qlib.data.D`` (per
      :func:`_is_qlib_data_d_chain`).
    """
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:  # pragma: no cover - all src/ must parse
        raise AssertionError(f"src/ file failed to AST-parse: {exc}") from exc

    aliases = _collect_qlib_d_aliases(tree)
    sites: list[_CallSite] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "features"):
            continue
        receiver = func.value
        is_match = False
        if isinstance(receiver, ast.Name) and receiver.id in aliases:
            is_match = True
        elif _is_qlib_data_d_chain(receiver):
            is_match = True
        if not is_match:
            continue
        enclosing = _enclosing_function(tree, node)
        sites.append(_CallSite(
            lineno=node.lineno,
            enclosing_function_name=enclosing.name if enclosing else "<module>",
            enclosing_function_lineno=enclosing.lineno if enclosing else -1,
            enclosing_function_end_lineno=(
                enclosing.end_lineno
                if enclosing and enclosing.end_lineno is not None
                else -1
            ),
        ))
    return sites


def _enclosing_function_source(text: str, site: _CallSite) -> str:
    """Return the source text of the function enclosing *site*, or
    empty string if the call was at module scope.
    """
    if site.enclosing_function_lineno < 0:
        return ""
    lines = text.splitlines()
    start = site.enclosing_function_lineno - 1
    end = site.enclosing_function_end_lineno  # end_lineno is inclusive
    return "\n".join(lines[start:end])


def _scan_src() -> dict[str, list[_CallSite]]:
    """Return ``{relative-posix-path: [_CallSite, ...]}`` for every
    ``src/`` file with at least one direct ``D.features(...)`` call.
    """
    out: dict[str, list[_CallSite]] = {}
    src_root = _PROJECT_ROOT / "src"
    for py in sorted(src_root.rglob("*.py")):
        text = py.read_text(encoding="utf-8")
        sites = _find_d_features_call_sites(text)
        if sites:
            rel = py.relative_to(_PROJECT_ROOT).as_posix()
            out[rel] = sites
    return out


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class PitProviderIsSoleQlibFeaturesCallerTests(unittest.TestCase):
    """Four orthogonal checks. Splitting them keeps the failure
    message specific: a regression in one dimension does not muddy
    the others."""

    def test_no_unexpected_callers_outside_allowlist(self) -> None:
        actual = _scan_src()
        unexpected = sorted(set(actual) - set(PIT_FEATURES_BYPASS_ALLOWLIST))
        msg = (
            "New file(s) call ``D.features(...)`` directly without "
            "being in PIT_FEATURES_BYPASS_ALLOWLIST:\n  "
            + "\n  ".join(unexpected)
            + "\n\nEither route the call through "
            "PITDataProvider.get_features, or add the file to the "
            "allowlist with a justification + WARN log + "
            f"``{_BYPASS_MARKER}`` marker. See the module docstring "
            "for the procedure. Audit P0-6."
        )
        self.assertEqual(unexpected, [], msg=msg)

    def test_no_stale_allowlist_entries(self) -> None:
        """A file in the allowlist that no longer has any
        ``D.features(...)`` calls means it migrated to PIT (great!)
        and should be removed from the allowlist so the next bypass
        regression there fails this test loudly."""
        actual = _scan_src()
        stale = sorted(set(PIT_FEATURES_BYPASS_ALLOWLIST) - set(actual))
        msg = (
            "Allowlist entry/entries no longer present in src/:\n  "
            + "\n  ".join(stale)
            + "\n\nThese files have been migrated to PIT — please "
            "remove them from PIT_FEATURES_BYPASS_ALLOWLIST so this "
            "test guards against future bypass regressions there."
        )
        self.assertEqual(stale, [], msg=msg)

    def test_allowlist_counts_match_exactly(self) -> None:
        """Adding a NEW D.features call inside an already-allow-listed
        file FAILS this test until the engineer explicitly bumps the
        count — forces governance review of every new bypass site,
        not just first-time-in-file bypasses."""
        actual = _scan_src()
        mismatches: list[str] = []
        for fpath, expected_n in PIT_FEATURES_BYPASS_ALLOWLIST.items():
            actual_n = len(actual.get(fpath, []))
            if actual_n != expected_n:
                mismatches.append(
                    f"  {fpath}: allowlist={expected_n}, actual={actual_n}"
                )
        msg = (
            "D.features(...) call count mismatch for allow-listed file(s):\n"
            + "\n".join(mismatches)
            + "\n\nIf you added a NEW call: confirm the new bypass site "
            "was intentional, add the WARN log next to it, and bump the "
            "allowlist count. If you removed one: lower the count. "
            "Audit P0-6."
        )
        self.assertEqual(mismatches, [], msg=msg)

    def test_every_call_has_pit_bypass_ok_marker_in_enclosing_function(
        self,
    ) -> None:
        """Codex P2 follow-up on PR #177.

        Every approved ``D.features(...)`` call must live inside a
        function whose source contains the literal marker
        ``pit-bypass-ok``. Without this, a refactor inside an
        already-allow-listed file could replace the reviewed call
        with a different direct-qlib read (e.g. inside a NEW
        helper function on the same file) and still pass the count
        check, defeating the governance signal.

        The marker can appear in a comment, docstring, or string
        literal anywhere inside the function — one marker per
        function covers all calls within that function. The point
        is that the engineer who added the call had to write the
        marker, which is the same act that documents the bypass.
        """
        actual = _scan_src()
        missing: list[str] = []
        for fpath, sites in actual.items():
            full_text = (_PROJECT_ROOT / fpath).read_text(encoding="utf-8")
            for site in sites:
                func_src = _enclosing_function_source(full_text, site)
                if _BYPASS_MARKER not in func_src:
                    missing.append(
                        f"  {fpath}:{site.lineno}  "
                        f"(inside ``{site.enclosing_function_name}``)"
                    )
        msg = (
            f"D.features(...) call(s) without ``{_BYPASS_MARKER}`` "
            "marker in the enclosing function:\n"
            + "\n".join(missing)
            + "\n\nAdd the literal string "
            f"``{_BYPASS_MARKER}`` in a comment or docstring inside "
            "each listed function. The marker documents that the "
            "bypass was a conscious decision (rather than a refactor "
            "that drifted a guarded call into an unguarded one). "
            "Audit P0-6 / codex P2 follow-up on PR #177."
        )
        self.assertEqual(missing, [], msg=msg)


# ---------------------------------------------------------------------------
# Unit tests on the scanner (so future refactors of the scanner can't
# silently mis-count and let bypass regressions slip past the
# governance tests above).
# ---------------------------------------------------------------------------


class PitProviderAstCounterUnitTests(unittest.TestCase):

    def test_counts_simple_D_features_call(self) -> None:
        src = "from qlib.data import D\nclose = D.features(['A'], ['$close'])\n"
        self.assertEqual(len(_find_d_features_call_sites(src)), 1)

    def test_counts_multiple_calls(self) -> None:
        src = (
            "from qlib.data import D\n"
            "a = D.features(['A'], ['$close'])\n"
            "b = D.features(['B'], ['$open'])\n"
        )
        self.assertEqual(len(_find_d_features_call_sites(src)), 2)

    def test_ignores_d_features_in_docstring(self) -> None:
        src = (
            '"""See ``D.features(...)`` for details."""\n'
            "# fallback: D.features(...)\n"
            "x = 1\n"
        )
        self.assertEqual(len(_find_d_features_call_sites(src)), 0)

    def test_ignores_method_call_on_other_object(self) -> None:
        src = (
            "provider.features(['A'])\n"
            "obj.D.features(['A'])\n"  # attribute access, not import-resolved D
        )
        self.assertEqual(len(_find_d_features_call_sites(src)), 0)

    def test_counts_when_D_is_local_shadow(self) -> None:
        """A local rebind of ``D`` (without ``from qlib.data import D``)
        is NOT a qlib alias — the scanner should NOT count it. This
        differs from the previous pre-#177-followup behaviour, which
        counted any name spelled ``D``. The new behaviour is correct
        because the only safe heuristic for "is this qlib's D" is
        "did the file import it from qlib.data"."""
        src = (
            "D = object()\n"
            "D.features(['A'])\n"
        )
        self.assertEqual(len(_find_d_features_call_sites(src)), 0)

    # --- alias resolution (codex P2 follow-up) ---

    def test_counts_aliased_import_from(self) -> None:
        """``from qlib.data import D as QD`` should bind QD as a
        qlib.D alias; ``QD.features(...)`` then counts."""
        src = (
            "from qlib.data import D as QD\n"
            "close = QD.features(['A'], ['$close'])\n"
        )
        self.assertEqual(len(_find_d_features_call_sites(src)), 1)

    def test_counts_aliased_import_from_multi(self) -> None:
        """Multi-name ImportFrom only adds the D alias, not the
        other names imported alongside."""
        src = (
            "from qlib.data import D as QD, Other\n"
            "close = QD.features(['A'])\n"
            "Other.features(['B'])\n"  # Other is NOT a qlib.D alias
        )
        self.assertEqual(len(_find_d_features_call_sites(src)), 1)

    def test_counts_qlib_data_D_chain(self) -> None:
        """The fully-qualified ``qlib.data.D.features(...)`` chain
        (``import qlib.data`` style) is detected without needing the
        ImportFrom shortcut."""
        src = (
            "import qlib.data\n"
            "close = qlib.data.D.features(['A'])\n"
        )
        self.assertEqual(len(_find_d_features_call_sites(src)), 1)

    def test_does_not_count_unrelated_chain_named_D(self) -> None:
        """``other.lib.D.features`` (not ``qlib.data.D``) does NOT
        count — only the literal ``qlib.data.D`` chain."""
        src = (
            "import other.lib\n"
            "close = other.lib.D.features(['A'])\n"
        )
        self.assertEqual(len(_find_d_features_call_sites(src)), 0)

    # --- enclosing function detection ---

    def test_enclosing_function_resolved(self) -> None:
        src = (
            "from qlib.data import D\n"
            "def outer():\n"
            "    def inner():\n"
            "        return D.features(['A'])\n"
            "    return inner\n"
        )
        sites = _find_d_features_call_sites(src)
        self.assertEqual(len(sites), 1)
        # Innermost wins.
        self.assertEqual(sites[0].enclosing_function_name, "inner")

    def test_marker_check_passes_when_present_in_function_body(self) -> None:
        src = (
            "from qlib.data import D\n"
            "def f():\n"
            "    # pit-bypass-ok: legacy fallback\n"
            "    return D.features(['A'])\n"
        )
        sites = _find_d_features_call_sites(src)
        self.assertEqual(len(sites), 1)
        func_src = _enclosing_function_source(src, sites[0])
        self.assertIn(_BYPASS_MARKER, func_src)

    def test_marker_check_fails_when_absent(self) -> None:
        src = (
            "from qlib.data import D\n"
            "def f():\n"
            "    return D.features(['A'])\n"
        )
        sites = _find_d_features_call_sites(src)
        self.assertEqual(len(sites), 1)
        func_src = _enclosing_function_source(src, sites[0])
        self.assertNotIn(_BYPASS_MARKER, func_src)

    def test_marker_check_does_not_leak_across_functions(self) -> None:
        """A marker in function A must NOT make a call in function B
        pass. Each function is checked independently against its own
        source span.
        """
        src = (
            "from qlib.data import D\n"
            "def a():\n"
            "    # pit-bypass-ok: this function is documented\n"
            "    return 1\n"
            "def b():\n"
            "    return D.features(['A'])\n"
        )
        sites = _find_d_features_call_sites(src)
        self.assertEqual(len(sites), 1)
        self.assertEqual(sites[0].enclosing_function_name, "b")
        func_src = _enclosing_function_source(src, sites[0])
        self.assertNotIn(_BYPASS_MARKER, func_src)


if __name__ == "__main__":
    unittest.main()
