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

from pathlib import Path

import pandas as pd


def atomic_write_parquet(df: pd.DataFrame, path: Path) -> None:
    """Write ``df`` to ``path`` as parquet atomically.

    A temp sibling (``<path>.tmp``) is written then renamed over ``path``, so a
    crash mid-write leaves either the old file or the complete new one — never a
    truncated parquet that a later resume would treat as already present.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    df.to_parquet(tmp_path, index=False)
    tmp_path.replace(path)
