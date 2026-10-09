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
import stat
import sys
import threading
from collections.abc import Iterator
from dataclasses import dataclass
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
_REPARSE_POINT = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


class AlreadyRunningError(RuntimeError):
    """Another daily-update run holds the single-flight lock for this provider."""


class SingleFlightSetupError(RuntimeError):
    """The single-flight lock FILE could not be opened (unwritable path, read-only fs,
    permission) — a setup failure distinct from contention; the CLI maps it to a defined
    exit code instead of crashing with an undefined one."""


class LockInspectionError(RuntimeError):
    """An active lock's ordinary one-byte owner-descriptor evidence is unavailable."""


@dataclass(frozen=True)
class LockedByteSnapshot:
    """Immutable coordination evidence, not a live lease or data acceptance."""

    lock_path: Path
    byte: bytes
    device: int
    inode: int
    size: int
    mtime_ns: int
    owner_pid: int


def _inspection_state(info: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_mode, info.st_nlink, int(getattr(info, "st_file_attributes", 0)))


def _inspection_initial_state(path: Path) -> tuple[int, int, int, int, int, int, int] | None:
    """Record the pre-open inode; absence is ineligible, not assumed-zero evidence."""
    try:
        return _inspection_state(path.lstat())
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise LockInspectionError("Cannot establish pre-existing lock inspection state") from exc


def _inspection_ordinary_byte(info: os.stat_result) -> None:
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != 1
            or int(getattr(info, "st_file_attributes", 0)) & _REPARSE_POINT):
        raise LockInspectionError("Lock inspection requires an ordinary single-link one-byte file")


def _inspection_path_state(path: Path) -> os.stat_result:
    """Reject aliases in the leaf and every existing parent without opening a handle."""
    for parent in path.parents:
        info = parent.lstat()
        if (not stat.S_ISDIR(info.st_mode)
                or int(getattr(info, "st_file_attributes", 0)) & _REPARSE_POINT):
            raise LockInspectionError("Lock inspection parent is not an ordinary directory")
    info = path.lstat()
    _inspection_ordinary_byte(info)
    return info


class SingleFlightLockInspection:
    """Lifetime-bound, same-process/thread reading through actual acquired descriptors.

    A directly constructed inspector is inactive. Only the canonical acquisition
    context activates it, and no descriptor is exposed by the public API.
    """

    def __init__(self) -> None:
        self._active = False
        self._owner_pid = os.getpid()
        self._owner_thread = threading.current_thread()
        self._descriptors: dict[Path, int] = {}
        self._initial_states: dict[Path, tuple[int, int, int, int, int, int, int] | None] = {}

    def _activate(self, descriptors: dict[Path, int],
                  initial_states: dict[Path, tuple[int, int, int, int, int, int, int] | None]) -> None:
        self._descriptors = descriptors.copy()
        self._initial_states = initial_states.copy()
        self._active = True

    def _invalidate(self) -> None:
        self._active = False
        self._descriptors.clear()
        self._initial_states.clear()

    def _check_owner(self) -> None:
        if (not self._active or os.getpid() != self._owner_pid
                or threading.current_thread() is not self._owner_thread):
            raise LockInspectionError("Lock inspection requires its active owning process and thread")

    def read_byte(self, resource: Path) -> LockedByteSnapshot:
        """Read exactly byte zero without releasing/reopening the owned lock.

        The descriptor's position is restored even on short reads or read errors.
        Identity, ordinary-file shape and metadata must agree before and after;
        no snapshot is returned when any inspection or restoration fails.
        """
        self._check_owner()
        try:
            path = lock_path_for(Path(os.path.abspath(resource)))
        except (TypeError, ValueError, OSError) as exc:
            raise LockInspectionError("Invalid lock-inspection resource") from exc
        if path not in self._descriptors:
            raise LockInspectionError("Lock inspection resource was not acquired by this context")
        initial = self._initial_states.get(path)
        if initial is None or initial[2] != 1:
            raise LockInspectionError("Lock inspection requires a pre-existing one-byte file")
        fd = self._descriptors[path]
        try:
            before_path = _inspection_path_state(path)
            before_fd = os.fstat(fd)
            _inspection_ordinary_byte(before_fd)
            before = _inspection_state(before_fd)
            if before != initial:
                raise LockInspectionError("Lock inspection pre-existing identity or metadata changed")
            if _inspection_state(before_path) != before:
                raise LockInspectionError("Lock inspection path no longer identifies its owning descriptor")
            position = os.lseek(fd, 0, os.SEEK_CUR)
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                byte = os.read(fd, 1)
            finally:
                os.lseek(fd, position, os.SEEK_SET)
            if len(byte) != 1:
                raise LockInspectionError("Lock inspection did not read exactly one actual byte")
            after_fd = os.fstat(fd)
            _inspection_ordinary_byte(after_fd)
            after_path = _inspection_path_state(path)
            if _inspection_state(after_fd) != before or _inspection_state(after_path) != before:
                raise LockInspectionError("Lock inspection identity or metadata changed while reading")
            self._check_owner()
        except OSError as exc:
            raise LockInspectionError("Owner-descriptor lock inspection failed") from exc
        return LockedByteSnapshot(path, byte, before_fd.st_dev, before_fd.st_ino,
                                  before_fd.st_size, before_fd.st_mtime_ns, self._owner_pid)


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
    with _single_flight(*resources, inspect_bytes=False):
        yield


@contextlib.contextmanager
def single_flight_with_inspection(*resources: Path) -> Iterator[SingleFlightLockInspection]:
    """Use the same canonical acquisition with optional live owner-byte inspection.

    Inspection does not change lock admission, authorize a data retry or accept
    data. Invalid inspection fails closed without releasing the held locks.
    """
    with _single_flight(*resources, inspect_bytes=True) as inspection:
        yield inspection


@contextlib.contextmanager
def _single_flight(*resources: Path, inspect_bytes: bool) -> Iterator[SingleFlightLockInspection]:
    """Sole acquisition body; only opt-in Windows descriptors use binary mode."""
    if not resources:
        raise ValueError("single_flight requires at least one resource path")
    paths = sorted({lock_path_for(Path(os.path.abspath(r))) for r in resources}, key=str)
    held: list[int] = []
    descriptors: dict[Path, int] = {}
    initial_states: dict[Path, tuple[int, int, int, int, int, int, int] | None] = {}
    inspection = SingleFlightLockInspection()
    try:
        for path in paths:
            if inspect_bytes:
                # Canonical acquisition can initialize fresh/empty Windows locks.
                # They still exclude normally, but cannot become original-byte evidence.
                initial_states[path] = _inspection_initial_state(path)
            # Fresh-machine bootstrap: the parent may not exist yet. A real run needs it.
            with contextlib.suppress(OSError):
                path.parent.mkdir(parents=True, exist_ok=True)
            try:
                flags = os.O_CREAT | os.O_RDWR
                if sys.platform == "win32" and inspect_bytes:
                    # Binary must be selected at OPEN, not just at read: the CRT
                    # can strip a trailing Ctrl-Z during a text-mode O_RDWR open.
                    flags |= os.O_BINARY
                fd = os.open(str(path), flags, 0o644)
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
            descriptors[path] = fd
        inspection._activate(descriptors, initial_states)
        yield inspection
    finally:
        inspection._invalidate()
        for fd in reversed(held):
            _unlock(fd)
            os.close(fd)
