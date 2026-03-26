"""Placeholder for runtime universe selection (intentionally out of scope in contract-only changes)."""

from __future__ import annotations


class RuntimeUniverseSelectionPlaceholder:
    """Explicit boundary marker: runtime universe-selection semantics are not implemented here."""

    @staticmethod
    def select(*_args, **_kwargs):
        raise NotImplementedError(
            "Runtime universe-selection semantics are intentionally unimplemented "
            "in define-v2-universe-data-contract-foundation."
        )
