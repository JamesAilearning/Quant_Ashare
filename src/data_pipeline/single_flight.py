"""Process-exclusive single-flight lock for the daily-update orchestrator (阶段5 PR-P).

``run_daily_update`` mutates the live qlib provider through a swap that is crash-atomic
but NOT run-concurrent (see ``bundle_swap.swap``), AND it writes fixed-name temp files
under the shared raw inputs (the tushare dump, the delisted registry). Two overlapping
runs would fight over the ``provider`` / ``.bak`` / ``.new`` triplet OR clobber each
other's raw temp files — even with different providers, if they share a raw input. The
PR-O calendar-gate comment designated the scheduler as the mutual-exclusion owner; this
is that guard, made explicit at the CLI entry so any two runs sharing a mutable resource
(provider, tushare dump, or registry) are serialized: the second acquirer fails FAST.

This uses an **OS advisory lock** (``fcntl.flock`` / ``msvcrt.locking``), one per mutable
resource — NOT a pidfile. The kernel releases the lock when the holding process exits,
INCLUDING on a crash or kill, so there is no stale lock to reclaim, no PID-liveness
probing, and no PID-reuse / corrupt-lock wedge (a naive pidfile + stale-reclaim is
inherently racy — two reclaimers can both proceed). A lock file is LEFT on disk between
runs: unlinking it would break the lock (a deleted-but-still-open inode no longer excludes
a freshly re-created path), and its content is irrelevant to correctness.

Assumes a LOCAL filesystem for the locked paths — advisory locks are unreliable over
NFS/SMB. The qlib bundle and raw dump are local-disk artifacts, so this holds.
"""

from __future__ import annotations

import contextlib
import os
import sys
from collections.abc import Iterator
from pathlib import Path

from src.core.logger import get_logger

# Platform-conditional advisory-lock primitives. ``sys.platform`` (not ``os.name``) is the
# check mypy narrows on; otherwise the cross-platform run sees ``fcntl`` / ``msvcrt`` as
# unbound on the other OS. Same pattern as web/operator_ui/job_io.py.
if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

_logger = get_logger(__name__)


class AlreadyRunningError(RuntimeError):
    """Another daily-update run holds the single-flight lock for this provider."""


class SingleFlightSetupError(RuntimeError):
    """The single-flight lock FILE could not be opened (unwritable path, read-only fs,
    permission) — a setup failure distinct from contention; the CLI maps it to a defined
    exit code instead of crashing with an undefined one."""


def lock_path_for(provider_dir: Path) -> Path:
    """The single-flight lock file for ``provider_dir``.

    A SIBLING of the provider dir, not a child: the swap renames the provider dir (and
    its ``.new`` / ``.bak`` siblings) wholesale, so a lock placed inside would be renamed
    away mid-run.
    """
    return provider_dir.with_name(provider_dir.name + ".daily_update.lock")


def _try_lock_exclusive(fd: int) -> bool:
    """Take the OS advisory exclusive lock NON-BLOCKING. True iff acquired.

    On a local filesystem the only realistic ``OSError`` here is "would block" (the lock
    is held by another run); we map that — and, conservatively, any lock-setup error — to
    "could not acquire" so the run refuses rather than proceeding unprotected.
    """
    try:
        if sys.platform == "win32":
            # msvcrt locks a byte range, which must exist — ensure ≥1 byte first. The
            # write only runs when the file is empty (nobody can be holding byte 0 yet,
            # since locking requires that byte to exist).
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(fd: int) -> None:
    """Release the advisory lock (the OS also releases it on close / process exit)."""
    with contextlib.suppress(OSError):
        if sys.platform == "win32":
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)


@contextlib.contextmanager
def single_flight(*resources: Path) -> Iterator[None]:
    """Hold a process-exclusive OS advisory lock on EVERY ``resources`` path.

    Pass every mutable root a run touches — the provider dir AND the shared raw inputs
    (tushare dump, delisted registry). ``run_daily_update`` writes fixed-name temp files
    under those shared paths, so two runs that share ANY of them would clobber each other
    even with different providers; locking each serializes them, while runs that share
    none stay independent. Raises :class:`AlreadyRunningError` if any lock is held.

    Locks are taken in a canonical (sorted) order so two contending runs reach the shared
    lock in the same sequence — exactly one wins, the other refuses cleanly (no
    double-refusal, no deadlock; the locks are non-blocking). The kernel releases every
    lock when the holder exits — including on a crash or kill — so nothing wedges the next
    run. Paths are normalized (absolute) so spelling differences map to the same lock.
    """
    if not resources:
        raise ValueError("single_flight requires at least one resource path")
    paths = sorted({lock_path_for(Path(os.path.abspath(r))) for r in resources}, key=str)
    held: list[int] = []
    try:
        for path in paths:
            # Fresh-machine bootstrap: the parent may not exist yet. A real run needs it.
            with contextlib.suppress(OSError):
                path.parent.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o644)
            except OSError as exc:
                # Unwritable lock path / read-only fs / permission — a SETUP failure, not
                # contention. Surface a typed error the CLI maps to a defined exit code.
                raise SingleFlightSetupError(
                    f"could not open the single-flight lock {path}: {exc}"
                ) from exc
            if not _try_lock_exclusive(fd):
                os.close(fd)
                raise AlreadyRunningError(
                    f"daily_update already running (lock {path} is held by another "
                    "process). Refusing to run concurrently — it releases automatically "
                    "when that run exits."
                )
            held.append(fd)
        yield
    finally:
        for fd in reversed(held):
            _unlock(fd)
            os.close(fd)
