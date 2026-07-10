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


def _imported_modules(text: str) -> set[str]:
    """Every absolute module name imported by ``text`` (``import X`` and
    ``from X import ...``; relative imports are resolved by the caller's package
    so we only track absolute ones, which is what a cross-package leak uses)."""
    tree = ast.parse(text)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                names.add(node.module)
    return names


def _imports_prefix(text: str, prefix: str) -> bool:
    return any(
        n == prefix or n.startswith(prefix + ".") for n in _imported_modules(text)
    )


class FinancialViewIsolationTests(unittest.TestCase):
    def test_no_canonical_src_imports_research(self) -> None:
        offenders = []
        for py in sorted(_SRC.rglob("*.py")):
            rel = py.relative_to(_ROOT).as_posix()
            if rel.startswith("src/research/"):
                continue
            if _imports_prefix(py.read_text(encoding="utf-8"), _RESEARCH_PKG):
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
        imported = _imported_modules(src)
        forbidden = (
            "qlib", "daily_recommend", "model_trainer", "feature_dataset_builder",
            "mined_factor_handler", "src.core.pipeline", "src.pit.query",
        )
        hits = sorted({m for m in imported for f in forbidden if f in m})
        self.assertEqual(
            hits, [],
            msg=(
                f"FinancialPITDataView imports canonical-runtime/qlib module(s) "
                f"{hits} — the view must stay isolated (D5-style)."
            ),
        )

    def test_only_the_view_imports_lowlevel_store_within_research(self) -> None:
        offenders = []
        for py in sorted((_SRC / "research").rglob("*.py")):
            rel = py.relative_to(_ROOT).as_posix()
            if rel == _VIEW_REL:
                continue
            text = py.read_text(encoding="utf-8")
            if any(_imports_prefix(text, m) for m in _LOWLEVEL):
                offenders.append(rel)
        self.assertEqual(
            offenders, [],
            msg=(
                "Research module(s) other than the view import the low-level "
                "store/contract directly:\n  " + "\n  ".join(offenders)
                + "\n\nFinancialPITDataView is the SOLE access path — reach "
                "financial data through it, not by reading raw filings."
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

    def test_ignores_substring_prefix_collision(self) -> None:
        # "src.research_utils" must NOT match the "src.research" package prefix.
        self.assertFalse(_imports_prefix("import src.research_utils\n", _RESEARCH_PKG))

    def test_ignores_mention_in_string_or_comment(self) -> None:
        self.assertFalse(_imports_prefix(
            '"""see src.research.financial_pit_view"""\n# import src.research.x\nx=1\n',
            _RESEARCH_PKG))


if __name__ == "__main__":
    unittest.main()
