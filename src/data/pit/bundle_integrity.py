"""The build → recommend fetch-integrity contract (P3-4c).

`QlibBinBuilder` writes ``{bundle_dir}/_fetch_integrity.json`` recording whether
the bundle was built from a HOLEY tushare fetch (P3-4b's ``fetch_manifest`` had
holes, or was missing). `daily_recommend` reads it and REFUSES to recommend from
a holey bundle unless explicitly overridden — a SEPARATE decision from the build
override (``--allow-holey-fetch``). The stamp propagates the FACT (was the fetch
holey?) ONLY, never the authorization: building a holey bundle for research /
inspection does not sanction trading on its recommendations. Each downstream
boundary must opt in to partial data on its own.

This is a deliberately MINIMAL completeness contract. The richer bundle-provenance
manifest + atomic-swap orchestration is P3-6, which may fold this stamp into a
larger document; P3-4c only defines the gate + the minimal stamp.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.data.tushare.fetch_types import FetchHole

SCHEMA_VERSION = 1
INTEGRITY_FILENAME = "_fetch_integrity.json"


class BundleIntegrityError(RuntimeError):
    """Raised on an unreadable / unknown-schema integrity stamp — fail-loud rather
    than silently parsing an unrecognized shape into wrong integrity state."""


@dataclass(frozen=True)
class BundleIdentity:
    """A bundle's content identity (PR-G+I). Folded into the SAME stamp as the
    holey-fetch provenance so there is ONE bundle sidecar written on the build
    path, replacing the never-written ``bundle_manifest.json`` as the identity
    source for the feature-cache key, the walk-forward freshness check, the
    resume fingerprint, and the UI bundle-health banner.

    ``content_hash`` is a sha256 over ``calendars/day.txt`` ONLY (the same scope
    as :func:`src.data.bundle_manifest.compute_bundle_content_hash`); it is a
    cheap, deterministic bundle-version key, NOT a full-bin integrity guarantee
    — an out-of-band edit to a single ticker bin that leaves the calendar
    unchanged does not change it.
    """

    tail_date: str  # last calendar trading day (ISO date)
    content_hash: str  # sha256 of calendars/day.txt
    instrument_count: int
    calendar_start: str  # first calendar trading day (ISO date)
    calendar_end: str  # == tail_date; kept explicit for span readability

    @property
    def tag(self) -> str:
        """The compact identity string used as the feature-cache key / resume
        fingerprint input: ``<tail_date>@<content_hash>``."""
        return f"{self.tail_date}@{self.content_hash}"


@dataclass(frozen=True)
class BundleIntegrity:
    """The bundle's fetch-integrity stamp (one ``_fetch_integrity.json`` per
    qlib provider dir)."""

    schema_version: int
    built_from_holey_fetch: bool
    built_at: str  # ISO-8601
    holes: tuple[FetchHole, ...]  # the fetch holes (provenance; empty when clean)
    # PR-G+I: content identity. OPTIONAL within schema_version 1 — bundles built
    # before PR-G+I have no identity block (``None``); the schema version is NOT
    # bumped precisely so those v1 stamps (and the daily_recommend gate that reads
    # them) keep working without a forced rebuild.
    identity: BundleIdentity | None = None


def write_bundle_integrity(
    bundle_dir: Path,
    *,
    built_from_holey_fetch: bool,
    holes: tuple[FetchHole, ...] = (),
    identity: BundleIdentity | None = None,
    now: datetime | None = None,
) -> None:
    """Atomically write the bundle's fetch-integrity stamp (temp + ``os.replace``)
    so a crash mid-write never leaves a half-written stamp. ``now`` is injectable
    for tests / determinism (value-injection, as elsewhere); production default is
    the system clock. A clean build writes ``built_from_holey_fetch=False`` with no
    holes; a ``--allow-holey-fetch`` build writes ``True`` plus the holes.

    ``identity`` (PR-G+I) is the bundle's content identity; when omitted the
    ``identity`` key is left out entirely (byte-stable for pre-PR-G+I callers and
    tests)."""
    stamp = (now if now is not None else datetime.now(tz=timezone.utc)).isoformat()
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "built_from_holey_fetch": built_from_holey_fetch,
        "built_at": stamp,
        "holes": [
            {
                "endpoint": h.endpoint,
                "unit": h.unit,
                "reason_class": h.reason_class,
                "attempts": h.attempts,
                "last_error": h.last_error,
            }
            for h in holes
        ],
    }
    if identity is not None:
        payload["identity"] = {
            "tail_date": identity.tail_date,
            "content_hash": identity.content_hash,
            "instrument_count": identity.instrument_count,
            "calendar_start": identity.calendar_start,
            "calendar_end": identity.calendar_end,
        }
    bundle_dir.mkdir(parents=True, exist_ok=True)
    path = bundle_dir / INTEGRITY_FILENAME
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8",
    )
    os.replace(tmp, path)


def read_bundle_integrity(bundle_dir: Path) -> BundleIntegrity | None:
    """Read the bundle's fetch-integrity stamp.

    MISSING → ``None`` (the caller's gate decides; P3-4c's recommend gate treats a
    missing stamp as "cannot confirm complete" and refuses). Malformed JSON, a
    non-object document, a non-UTF-8 file, an unknown ``schema_version``, or a
    missing required field → :class:`BundleIntegrityError` (fail-loud).
    """
    path = bundle_dir / INTEGRITY_FILENAME
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        raise BundleIntegrityError(
            f"unreadable bundle integrity stamp {path}: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise BundleIntegrityError(
            f"bundle integrity stamp {path} is not a JSON object "
            f"(got {type(raw).__name__}); refusing to parse."
        )
    version = raw.get("schema_version")
    if version != SCHEMA_VERSION:
        raise BundleIntegrityError(
            f"unknown bundle-integrity schema_version {version!r} in {path} "
            f"(expected {SCHEMA_VERSION}); refusing to parse."
        )
    # codex P2: validate each field's TYPE, not just presence — a hand-edited /
    # corrupt stamp with e.g. "built_from_holey_fetch": 0 must fail loud, not be
    # read as a clean (falsy) bundle.
    ctx = f"bundle integrity stamp {path}"
    holes = tuple(
        FetchHole(
            endpoint=_require(h, "endpoint", str, ctx),
            unit=_require(h, "unit", str, ctx),
            reason_class=_require(h, "reason_class", str, ctx),
            attempts=_require(h, "attempts", int, ctx),
            last_error=_require(h, "last_error", str, ctx),
        )
        for h in _require(raw, "holes", list, ctx)
    )
    built_from_holey_fetch = _require(raw, "built_from_holey_fetch", bool, ctx)
    # codex P2: a "clean" stamp that nonetheless lists holes is internally
    # inconsistent (a hand edit, or a buggy write_bundle_integrity caller). The
    # recommend gate keys on built_from_holey_fetch alone, so accepting this would
    # treat semantically corrupt provenance as clean — fail loud instead.
    if not built_from_holey_fetch and holes:
        raise BundleIntegrityError(
            f"{ctx}: inconsistent — built_from_holey_fetch is false but {len(holes)} "
            "hole(s) are listed; refusing to parse a clean stamp that records holes."
        )
    # PR-G+I: parse the OPTIONAL identity block only when present. A pre-PR-G+I
    # v1 stamp has no "identity" key → identity stays None (no fail-loud), so the
    # daily_recommend gate and any other v1 reader keep working unchanged.
    identity: BundleIdentity | None = None
    if "identity" in raw:
        ident_ctx = f"{ctx} identity"
        ident_raw = _require(raw, "identity", dict, ctx)
        identity = BundleIdentity(
            tail_date=_require(ident_raw, "tail_date", str, ident_ctx),
            content_hash=_require(ident_raw, "content_hash", str, ident_ctx),
            instrument_count=_require(ident_raw, "instrument_count", int, ident_ctx),
            calendar_start=_require(ident_raw, "calendar_start", str, ident_ctx),
            calendar_end=_require(ident_raw, "calendar_end", str, ident_ctx),
        )
    return BundleIntegrity(
        schema_version=SCHEMA_VERSION,  # already validated equal above
        built_from_holey_fetch=built_from_holey_fetch,
        built_at=_require(raw, "built_at", str, ctx),
        holes=holes,
        identity=identity,
    )


def _require(obj: Any, key: str, typ: type, ctx: str) -> Any:
    """Fetch ``obj[key]`` and validate it is present and of type ``typ``, else
    raise :class:`BundleIntegrityError`. ``bool`` is a subclass of ``int``, so an
    ``int`` field explicitly rejects a bool (and vice versa via ``isinstance``)."""
    if not isinstance(obj, dict):
        raise BundleIntegrityError(f"{ctx}: expected a JSON object, got {type(obj).__name__}")
    if key not in obj:
        raise BundleIntegrityError(f"{ctx}: missing required field {key!r}")
    val = obj[key]
    if typ is int and isinstance(val, bool):
        raise BundleIntegrityError(f"{ctx}: field {key!r} must be int, got bool")
    if not isinstance(val, typ):
        raise BundleIntegrityError(
            f"{ctx}: field {key!r} must be {typ.__name__}, got {type(val).__name__}"
        )
    return val
