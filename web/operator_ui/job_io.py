"""Shared job.json IO helpers for operator UI job lifecycle state."""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if os.name == "nt":
    import msvcrt
else:
    import fcntl


def read_job_json(job_dir: Path) -> dict[str, Any]:
    path = job_dir / "job.json"
    if path.is_file():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            return loaded
    return {}


def write_job_json(job_dir: Path, updates: dict[str, Any]) -> None:
    job_dir.mkdir(parents=True, exist_ok=True)
    with _job_lock(job_dir):
        existing = read_job_json(job_dir)
        existing.update(updates)
        tmp = job_dir / "job.json.tmp"
        tmp.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(tmp, job_dir / "job.json")


@contextlib.contextmanager
def _job_lock(job_dir: Path) -> Iterator[None]:
    lock_path = job_dir / "job.json.lock"
    with open(lock_path, "a+b") as lock_file:
        lock_file.seek(0)
        if os.name == "nt":
            lock_file.write(b"\0")
            lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
        else:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            lock_file.seek(0)
            if os.name == "nt":
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# Unified job listing (UI + CLI) for the Jobs page
# ---------------------------------------------------------------------------

# Path constants — defined here so the listing helpers live close to the
# existing job_dir helpers in the same module.
_JOB_ROOT = Path(__file__).resolve().parents[2] / "output" / "operator_ui" / "jobs"
_RUNS_INDEX = (
    Path(__file__).resolve().parents[2] / "output" / "runs" / "_index.jsonl"
)


@dataclass
class JobSummary:
    """Normalised view of a single run, regardless of launch source."""

    run_id: str
    type: str  # pipeline / walk_forward / tushare_provider
    status: str
    source: str = "ui"  # "ui" or "cli"
    created_at: str = ""
    started_at: str = ""
    finished_at: str = ""
    duration_seconds: float | None = None
    key_metric_label: str = ""
    key_metric_value: str = ""
    config_summary: dict[str, str] = field(default_factory=dict)
    error_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "type": self.type,
            "status": self.status,
            "source": self.source,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": self.duration_seconds,
            "key_metric_label": self.key_metric_label,
            "key_metric_value": self.key_metric_value,
            "config_summary": self.config_summary,
            "error_message": self.error_message,
        }


def _load_ui_jobs() -> list[dict[str, Any]]:
    """Return raw dicts for every UI-launched job directory."""
    from web.operator_ui.progress import build_job_progress

    if not _JOB_ROOT.is_dir():
        return []
    results: list[dict[str, Any]] = []
    for job_dir in sorted(_JOB_ROOT.iterdir(), reverse=True):
        if not job_dir.is_dir():
            continue
        data = read_job_json(job_dir)
        if not data:
            continue
        data["progress"] = build_job_progress(job_dir, data)
        data["_job_dir"] = str(job_dir)
        results.append(data)
    return results


def _load_cli_entries() -> list[dict[str, Any]]:
    """Return raw dicts for every CLI catalog entry."""
    if not _RUNS_INDEX.is_file():
        return []
    entries: list[dict[str, Any]] = []
    with open(_RUNS_INDEX, encoding="utf-8") as f:
        for line in f:
            try:
                record = json.loads(line)
                record["_cli_source"] = True
                entries.append(record)
            except json.JSONDecodeError:
                continue
    return sorted(entries, key=lambda e: str(e.get("completed_at") or ""), reverse=True)


