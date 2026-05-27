"""Bundle manifest loader and freshness validator.

A qlib bundle is a directory of pre-built feature files. Every
walk-forward run reads from one. Historically, operators had no
programmatic way to check that the dates in their YAML config
(``test_end`` / ``overall_end``) fell inside the bundle's actual
coverage — a config that pointed past the bundle tail would fail
deep inside ``FeatureDatasetBuilder`` with an opaque "empty
dataset" message after many seconds of qlib loading.

This module fixes that by reading a small JSON sidecar
(``bundle_manifest.json``) that ingest scripts SHOULD drop next to
the bundle's ``calendars/`` / ``features/`` / ``instruments/``
directories, and provides a validator that callers invoke BEFORE
``init_qlib_canonical``.

Schema (``bundle_manifest.json``)::

    {
      "provider_uri": "D:/qlib_data/my_cn_data",
      "tail_date":    "2026-03-06",
      "instrument_count": 4128,
      "built_at":     "2026-03-08T12:34:56Z",
      "content_hash": "sha256:9f86d081884c..."  // OPTIONAL
    }

``content_hash``
----------------
Optional. When present, it is the SHA-256 of the bundle's
``calendars/day.txt`` file, prefixed with ``sha256:``. Calendar bytes
are the most stable fingerprint of "what this bundle covers"; if the
calendar drifts (extended one day, holiday correction, re-ingest)
without an accompanying manifest refresh, the hash will mismatch and
the validator fails closed. Legacy manifests that pre-date this
field load fine (the field is treated as ``None`` and no hash check
runs); enable verification by re-running the ingest script.

Boundaries
----------
- Loading the manifest does NOT initialize qlib.
- A missing manifest is logged as INFO and treated as "no validation
  possible" — never as an error (legacy bundles predate this contract;
  asking operators to hand-write a manifest for every existing dump
  on day one would be a needless adoption barrier).
- A malformed manifest IS an error — half-written JSON or a missing
  required field means something is wrong with the bundle and the
  operator should know.

Opt-out
-------
The env var ``QLIB_SKIP_BUNDLE_VALIDATION=1`` makes the validator
return immediately with an INFO log. Intended for tests that
exercise downstream components on fixture bundles without manifests,
and for one-off operator bypass when they know what they're doing.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from src.core.logger import get_logger

_logger = get_logger(__name__)


MANIFEST_FILENAME = "bundle_manifest.json"
SKIP_ENV_VAR = "QLIB_SKIP_BUNDLE_VALIDATION"
MANIFEST_REQUIRED_FIELDS = ("provider_uri", "tail_date", "instrument_count", "built_at")
# Optional schema fields. Manifests that omit them load fine; manifests
# that include them get the corresponding integrity check at validation
# time. Currently:
#   - ``content_hash``: SHA-256 of ``calendars/day.txt``, prefixed
#     with ``sha256:`` (see :func:`compute_bundle_content_hash`).
MANIFEST_OPTIONAL_FIELDS: tuple[str, ...] = ("content_hash",)
CONTENT_HASH_ALGO = "sha256"
CONTENT_HASH_PREFIX = f"{CONTENT_HASH_ALGO}:"


class BundleManifestError(ValueError):
    """Raised on a malformed or unparseable bundle manifest."""


class BundleStaleError(RuntimeError):
    """Raised when the requested ``test_end`` falls after the bundle tail.

    The message names BOTH the requested date AND the bundle's
    ``tail_date`` so the operator can decide whether to refresh the
    bundle or pull back the date window.
    """


class BundleContentHashMismatchError(RuntimeError):
    """Raised when the manifest's ``content_hash`` does not match the
    bundle's actual ``calendars/day.txt`` SHA-256.

    Distinct from :class:`BundleStaleError` because the cause is
    different: a stale bundle is "your config window extends past
    coverage", a hash mismatch is "the bytes on disk don't match what
    the manifest claims" — which means either the bundle was edited
    out-of-band, or the manifest is from a different (older /
    parallel) build of the same path. Callers can react differently
    (e.g. the UI may want to show a different remediation hint).
    """


@dataclass(frozen=True)
class BundleManifest:
    """In-memory representation of a parsed ``bundle_manifest.json``.

    Attributes
    ----------
    provider_uri : str
        Self-reported provider URI of the bundle. Typically matches
        the directory the manifest sits in, but the validator does
        NOT cross-check this — operators sometimes copy a bundle to
        a new location for staging, and forcing the manifest to be
        re-edited every time would be friction without a real safety
        benefit.
    tail_date : datetime.date
        Last calendar day the bundle has feature data for. Parsed
        from the JSON ``tail_date`` string (ISO YYYY-MM-DD).
    instrument_count : int
        How many tickers the bundle covers. Surfaced for operator
        visibility (in WARNING / INFO log lines); not validated.
    built_at : str
        ISO timestamp the bundle was constructed. Stored verbatim;
        not parsed into a datetime because the validator does not
        compare against it.
    content_hash : str or None
        Optional. ``"sha256:<hex>"`` of the bundle's ``calendars/day.txt``
        when set by the ingest script. ``None`` for legacy manifests
        emitted before this field existed; consumers MUST treat
        ``None`` as "no integrity check available" rather than as a
        failure (see ``MANIFEST_OPTIONAL_FIELDS``).
    """

    provider_uri: str
    tail_date: date
    instrument_count: int
    built_at: str
    content_hash: str | None = None


def _manifest_path(provider_uri: str | Path) -> Path:
    """Return the conventional location of the manifest file."""
    return Path(provider_uri) / MANIFEST_FILENAME


def _calendar_path(provider_uri: str | Path) -> Path:
    """Return the bundle's calendar file path. The calendar is the
    fingerprint surface for :func:`compute_bundle_content_hash` —
    pinned to ``calendars/day.txt`` to match the qlib bundle layout
    (see ``src/data/pit/qlib_bin_builder.py`` and the references in
    ``src/pit/query.py`` / ``src/data/tushare/provider_bundle/_utils.py``).
    """
    return Path(provider_uri) / "calendars" / "day.txt"


def compute_bundle_content_hash(provider_uri: str | Path) -> str:
    """SHA-256 fingerprint of a bundle's ``calendars/day.txt``.

    The calendar file is the smallest stable summary of "what time
    range / which trading days this bundle covers". A change to it
    means the bundle's date axis changed — re-ingest, holiday
    correction, calendar extension — which is exactly the class of
    drift the integrity check is meant to catch.

    We deliberately fingerprint **only** the calendar file, not the
    feature bin directory:

    * The calendar is ~hundreds of KB; the bin directory can be
      tens of GB. SHA-256 over the bins would dominate every load.
    * The calendar moves whenever the bundle is regenerated for a
      different date window — which is the most operationally
      relevant drift. Mid-bin numeric drift (a single ticker's bin
      file modified out-of-band) is rare and out of scope.

    Parameters
    ----------
    provider_uri : str or Path
        The bundle directory. ``Path(provider_uri) / "calendars" /
        "day.txt"`` MUST exist.

    Returns
    -------
    str
        ``"sha256:<hex>"`` — the prefix is part of the contract so
        a future migration to a different algo (e.g. blake3) does not
        require sniffing length / character set.

    Raises
    ------
    BundleManifestError
        If the calendar file is missing or unreadable. We surface
        this as a manifest error rather than a generic IOError so
        callers can catch the bundle-integrity surface as one type.
    """
    calendar = _calendar_path(provider_uri)
    if not calendar.is_file():
        raise BundleManifestError(
            f"compute_bundle_content_hash: cannot fingerprint {provider_uri} — "
            f"{calendar} is missing. The calendar is required for the "
            "content-hash integrity check; this bundle either isn't a "
            "qlib provider, or the calendar was deleted out-of-band."
        )
    digest = hashlib.sha256()
    # Stream in 64 KB chunks so an unusually large calendar (millions of
    # rows, decades of history) doesn't spike memory.
    #
    # The ``is_file()`` check above narrows missing-file errors, but a
    # PermissionError, an EIO, or a TOCTOU race (file removed between
    # check and open) can still raise ``OSError`` here. Without the
    # except below, those would escape as raw OSError — the docstring
    # promises "missing or unreadable => BundleManifestError" and the
    # ingest script's except-branch only catches BundleManifestError,
    # so an unreadable calendar would bypass the controlled hard-exit
    # and surface as an uncaught traceback. Codex P2 on PR #175.
    try:
        with calendar.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                digest.update(chunk)
    except OSError as exc:
        raise BundleManifestError(
            f"compute_bundle_content_hash: cannot read {calendar} "
            f"({type(exc).__name__}: {exc}). The calendar exists but "
            "is unreadable — investigate filesystem permissions, disk "
            "errors, or a concurrent process that removed the file "
            "between the existence check and the read."
        ) from exc
    return f"{CONTENT_HASH_PREFIX}{digest.hexdigest()}"


def load_manifest(provider_uri: str | Path) -> BundleManifest | None:
    """Load and parse ``bundle_manifest.json`` from a provider directory.

    Parameters
    ----------
    provider_uri : str or Path
        The qlib provider URI. The manifest is read from
        ``Path(provider_uri) / "bundle_manifest.json"``.

    Returns
    -------
    BundleManifest or None
        ``None`` if the manifest file does not exist (legacy bundle).
        A populated :class:`BundleManifest` if it parses cleanly.

    Raises
    ------
    BundleManifestError
        If the manifest file exists but the JSON is malformed, or a
        required field is missing or of the wrong type.
    """
    manifest_path = _manifest_path(provider_uri)
    if not manifest_path.is_file():
        return None

    try:
        with open(manifest_path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except json.JSONDecodeError as exc:
        raise BundleManifestError(
            f"Malformed bundle manifest at {manifest_path}: {exc}"
        ) from exc

    if not isinstance(raw, dict):
        raise BundleManifestError(
            f"Bundle manifest at {manifest_path} must be a JSON object; "
            f"got {type(raw).__name__}"
        )

    required = ("provider_uri", "tail_date", "instrument_count", "built_at")
    missing = [k for k in required if k not in raw]
    if missing:
        raise BundleManifestError(
            f"Bundle manifest at {manifest_path} is missing required "
            f"field(s): {missing}. Expected schema keys: {list(required)}."
        )

    tail_raw = raw["tail_date"]
    if not isinstance(tail_raw, str):
        raise BundleManifestError(
            f"Bundle manifest at {manifest_path}: 'tail_date' must be "
            f"an ISO YYYY-MM-DD string, got {type(tail_raw).__name__}"
        )
    try:
        tail_date = date.fromisoformat(tail_raw)
    except ValueError as exc:
        raise BundleManifestError(
            f"Bundle manifest at {manifest_path}: 'tail_date' "
            f"({tail_raw!r}) is not an ISO YYYY-MM-DD date: {exc}"
        ) from exc

    instrument_count_raw = raw["instrument_count"]
    if not isinstance(instrument_count_raw, int) or isinstance(
        instrument_count_raw, bool
    ):
        # ``bool`` is an ``int`` subclass; the manifest must carry an
        # honest integer.
        raise BundleManifestError(
            f"Bundle manifest at {manifest_path}: 'instrument_count' "
            f"must be an integer, got {type(instrument_count_raw).__name__}"
        )

    # ``provider_uri`` and ``built_at`` were previously coerced with
    # ``str(...)``, which silently accepts ``None`` ("None") and dict
    # values ("{'a': 1}"). Reject non-string types up front so a
    # malformed manifest raises ``BundleManifestError`` instead of
    # propagating gibberish downstream. (Codex P2 on PR #149.)
    provider_uri_raw = raw["provider_uri"]
    if not isinstance(provider_uri_raw, str):
        raise BundleManifestError(
            f"Bundle manifest at {manifest_path}: 'provider_uri' must be "
            f"a string, got {type(provider_uri_raw).__name__}"
        )
    built_at_raw = raw["built_at"]
    if not isinstance(built_at_raw, str):
        raise BundleManifestError(
            f"Bundle manifest at {manifest_path}: 'built_at' must be "
            f"a string, got {type(built_at_raw).__name__}"
        )

    # Optional ``content_hash``. Three distinct cases:
    #   (a) key absent  => legacy manifest, no integrity check at
    #       validate time (the documented backwards-compat path).
    #   (b) key present with a valid string => integrity check is on.
    #   (c) key present but explicit ``null`` => REJECT. ``null`` and
    #       "absent" carry different intent for a NEW field: ``null``
    #       looks like a producer that meant to emit a hash but
    #       failed (e.g. a third-party tool, or a manifest someone
    #       hand-edited to "disable" the check by setting null). The
    #       PR contract is "present => verify"; treating null as an
    #       implicit opt-out silently disables a check the operator
    #       believed was in place. Codex P2 on PR #175.
    if "content_hash" in raw:
        content_hash_raw = raw["content_hash"]
        if content_hash_raw is None:
            raise BundleManifestError(
                f"Bundle manifest at {manifest_path}: 'content_hash' "
                "is explicitly null. Either omit the key entirely for "
                "a legacy bundle with no integrity check, or supply a "
                f"valid {CONTENT_HASH_PREFIX}<64-lowercase-hex> value."
            )
        if not isinstance(content_hash_raw, str):
            raise BundleManifestError(
                f"Bundle manifest at {manifest_path}: 'content_hash' "
                f"must be a string when present, got "
                f"{type(content_hash_raw).__name__}"
            )
        if not content_hash_raw.startswith(CONTENT_HASH_PREFIX):
            raise BundleManifestError(
                f"Bundle manifest at {manifest_path}: 'content_hash' "
                f"({content_hash_raw!r}) must start with "
                f"{CONTENT_HASH_PREFIX!r}; got an unknown algorithm prefix."
            )
        hex_part = content_hash_raw[len(CONTENT_HASH_PREFIX):]
        # 64 lowercase hex chars = SHA-256. We deliberately reject
        # uppercase here (case-sensitive set) — ``hashlib.hexdigest()``
        # and :func:`compute_bundle_content_hash` always emit lowercase,
        # so an uppercase hash on disk would pass shape validation but
        # then byte-mismatch the recomputed value at
        # :func:`verify_content_hash` time. Failing here is the honest
        # error. Codex P2 on PR #175 (option: reject vs normalise; we
        # picked reject).
        if len(hex_part) != 64 or any(c not in "0123456789abcdef" for c in hex_part):
            raise BundleManifestError(
                f"Bundle manifest at {manifest_path}: 'content_hash' "
                f"hex body ({hex_part!r}) is not a 64-char SHA-256 hex "
                "string (lowercase a-f / 0-9 only)."
            )
    else:
        content_hash_raw = None

    return BundleManifest(
        provider_uri=provider_uri_raw,
        tail_date=tail_date,
        instrument_count=instrument_count_raw,
        built_at=built_at_raw,
        content_hash=content_hash_raw,
    )


def save_manifest(
    provider_uri: str | Path,
    *,
    tail_date: str | date,
    instrument_count: int,
    built_at: str | None = None,
    content_hash: str | None = None,
) -> Path:
    """Write ``bundle_manifest.json`` next to a freshly-built bundle.

    Called from ingest scripts after the bundle directory layout is
    complete (calendars/, features/, instruments/ all written). The
    file lands at ``Path(provider_uri) / "bundle_manifest.json"`` —
    the same location :func:`load_manifest` reads from.

    Parameters
    ----------
    provider_uri : str or Path
        The bundle directory. Will be created if missing (so callers
        can write the manifest before the bundle dir exists).
    tail_date : str or datetime.date
        Last calendar day the bundle has feature data for. Strings
        are required to parse as ISO YYYY-MM-DD; dates are formatted
        in the same shape.
    instrument_count : int
        How many tickers the bundle covers.
    built_at : str or None
        ISO timestamp. Defaults to ``datetime.now(tz=timezone.utc).isoformat()``.
    content_hash : str or None
        Optional ``"sha256:<hex>"`` fingerprint of the bundle's
        ``calendars/day.txt``. Pass the result of
        :func:`compute_bundle_content_hash` to opt in to the
        integrity check at validation time. ``None`` (default)
        omits the field entirely so the resulting manifest stays
        byte-identical to legacy emit calls that don't compute a
        hash.

    Returns
    -------
    pathlib.Path
        The resolved path the manifest was written to.

    Raises
    ------
    BundleManifestError
        If ``tail_date`` is a malformed string, ``instrument_count``
        is not a non-bool integer, or ``content_hash`` is non-None
        but malformed.

    The write is atomic (``*.tmp`` + ``os.replace``) so a crash mid-
    write does not leave a half-parsed file behind for the next
    ``load_manifest`` to choke on.
    """
    # Validate the inputs the same way load_manifest validates the
    # disk file, so a misuse here surfaces as the same error type
    # callers already handle.
    if isinstance(tail_date, str):
        try:
            date.fromisoformat(tail_date)
        except ValueError as exc:
            raise BundleManifestError(
                f"save_manifest: tail_date ({tail_date!r}) is not an "
                f"ISO YYYY-MM-DD date: {exc}"
            ) from exc
        tail_iso = tail_date
    elif isinstance(tail_date, date):
        tail_iso = tail_date.isoformat()
    else:
        raise BundleManifestError(
            f"save_manifest: tail_date must be a str or datetime.date; "
            f"got {type(tail_date).__name__}"
        )
    if not isinstance(instrument_count, int) or isinstance(
        instrument_count, bool
    ):
        raise BundleManifestError(
            f"save_manifest: instrument_count must be an integer; "
            f"got {type(instrument_count).__name__}"
        )
    built_at_iso = built_at or datetime.now(tz=timezone.utc).isoformat()

    # Validate content_hash shape up-front (same checks load_manifest
    # applies on read) so an ingest script with a buggy hash producer
    # fails at write time, not later at load time on a different
    # machine. Skipping when None — that's the documented "no hash
    # available" path that keeps the resulting manifest backwards
    # compatible.
    if content_hash is not None:
        if not isinstance(content_hash, str):
            raise BundleManifestError(
                f"save_manifest: content_hash must be a string when "
                f"set; got {type(content_hash).__name__}"
            )
        if not content_hash.startswith(CONTENT_HASH_PREFIX):
            raise BundleManifestError(
                f"save_manifest: content_hash ({content_hash!r}) must "
                f"start with {CONTENT_HASH_PREFIX!r}; use "
                "compute_bundle_content_hash() to produce a valid "
                "value."
            )
        hex_part = content_hash[len(CONTENT_HASH_PREFIX):]
        # Same case-sensitive lowercase requirement as load_manifest —
        # see the comment there. compute_bundle_content_hash always
        # emits lowercase, so accepting uppercase at write time would
        # plant a manifest that later fails comparison even though
        # nothing changed on disk.
        if len(hex_part) != 64 or any(
            c not in "0123456789abcdef" for c in hex_part
        ):
            raise BundleManifestError(
                f"save_manifest: content_hash hex body ({hex_part!r}) "
                "is not a 64-char SHA-256 hex string (lowercase a-f / "
                "0-9 only)."
            )

    payload: dict = {
        "provider_uri": str(provider_uri),
        "tail_date": tail_iso,
        "instrument_count": int(instrument_count),
        "built_at": built_at_iso,
    }
    # Add ``content_hash`` only when supplied so callers that don't
    # opt in produce the same byte-stream they used to. Keeps
    # round-trip tests / fixtures stable.
    if content_hash is not None:
        payload["content_hash"] = content_hash

    target = _manifest_path(provider_uri)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=False),
        encoding="utf-8",
    )
    os.replace(tmp, target)
    _logger.info(
        "Wrote bundle manifest at %s (tail_date=%s, instrument_count=%d)",
        target, tail_iso, instrument_count,
    )
    return target


def _is_skip_enabled() -> bool:
    """Return True when the env-var opt-out is set."""
    return os.environ.get(SKIP_ENV_VAR, "").strip() in ("1", "true", "yes")


def verify_content_hash(
    provider_uri: str | Path,
    *,
    soft: bool = False,
) -> None:
    """Verify the bundle's ``calendars/day.txt`` matches the
    manifest's ``content_hash`` (when present).

    Semantics:

    * No manifest, or manifest without ``content_hash``  → silent
      return (legacy bundle, integrity check unavailable). This is
      the same "no check possible" stance ``validate_test_end_against_bundle``
      takes on a missing manifest.
    * Manifest with ``content_hash`` and calendar bytes match →
      silent return.
    * Manifest with ``content_hash`` and calendar bytes mismatch →
      :class:`BundleContentHashMismatchError` (or WARNING when
      ``soft=True``).
    * Manifest with ``content_hash`` but ``calendars/day.txt``
      missing → :class:`BundleManifestError` (manifest claims an
      integrity surface that no longer exists; propagated from
      :func:`compute_bundle_content_hash`).

    Parameters
    ----------
    provider_uri : str or Path
        The qlib provider URI; the manifest and calendar are read
        from the same root.
    soft : bool, default False
        Same convention as :func:`validate_test_end_against_bundle`
        — when True, a mismatch logs a WARNING and the call returns;
        when False the mismatch raises.

    Raises
    ------
    BundleContentHashMismatchError
        On hash mismatch when ``soft=False``.
    BundleManifestError
        When the manifest claims a hash but the calendar file is
        missing, or the manifest is malformed.
    """
    if _is_skip_enabled():
        # Same env-var bypass as the date check — convenient for
        # tests that exercise downstream wiring against fixture
        # bundles without a real calendar.
        return
    manifest = load_manifest(provider_uri)
    if manifest is None or manifest.content_hash is None:
        # Legacy bundle / opt-out caller. No-op without logging — the
        # date validator already emits an INFO log for the no-manifest
        # path, so a second INFO here would be noisy duplicate.
        return

    actual = compute_bundle_content_hash(provider_uri)
    if actual == manifest.content_hash:
        return

    message = (
        f"Bundle content_hash mismatch at {provider_uri}. The manifest "
        f"claims {manifest.content_hash!r} but the calendar bytes on "
        f"disk hash to {actual!r}. This means the bundle was modified "
        "out-of-band, OR the manifest is from a different (older / "
        "parallel) build of the same path. Re-run the ingest script "
        "to refresh both atomically, or — if the modification is "
        "deliberate — delete bundle_manifest.json so subsequent runs "
        "skip the integrity check. To bypass for a single run, set "
        f"{SKIP_ENV_VAR}=1."
    )
    if soft:
        _logger.warning(message)
        return
    raise BundleContentHashMismatchError(message)


def validate_test_end_against_bundle(
    provider_uri: str | Path,
    test_end: str | date,
    *,
    soft: bool = False,
) -> None:
    """Check that the requested ``test_end`` is inside the bundle.

    The check is inclusive: ``test_end == tail_date`` passes. That
    matches how qlib slices data — a request whose last day equals
    the calendar's last day is fully covered, not "one day past".

    Parameters
    ----------
    provider_uri : str or Path
        The qlib provider URI. The manifest is read from
        ``Path(provider_uri) / "bundle_manifest.json"``.
    test_end : str or datetime.date
        The last day the caller intends to use feature data for. The
        walk-forward CLI passes ``WalkForwardConfig.overall_end``;
        callers using a single-fold pipeline pass
        ``PipelineConfig.test_end``.
    soft : bool, default False
        When False (the default), a stale bundle raises
        :class:`BundleStaleError`. When True, a stale bundle logs a
        WARNING and the call returns. Intended for non-canonical
        scripts that prefer a warning to an abort.

    Returns
    -------
    None
        The validator does not return a value; it raises on hard
        failures and is silent on success (apart from an INFO log
        line on the no-manifest path).

    Raises
    ------
    BundleManifestError
        When ``test_end`` is a malformed ISO date string, or the
        manifest exists but is malformed (propagated from
        :func:`load_manifest`). Checked FIRST so a caller-config
        bug surfaces before any bundle-state inspection — the
        operator's wrong YAML is more actionable than a downstream
        environmental error.
    BundleContentHashMismatchError
        When the manifest declares a ``content_hash`` and the bundle's
        actual calendar bytes hash to a different value, and ``soft=False``.
        Checked AFTER ``test_end`` parsing but BEFORE the date
        comparison: a hash mismatch means the bundle was tampered /
        partially regenerated, which is a higher-priority signal than
        "date window is past coverage". Surfacing the mismatch first
        prevents an operator from chasing the wrong problem.
    BundleStaleError
        When ``test_end > manifest.tail_date`` and ``soft=False``.
        Checked LAST — only after the config is well-formed and the
        bundle bytes match what the manifest claims.
    """
    if _is_skip_enabled():
        _logger.info(
            "Bundle validation skipped (%s=1). Caller assumes the "
            "configured test_end (%s) is inside the bundle at %s.",
            SKIP_ENV_VAR,
            test_end,
            provider_uri,
        )
        return

    manifest = load_manifest(provider_uri)
    if manifest is None:
        _logger.info(
            "No bundle manifest at %s, skipping bundle freshness "
            "validation. Add a bundle_manifest.json to enable the "
            "test_end vs tail_date check.",
            _manifest_path(provider_uri),
        )
        return

    # Parse ``test_end`` BEFORE running any bundle-state checks. The
    # malformed-date case is a caller-config bug (their YAML / CLI arg
    # is wrong); they should see that actionable error first instead
    # of an environmental "hash mismatch" / "calendar missing" message
    # that hides the real problem. Codex P2 on PR #175.
    if isinstance(test_end, str):
        try:
            test_end_date = date.fromisoformat(test_end)
        except ValueError as exc:
            raise BundleManifestError(
                f"validate_test_end_against_bundle: test_end "
                f"({test_end!r}) is not an ISO YYYY-MM-DD date: {exc}"
            ) from exc
    else:
        test_end_date = test_end

    # Hash check next: a mismatch means the bundle bytes on disk no
    # longer match what the manifest claims, which is a higher-priority
    # signal than "tail_date is past your window". An operator chasing
    # a hash mismatch may not need to refresh the date window at all
    # (e.g. they may just need to revert an out-of-band edit), so we
    # surface that error before the stale-date one. ``soft`` carries
    # through with the same warn-vs-raise semantics as the date check.
    verify_content_hash(provider_uri, soft=soft)

    if test_end_date <= manifest.tail_date:
        # Bundle covers the requested window. Stay silent — chatty
        # success logs would just add noise on every walk-forward run.
        return

    message = (
        f"Configured test_end ({test_end_date.isoformat()}) is after "
        f"the bundle's tail_date ({manifest.tail_date.isoformat()}) "
        f"at {provider_uri}. The bundle does not cover the requested "
        f"window; either refresh the bundle (re-ingest) or pull "
        f"test_end back to {manifest.tail_date.isoformat()} or earlier. "
        f"To bypass this check (e.g. for a one-off operator override), "
        f"set {SKIP_ENV_VAR}=1."
    )

    if soft:
        _logger.warning(message)
        return

    raise BundleStaleError(message)
