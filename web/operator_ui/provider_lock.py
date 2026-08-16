"""更新单飞锁的 web 侧镜像 — 出单与换库串行化的权威依据。

The recommend execution must serialize against the updater: the bundle
swap's two-rename window is not reader-concurrent, and the STATUS
ARTIFACT is only advisory — its write is deliberately best-effort
(a failed write never changes the updater's exit code) and a >6h
"stale" running record may still be a live, abnormally slow process
(codex #440 r2). The authoritative liveness signal is the updater's OS
advisory lock: the kernel releases it when the holder exits, including
on a crash, so there is no stale state to misread.

web/ must not import the pipeline layer (the CLI process boundary is
the only coupling), so this module MIRRORS the lock mechanics of
``src.data_pipeline.single_flight`` for the PROVIDER resource only —
same sibling lock path, same byte-0 ``msvcrt.locking`` /
``fcntl.flock`` non-blocking exclusive semantics. The mirror is pinned
by behavioral tests that prove exclusion against the real ``src``
implementation in BOTH directions (holding here refuses there and vice
versa) — string parity alone could drift.

Holding the lock here means: a starting updater refuses fast with its
normal exit 17, and a running updater makes the recommend refuse. The
lock FILE is left on disk between holds (unlinking would break the
primitive — a deleted-but-still-open inode no longer excludes a
re-created path).
"""

from __future__ import annotations

import contextlib
import os
import sys
from collections.abc import Iterator
from pathlib import Path

# Platform-conditional advisory-lock primitives; ``sys.platform`` is the
# check mypy narrows on (same pattern as the src implementation).
if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

#: Mirror of the writer's suffix — pinned against ``lock_path_for`` by test.
LOCK_SUFFIX = ".daily_update.lock"


def update_lock_path(provider_dir: Path) -> Path:
    """Mirror of ``single_flight.lock_path_for``: the provider's SIBLING.

    Normalized to absolute first, exactly like ``single_flight()`` does
    before deriving the lock path, so spelling differences map to the
    same lock file.
    """
    normalized = Path(os.path.abspath(provider_dir))
    return normalized.with_name(normalized.name + LOCK_SUFFIX)


@contextlib.contextmanager
def hold_update_lock(provider_dir: Path) -> Iterator[bool]:
    """Try to hold the updater's provider lock; yield True iff held.

    Non-blocking. False = another process (normally the updater) holds
    it, OR the lock file cannot even be opened — in both cases the
    caller cannot PROVE exclusivity and must refuse (fail-closed, the
    sibling of the updater's own setup-failure refusal). The lock is
    released on context exit; the kernel also releases it if this
    process dies.
    """
    path = update_lock_path(provider_dir)
    with contextlib.suppress(OSError):
        path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o644)
    except OSError:
        yield False
        return
    acquired = False
    try:
        try:
            if sys.platform == "win32":
                # msvcrt locks a byte range, which must exist — ensure
                # ≥1 byte first (only when empty: nobody can hold byte 0
                # of a zero-byte file, since locking needs it to exist).
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"\0")
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError:
            acquired = False
        yield acquired
    finally:
        if acquired:
            with contextlib.suppress(OSError):
                if sys.platform == "win32":
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
