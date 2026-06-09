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

from src.data.tushare.fetcher import FetchHole

SCHEMA_VERSION = 1
INTEGRITY_FILENAME = "_fetch_integrity.json"


class BundleIntegrityError(RuntimeError):
    """Raised on an unreadable / unknown-schema integrity stamp — fail-loud rather
    than silently parsing an unrecognized shape into wrong integrity state."""


@dataclass(frozen=True)
class BundleIntegrity:
    """The bundle's fetch-integrity stamp (one ``_fetch_integrity.json`` per
    qlib provider dir)."""

    schema_version: int
    built_from_holey_fetch: bool
    built_at: str  # ISO-8601
    holes: tuple[FetchHole, ...]  # the fetch holes (provenance; empty when clean)


def write_bundle_integrity(
    bundle_dir: Path,
    *,
    built_from_holey_fetch: bool,
    holes: tuple[FetchHole, ...] = (),
    now: datetime | None = None,
) -> None:
    """Atomically write the bundle's fetch-integrity stamp (temp + ``os.replace``)
    so a crash mid-write never leaves a half-written stamp. ``now`` is injectable
    for tests / determinism (value-injection, as elsewhere); production default is
    the system clock. A clean build writes ``built_from_holey_fetch=False`` with no
    holes; a ``--allow-holey-fetch`` build writes ``True`` plus the holes."""
    stamp = (now if now is not None else datetime.now(tz=timezone.utc)).isoformat()
    payload = {
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
    try:
        holes = tuple(
            FetchHole(
                endpoint=h["endpoint"],
                unit=h["unit"],
                reason_class=h["reason_class"],
                attempts=h["attempts"],
                last_error=h["last_error"],
            )
            for h in raw["holes"]
        )
        return BundleIntegrity(
            schema_version=raw["schema_version"],
            built_from_holey_fetch=raw["built_from_holey_fetch"],
            built_at=raw["built_at"],
            holes=holes,
        )
    except (KeyError, TypeError, AttributeError) as exc:
        raise BundleIntegrityError(
            f"malformed bundle integrity stamp {path} (missing or invalid field: "
            f"{exc}); refusing to parse."
        ) from exc