def _normalise_ui_job(raw: dict[str, Any]) -> JobSummary:
    job_id = str(raw.get("job_id") or raw.get("run_id") or "")
    mode = str(raw.get("mode") or "").replace("tushare_provider", "provider")
    status = str(raw.get("status") or "unknown")
    if status == "success":
        status = "completed"

    created = str(raw.get("created_at") or "")
    started = str(raw.get("started_at") or "")
    finished = str(raw.get("ended_at") or "")
    dur = raw.get("duration_seconds") if isinstance(raw.get("duration_seconds"), (int, float)) else None

    key_label, key_value = "", ""
    if status == "running":
        progress = raw.get("progress") if isinstance(raw.get("progress"), dict) else {}
        key_label = "Stage"
        key_value = str(progress.get("label") or status)
    elif status == "completed":
        key_label = "Result"
        key_value = "✓"
    elif status == "failed":
        progress = raw.get("progress") if isinstance(raw.get("progress"), dict) else {}
        key_label = "Failed at"
        key_value = str(progress.get("label") or "?")

    config = raw.get("config")
    if isinstance(raw.get("config_yaml"), str):
        try:
            import yaml
            config = yaml.safe_load(raw["config_yaml"]) if isinstance(config, str) else config
        except Exception:
            pass

    cfg_summary: dict[str, str] = {}
    if isinstance(config, dict):
        inst = config.get("instruments", "")
        if inst:
            cfg_summary["instruments"] = str(inst) if isinstance(inst, str) else ",".join(inst) if isinstance(inst, list) else str(inst)
        model = config.get("model_type", "")
        if model:
            cfg_summary["model"] = str(model)

    error_msg = str(raw.get("stop_error") or raw.get("error") or "")

    return JobSummary(
        run_id=job_id[:40],
        type=mode,
        status=status,
        source="ui",
        created_at=created,
        started_at=started,
        finished_at=finished,
        duration_seconds=dur,
        key_metric_label=key_label,
        key_metric_value=key_value,
        config_summary=cfg_summary,
        error_message=error_msg,
    )


def _normalise_cli_entry(raw: dict[str, Any]) -> JobSummary:
    run_id = str(raw.get("run_id") or "")[:40]
    engine = str(raw.get("engine") or "")
    etype = engine if engine else "unknown"
    status = str(raw.get("status") or "completed")
    created = str(raw.get("completed_at") or "")
    dur = raw.get("duration_seconds") if isinstance(raw.get("duration_seconds"), (int, float)) else None

    key_label, key_value = "", ""
    if status == "completed":
        key_label = "Result"
        key_value = "✓"

    cfg_summary: dict[str, str] = {}
    return JobSummary(
        run_id=run_id,
        type=etype,
        status=status,
        source="cli",
        created_at=created,
        finished_at=created,
        duration_seconds=dur,
        key_metric_label=key_label,
        key_metric_value=key_value,
        config_summary=cfg_summary,
    )


SORT_OPTIONS: tuple[str, ...] = (
    "created_at",
    "duration",
    "status",
    "type",
    "run_id",
)
SORT_DIRECTIONS: tuple[str, ...] = ("desc", "asc")


def list_all_jobs(
    *,
    type_filter: str = "all",
    status_filter: str = "all",
    source_filter: str = "all",
    search: str = "",
    date_from: str = "",
    date_to: str = "",
    sort_by: str = "created_at",
    sort_dir: str = "desc",
    page: int = 1,
    page_size: int = 25,
) -> tuple[list[JobSummary], int]:
    """Return a page of unified job summaries plus the total matched count.

    Filters are applied *before* sort, sort before pagination.

    ``type_filter`` accepts ``"all"`` or one of ``"pipeline"``,
    ``"walk_forward"``, ``"provider"``.

    ``status_filter`` accepts ``"all"``, ``"queued"``, ``"running"``,
    ``"completed"``, ``"failed"``, ``"cancelled"``.

    ``source_filter`` accepts ``"all"``, ``"ui"``, ``"cli"``.

    ``date_from`` / ``date_to`` are inclusive ISO-8601 date strings
    (``YYYY-MM-DD``).  Empty strings disable that side of the range.  The
    range is applied against each job's ``created_at`` (UI jobs) or
    ``completed_at`` (CLI catalog) timestamp.  Malformed dates are
    rejected loudly so caller bugs do not silently widen the result set
    (AGENTS.md #8 "no silent fallback").

    ``sort_by`` is one of :data:`SORT_OPTIONS`; ``sort_dir`` is one of
    :data:`SORT_DIRECTIONS`.  Unknown values raise :class:`ValueError`.
    """
    if sort_by not in SORT_OPTIONS:
        raise ValueError(
            f"sort_by={sort_by!r} not in {SORT_OPTIONS}; "
            "extend SORT_OPTIONS if a new key is required."
        )
    if sort_dir not in SORT_DIRECTIONS:
        raise ValueError(
            f"sort_dir={sort_dir!r} not in {SORT_DIRECTIONS}."
        )
    _parse_date_or_raise(date_from, field="date_from")
    _parse_date_or_raise(date_to, field="date_to")

    # Load raw data
    ui_raw = _load_ui_jobs()
    cli_raw = _load_cli_entries()

    # Normalise
    all_items: list[JobSummary] = []
    for raw in ui_raw:
        all_items.append(_normalise_ui_job(raw))
    for raw in cli_raw:
        all_items.append(_normalise_cli_entry(raw))

    # Filter
    filtered = _apply_filters(
        all_items,
        type_filter,
        status_filter,
        source_filter,
        search,
        date_from,
        date_to,
    )

    # Sort
    sorted_items = _apply_sort(filtered, sort_by, sort_dir)

    # Paginate — cumulative for load-more UX (page N returns first N*size items)
    total = len(sorted_items)
    page_items = sorted_items[: page * page_size]

    return page_items, total


