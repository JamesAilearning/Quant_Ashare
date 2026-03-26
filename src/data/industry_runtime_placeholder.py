"""Placeholder for industry-aware runtime behavior (intentionally out of scope in contract-only changes)."""

from __future__ import annotations


class IndustryAwareRuntimePlaceholder:
    """Explicit boundary marker: industry-aware runtime semantics are not implemented here."""

    @staticmethod
    def apply(*_args, **_kwargs):
        raise NotImplementedError(
            "Industry-aware runtime semantics are intentionally unimplemented "
            "in define-v2-taxonomy-data-contract-foundation."
        )
