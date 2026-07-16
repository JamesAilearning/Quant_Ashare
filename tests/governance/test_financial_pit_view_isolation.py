"""Governance: the research-side financial-PIT view is machine-isolated from the
canonical runtime (阶段8 Gate-2 PR-2).

This is the same kind of machine-enforced boundary the D5 gate gives
``src/factor_mining/`` — it runs in CI, so the isolation cannot rot into a
stale unit test. Three orthogonal AST checks:

* **reverse** — no ``src/`` module OUTSIDE ``src/research/`` may import
  ``src.research.*``; the research view must never leak into the canonical
  feature registry / training / ``daily_recommend`` import graph;
* **forward** — the view itself stays out of the qlib / canonical-runtime graph;
* **sole path** — within ``src/research/``, ONLY the view may import the PR-1
  low-level store / contract modules, so factor-research code reaches financial
  data through the view, never by reading the raw filings directly.
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
_RESEARCH_PKG = "src.research"
_VIEW_REL = "src/research/financial_pit_view.py"
# PR-1 low-level modules the view wraps; research code must not import them direct.
_LOWLEVEL = (
    "src.data.tushare.financial_statements",
    "src.data.pit.financial_pit_contract",
)
# qlib / canonical-runtime import targets research code must stay out of.
# Module-level so the generalized research gate
# (test_factor_mining_import_isolation.py) enforces the SAME list — one
# source of truth, no drift. Bare tokens match whole path components;
# dotted entries match by module prefix (see _matches_forbidden).
#
# WHOLE-PACKAGE bans for the canonical packages (codex P2 on #348: listing
# individual modules left src.core.backtest_runner / src.core.qlib_runtime /
# walk_forward etc. importable): src.core (orchestration + official metrics),
# src.inference (production serving), src.pit (the canonical PIT layer the D5
# gate protects). src.data can NOT be blanket-banned — the research view
# legitimately uses src.data.pit._common / financial_pit_contract /
# trading_calendar — so its canonical members stay listed as bare tokens
# (component-matched, so they are caught wherever the file lives or moves).
_CANONICAL_RUNTIME_FORBIDDEN = (
    "qlib",
    "src.core", "src.inference", "src.pit",
    "daily_recommend", "model_trainer",
    "feature_dataset_builder", "mined_factor_handler",
)


def _imported_modules(text: str, module_dotted: str) -> set[str]:
    """Absolute module names imported by ``text``. RELATIVE imports are resolved
    against ``module_dotted`` (the importing file's own dotted path), so a
    package-relative bypass like ``from ..research.x import y`` in
    ``src.core.foo`` resolves to ``src.research.x`` and is caught (codex #342)."""
    tree = ast.parse(text)
    parts = module_dotted.split(".")
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                base_module = node.module
            else:
                # level=L drops the last L path components of the importer's
                # dotted path (the module name for L=1, its package for L=2, …).
                base = parts[: len(parts) - node.level]
                tail = node.module.split(".") if node.module else []
                base_module = ".".join([*base, *tail]) or None
            if base_module:
                names.add(base_module)
                # ``from X import Y`` ALSO imports the submodule/package X.Y
                # (e.g. ``from src import research`` / ``from .. import research``
                # -> src.research) — record each imported name so a package-alias
                # import cannot bypass the boundary (codex #342 r2).
                for alias in node.names:
                    if alias.name != "*":
                        names.add(f"{base_module}.{alias.name}")
    return names


def _module_dotted(py: Path) -> str:
    """``src/core/foo.py`` -> ``src.core.foo`` (its importer dotted path)."""
    return py.relative_to(_ROOT).with_suffix("").as_posix().replace("/", ".")


def _imports_prefix(text: str, prefix: str, module_dotted: str = "pkg.mod") -> bool:
    return any(
        n == prefix or n.startswith(prefix + ".")
        for n in _imported_modules(text, module_dotted)
    )


def _matches_forbidden(module: str, forbidden: str) -> bool:
    """Whether an imported ``module`` is the ``forbidden`` target. A DOTTED entry
    (``src.pit.query``) matches by module prefix; a BARE-TOKEN entry (``qlib``,
    ``daily_recommend``) matches a whole path COMPONENT — so a helper whose name
    merely CONTAINS the token (``qlib_to_ts_code``) is not a false hit and a real
    ``import qlib`` / ``from qlib.data import D`` still is (codex #342 r10)."""
    if "." in forbidden:
        return module == forbidden or module.startswith(forbidden + ".")
    return forbidden in module.split(".")


class FinancialViewIsolationTests(unittest.TestCase):
    def test_no_canonical_src_imports_research(self) -> None:
        offenders = []
        for py in sorted(_SRC.rglob("*.py")):
            rel = py.relative_to(_ROOT).as_posix()
            if rel.startswith("src/research/"):
                continue
            if _imports_prefix(
                py.read_text(encoding="utf-8"), _RESEARCH_PKG, _module_dotted(py),
            ):
                offenders.append(rel)
        self.assertEqual(
            offenders, [],
            msg=(
                "Canonical/non-research src/ module(s) import the research view:\n  "
                + "\n  ".join(offenders)
                + "\n\nThe financial-PIT view is research-only and MUST stay out "
                "of the canonical feature registry / training / daily_recommend "
                "import graph. Consume financial data only in research code."
            ),
        )

    def test_view_does_not_import_qlib_or_canonical_runtime(self) -> None:
        # AST-based (so the docstring's mentions of daily_recommend / qlib do
        # NOT count) — the view must not IMPORT qlib or any canonical-runtime
        # module; it reaches data only through the PR-1 contract/store + calendar.
        src = (_SRC / "research" / "financial_pit_view.py").read_text(encoding="utf-8")
        imported = _imported_modules(src, "src.research.financial_pit_view")
        forbidden = _CANONICAL_RUNTIME_FORBIDDEN
        hits = sorted({m for m in imported for f in forbidden if _matches_forbidden(m, f)})
        self.assertEqual(
            hits, [],
            msg=(
                f"FinancialPITDataView imports canonical-runtime/qlib module(s) "
                f"{hits} — the view must stay isolated (D5-style)."
            ),
        )

    # Sanctioned importers of the raw store/contract — every OTHER src/ AND
    # scripts/ module must reach financial data through the view. The view is
    # the sole research access path; the contract layer legitimately wraps the
    # store constants; the ingest WRITES the store; Step-A audits the store
    # at the storage level by design.
    _LOWLEVEL_IMPORT_ALLOWLIST = {
        _VIEW_REL,                                   # sole research access path
        "src/data/pit/financial_pit_contract.py",    # contract layer wraps the store
        "scripts/data_pipeline/08_fetch_financials.py",   # the ingest writes the store
        "scripts/research/gate3_step_a_coverage_report.py",  # store-level coverage auditor
    }

    def test_only_sanctioned_modules_import_lowlevel_store(self) -> None:
        # ALL of src/ AND scripts/ (hardening backlog: the Gate-4A evaluator
        # landed under scripts/research/ — decision-level tooling outside
        # src/ must not bypass the view either): a future evaluator /
        # feature / canonical module reading the raw store directly would
        # bypass the view's original-first / exclusion / missingness
        # semantics — a contract violation CI must catch (codex #342 r4).
        offenders = []
        scan = list(sorted(_SRC.rglob("*.py"))) + list(
            sorted((_ROOT / "scripts").rglob("*.py")))
        for py in scan:
            rel = py.relative_to(_ROOT).as_posix()
            if rel in self._LOWLEVEL_IMPORT_ALLOWLIST:
                continue
            text = py.read_text(encoding="utf-8")
            if any(_imports_prefix(text, m, _module_dotted(py)) for m in _LOWLEVEL):
                offenders.append(rel)
        self.assertEqual(
            offenders, [],
            msg=(
                "Module(s) import the raw financial store/contract directly, "
                "bypassing FinancialPITDataView:\n  " + "\n  ".join(offenders)
                + "\n\nAll financial-data access must go through the view (its "
                "original-first / exclusion / missingness semantics). If a new "
                "data-layer module legitimately wraps the store, add it to "
                "_LOWLEVEL_IMPORT_ALLOWLIST with a justification."
            ),
        )


class ImportScannerUnitTests(unittest.TestCase):
    """Guard the AST scanner so a refactor cannot silently mis-detect and let a
    boundary violation slip past the governance tests above."""

    def test_detects_import_statement(self) -> None:
        self.assertTrue(_imports_prefix("import src.research.financial_pit_view\n",
                                        _RESEARCH_PKG))

    def test_detects_from_import(self) -> None:
        self.assertTrue(_imports_prefix(
            "from src.research.financial_pit_view import FinancialPITDataView\n",
            _RESEARCH_PKG))

    def test_ignores_unrelated_import(self) -> None:
        self.assertFalse(_imports_prefix("import src.data.pit.query\n", _RESEARCH_PKG))

    def test_detects_relative_import_bypass(self) -> None:
        # a package-relative import of the research view from src/core must be
        # caught (codex #342): from ..research.x in src.core.foo -> src.research.x
        self.assertTrue(_imports_prefix(
            "from ..research.financial_pit_view import FinancialPITDataView\n",
            _RESEARCH_PKG, module_dotted="src.core.foo"))
        # and from a deeper module: src.core.walk_forward.engine, level=2 -> src.core
        self.assertFalse(_imports_prefix(
            "from ..other.thing import X\n",
            _RESEARCH_PKG, module_dotted="src.core.walk_forward"))

    def test_detects_from_import_alias_of_package(self) -> None:
        # `from src import research` / `from .. import research` import the
        # src.research PACKAGE bound as a name — must be caught (codex #342 r2).
        self.assertTrue(_imports_prefix("from src import research\n", _RESEARCH_PKG,
                                        module_dotted="src.core.foo"))
        self.assertTrue(_imports_prefix("from .. import research\n", _RESEARCH_PKG,
                                        module_dotted="src.core.foo"))
        # a `research` submodule under a DIFFERENT package must NOT match
        self.assertFalse(_imports_prefix("from src.data import research\n",
                                         _RESEARCH_PKG, module_dotted="src.core.foo"))

    def test_ignores_substring_prefix_collision(self) -> None:
        # "src.research_utils" must NOT match the "src.research" package prefix.
        self.assertFalse(_imports_prefix("import src.research_utils\n", _RESEARCH_PKG))

    def test_ignores_mention_in_string_or_comment(self) -> None:
        self.assertFalse(_imports_prefix(
            '"""see src.research.financial_pit_view"""\n# import src.research.x\nx=1\n',
            _RESEARCH_PKG))

    def test_forbidden_matches_component_not_substring(self) -> None:
        # a bare-token forbidden entry matches a whole path COMPONENT: a real
        # qlib / canonical-runtime import is still caught...
        self.assertTrue(_matches_forbidden("qlib", "qlib"))
        self.assertTrue(_matches_forbidden("qlib.data", "qlib"))
        self.assertTrue(
            _matches_forbidden("src.inference.daily_recommend", "daily_recommend"))
        # ...but a helper whose NAME merely contains the token is NOT (the
        # view imports src.data.pit._common.qlib_to_ts_code — pure stdlib).
        self.assertFalse(
            _matches_forbidden("src.data.pit._common.qlib_to_ts_code", "qlib"))
        self.assertFalse(_matches_forbidden("src.data.pit._common", "qlib"))
        # dotted forbidden entries match by module prefix, not substring.
        self.assertTrue(_matches_forbidden("src.pit.query", "src.pit.query"))
        self.assertTrue(_matches_forbidden("src.pit.query.foo", "src.pit.query"))
        self.assertFalse(_matches_forbidden("src.pit.querytools", "src.pit.query"))


if __name__ == "__main__":
    unittest.main()
