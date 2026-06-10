"""Persist a Tushare fetch run's coverage + holes to ``fetch_manifest.json``.

P3-4b — the read / write / merge / clear of ``{output_dir}/fetch_manifest.json``,
the on-disk record of what a fetch run (P3-4a) covered and where it left holes.
4a kept holes in memory and surfaced them via exit code only; this layer persists
them so a later run can SELF-HEAL (drop a hole whose unit was re-fetched) and so a
downstream gate (P3-4c) can refuse a holey dump.

This module deliberately does NOT gate any consumer (that is P3-4c) and does NOT
drive incremental fetches (that is P3-6). It is pure manifest CRUD + merge.

Schema
------
::

    {
      "schema_version": 1,
      "fetched_at": "2026-06-09T04:30:00+00:00",
      "endpoints": {
        "daily": {
          "status": "complete" | "holes",
          "coverage_end_date": "20251231",
          "units_written": 12345,
          "holes": [
            {"unit": "ts_code=600001.SH year=2020",
             "reason_class": "transient", "attempts": 5, "last_error": "..."}
          ]
        }
      }
    }

Self-heal merge (the red line)
------------------------------
:func:`merge_manifest` carries the previous run's manifest forward:

- An endpoint that ran THIS run (present in ``current``) is re-resolved from
  ``current``. A full-scope re-run re-attempts every missing unit, so
  ``current``'s holes ARE the post-run truth: a prev hole ABSENT from
  ``current``'s holes self-healed and is dropped; a recurring hole is kept with
  its attempt count ACCUMULATED across runs.
- An endpoint that did NOT run this run (absent from ``current``) is preserved
  verbatim from ``prev`` — its holes are neither dropped (that would be a silent
  partial) nor touched.

Precision: a hole is removed ONLY when its exact ``(endpoint, unit)`` was
re-attempted-and-succeeded this run; it is never dropped for an endpoint that did
not run, and a still-failing unit is never silently healed. Full-scope runs are
assumed — an incremental NARROWER-range re-run is P3-6, which must extend the
merge with per-unit scope awareness before relying on it for a partial range.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.data.tushare.fetcher import FetchHole, TushareFetchResult

SCHEMA_VERSION = 1
MANIFEST_FILENAME = "fetch_manifest.json"

# Endpoints whose holes are DATE-scoped: a narrower-range re-run does not
# re-attempt every prior hole, so self-healing one is only safe when this run's
# range covers the prior coverage. The merge refuses a narrower-scope merge for
# these (codex P1). stock_basic is the exception — it re-fetches the whole ticker
# universe regardless of date range, so its holes always re-attempt.
_DATE_SCOPED_ENDPOINTS = frozenset({
    "daily", "adj_factor", "daily_basic", "namechange", "suspend_d", "index_weight",
})


class FetchManifestError(RuntimeError):
    """Raised on an unreadable / unknown-schema manifest — fail-loud rather than
    silently parsing an unrecognized shape into wrong coverage / hole state."""


@dataclass(frozen=True)
class EndpointCoverage:
    """Per-endpoint coverage + hole state recorded in the manifest."""

    status: str  # "complete" | "holes"
    coverage_start_date: str  # YYYYMMDD
    coverage_end_date: str  # YYYYMMDD
    units_written: int
    holes: tuple[FetchHole, ...]


@dataclass(frozen=True)
class FetchManifest:
    """The whole ``fetch_manifest.json`` document (one per ``--output-dir``)."""

    schema_version: int
    fetched_at: str  # ISO-8601
    endpoints: dict[str, EndpointCoverage]


def build_manifest(
    results: list[TushareFetchResult],
    holes: tuple[FetchHole, ...],
    coverage_start_date: str,
    coverage_end_date: str,
    *,
    now: datetime | None = None,
) -> FetchManifest:
    """Build THIS run's manifest from the fetcher's ``results`` + ``holes``.

    Only the endpoints that ran (present in ``results``) appear, each tagged with
    this run's ``[coverage_start_date, coverage_end_date]`` so the merge can refuse
    a later narrower-scope run. ``now`` is injectable for tests / determinism — the
    same value-injection pattern as the Phase 2 staleness guard
    (``recommend(..., now=...)``); the production default is the system clock.
    """
    stamp = (now if now is not None else datetime.now(tz=timezone.utc)).isoformat()
    holes_by_ep: dict[str, list[FetchHole]] = {}
    for h in holes:
        holes_by_ep.setdefault(h.endpoint, []).append(h)
    endpoints: dict[str, EndpointCoverage] = {}
    for r in results:
        ep_holes = tuple(holes_by_ep.get(r.endpoint, ()))
        # Coverage reflects FETCHED data: an endpoint that wrote nothing and
        # holed nothing was entirely SKIPPED by resume (its files already
        # existed), so THIS run established no coverage for it — record it empty
        # rather than claim the requested range. Otherwise a first manifest built
        # over a pre-existing narrow dump (no prior manifest to fall back to)
        # would over-claim the requested wide range (codex P2). A written or
        # holed endpoint records the requested range. The merge keeps the prior
        # actually-fetched coverage when a later run skips (units_written == 0).
        established = r.files_written > 0 or bool(ep_holes)
        endpoints[r.endpoint] = EndpointCoverage(
            status="holes" if ep_holes else "complete",
            coverage_start_date=coverage_start_date if established else "",
            coverage_end_date=coverage_end_date if established else "",
            units_written=r.files_written,
            holes=ep_holes,
        )
    return FetchManifest(SCHEMA_VERSION, stamp, endpoints)


def merge_manifest(
    prev: FetchManifest | None, current: FetchManifest,
) -> FetchManifest:
    """Self-heal merge of ``current`` onto ``prev`` — see the module docstring.

    ``prev`` is ``None`` on the first run (no manifest yet) → ``current`` is
    returned unchanged.
    """
    if prev is None:
        return current
    # Start from prev so endpoints that did NOT run this run are preserved.
    merged: dict[str, EndpointCoverage] = dict(prev.endpoints)
    for ep, cur in current.endpoints.items():
        prev_ep = prev.endpoints.get(ep)
        # codex P1: a self-heal drops a prev hole that is absent from this run's
        # holes — valid ONLY if this run RE-ATTEMPTED that hole. For a date-scoped
        # endpoint that HAS prior holes, a NARROWER range does not re-attempt the
        # out-of-range holed units, so a narrower-scope merge would silently drop
        # their holes (a silent partial). Refuse it (a hole-free narrower run is
        # harmless; narrower incremental fetches are P3-6).
        if (
            prev_ep is not None
            and prev_ep.holes
            and ep in _DATE_SCOPED_ENDPOINTS
            and (cur.coverage_start_date > prev_ep.coverage_start_date
                 or cur.coverage_end_date < prev_ep.coverage_end_date)
        ):
            raise FetchManifestError(
                f"refusing narrower-scope merge for endpoint {ep!r}: this run "
                f"covered [{cur.coverage_start_date}, {cur.coverage_end_date}] but "
                f"the manifest already covers [{prev_ep.coverage_start_date}, "
                f"{prev_ep.coverage_end_date}] with unresolved holes. A narrower "
                f"range does not re-attempt every prior hole, so self-healing would "
                f"silently drop out-of-range holes. Re-run the full range, or clear "
                f"the manifest (narrower incremental fetches are P3-6)."
            )
        prev_holes = {h.unit: h for h in (prev_ep.holes if prev_ep else ())}
        carried: list[FetchHole] = []
        for h in cur.holes:
            # A still-failing unit carries its cumulative attempt count forward.
            prior = prev_holes.get(h.unit)
            attempts = prior.attempts + h.attempts if prior else h.attempts
            carried.append(replace(h, attempts=attempts))
        # codex P1-B: coverage reflects what was ACTUALLY fetched, not what was
        # requested. A run that wrote NOTHING for this endpoint (every file
        # skipped by resume — e.g. a wider run that skips a prior narrow aggregate
        # file like namechange / suspend_d / index_weight) did NOT establish its
        # requested range, so coverage must NOT advance to it; keep the prior
        # actually-fetched coverage. A run that wrote data spans the widest range.
        if prev_ep is None:
            cov_start, cov_end = cur.coverage_start_date, cur.coverage_end_date
        elif cur.units_written > 0:
            cov_start = _min_yyyymmdd(prev_ep.coverage_start_date, cur.coverage_start_date)
            cov_end = _max_yyyymmdd(prev_ep.coverage_end_date, cur.coverage_end_date)
        else:
            cov_start, cov_end = prev_ep.coverage_start_date, prev_ep.coverage_end_date
        merged[ep] = EndpointCoverage(
            status="holes" if carried else "complete",
            coverage_start_date=cov_start,
            coverage_end_date=cov_end,
            units_written=cur.units_written,
            holes=tuple(carried),
        )
    return FetchManifest(SCHEMA_VERSION, current.fetched_at, merged)


def read_manifest(path: Path) -> FetchManifest | None:
    """Read the manifest at ``path``.

    Not-exists (first run) → ``None`` (a fresh start, NOT an error). An unknown
    ``schema_version`` or malformed JSON → :class:`FetchManifestError` (fail-loud:
    never silently parse an unrecognized shape).
    """
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        # codex P2: a corrupt / non-UTF-8 manifest makes read_text raise
        # UnicodeDecodeError BEFORE json.loads — fold it into the fail-loud path
        # too, so the CLI surfaces a clean error rather than a traceback.
        raise FetchManifestError(f"unreadable fetch manifest {path}: {exc}") from exc
    # codex P2: valid JSON that is not an OBJECT (e.g. `[]`, a string, a number)
    # would make the `.get(...)` below raise AttributeError outside the fail-loud
    # path. Reject it as a FetchManifestError so the CLI surfaces it cleanly.
    if not isinstance(raw, dict):
        raise FetchManifestError(
            f"fetch manifest {path} is not a JSON object (got {type(raw).__name__}); "
            f"refusing to parse — delete it to rebuild."
        )
    version = raw.get("schema_version")
    if version != SCHEMA_VERSION:
        raise FetchManifestError(
            f"unknown fetch-manifest schema_version {version!r} in {path} "
            f"(expected {SCHEMA_VERSION}); refusing to parse — delete it to rebuild."
        )
    return _manifest_from_dict(raw)


def write_manifest(path: Path, manifest: FetchManifest) -> None:
    """Atomically write ``manifest`` to ``path`` (temp file + :func:`os.replace`)
    so a crash mid-write never leaves a half-written / corrupt manifest — the old
    file stays intact until the rename swaps the complete new one in."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(_manifest_to_dict(manifest), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(tmp, path)


