"""Canonical-vs-non-canonical boundary declarations for V2."""

from __future__ import annotations

CANONICAL_RUNTIME_LAYER = "canonical_runtime"
EXPERIMENTAL_RUNTIME_LAYER = "experimental_runtime"
RESEARCH_FACTOR_LAB_LAYER = "research_factor_lab"

NON_CANONICAL_LAYERS = (
    EXPERIMENTAL_RUNTIME_LAYER,
    RESEARCH_FACTOR_LAB_LAYER,
)


class CanonicalBoundaryError(ValueError):
    """Raised when canonical boundary rules are violated."""


def assert_canonical_runtime_layer(layer: str) -> None:
    """Enforce that canonical contracts only accept canonical runtime layer."""
    normalized = str(layer or "").strip()
    if normalized != CANONICAL_RUNTIME_LAYER:
        raise CanonicalBoundaryError(
            f"Canonical contract accepts layer '{CANONICAL_RUNTIME_LAYER}' only, got '{normalized or 'empty'}'."
        )

