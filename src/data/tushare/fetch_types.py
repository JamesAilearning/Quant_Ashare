"""Neutral fetch-outcome DTOs shared across layer boundaries (P3-6b).

These dataclasses are the CONTRACT between the fetcher and everything
downstream of it — the fetch manifest (P3-4b), the bundle integrity stamp
(P3-4c), and read-only consumers like the operator UI's 数据检视 page. They
live in their own dependency-free module so that READING a manifest or a
stamp never imports the fetcher/client network stack: the UI inspector must
stay import-clean of fetch machinery (its read-only governance contract),
and ``src.data.tushare.fetcher`` re-exports these names unchanged for every
existing importer.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TushareFetchResult:
    """Per-endpoint summary returned by ``TushareFetcher.fetch``.

    ``units_verified`` (P3-7b, codex P2 on PR #240) counts existing files the
    freshness rule POSITIVELY confirmed complete this run (content reached the
    expected end, or the listing window proves no data can exist). It is
    POSITIVE evidence that establishes manifest coverage — unlike a blind
    watermark/resume skip, which proves nothing and is counted in ``skipped``
    only. Both kinds of skip also count in ``skipped`` (total not-fetched)."""

    endpoint: str
    files_written: int
    rows_total: int
    skipped: int = 0
    units_verified: int = 0


@dataclass(frozen=True)
class FetchHole:
    """A unit that could not be fetched after exhausting retryable retries.

    Recorded by the per-endpoint loops under continue-on-error (P3-4a) and
    surfaced via ``TushareFetcher.holes``. ``last_error`` is the client's
    already-sanitised error string (the token never appears in
    ``TushareClientError`` messages — ``client.py`` is the secrets boundary);
    ``unit`` is a stable human/JSON label so P3-4b can persist it verbatim.
    """

    endpoint: str
    unit: str
    reason_class: str
    attempts: int
    last_error: str


__all__ = ["FetchHole", "TushareFetchResult"]
