"""Process-exclusive single-flight lock for the daily-update orchestrator (阶段5 PR-P).

``run_daily_update`` mutates the live qlib provider through a swap that is
crash-atomic but NOT run-concurrent (see ``bundle_swap.swap``): between its two
renames the live path briefly does not exist, and two overlapping runs would fight
over the ``provider`` / ``.bak`` / ``.new`` triplet (double rename, ``rmtree`` of a
``.bak`` under the other's feet) and could corrupt the bundle. The PR-O calendar
gate's comment already designates the scheduler as the mutual-exclusion owner; this
is that guard, made explicit at the CLI entry so a scheduled firing and a manual run
(or a hung run and the next day's firing) targeting the SAME provider are serialized:
the second acquirer fails FAST and LOUD rather than racing the swap.

The lock is an ``O_EXCL`` pidfile keyed to the provider dir (a SIBLING, never inside
it — the swap renames the provider dir whole). A STALE lock whose holder PID is
confirmed dead is reclaimed; an UNKNOWN liveness (the probe itself failed) is treated
as still-held — fail-closed, so a transient probe error can never steal a live run's
lock. Single-flight is per-provider: runs against different providers never contend.
"""

from __future__ import annotations

import contextlib
import csv
import os
import platform
import subprocess
from collections.abc import Iterator
from pathlib import Path

from src.core.logger import get_logger

_logger = get_logger(__name__)


class AlreadyRunningError(RuntimeError):
    """Another daily-update run holds the single-flight lock for this provider."""


def lock_path_for(provider_dir: Path) -> Path:
    """The single-flight lock file for ``provider_dir``.

    A SIBLING of the provider dir, not a child: the swap renames the provider dir
    (and its ``.new`` / ``.bak`` siblings) wholesale, so a lock placed inside would
    be renamed away mid-run.
    """
    return provider_dir.with_name(provider_dir.name + ".daily_update.lock")


def _pid_is_alive(pid: int) -> bool | None:
    """True=alive, False=confirmed dead, None=unknown (probe failed).

    NEVER signals on Windows: ``os.kill(pid, 0)`` there routes to
    ``TerminateProcess`` and would KILL the pid (and a reused pid kills an unrelated
    process). POSIX uses the real signal-0 no-op; Windows shells out to ``tasklist``.
    Mirrors ``web/operator_ui/job_manager._pid_is_alive`` — kept local because the
    ``data_pipeline`` layer must not import the UI layer. The three-valued return is
    load-bearing: ``None`` NEVER reads as dead, so a transient probe failure cannot
    reclaim a live holder's lock.
    """
    if pid <= 0:
        return False
    if platform.system() == "Windows":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                shell=False, capture_output=True,
                encoding="utf-8", errors="replace", timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            return None  # probe failed — unknown, NOT dead
        if result.returncode != 0:
            return None  # abnormal tasklist exit (rc==0 for both match/no-match)
        for row in csv.reader((result.stdout or "").splitlines()):
            if len(row) > 1 and row[1].strip() == str(pid):
                return True
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False  # no such process — confirmed dead
    except PermissionError:
        return True  # alive, owned by another user
    except OSError:
        return None  # unexpected probe error — unknown, NOT dead
    return True


def _read_holder_pid(path: Path) -> int | None:
    """The PID recorded in an existing lock file, or None if unreadable/garbage."""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        pid = int(text)
    except ValueError:
        return None
    return pid if pid > 0 else None


def _try_create(path: Path) -> int | None:
    """Atomically create the lock file ``O_EXCL``; None if it already exists."""
    try:
        return os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return None


_MAX_ACQUIRE_ATTEMPTS = 5


def _acquire(path: Path, provider_dir: Path) -> int:
    """Return an open fd for a freshly-created lock, or raise AlreadyRunningError.

    ``O_EXCL`` is the sole arbiter: exactly one racer creates the file. A live or
    unprovable holder is refused (fail-closed). A CONFIRMED-dead holder's stale lock
    is reclaimed by claiming its REMOVAL atomically — ``os.replace`` of a given source
    succeeds for exactly ONE racer; the loser gets ``OSError`` (the source is already
    gone) and retries, so two reclaimers can never both proceed (this is the TOCTOU
    the naive unlink-then-create had). Bounded so persistent contention fails loud
    rather than spinning.
    """
    for _ in range(_MAX_ACQUIRE_ATTEMPTS):
        fd = _try_create(path)
        if fd is not None:
            return fd  # we created it — O_EXCL guarantees we are the sole owner
        holder = _read_holder_pid(path)
        alive = _pid_is_alive(holder) if holder is not None else None
        if alive is not False:  # alive, OR unprovable/empty -> fail-closed
            raise AlreadyRunningError(
                f"daily_update already running for {provider_dir} (lock {path}, "
                f"holder pid {holder}, liveness {'alive' if alive else 'unknown'}). "
                "Refusing to run concurrently. If no daily_update process is running, "
                f"this is a stale/corrupt lock — confirm none is live, then delete "
                f"{path} and re-run."
            )
        # Confirmed-dead holder -> atomically claim the stale lock's removal. A
        # uniquely-named target means concurrent reclaimers do not collide; only the
        # racer whose os.replace finds the source still present wins, the rest retry.
        private = path.with_name(f"{path.name}.stale.{os.getpid()}")
        try:
            os.replace(path, private)
        except OSError:
            continue  # another run already moved the stale lock — retry the create
        _logger.warning(
            "Reclaimed stale single-flight lock %s (holder pid %s confirmed dead).",
            path, holder,
        )
        with contextlib.suppress(OSError):
            os.unlink(private)
        # loop: the next _try_create arbitrates the fresh acquire via O_EXCL
    raise AlreadyRunningError(
        f"daily_update could not acquire {path} after {_MAX_ACQUIRE_ATTEMPTS} "
        "attempts (repeated stale-lock contention). Refusing to run."
    )


@contextlib.contextmanager
def single_flight(provider_dir: Path) -> Iterator[None]:
    """Hold a process-exclusive lock for ``provider_dir`` for the duration.

    Raises :class:`AlreadyRunningError` if a live run already holds it (fail-closed on
    unknown liveness); a CONFIRMED-dead holder's stale lock is reclaimed atomically.
    The lock is released on exit (including if the body or the pid-write raises), so a
    crash mid-acquire never leaves an orphaned lock that wedges the next run.
    """
    path = lock_path_for(provider_dir)
    # Fresh-machine bootstrap: the provider dir's parent may not exist yet. A real run
    # needs it anyway; create it so the O_EXCL open does not crash with a bare OSError.
    with contextlib.suppress(OSError):
        path.parent.mkdir(parents=True, exist_ok=True)
    fd = _acquire(path, provider_dir)
    created = True
    try:
        try:
            os.write(fd, str(os.getpid()).encode("ascii"))
        finally:
            os.close(fd)
        yield
    finally:
        # We created the lock via O_EXCL and no other run reclaims a LIVE holder, so it
        # is ours to remove. Guard against deleting a foreign lock (should not happen):
        # unlink only when it still records our pid OR is our own empty/partial write.
        if created and _read_holder_pid(path) in (os.getpid(), None):
            with contextlib.suppress(OSError):
                path.unlink()
