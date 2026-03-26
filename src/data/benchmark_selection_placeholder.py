"""Placeholder for runtime benchmark selection (intentionally out of scope in contract-only changes)."""

from __future__ import annotations


class RuntimeBenchmarkSelectionPlaceholder:
    """Explicit boundary marker: runtime benchmark selection is not implemented here."""

    @staticmethod
    def select(*_args, **_kwargs):
        raise NotImplementedError(
            "Runtime benchmark-selection semantics are intentionally unimplemented "
            "in define-v2-benchmark-data-contract-foundation."
        )
