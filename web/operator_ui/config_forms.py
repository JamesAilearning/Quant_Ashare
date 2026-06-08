"""Streamlit configuration forms and validation.

Heavy config classes (``PipelineConfig``, ``WalkForwardConfig``)
transitively import ``qlib``, which is intentionally NOT a
pyproject.toml dependency (see ``pyproject.toml`` lines 12-14).
Streamlit auto-imports every page module at startup to build the
sidebar, so a top-level import of those config classes here would
crash the entire UI on any environment without qlib. The page modules
that consume ``PIPELINE_KEYS`` / ``WALK_FORWARD_KEYS`` only need the
field-name sets, not the config classes themselves;
:pep:`562` ``__getattr__`` defers the import to first access.
(bug.md P2-2.)
"""

from __future__ import annotations

from dataclasses import fields
from typing import Any


def validate_provider_uri(uri: str) -> None:
    """Raise ValueError if provider_uri is empty or whitespace-only."""
    if not str(uri or "").strip():
        raise ValueError("provider_uri 不能为空，规范化 qlib 初始化需要它。")


def validate_config_keys(config: dict[str, Any], known_keys: set[str]) -> None:
    """Reject unknown config keys — no silent fallback."""
    unknown = set(config) - known_keys
    if unknown:
        raise ValueError(
            f"配置中含有未知字段：{sorted(unknown)}。"
            f"允许的字段：{sorted(known_keys)}。"
        )


def _dataclass_field_names(cls: type) -> set[str]:
    return {field.name for field in fields(cls)}


# First-access cache for the lazily-computed key sets. Without this
# every access would re-import and re-introspect; with it, the
# second and subsequent lookups are O(1).
_KEY_SET_CACHE: dict[str, frozenset[str]] = {}


def _pipeline_keys() -> frozenset[str]:
    if "pipeline" not in _KEY_SET_CACHE:
        from src.core.pipeline import PipelineConfig
        _KEY_SET_CACHE["pipeline"] = frozenset(_dataclass_field_names(PipelineConfig))
    return _KEY_SET_CACHE["pipeline"]


def _walk_forward_keys() -> frozenset[str]:
    if "walk_forward" not in _KEY_SET_CACHE:
        from src.core.walk_forward import WalkForwardConfig
        _KEY_SET_CACHE["walk_forward"] = frozenset(
            _dataclass_field_names(WalkForwardConfig) | {"provider_uri", "region"}
        )
    return _KEY_SET_CACHE["walk_forward"]


_LAZY_ATTRS = {
    "PIPELINE_KEYS": _pipeline_keys,
    "WALK_FORWARD_KEYS": _walk_forward_keys,
}


def __getattr__(name: str) -> Any:
    """:pep:`562` module-level ``__getattr__`` — defers the heavy
    config-class imports until the first access of one of the
    KEY-set names. Operators running the UI without qlib (for
    example, opening the Streamlit app on a machine that only has
    the operator-UI dependencies) will still see the sidebar.
    """
    loader = _LAZY_ATTRS.get(name)
    if loader is not None:
        return loader()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
