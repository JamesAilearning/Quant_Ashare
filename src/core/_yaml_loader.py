"""YAML loader with ``extends`` inheritance and ``${VAR}`` env-var expansion.

Two features:

1. **``extends`` inheritance.** Supports a flat ``extends: <path>`` key
   at the top level of any YAML config. The loader recursively merges
   parent configs (child keys override parent keys with shallow dict
   merge) before returning the final dict. Circular references are
   detected and rejected.

2. **Environment-variable expansion.** Every YAML file's string values
   are rewritten by substituting ``${VAR_NAME}`` and
   ``${VAR_NAME:-default_text}`` references (POSIX-shell style)
   **against that file's own path** — so a chain like
   ``child.yaml → parent.yaml`` with an unresolved ``${VAR}`` in
   ``parent.yaml`` reports the error against ``parent.yaml``, not the
   outermost child. Dict keys, integers, floats, booleans, and
   ``None`` are passed through unchanged — only string-typed values
   are expanded. An unresolved ``${VAR}`` (env var truly missing AND
   no default supplied) raises :class:`YamlEnvVarError` with both the
   variable name and the YAML file path that referenced it. Codex P2
   on PR #149: previously the merged tree was expanded once at the
   outermost call with the child's source path, so parent-origin
   placeholders were misattributed.

Usage::

    from src.core._yaml_loader import load_yaml_with_inheritance
    config = load_yaml_with_inheritance("config_walk_n3.yaml")
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml


class YamlInheritanceError(RuntimeError):
    """Raised on circular extends or missing parent files."""


class YamlEnvVarError(RuntimeError):
    """Raised when a ``${VAR}`` reference in YAML cannot be resolved.

    The exception message names BOTH the unresolved variable AND the
    source YAML file so the operator can immediately tell which config
    and which placeholder need attention.
    """


# Matches ``${NAME}`` or ``${NAME:-default text}``. The variable name
# is one or more ASCII letters / digits / underscores (POSIX env-var
# convention). The default — if present — runs from ``:-`` to the
# closing ``}`` and may contain any character except ``}``.
_ENV_VAR_PATTERN = re.compile(
    r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?::-(?P<default>[^}]*))?\}"
)


def expand_env_vars(value: str, *, source_path: str | Path | None = None) -> str:
    """Resolve ``${VAR}`` and ``${VAR:-default}`` in *value*.

    Parameters
    ----------
    value : str
        A YAML string scalar that may contain zero or more env-var
        references. Plain strings without ``${...}`` pass through
        unchanged.
    source_path : str or Path, optional
        The YAML file the value was loaded from. Used only to build
        the :class:`YamlEnvVarError` message when an unresolved
        reference is encountered; not consulted otherwise.

    Returns
    -------
    str
        The string with every recognised ``${VAR}`` /
        ``${VAR:-default}`` substituted.

    Raises
    ------
    YamlEnvVarError
        If a bare ``${VAR}`` (no default) refers to an environment
        variable that is not set.
    """

    def _substitute(match: re.Match[str]) -> str:
        var_name = match.group("name")
        default = match.group("default")
        env_value = os.environ.get(var_name)
        if env_value is not None:
            return env_value
        if default is not None:
            # ``${VAR:-}`` (empty default) intentionally returns ""
            return default
        source_repr = f" referenced by {source_path}" if source_path else ""
        raise YamlEnvVarError(
            f"Unresolved environment variable ${{{var_name}}}{source_repr}. "
            f"Either set {var_name} in the process environment, or change "
            f"the YAML to use the default syntax ${{{var_name}:-<fallback>}}."
        )

    return _ENV_VAR_PATTERN.sub(_substitute, value)


def _expand_env_vars_in_tree(
    obj: Any,
    *,
    source_path: str | Path | None = None,
) -> Any:
    """Recursively expand ``${VAR}`` in every string scalar *value*.

    Walks dicts and lists. For dicts: keys pass through unchanged
    (only values are rewritten). For lists: each element is recursed
    into. Non-string scalars (int, float, bool, None) pass through.
    Returns the rewritten structure; mutates in place where possible
    (lists) but always returns a value for the caller to use, so the
    caller doesn't need to know whether the input was a container or
    a scalar.
    """
    if isinstance(obj, str):
        return expand_env_vars(obj, source_path=source_path)
    if isinstance(obj, dict):
        # Walk values; leave keys alone (env-var expansion in keys
        # would break the strict-unknown-key rejection contract that
        # callers like scripts/run_walk_forward.py rely on).
        for k, v in obj.items():
            obj[k] = _expand_env_vars_in_tree(v, source_path=source_path)
        return obj
    if isinstance(obj, list):
        for i, v in enumerate(obj):
            obj[i] = _expand_env_vars_in_tree(v, source_path=source_path)
        return obj
    # int, float, bool, None, and any other YAML scalar: pass through.
    return obj


def load_yaml_with_inheritance(
    path: str | Path,
    *,
    _chain: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Load a YAML file, resolving ``extends`` chains and ``${VAR}`` refs.

    Parameters
    ----------
    path : str or Path
        Path to the YAML file.
    _chain : tuple of str
        Internal recursion guard — tracks parent paths to detect cycles.

    Returns
    -------
    dict
        Merged configuration dictionary (child keys override parents),
        with every ``${VAR}`` reference in string values expanded.

    Raises
    ------
    YamlInheritanceError
        On circular references or missing parent files.
    YamlEnvVarError
        On unresolved ``${VAR}`` references with no default supplied.
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

    # Expand env vars in THIS file's tree before merging. Each level
    # of the extends chain gets its own ``source_path``, so an
    # unresolved ``${VAR}`` is reported against the file that wrote
    # it rather than the outermost child (Codex P2 on PR #149). The
    # recursive call below will do the same for the parent's tree,
    # so by the time we merge both sides already hold concrete
    # strings — every leaf carries the correct attribution should a
    # later error reference it.
    _expand_env_vars_in_tree(raw, source_path=file_path)

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

    # Shallow merge: child keys override parent keys. Both sides are
    # already env-var-expanded so the merge is a pure dict op.
    merged: dict[str, Any] = dict(base)
    merged.update(raw)
    return merged
