"""YAML loader with ``extends`` inheritance.

Supports a flat ``extends: <path>`` key at the top level of any YAML
config. The loader recursively merges parent configs (child keys override
parent keys with shallow dict merge) before returning the final dict.

Circular references are detected and rejected.

Usage::

    from src.core._yaml_loader import load_yaml_with_inheritance
    config = load_yaml_with_inheritance("config_walk_n3.yaml")
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class YamlInheritanceError(RuntimeError):
    """Raised on circular extends or missing parent files."""


def load_yaml_with_inheritance(
    path: str | Path,
    *,
    _chain: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Load a YAML file, resolving ``extends`` chains recursively.

    Parameters
    ----------
    path : str or Path
        Path to the YAML file.
    _chain : tuple of str
        Internal recursion guard — tracks parent paths to detect cycles.

    Returns
    -------
    dict
        Merged configuration dictionary (child keys override parents).

    Raises
    ------
    YamlInheritanceError
        On circular references or missing parent files.
    FileNotFoundError
        If ``path`` does not exist.
    """
    file_path = Path(path).resolve()

    # Cycle detection
    path_str = str(file_path)
    if path_str in _chain:
        raise YamlInheritanceError(
            f"Circular extends chain detected: "
            f"{' → '.join(_chain)} → {path_str}"
        )

    with open(file_path, encoding="utf-8") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh)

    if not isinstance(raw, dict):
        raise YamlInheritanceError(
            f"YAML root must be a mapping; got {type(raw).__name__} "
            f"in {path_str}"
        )

    parent = raw.pop("extends", None)
    if parent is None:
        return raw

    # Resolve parent path relative to the child file's directory
    parent_path = file_path.parent / str(parent)
    if not parent_path.is_file():
        raise YamlInheritanceError(
            f"Parent config not found: {parent_path} "
            f"(referenced by {path_str})"
        )

    base = load_yaml_with_inheritance(
        parent_path,
        _chain=(*_chain, path_str),
    )

    # Shallow merge: child keys override parent keys
    merged: dict[str, Any] = dict(base)
    merged.update(raw)
    return merged
