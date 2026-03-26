"""Placeholder for runtime execution semantics (intentionally out of scope in contract-only changes)."""

from __future__ import annotations


class RuntimeExecutionPlaceholder:
    """Explicit boundary marker: runtime execution semantics are not implemented here."""

    @staticmethod
    def run(*_args, **_kwargs):
        raise NotImplementedError(
            "Runtime execution semantics are intentionally unimplemented "
            "in define-v2-run-artifact-contract-foundation."
        )
