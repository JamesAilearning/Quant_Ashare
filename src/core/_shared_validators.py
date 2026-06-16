"""Shared boundary validators for runtime config dataclasses.

A small home for validation rules that MUST stay byte-identical across
more than one config boundary, so a rule cannot silently drift between
hand-maintained copies. Each helper takes the caller's own exception
type (``error_class``) plus an optional ``prefix`` (e.g.
``"PipelineConfig."``) so every config keeps its own exception type and
message namespace — the same pattern as
``attribution_industry_loader.assert_industry_config_complete_or_empty``.

Intentionally NOT a home for the model-hyperparameter checks: those are
deliberately layered (cheap "definitely wrong" checks at config
construction vs. the full checks in ``ModelTrainer._validate``) with
distinct exception types, and collapsing them would erase that layering
(T2-5 scope decision).
"""

from __future__ import annotations


def validate_topk(
    topk: int,
    *,
    error_class: type[Exception],
    prefix: str = "",
) -> None:
    """Validate ``topk`` is a positive int.

    Shared by ``PipelineConfig`` and ``WalkForwardConfig``. The
    ``isinstance`` guards reject ``bool`` (a copy-pasted ``topk=True``
    would otherwise satisfy ``topk >= 1``) and non-int values (which
    would raise a cryptic ``TypeError`` deep in a later comparison such
    as ``n_drop >= topk``). ``error_class`` is the caller's exception
    type; ``prefix`` is prepended to the field name.
    """
    if not isinstance(topk, int) or isinstance(topk, bool) or topk < 1:
        raise error_class(
            f"{prefix}topk must be a positive int; got {topk!r}."
        )


def validate_n_drop(
    n_drop: int,
    topk: int,
    *,
    error_class: type[Exception],
    prefix: str = "",
) -> None:
    """Validate ``n_drop`` is a non-negative int strictly less than ``topk``.

    Shared by ``PipelineConfig`` and ``WalkForwardConfig`` so a
    copy-pasted ``topk=10, n_drop=10`` is rejected identically on both
    paths — ``n_drop >= topk`` empties a ``TopkDropoutStrategy`` portfolio
    after the first rebalance. The ``isinstance`` guards stay (the field
    is annotated ``int`` but the value originates from YAML / operator
    input). ``error_class`` is the caller's exception type; ``prefix`` is
    prepended to the field name (``"PipelineConfig."`` / ``""``).
    """
    if not isinstance(n_drop, int) or isinstance(n_drop, bool) or n_drop < 0:
        raise error_class(
            f"{prefix}n_drop must be a non-negative int; got {n_drop!r}."
        )
    if n_drop >= topk:
        raise error_class(
            f"{prefix}n_drop ({n_drop}) must be strictly less than "
            f"topk ({topk}); otherwise TopkDropoutStrategy would empty "
            "the portfolio after the first rebalance."
        )