def clear_manifest(path: Path) -> None:
    """Remove the manifest entirely (for a fresh full rebuild). No-op if absent."""
    path.unlink(missing_ok=True)


def all_holes(manifest: FetchManifest) -> tuple[FetchHole, ...]:
    """Every hole across all endpoints, flattened (for a downstream completeness
    gate / provenance stamp — P3-4c)."""
    return tuple(h for ep in manifest.endpoints.values() for h in ep.holes)


def is_complete(manifest: FetchManifest) -> bool:
    """A fetch is complete when NO endpoint has any hole. A downstream build
    gate (P3-4c) treats a holey — or entirely MISSING — manifest as incomplete
    and refuses unless explicitly overridden."""
    return not any(ep.holes for ep in manifest.endpoints.values())


def covered_endpoints(manifest: FetchManifest) -> frozenset[str]:
    """Endpoints whose coverage was actually ESTABLISHED — both coverage dates
    non-empty. ``build_manifest`` records a SKIPPED endpoint (wrote nothing, holed
    nothing — e.g. a first manifest over a pre-existing dump) with EMPTY coverage,
    so it is NOT covered here. A downstream build gate (P3-4c) treats a required
    endpoint that is absent OR has empty coverage as not fetched — absence of holes
    on an empty-coverage endpoint is NOT confirmation it was fetched."""
    return frozenset(
        name for name, ep in manifest.endpoints.items()
        if ep.coverage_start_date and ep.coverage_end_date
    )


