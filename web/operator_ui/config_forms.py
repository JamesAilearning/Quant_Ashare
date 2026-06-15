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

import os
from dataclasses import fields
from typing import Any

# Parity with config.yaml / config_walk.yaml's
# ``${QUANT_NAMECHANGE_PATH:-…}`` default. The official single-fold AND
# walk-forward backtest paths now hard-require a non-empty
# ``namechange_path`` (``require_st_mask=True`` in ``src/core/pipeline.py``
# and ``src/core/walk_forward/engine.py``), so a UI job that omits it would
# RAISE after full training. The UI writes a STANDALONE job config (no
# ``extends`` / no loader env-expansion), so the path must be resolved to a
# concrete literal here at build time. (PR-F, audit E1.)
DEFAULT_NAMECHANGE_PATH = "D:/qlib_data/tushare_raw/all_namechanges.parquet"


def resolve_namechange_path() -> str:
    """Return the operator's ``namechange_path``, env-overridable.

    Reads ``QUANT_NAMECHANGE_PATH`` (the same env var config.yaml /
    config_walk.yaml expand), falling back to :data:`DEFAULT_NAMECHANGE_PATH`.
    Returns a concrete literal because the UI emits a standalone job config
    the runner does not run through the ``${VAR:-default}`` YAML loader.
    """
    value = os.environ.get("QUANT_NAMECHANGE_PATH", "").strip()
    return value or DEFAULT_NAMECHANGE_PATH


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
