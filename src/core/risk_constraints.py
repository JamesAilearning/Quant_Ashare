"""Canonical boundary for risk constraints.

Risk constraints are runtime trading behavior: they can filter or reshape
model predictions before backtest execution. That behavior is not approved as
canonical runtime logic yet, so the canonical core import path fails closed.

Experimental research / migration work lives in
``src.experimental.risk_constraints`` and must not be treated as an official
metrics path.
"""

from __future__ import annotations

from typing import Any


class RiskConstraintError(RuntimeError):
    """Raised when risk constraints are invoked through the canonical layer."""


class RiskConstraintEngine:
    """Fail-closed canonical compatibility surface."""

    @classmethod
    def apply(cls, *_args: Any, **_kwargs: Any) -> None:
        raise RiskConstraintError(
            "RiskConstraintEngine is experimental runtime trading behavior "
            "and is not approved in src.core canonical runtime. Import "
            "src.experimental.risk_constraints for explicitly experimental "
            "work, and do not treat those results as official metrics."
        )


__all__ = ("RiskConstraintEngine", "RiskConstraintError")
