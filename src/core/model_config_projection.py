"""Shared projection helpers for model-training runtime config."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import MISSING, fields
from typing import Any

from src.core.model_trainer import ModelTrainConfig


def model_train_config_kwargs(
    source: Any | None = None,
    /,
    **overrides: Any,
) -> dict[str, Any]:
    """Return kwargs for ``ModelTrainConfig`` from an object or mapping.

    Runtime configs stay flat for backward compatibility. This helper is the
    single projection boundary that pulls matching model fields off those
    configs, applies explicit overrides, and falls back to
    ``ModelTrainConfig`` defaults.
    """

    values: dict[str, Any] = {}
    for item in fields(ModelTrainConfig):
        value = overrides[item.name] if item.name in overrides else _read_field(source, item.name)
        if value is MISSING:
            if item.default is not MISSING:
                value = item.default
            elif item.default_factory is not MISSING:  # type: ignore[comparison-overlap]
                value = item.default_factory()  # type: ignore[misc]
            else:
                raise TypeError(f"Missing required ModelTrainConfig field {item.name!r}.")
        values[item.name] = value
    return values


def build_model_train_config(
    source: Any | None = None,
    /,
    **overrides: Any,
) -> ModelTrainConfig:
    """Build ``ModelTrainConfig`` through the shared projection boundary."""

    return ModelTrainConfig(**model_train_config_kwargs(source, **overrides))


def _read_field(source: Any | None, name: str) -> Any:
    if source is None:
        return MISSING
    if isinstance(source, Mapping):
        return source[name] if name in source else MISSING
    return getattr(source, name) if hasattr(source, name) else MISSING
