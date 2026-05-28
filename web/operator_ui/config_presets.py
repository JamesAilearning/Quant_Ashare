"""Preset helpers for the operator Config & Run page.

``_detect_preset`` in ``pages/config_run.py`` re-walks every preset on
every Streamlit rerun, and rerun frequency on the config page is
extremely high (any widget edit fires one). The two functions exposed
here do all the disk IO — ``list_preset_names`` scans the preset
directory, ``load_preset`` reads + parses a single YAML file — so a
no-cache implementation stat'd and read every preset file on every
keystroke. UI review P1-4 traced visible UI lag to this loop.

Both functions wrap an ``lru_cache``-backed implementation whose key
includes the source file/dir mtime. That means:

* same key, same mtime → O(1) cache hit, zero disk IO.
* same key, mtime changed (operator saved a preset, ran ``cleanup
  output``, hand-edited YAML) → cache miss, fresh read. No TTL guess.
* different key → cache miss as usual.

The cached function returns an **immutable** ``tuple[tuple[str, Any], ...]``
of items; the public wrapper rebuilds a fresh ``dict`` per call so a
caller mutating the returned mapping cannot pollute the cache. Preset
values are flat (no nested dicts in any current preset), so a shallow
copy is sufficient.
"""

from __future__ import annotations

import functools
import re
from pathlib import Path
from typing import Any

import yaml

BUILT_IN_PRESET_NAMES = ("Smoke", "Default", "Production")
CUSTOM_PRESET_NAME = "Custom"

# Cache sizes are deliberately small — operators in practice have one
# active preset directory and < 10 saved custom presets. Keeping the
# caches bounded prevents long-lived UI sessions from accumulating
# stale entries.
_LIST_CACHE_SIZE = 8
_LOAD_CACHE_SIZE = 32


def sanitise_preset_name(raw: str) -> str:
    """Return a filesystem-safe preset stem."""
    return re.sub(r"[^a-zA-Z0-9_-]", "", str(raw or "")).strip("_-")


def _safe_mtime(path: Path) -> float:
    """Return ``path``'s mtime, or 0.0 if it cannot be stat'd.

    Used as part of the cache key so the cache invalidates whenever
    the on-disk source changes. Falling back to 0.0 for missing /
    inaccessible paths is safe because the underlying cached function
    also handles the missing case — they'll consistently return the
    empty result.
    """

    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def list_preset_names(presets_dir: Path) -> tuple[str, ...]:
    """Return built-in presets, saved custom presets, and the Custom sentinel.

    Result is cached against ``(str(presets_dir), dir_mtime)``; saving a
    new preset bumps the directory mtime and invalidates the cache,
    so the operator sees the new entry on the next rerun (no TTL wait).
    """

    return _list_preset_names_cached(
        str(presets_dir),
        _safe_mtime(presets_dir),
    )


@functools.lru_cache(maxsize=_LIST_CACHE_SIZE)
def _list_preset_names_cached(
    presets_dir_str: str,
    _dir_mtime: float,  # noqa: ARG001 — part of the cache key only
) -> tuple[str, ...]:
    presets_dir = Path(presets_dir_str)
    builtin_stems = {name.lower() for name in BUILT_IN_PRESET_NAMES}
    saved: list[str] = []
    seen: set[str] = set()
    if presets_dir.is_dir():
        for path in sorted(presets_dir.glob("*.yaml")):
            name = sanitise_preset_name(path.stem).lower()
            if not name or name in builtin_stems or name in seen:
                continue
            saved.append(name)
            seen.add(name)
    return (*BUILT_IN_PRESET_NAMES, *saved, CUSTOM_PRESET_NAME)


def load_preset(presets_dir: Path, name: str) -> dict[str, Any]:
    """Load a preset by display name or saved preset stem.

    Result is cached against ``(str(path), file_mtime)``. Each call
    constructs a fresh ``dict`` from the cached items tuple so a
    downstream caller cannot accidentally mutate the cache.
    """

    safe_name = sanitise_preset_name(name).lower()
    if not safe_name:
        return {}
    path = presets_dir / f"{safe_name}.yaml"
    items = _load_preset_cached(str(path), _safe_mtime(path))
    return dict(items)


@functools.lru_cache(maxsize=_LOAD_CACHE_SIZE)
def _load_preset_cached(
    path_str: str,
    _file_mtime: float,  # noqa: ARG001 — part of the cache key only
) -> tuple[tuple[str, Any], ...]:
    """Read + parse the preset YAML; return as an immutable tuple of items.

    Using an items tuple (rather than a dict) for the cached value
    means the wrapper above gets a fresh ``dict`` on every call,
    sidestepping the "callers mutate the cached value" footgun that
    bites ``lru_cache`` of mutable returns. Preset YAML in this
    project is flat (no nested dicts), so a shallow ``dict(items)``
    copy is sufficient — a deepcopy would be safer if presets ever
    grow nested values.
    """

    path = Path(path_str)
    if not path.is_file():
        return ()
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return ()
    if not isinstance(loaded, dict):
        return ()
    return tuple(loaded.items())


def clear_preset_caches() -> None:
    """Drop both LRU caches. Useful in tests and rare runtime cases
    (e.g., the operator manually edits a YAML and Streamlit's session
    doesn't naturally rerender)."""

    _list_preset_names_cached.cache_clear()
    _load_preset_cached.cache_clear()
