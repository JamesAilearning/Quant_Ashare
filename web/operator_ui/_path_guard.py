"""Shared output path guards for operator UI artifact readers."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ALLOWED_ROOTS: tuple[Path, ...] | None = None


def allowed_output_roots() -> tuple[Path, ...]:
    if _ALLOWED_ROOTS is not None:
        return tuple(root.resolve() for root in _ALLOWED_ROOTS)
    output_root = PROJECT_ROOT / "output"
    return (output_root.resolve(), (output_root / "operator_ui").resolve())


def output_path(*parts: str) -> Path:
    return PROJECT_ROOT.joinpath("output", *parts)


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def guard_output_path(path: Path, roots: Iterable[Path] | None = None) -> None:
    resolved = path.resolve()
    allowed_roots = tuple(roots) if roots is not None else allowed_output_roots()
    for root in allowed_roots:
        if _is_under(resolved, root):
            return
    raise ValueError(f"Path {path} is outside allowed roots {allowed_roots}")
