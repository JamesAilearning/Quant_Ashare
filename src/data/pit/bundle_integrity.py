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
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
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
    # codex #412 r2: the authoritative expected-first-session anchor
    # for coverage guards. Copied by the builder from the fetch
    # manifest's required-endpoint coverage (max coverage_start_date,
    # ISO yyyy-mm-dd): a complete zero-hole fetch from date X means
    # every session from X onward is present, so the bundle
    # calendar's first day IS the first real session >= X - no
    # gap-size heuristic a truncated bundle could hide inside.
    # OPTIONAL within schema_version 1 for the same reason
    # ``identity`` is: pre-existing stamps (the production bundle's
    # included) keep working; consumers fall back to their
    # weekday-tolerance guard.
    data_coverage_start: str | None = None
    # codex #412 r6: the ANCHOR-SPECIFIC expected first session,
    # derived by the builder from the fetched exchange calendar
    # (trade_cal.parquet): the first is_open session at or after
    # data_coverage_start. With this present, the walk-forward guard
    # requires the bundle calendar to start EXACTLY here - zero gap
    # tolerance - because every global gap threshold K leaves a hiding
    # place of exactly size K (lowering 7 to 6 only moved it from
    # 10-12 to 10-09). Optional for the same schema-v1 reasons as the
    # fields above; absent = the closure-bound fallback applies.
    expected_first_session: str | None = None


MAX_EXCHANGE_CLOSURE_WEEKDAYS = 6
"""Longest run of weekdays the A-share exchange has EVER been closed.

Spring Festival 2020 (extended, 01-24..02-02) and National Day +
Mid-Autumn 2023 (09-29..10-08) both span exactly 6 weekdays; National
Day 1999 (50th anniversary) spanned 5. The bound is EXACT on purpose
(codex #412 r5): an earlier revision kept "one day of slack" at 7, and
that slack was precisely where truncation could hide - a bundle whose
leading files are missing through 10-09 gaps exactly 7 weekdays from a
10-01 anchor and sailed through the strict-greater check. Sessions are
a subset of Mon-Fri, so every missing session costs a weekday; a gap
beyond 6 cannot be a closure. Should the exchange ever close longer,
this refuses loudly and the operator raises the constant with the new
historical fact - fail-loud beats silently accepting truncation."""


def missing_weekdays_between(start: date, first_present: date) -> int:
    """Weekdays in [start, first_present) - the coverage-gap metric
    shared by the builder's stamp cross-check and the walk-forward
    calendar guard, so the two ends cannot drift apart."""
    if first_present <= start:
        return 0
    return sum(1 for i in range((first_present - start).days)
               if (start + timedelta(days=i)).weekday() < 5)


def write_bundle_integrity(
    bundle_dir: Path,
    *,
    built_from_holey_fetch: bool,
    holes: tuple[FetchHole, ...] = (),
    identity: BundleIdentity | None = None,
    data_coverage_start: str | None = None,
    expected_first_session: str | None = None,
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
    if data_coverage_start is not None:
        payload["data_coverage_start"] = data_coverage_start
    if expected_first_session is not None:
        payload["expected_first_session"] = expected_first_session
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
    cov = _validated_stamp_date(raw, "data_coverage_start", ctx)
    expected = _validated_stamp_date(raw, "expected_first_session", ctx)
    return BundleIntegrity(
        schema_version=SCHEMA_VERSION,  # already validated equal above
        built_from_holey_fetch=built_from_holey_fetch,
        built_at=_require(raw, "built_at", str, ctx),
        holes=holes,
        identity=identity,
        data_coverage_start=cov,
        expected_first_session=expected,
    )


def _validated_stamp_date(raw: Any, key: str, ctx: str) -> str | None:
    """An optional stamp date, validated for syntax AND calendar
    validity (codex #412 r3 P2): a damaged or hand-edited stamp
    ("not-a-date", "2015-13-40") must be a BundleIntegrityError here -
    the consumer translates that into an actionable refusal, whereas
    letting it through surfaces as a raw parse traceback deep in
    window generation. Explicit yyyy-mm-dd: py3.11's fromisoformat
    also accepts the compact form ("20151001"), which is NOT this
    stamp's contract (caught by the malformed-stamp pin)."""
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise BundleIntegrityError(
            f"{ctx}: {key} must be a string, got {type(value).__name__}")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise BundleIntegrityError(f"{ctx}: {key} {value!r} is not yyyy-mm-dd")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise BundleIntegrityError(
            f"{ctx}: {key} {value!r} is not a valid "
            "ISO calendar date (yyyy-mm-dd)") from exc
    return value


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
