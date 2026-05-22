"""Factor Mining Foundations (Phase 1).

Pure-Python operator library, expression tree, and grammar. Phase 1
performs no data access; it does not import qlib, does not touch
``src.pit``, does not compute IC, and does not implement the GP loop.

See ``docs/factor_mining/`` for the design baseline:

- ``factor_mining_claude_code_design.md`` — implementation roadmap.
- ``scale_invariance.md`` — normative type rules (kind × taint).
- ``decisions.md`` — D1–D5 locked decisions (feature universe, data
  gate, etc.).
- ``inventory.md`` — Phase 0 repo survey.

The strict data gate D5 (see ``docs/factor_mining/decisions.md``) is
enforced at the source level: this subpackage does not call the qlib
data API, does not bootstrap qlib runtime, and does not import the
qlib module — zero violations are permitted.
"""

from .expression import (
    Expression,
    OperatorCall,
    Terminal,
    parse_expression,
)
from .grammar import (
    REGISTRY,
    WINDOW_LITERALS,
    ExprType,
    FeatureRegistry,
    GrammarError,
    Operator,
    OutputKind,
    ScaleTaint,
    random_expression,
)

__all__ = [
    "REGISTRY",
    "WINDOW_LITERALS",
    "ExprType",
    "Expression",
    "FeatureRegistry",
    "GrammarError",
    "Operator",
    "OperatorCall",
    "OutputKind",
    "ScaleTaint",
    "Terminal",
    "parse_expression",
    "random_expression",
]
