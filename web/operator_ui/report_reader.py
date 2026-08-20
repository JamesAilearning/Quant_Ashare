"""Report artifact reader — read-only, no metric recomputation, path-guarded."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from web.operator_ui._path_guard import guard_output_path


def _guard_path(path: Path) -> None:
    guard_output_path(path)


def read_pipeline_report(run_dir: Path) -> dict[str, Any]:
    _guard_path(run_dir)
    path = run_dir / "pipeline_report.json"
    if not path.is_file():
        return {}
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return payload


def read_walk_forward_report(run_dir: Path) -> dict[str, Any]:
    _guard_path(run_dir)
    path = run_dir / "walk_forward_report.json"
    if not path.is_file():
        return {}
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return payload


def read_fold_reports(run_dir: Path) -> list[dict[str, Any]]:
    _guard_path(run_dir)
    folds: list[dict[str, Any]] = []
    for entry in sorted(run_dir.iterdir()):
        if entry.name.startswith("fold_") and entry.name.endswith("_report.json"):
            folds.append(json.loads(entry.read_text(encoding="utf-8")))
    return folds
