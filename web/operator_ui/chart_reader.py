"""Chart artifact reader — discover PNG files, no recomputation, path-guarded."""

from __future__ import annotations

from pathlib import Path

_ALLOWED_ROOTS = (Path("output").resolve(), Path("output").resolve() / "operator_ui")


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _guard_path(path: Path) -> None:
    resolved = path.resolve()
    for root in _ALLOWED_ROOTS:
        if _is_under(resolved, root):
            return
    raise ValueError(f"Path {path} is outside allowed roots {_ALLOWED_ROOTS}")


def discover_charts(run_dir: Path) -> dict[str, Path]:
    """Return a map of chart name → file path for all PNGs in and under run_dir."""
    _guard_path(run_dir)
    charts: dict[str, Path] = {}
    charts_dir = run_dir / "charts"
    search_root = charts_dir if charts_dir.is_dir() else run_dir
    for png in sorted(search_root.rglob("*.png")):
        rel = png.relative_to(search_root)
        label = str(rel.with_suffix("")).replace("\\", " / ").replace("/", " / ")
        charts[label] = png
    return charts
