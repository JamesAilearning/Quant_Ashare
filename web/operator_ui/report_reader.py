"""Report artifact reader — read-only, no metric recomputation, path-guarded."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


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


def read_pipeline_report(run_dir: Path) -> dict[str, Any]:
    _guard_path(run_dir)
    path = run_dir / "pipeline_report.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def read_walk_forward_report(run_dir: Path) -> dict[str, Any]:
    _guard_path(run_dir)
    path = run_dir / "walk_forward_report.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def read_fold_reports(run_dir: Path) -> list[dict[str, Any]]:
    _guard_path(run_dir)
    folds = []
    for entry in sorted(run_dir.iterdir()):
        if entry.name.startswith("fold_") and entry.name.endswith("_report.json"):
            folds.append(json.loads(entry.read_text(encoding="utf-8")))
    return folds


def read_job_from_catalog(run_dir: Path) -> dict[str, Any]:
    """Read a single job-like entry from the run catalog index."""
    _guard_path(run_dir)
    index_path = Path("output/runs/_index.jsonl")
    if not index_path.is_file():
        return {}
    with open(index_path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                record = json.loads(line)
                if record.get("report_path", "").startswith(str(run_dir)):
                    return record
            except json.JSONDecodeError:
                continue
    return {}


def read_all_catalog_entries() -> list[dict[str, Any]]:
    """Read all entries from the run catalog JSONL."""
    index_path = Path("output/runs/_index.jsonl")
    if not index_path.is_file():
        return []
    entries = []
    with open(index_path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries
