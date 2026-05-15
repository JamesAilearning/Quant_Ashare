"""Chart artifact reader — discover PNG files, no recomputation, path-guarded."""

from __future__ import annotations

from pathlib import Path

from web.operator_ui._path_guard import guard_output_path


def _guard_path(path: Path) -> None:
    guard_output_path(path)


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
