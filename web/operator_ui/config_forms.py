"""Streamlit configuration forms and validation."""

from __future__ import annotations

from dataclasses import fields
from typing import Any

from src.core.pipeline import PipelineConfig
from src.core.walk_forward import WalkForwardConfig
from src.data.tushare.provider_bundle import TushareQlibProviderBundleConfig


def validate_provider_uri(uri: str) -> None:
    """Raise ValueError if provider_uri is empty or whitespace-only."""
    if not str(uri or "").strip():
        raise ValueError("provider_uri is required for canonical qlib init.")


def validate_config_keys(config: dict[str, Any], known_keys: set[str]) -> None:
    """Reject unknown config keys — no silent fallback."""
    unknown = set(config) - known_keys
    if unknown:
        raise ValueError(
            f"Unknown config keys: {sorted(unknown)}. "
            f"Allowed: {sorted(known_keys)}."
        )


def _dataclass_field_names(cls: type) -> set[str]:
    return {field.name for field in fields(cls)}


PIPELINE_KEYS = _dataclass_field_names(PipelineConfig)
WALK_FORWARD_KEYS = _dataclass_field_names(WalkForwardConfig) | {"provider_uri", "region"}
TUSHARE_PROVIDER_KEYS = _dataclass_field_names(TushareQlibProviderBundleConfig)
