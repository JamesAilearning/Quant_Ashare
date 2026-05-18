"""Shared job.json IO helpers for operator UI job lifecycle state."""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Iterator
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
