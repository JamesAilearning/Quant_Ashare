"""Inventory + cleanup helper for the ``output/`` directory.

Walk-forward and single-fold pipeline runs accumulate per-fold pickles,
position dumps, prediction artifacts, and manifests under ``output/``.
A few weeks of operator runs can easily reach 10+ GB. This script
inventories the subdirectories of ``output/`` (each one a discrete
run) and optionally deletes ones matching filters.

**By default the script does NOT delete anything.** It prints a
size-sorted table; pass ``--execute`` to actually delete. Filters
combine as AND — pass both ``--older-than`` and ``--keep-last`` to
require BOTH conditions to mark a directory for cleanup.

Usage examples::

    # Just inventory what's there
    python scripts/cleanup_output.py

    # Preview what would be deleted (older than 30 days, but keep last 3)
    python scripts/cleanup_output.py --older-than 30 --keep-last 3

    # Actually delete (be careful)
    python scripts/cleanup_output.py --older-than 30 --keep-last 3 --execute

    # Narrow to a specific output family
    python scripts/cleanup_output.py --include 'walk_forward*'

The script lives under ``scripts/`` because it's an operator utility
— it neither imports ``src/`` nor needs qlib initialised.
"""

from __future__ import annotations

import argparse
import fnmatch
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RunDirEntry:
    """One subdirectory of ``output/`` summarised for the inventory table."""

    path: Path
    size_bytes: int
    mtime: float  # Unix epoch seconds — most-recent mtime among contents


def _human_size(num_bytes: int) -> str:
    """Format bytes as a short string like ``5.2 MB``.

    Returns ``"0 B"`` for negative inputs — defensive against
    miscounted sizes (a malformed file system entry shouldn't
    crash the inventory loop)."""
    n = max(0, int(num_bytes))
    if n < 1024:
        return f"{n} B"
    n_f = float(n)
    for unit in ("KB", "MB", "GB", "TB"):
        n_f = n_f / 1024
        if n_f < 1024 or unit == "TB":
            return f"{n_f:.1f} {unit}"
    return f"{n} B"  # unreachable; satisfies type checker


def _dir_size_and_mtime(path: Path) -> tuple[int, float]:
    """Sum file sizes + return the most-recent file mtime in ``path``.

    Mtime convention: we use the **most recent file** mtime as the
    run's "age" — NOT the directory's own mtime, which often
    reflects the moment ``output_dir.mkdir()`` ran (i.e. ~now) even
    when the contents are weeks old. This matters for resumed runs
    where the dir was created recently but artifacts are old, and
    for runs that have been ``touch``-ed without rewriting files.

    Empty directories report the directory's own mtime so they
    still have a sortable timestamp (rare in practice — production
    runs always emit at least a report JSON).
    """
    total = 0
    newest = 0.0
    for entry in path.rglob("*"):
        try:
            if entry.is_file():
                stat = entry.stat()
                total += stat.st_size
                newest = max(newest, stat.st_mtime)
        except OSError:
            # Files in flight, locked, or removed between scan and
            # stat — skip without crashing the inventory.
            continue
    if newest == 0.0:
        # No files seen; fall back to the directory's own mtime so
        # the entry is still sortable.
        newest = path.stat().st_mtime
    return total, newest


def discover_run_dirs(
    root: Path | str,
    *,
    include: str = "*",
) -> list[RunDirEntry]:
    """Inventory direct subdirectories of ``root`` matching ``include``.

    Returns an empty list when ``root`` does not exist (no exception
    — a missing output dir is benign for the cleanup script).

    ``include`` is a glob (``fnmatch`` syntax) applied to the
    subdirectory's leaf name. Default ``*`` matches everything.
    """
    root = Path(root)
    if not root.is_dir():
        return []
    out: list[RunDirEntry] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if not fnmatch.fnmatch(child.name, include):
            continue
        size, mtime = _dir_size_and_mtime(child)
        out.append(RunDirEntry(path=child, size_bytes=size, mtime=mtime))
    return out


