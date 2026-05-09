"""Canonical boundary for risk constraints.

Risk constraint enforcement (sector caps, position limits, etc.) is an
experimental capability and is not currently implemented in this repo.
The experimental module was removed in PR #53; track
``openspec/changes/`` for any future proposal that re-introduces it.
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
            "Risk constraint enforcement is an experimental capability "
            "and is not currently implemented in this repo. Track "
            "openspec/changes/ for any future proposal that re-introduces it."
        )


__all__ = ("RiskConstraintEngine", "RiskConstraintError")
