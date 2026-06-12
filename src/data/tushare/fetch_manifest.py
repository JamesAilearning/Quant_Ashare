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
          "units_verified": 678,
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

from src.data.tushare.fetch_types import FetchHole, TushareFetchResult

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
    """Per-endpoint coverage + hole state recorded in the manifest.

    ``units_verified`` (P3-7b): files the freshness rule positively confirmed
    complete this run — established coverage on par with written units (codex
    P2 on PR #240: a first sweep over an already-complete dump writes nothing
    yet must not record empty coverage). Absent in pre-P3-7b manifests; read
    tolerantly as 0."""

    status: str  # "complete" | "holes"
    coverage_start_date: str  # YYYYMMDD
    coverage_end_date: str  # YYYYMMDD
    units_written: int
    holes: tuple[FetchHole, ...]
    units_verified: int = 0


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
        # Coverage reflects FETCHED-or-VERIFIED data: an endpoint that wrote
        # nothing, holed nothing, AND verified nothing was entirely BLIND-
        # skipped by resume (files merely existed), so THIS run established no
        # coverage for it — record it empty rather than claim the requested
        # range; otherwise a first manifest built over a pre-existing narrow
        # dump would over-claim the requested wide range (codex P2 on #234).
        # A unit the freshness rule POSITIVELY confirmed complete counts the
        # same as a written one (codex P2 on #240: the first P3-7b sweep over
        # an already-complete dump writes nothing, yet its full-range
        # verification is exactly what coverage means). The merge keeps the
        # prior coverage when a later run establishes nothing.
        established = (
            r.files_written > 0 or bool(ep_holes) or r.units_verified > 0
        )
        endpoints[r.endpoint] = EndpointCoverage(
            status="holes" if ep_holes else "complete",
            coverage_start_date=coverage_start_date if established else "",
            coverage_end_date=coverage_end_date if established else "",
            units_written=r.files_written,
            holes=ep_holes,
            units_verified=r.units_verified,
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
        # P3-7b: an endpoint that ran but ESTABLISHED nothing (wrote no file,
        # holed no unit — everything satisfied the freshness rule / resume) is
        # a no-op for the manifest: preserve the prior record verbatim. The
        # general loop below would otherwise treat cur's empty hole list as
        # "every prior hole self-healed" although nothing was re-attempted —
        # unreachable via the CLI (force_retry re-attempts every prior hole)
        # but a real trap for library callers.
        if (
            prev_ep is not None
            and cur.units_written == 0
            and not cur.holes
            and not cur.coverage_end_date
        ):
            merged[ep] = prev_ep
            continue
        # codex P1: a self-heal drops a prev hole that is absent from this run's
        # holes — valid ONLY if this run RE-ATTEMPTED that hole. For a date-scoped
        # endpoint that HAS prior holes, a NARROWER range does not re-attempt the
        # out-of-range holed units, so a narrower-scope merge would silently drop
        # their holes (a silent partial). Refuse it (a hole-free narrower run is
        # harmless; narrower incremental fetches are P3-6). The comparison only
        # applies when this run ESTABLISHED coverage — an empty-string sentinel
        # ("" sorts before every date) must not fake a narrower range (P3-7b).
        if (
            prev_ep is not None
            and prev_ep.holes
            and ep in _DATE_SCOPED_ENDPOINTS
            and cur.coverage_end_date
            and (cur.coverage_start_date > prev_ep.coverage_start_date
                 or cur.coverage_end_date < prev_ep.coverage_end_date)
        ):
            raise FetchManifestError(
                f"refusing narrower-scope merge for endpoint {ep!r}: this run "
                f"covered [{cur.coverage_start_date}, {cur.coverage_end_date}] but "
                f"the manifest already covers [{prev_ep.coverage_start_date}, "
                f"{prev_ep.coverage_end_date}] with unresolved holes. A narrower "
                f"range does not re-attempt every prior hole, so self-healing would "
                f"silently drop out-of-range holes. Re-run the full range, or pass "
                f"--reset-manifest for a deliberate fresh start."
            )
        # P3-7b truthfulness: refuse to UNION two coverage ranges separated by
        # a never-fetched gap. min/max below would otherwise fabricate
        # "complete [prev_start, cur_end]" over years no run ever requested —
        # coverage+holes must together reflect the real state of every unit.
        # Adjacent (gap <= 1 calendar day) or overlapping ranges merge fine.
        # DATE-SCOPED endpoints only, same as the narrower-scope guard (codex
        # P2 on #240): stock_basic re-fetches the whole universe regardless of
        # the requested dates, so disjoint request ranges are meaningless for
        # it and must not fail a run that genuinely refreshed the snapshots.
        if (
            prev_ep is not None
            and ep in _DATE_SCOPED_ENDPOINTS
            and (cur.units_written > 0 or cur.units_verified > 0)
            and cur.coverage_start_date
            and prev_ep.coverage_end_date
            and (_days_between(prev_ep.coverage_end_date, cur.coverage_start_date) > 1
                 or _days_between(cur.coverage_end_date, prev_ep.coverage_start_date) > 1)
        ):
            raise FetchManifestError(
                f"refusing disjoint coverage merge for endpoint {ep!r}: this run "
                f"covered [{cur.coverage_start_date}, {cur.coverage_end_date}] but "
                f"the manifest covers [{prev_ep.coverage_start_date}, "
                f"{prev_ep.coverage_end_date}] — the gap between them was never "
                f"fetched, and unioning the ranges would claim it as covered. "
                f"Re-run with a range that overlaps or extends the existing "
                f"coverage, or pass --reset-manifest for a deliberate fresh start."
            )
        prev_holes = {h.unit: h for h in (prev_ep.holes if prev_ep else ())}
        carried: list[FetchHole] = []
        for h in cur.holes:
            # A still-failing unit carries its cumulative attempt count forward.
            prior = prev_holes.get(h.unit)
            attempts = prior.attempts + h.attempts if prior else h.attempts
            carried.append(replace(h, attempts=attempts))
        # codex P1-B: coverage reflects what was ACTUALLY fetched (or, P3-7b,
        # freshness-VERIFIED), not what was requested. A run that established
        # NOTHING for this endpoint (every file blind-skipped by resume — e.g.
        # a wider run that skips a prior narrow aggregate file like namechange
        # / suspend_d / index_weight) must NOT advance coverage; keep the
        # prior coverage. A run that wrote or verified data spans the widest
        # range — verified-fresh files were positively confirmed against this
        # run's range, so extending over them is truthful (codex P2 on #240).
        if prev_ep is None:
            cov_start, cov_end = cur.coverage_start_date, cur.coverage_end_date
        elif cur.units_written > 0 or cur.units_verified > 0:
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
            units_verified=cur.units_verified,
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
    """Later of two ``YYYYMMDD`` strings (lexicographic == chronological here).
    ``None`` AND the empty-string "coverage not established" sentinel both mean
    "no prior value" — "" must never win a comparison (P3-7b)."""
    if not a:
        return b
    return a if a >= b else b


def _min_yyyymmdd(a: str | None, b: str) -> str:
    """Earlier of two ``YYYYMMDD`` strings (lexicographic == chronological here).
    ``None`` AND the empty-string "coverage not established" sentinel both mean
    "no prior value" — "" sorts before every date and would otherwise stick
    forever (P3-7b)."""
    if not a:
        return b
    return a if a <= b else b


def _days_between(a_yyyymmdd: str, b_yyyymmdd: str) -> int:
    """Calendar days from ``a`` to ``b`` (positive when ``b`` is later)."""
    a = datetime.strptime(a_yyyymmdd, "%Y%m%d").date()
    b = datetime.strptime(b_yyyymmdd, "%Y%m%d").date()
    return (b - a).days


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
                "units_verified": cov.units_verified,
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
                # Additive P3-7b field — absent in manifests written before
                # the freshness rule; default 0 (nothing verified) keeps them
                # readable without a schema bump.
                units_verified=cov.get("units_verified", 0),
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