def _max_yyyymmdd(a: str | None, b: str) -> str:
    """Later of two ``YYYYMMDD`` strings (lexicographic == chronological here)."""
    if a is None:
        return b
    return a if a >= b else b


def _min_yyyymmdd(a: str | None, b: str) -> str:
    """Earlier of two ``YYYYMMDD`` strings (lexicographic == chronological here)."""
    if a is None:
        return b
    return a if a <= b else b


def _manifest_to_dict(m: FetchManifest) -> dict[str, Any]:
    return {
        "schema_version": m.schema_version,
        "fetched_at": m.fetched_at,
        "endpoints": {
            ep: {
                "status": cov.status,
                "coverage_start_date": cov.coverage_start_date,
                "coverage_end_date": cov.coverage_end_date,
                "units_written": cov.units_written,
                "holes": [
                    {
                        "unit": h.unit,
                        "reason_class": h.reason_class,
                        "attempts": h.attempts,
                        "last_error": h.last_error,
                    }
                    for h in cov.holes
                ],
            }
            for ep, cov in m.endpoints.items()
        },
    }


def _manifest_from_dict(raw: dict[str, Any]) -> FetchManifest:
    # codex P2: require every field explicitly — a missing `endpoints` (or any
    # per-endpoint / per-hole key) must FAIL LOUD, never silently parse as an
    # empty / partial manifest (which the next merge would treat as "no prior
    # holes" and erase recorded non-run holes).
    try:
        endpoints: dict[str, EndpointCoverage] = {}
        for ep, cov in raw["endpoints"].items():
            holes = tuple(
                FetchHole(
                    endpoint=ep,
                    unit=h["unit"],
                    reason_class=h["reason_class"],
                    attempts=h["attempts"],
                    last_error=h["last_error"],
                )
                for h in cov["holes"]
            )
            endpoints[ep] = EndpointCoverage(
                status=cov["status"],
                coverage_start_date=cov["coverage_start_date"],
                coverage_end_date=cov["coverage_end_date"],
                units_written=cov["units_written"],
                holes=holes,
            )
        return FetchManifest(
            schema_version=raw["schema_version"],
            fetched_at=raw["fetched_at"],
            endpoints=endpoints,
        )
    except (KeyError, TypeError, AttributeError) as exc:
        raise FetchManifestError(
            f"malformed fetch manifest (missing or invalid field: {exc}); "
            "refusing to parse — delete it to rebuild."
        ) from exc