def _parse_date_or_raise(value: str, *, field: str) -> None:
    """Validate ISO-date string; raise on malformed input (no silent fallback)."""
    if not value:
        return
    from datetime import date

    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            f"{field}={value!r} is not a valid ISO date (YYYY-MM-DD): {exc}"
        ) from exc


def _job_timestamp(item: JobSummary) -> str:
    """Return the canonical date-stamp for filter / sort purposes.

    For UI jobs prefer ``created_at``; for CLI catalog entries the
    timestamp lives in ``finished_at`` (the catalog records
    ``completed_at`` which is normalised into both).
    """
    return item.created_at or item.finished_at or ""


def _apply_filters(
    items: list[JobSummary],
    type_filter: str,
    status_filter: str,
    source_filter: str,
    search: str,
    date_from: str = "",
    date_to: str = "",
) -> list[JobSummary]:
    result: list[JobSummary] = []
    search_lower = search.strip().lower()
    for item in items:
        if type_filter != "all" and item.type != type_filter:
            continue
        if status_filter != "all" and item.status != status_filter:
            continue
        if source_filter != "all" and item.source != source_filter:
            continue
        if date_from or date_to:
            stamp = _job_timestamp(item)
            if not stamp:
                # No timestamp at all — drop on any date filter so the
                # date range is honoured rather than silently widened.
                continue
            day = stamp[:10]
            if date_from and day < date_from:
                continue
            if date_to and day > date_to:
                continue
        if search_lower:
            combined = (
                f"{item.run_id} {item.type} {item.status} "
                f"{item.key_metric_label} {item.key_metric_value} "
                f"{item.error_message}"
            ).lower()
            if search_lower not in combined:
                continue
        result.append(item)
    return result


def _apply_sort(
    items: list[JobSummary], sort_by: str, sort_dir: str
) -> list[JobSummary]:
    """Return a new sorted list.

    For ``duration`` and ``created_at``, missing values are always
    rendered at the bottom regardless of ``sort_dir``.  Operationally
    "unknown" is never the most/least valued row — it just sits below
    the known rows so it never crowds the active comparison.
    """
    reverse = sort_dir == "desc"

    if sort_by == "duration":
        has = [x for x in items if x.duration_seconds is not None]
        missing = [x for x in items if x.duration_seconds is None]
        return (
            sorted(has, key=lambda x: float(x.duration_seconds or 0.0), reverse=reverse)
            + missing
        )

    if sort_by == "created_at":
        has = [x for x in items if _job_timestamp(x)]
        missing = [x for x in items if not _job_timestamp(x)]
        return (
            sorted(has, key=lambda x: _job_timestamp(x), reverse=reverse)
            + missing
        )

    key_fn: Any
    if sort_by == "status":
        key_fn = lambda x: x.status  # noqa: E731
    elif sort_by == "type":
        key_fn = lambda x: x.type  # noqa: E731
    elif sort_by == "run_id":
        key_fn = lambda x: x.run_id  # noqa: E731
    else:  # pragma: no cover — guarded earlier in list_all_jobs
        raise ValueError(f"sort_by={sort_by!r} not supported")

    return sorted(items, key=key_fn, reverse=reverse)
