"""Governance: canonical code reaches factor mining ONLY through the sanctioned
bridge, and research code stays out of the canonical runtime (hardening backlog
#3, the residue left after Gate-2's isolation gate).

Two directions, both machine-enforced in CI so they cannot rot:

* **canonical -> factor_mining internals** — every ``src/`` module OUTSIDE
  ``src/factor_mining/`` except the single sanctioned bridge
  (``src/data/mined_factor_handler.py``, the feature-handler-registry seam)
  MUST NOT import ``src.factor_mining.*``. The D5 gate protects the opposite
  direction (factor_mining must not import qlib / ``src.pit``); without THIS
  test nothing stops ``src/core/pipeline.py`` from importing
  ``src.factor_mining.gp_engine`` directly tomorrow.
* **research -> canonical runtime** — Gate-2's forward rule, generalized from
  the one ``financial_pit_view.py`` file to EVERY ``src/research/`` module:
  research code must not import qlib or the canonical-runtime modules.

The AST scanner (relative-import resolution, package-alias detection,
component-not-substring matching) is REUSED from the Gate-2 isolation test —
one scanner, two gates, no drift. 阶段8 Gate-4 note: when the quality-factor
evaluator lands, add its module to ``_CANONICAL_RUNTIME_FORBIDDEN`` (one line).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Scanner helpers shared with the Gate-2 isolation gate (functions only —
# importing a TestCase here would double-collect it under pytest).
from tests.governance.test_financial_pit_view_isolation import (  # noqa: E402
    _CANONICAL_RUNTIME_FORBIDDEN,
    _imported_modules,
    _imports_prefix,
    _matches_forbidden,
    _module_dotted,
)

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
_FM_PKG = "src.factor_mining"

# The ONE sanctioned canonical->factor_mining bridge: the qlib feature-handler
# seam that materializes promoted mined factors (evaluate_expression +
# factor_pool + pit_adapter). Anything else must go through it. Adding an
# entry here requires a design-level justification, mirroring the Gate-2
# low-level-store allowlist.
_BRIDGE_ALLOWLIST = {"src/data/mined_factor_handler.py"}


def _factor_mining_offenders() -> list[str]:
    """Non-allowlisted ``src/`` modules (outside factor_mining itself) that
    import ``src.factor_mining.*`` — module-level helper so the degenerate
    self-tests below can exercise the same code path the gate runs."""
    offenders = []
    for py in sorted(_SRC.rglob("*.py")):
        rel = py.relative_to(_ROOT).as_posix()
        if rel.startswith("src/factor_mining/") or rel in _BRIDGE_ALLOWLIST:
            continue
        text = py.read_text(encoding="utf-8")
        if _imports_prefix(text, _FM_PKG, _module_dotted(py)):
            offenders.append(rel)
    return offenders


class CanonicalFactorMiningGateTests(unittest.TestCase):
    def test_only_the_bridge_imports_factor_mining(self) -> None:
        offenders = _factor_mining_offenders()
        self.assertEqual(
            offenders, [],
            msg=(
                "Module(s) import src.factor_mining internals directly, "
                "bypassing the sanctioned bridge:\n  " + "\n  ".join(offenders)
                + "\n\nCanonical code reaches factor mining ONLY through "
                "src/data/mined_factor_handler.py (the feature-handler-registry "
                "seam). If a new module legitimately needs the bridge role, add "
                "it to _BRIDGE_ALLOWLIST with a design justification."
            ),
        )

    def test_bridge_allowlist_entry_is_live(self) -> None:
        # A stale allowlist is a silent hole: if the bridge ever stops
        # importing factor_mining, the entry must be REMOVED, not linger as a
        # free pass for whatever takes the filename over later.
        bridge = _ROOT / "src" / "data" / "mined_factor_handler.py"
        self.assertTrue(bridge.is_file(), "bridge module missing — update the gate")
        self.assertTrue(
            _imports_prefix(
                bridge.read_text(encoding="utf-8"), _FM_PKG,
                _module_dotted(bridge),
            ),
            "src/data/mined_factor_handler.py no longer imports "
            "src.factor_mining — remove it from _BRIDGE_ALLOWLIST.",
        )


class ResearchForwardGateTests(unittest.TestCase):
    """Gate-2's forward rule generalized: EVERY src/research/ module (not just
    financial_pit_view.py) stays out of the qlib / canonical-runtime graph."""

    def test_no_research_module_imports_canonical_runtime(self) -> None:
        offenders: list[str] = []
        for py in sorted((_SRC / "research").rglob("*.py")):
            rel = py.relative_to(_ROOT).as_posix()
            imported = _imported_modules(
                py.read_text(encoding="utf-8"), _module_dotted(py),
            )
            hits = sorted({
                m for m in imported
                for f in _CANONICAL_RUNTIME_FORBIDDEN
                if _matches_forbidden(m, f)
            })
            if hits:
                offenders.append(f"{rel} -> {hits}")
        self.assertEqual(
            offenders, [],
            msg=(
                "Research module(s) import qlib / canonical-runtime modules:\n  "
                + "\n  ".join(offenders)
                + "\n\nsrc/research/ is isolated D5-style: it reaches data only "
                "through the research view / PIT contract layer, never the "
                "canonical runtime."
            ),
        )


class GateScannerSelfTests(unittest.TestCase):
    """Degenerate-proofing for THIS gate's use of the shared scanner — the
    same misdetection classes the Gate-2 self-tests pin, exercised against the
    factor_mining prefix so a scanner refactor cannot silently unhook it."""

    def test_detects_direct_internal_import(self) -> None:
        self.assertTrue(_imports_prefix(
            "from src.factor_mining.evaluator import evaluate_expression\n",
            _FM_PKG, module_dotted="src.core.pipeline"))
        self.assertTrue(_imports_prefix(
            "import src.factor_mining.gp_engine\n",
            _FM_PKG, module_dotted="src.core.pipeline"))

    def test_detects_relative_import_bypass(self) -> None:
        # from ..factor_mining.evaluator import x inside src/data/foo.py
        self.assertTrue(_imports_prefix(
            "from ..factor_mining.evaluator import evaluate_expression\n",
            _FM_PKG, module_dotted="src.data.foo"))

    def test_detects_package_alias_import(self) -> None:
        self.assertTrue(_imports_prefix(
            "from src import factor_mining\n", _FM_PKG,
            module_dotted="src.core.pipeline"))

    def test_ignores_prefix_collision_and_mentions(self) -> None:
        self.assertFalse(_imports_prefix(
            "import src.factor_mining_utils\n", _FM_PKG,
            module_dotted="src.core.pipeline"))
        self.assertFalse(_imports_prefix(
            '"""docs mention src.factor_mining.evaluator"""\n'
            "# import src.factor_mining.gp_engine\nx = 1\n",
            _FM_PKG, module_dotted="src.core.pipeline"))

    def test_forbidden_list_covers_whole_canonical_packages(self) -> None:
        # codex P2 #348: the forward gate must catch ANY src.core /
        # src.inference / src.pit module, not just individually listed ones.
        for module in (
            "src.core.backtest_runner", "src.core.qlib_runtime",
            "src.core.walk_forward.engine", "src.inference.daily_recommend",
            "src.pit.cache", "src.pit.query",
        ):
            self.assertTrue(
                any(_matches_forbidden(module, f)
                    for f in _CANONICAL_RUNTIME_FORBIDDEN),
                f"{module} escapes _CANONICAL_RUNTIME_FORBIDDEN",
            )
        # ...while the research view's legitimate src.data dependencies stay
        # importable (a blanket src.data ban would break the view baseline).
        for module in (
            "src.data.pit._common", "src.data.pit.financial_pit_contract",
            "src.data.trading_calendar",
        ):
            self.assertFalse(
                any(_matches_forbidden(module, f)
                    for f in _CANONICAL_RUNTIME_FORBIDDEN),
                f"{module} wrongly banned for research code",
            )


if __name__ == "__main__":
    unittest.main()
