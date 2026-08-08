"""Atomic file writes shared across the data layer.

Several writers (the tushare fetcher, the delisted-registry builder) need the
same temp-file + rename dance so a killed process never leaves a half-written
file that an existence-check resume would later mis-skip. Centralised here at the
``src.data`` top level — the data layer's home (AGENTS.md reserves ``src/core/``
for canonical runtime contracts, not data-ingestion I/O) — so both the
``src.data.tushare`` and ``src.data.pit`` sub-packages import it instead of
re-implementing it per writer.

This module owns the GENERIC atomic-write mechanics only. Format-specific
writers that carry their own cleanup policy (the pickle / JSON / text variants
elsewhere) or that write into an already-atomic staging directory deliberately
do NOT route through here.
"""

from __future__ import annotations

import time
from pathlib import Path

import pandas as pd

# A multi-hour backfill writes tens of thousands of small files. Observed
# 2026-08-08 on a local NVMe volume with 900+ GB free: a long tushare run
# died on ``OSError: [Errno 22] Invalid argument`` mid-write, twice more on
# resume — while 400 rapid writes to the same directory in isolation showed
# a 0/400 failure rate, and the same file wrote fine seconds later. The
# failures coincided with DNS resolution failing in the same process, so
# the trigger is a transient SYSTEM-level I/O stall, not this writer, the
# path, the data, or write density.
#
# The write is retried rather than diagnosed further: one transient stall
# must not cost a three-hour backfill. Atomicity is what makes the retry
# safe — the destination is only ever replaced by a COMPLETE temp file, so
# a failed attempt can be discarded and repeated with no partial state.
_WRITE_ATTEMPTS = 4
_WRITE_BACKOFF_SECONDS = 0.5


def atomic_write_parquet(df: pd.DataFrame, path: Path) -> None:
    """Write ``df`` to ``path`` as parquet atomically.

    A temp sibling (``<path>.tmp``) is written then renamed over ``path``, so a
    crash mid-write leaves either the old file or the complete new one — never a
    truncated parquet that a later resume would treat as already present.

    A failed attempt is retried with exponential backoff (see module notes).
    The temp file is removed between attempts so a partially-written one is
    never renamed into place, and the ORIGINAL error is raised when every
    attempt fails — a caller must still see the real cause, not a retry
    artefact.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    first_error: OSError | None = None
    for attempt in range(_WRITE_ATTEMPTS):
        try:
            df.to_parquet(tmp_path, index=False)
            tmp_path.replace(path)
            return
        except OSError as exc:
            if first_error is None:
                first_error = exc
            # Never leave a partial temp behind: the next attempt writes a
            # fresh one, and a caller inspecting the directory must not see
            # a half-file that looks like work in progress.
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            if attempt + 1 < _WRITE_ATTEMPTS:
                time.sleep(_WRITE_BACKOFF_SECONDS * (2 ** attempt))
    assert first_error is not None
    raise first_error
