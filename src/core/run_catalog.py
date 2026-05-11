"""Append-only JSONL catalog of every pipeline / walk-forward run.

Each successful or partial run appends one JSON line to
``output/runs/_index.jsonl`` so operators can query historical runs
without resorting to ``find`` + ``jq``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.core._json_utils import _sanitize_for_json
from src.core.logger import get_logger

_logger = get_logger(__name__)

_DEFAULT_CATALOG_PATH = Path("output/runs/_index.jsonl")


def append_run_record(
    record: dict[str, Any],
    *,
    catalog_path: Path | None = None,
) -> None:
    """Append a single JSON line to the run catalog.

    Thread-safe on POSIX (O_APPEND + single write ≤ PIPE_BUF). On
    Windows the single ``json.dumps`` + ``os.write`` is also safe in
    practice because CPython holds the GIL during the write; for
    multi-process safety use a file lock or a dedicated writer process.
    """
    dest = catalog_path or _DEFAULT_CATALOG_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)

    line = json.dumps(_sanitize_for_json(record), ensure_ascii=False,
                      sort_keys=True, default=str, allow_nan=False) + "\n"

    try:
        with open(dest, "a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        _logger.warning(
            "Run catalog append failed (path=%s) — run results are "
            "still intact in the per-run directory.", dest,
        )


def build_record(
    *,
    engine: str,
    status: str,
    started_at: str | None = None,
    completed_at: str | None = None,
    config_fingerprint: str = "",
    config_summary: dict[str, Any] | None = None,
    headline_metrics: dict[str, Any] | None = None,
    report_path: str | None = None,
    output_dir: str = "",
) -> dict[str, Any]:
    """Build a run-catalog record with consistent schema."""
    return {
        "run_id": _build_run_id(engine, completed_at, config_fingerprint),
        "engine": engine,
        "started_at": started_at,
        "completed_at": completed_at or datetime.now(tz=timezone.utc).isoformat(),
        "status": status,
        "config_fingerprint": config_fingerprint,
        "config_summary": config_summary or {},
        "headline_metrics": headline_metrics or {},
        "report_path": report_path,
        "output_dir": output_dir,
    }


def _build_run_id(engine: str, completed_at: str | None,
                  fingerprint: str) -> str:
    ts = (completed_at or datetime.now(tz=timezone.utc).isoformat())[:19]
    fp = fingerprint[:12] if fingerprint else "no_fingerprint"
    return f"{engine}-{ts}-{fp}".replace(":", "-")