def select_candidates(
    entries: list[RunDirEntry],
    *,
    older_than_days: float | None = None,
    keep_last: int | None = None,
) -> list[RunDirEntry]:
    """Apply filters and return the entries that are cleanup candidates.

    Filters combine as AND:

    * ``older_than_days``  Entry's mtime is more than N days in the
      past. ``None`` disables the check.
    * ``keep_last``  Keep the N most-recent entries (sorted by
      mtime desc); everything else is a candidate. ``None`` disables.

    With no filters, every entry is a candidate (operator wants a
    nuke-everything preview). ``keep_last=0`` also returns every
    entry (literal interpretation: keep zero).

    Result order matches the input order so callers can render a
    stable table.
    """
    if not entries:
        return []
    keep: set[Path] = set()

    if keep_last is not None and keep_last > 0:
        recent = sorted(entries, key=lambda e: e.mtime, reverse=True)
        for entry in recent[:keep_last]:
            keep.add(entry.path)

    candidates: list[RunDirEntry] = []
    now = time.time()
    for entry in entries:
        if entry.path in keep:
            continue
        if older_than_days is not None:
            age_days = (now - entry.mtime) / 86400.0
            if age_days < older_than_days:
                continue
        candidates.append(entry)
    return candidates


def _format_age(mtime: float, *, now: float | None = None) -> str:
    age_seconds = (now if now is not None else time.time()) - mtime
    age_days = age_seconds / 86400.0
    if age_days < 1:
        return f"{age_days * 24:.1f}h"
    if age_days < 30:
        return f"{age_days:.1f}d"
    return f"{age_days / 30:.1f}mo"


def _print_table(entries: list[RunDirEntry], *, label: str) -> None:
    if not entries:
        print(f"  ({label}: nothing)")
        return
    print(f"  {label}:")
    # Sort by size desc so the biggest offenders are at the top.
    for entry in sorted(entries, key=lambda e: e.size_bytes, reverse=True):
        print(
            f"    {entry.path.name:<40s}  "
            f"{_human_size(entry.size_bytes):>10s}  "
            f"age {_format_age(entry.mtime):>6s}",
        )


def _delete(entry: RunDirEntry) -> None:
    """Recursively delete a run directory. Errors propagate — the
    operator passed ``--execute`` knowing what they were doing, and a
    silent failure would defeat the purpose."""
    shutil.rmtree(entry.path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cleanup_output.py",
        description=(
            "Inventory and optionally clean up the project's output/ "
            "directory. By default this is a read-only preview — pass "
            "--execute to actually delete."
        ),
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("output"),
        help="Root directory to scan (default: ``output/``).",
    )
    parser.add_argument(
        "--include",
        type=str,
        default="*",
        help=(
            "fnmatch glob applied to subdirectory leaf names. "
            "Example: ``walk_forward*`` matches walk_forward, "
            "walk_forward_mined, etc."
        ),
    )
    parser.add_argument(
        "--older-than",
        type=float,
        default=None,
        metavar="DAYS",
        help="Mark dirs not touched within this many days for cleanup.",
    )
    parser.add_argument(
        "--keep-last",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Keep the N most-recent dirs (by mtime). Combines with "
            "--older-than via AND."
        ),
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        default=False,
        help=(
            "Actually delete the cleanup candidates. Without this "
            "flag the script only previews."
        ),
    )
    ns = parser.parse_args(argv)

    root = ns.root
    if not root.is_dir():
        print(f"output root {root!s} does not exist — nothing to clean.")
        return 0

    entries = discover_run_dirs(root, include=ns.include)
    if not entries:
        print(f"No run directories under {root!s} match {ns.include!r}.")
        return 0

    total_size = sum(e.size_bytes for e in entries)
    print(
        f"Found {len(entries)} run dir(s) under {root!s} "
        f"(total {_human_size(total_size)}):",
    )

    candidates = select_candidates(
        entries,
        older_than_days=ns.older_than,
        keep_last=ns.keep_last,
    )
    candidate_paths = {c.path for c in candidates}
    keep = [e for e in entries if e.path not in candidate_paths]
    _print_table(keep, label="KEEP")
    _print_table(candidates, label="CANDIDATES")

    if not candidates:
        print("Nothing to delete.")
        return 0

    if not ns.execute:
        print(
            "\n(Preview only — pass --execute to delete the "
            f"{len(candidates)} candidate dir(s) above.)"
        )
        return 0

    print(f"\nDeleting {len(candidates)} dir(s)…")
    for entry in candidates:
        print(
            f"  rm -rf {entry.path}  "
            f"({_human_size(entry.size_bytes)})"
        )
        _delete(entry)
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
