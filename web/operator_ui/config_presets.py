"""Preset helpers for the operator Config & Run page."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

BUILT_IN_PRESET_NAMES = ("Smoke", "Default", "Production")
CUSTOM_PRESET_NAME = "Custom"


def sanitise_preset_name(raw: str) -> str:
    """Return a filesystem-safe preset stem."""
    return re.sub(r"[^a-zA-Z0-9_-]", "", str(raw or "")).strip("_-")


def list_preset_names(presets_dir: Path) -> tuple[str, ...]:
    """Return built-in presets, saved custom presets, and the Custom sentinel."""
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
    """Load a preset by display name or saved preset stem."""
    safe_name = sanitise_preset_name(name).lower()
    if not safe_name:
        return {}
    path = presets_dir / f"{safe_name}.yaml"
    if not path.is_file():
        return {}
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return {}
    return loaded if isinstance(loaded, dict) else {}
