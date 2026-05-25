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
      "built_at":     "2026-03-08T12:34:56Z"
    }

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


class BundleManifestError(ValueError):
    """Raised on a malformed or unparseable bundle manifest."""


class BundleStaleError(RuntimeError):
    """Raised when the requested ``test_end`` falls after the bundle tail.

    The message names BOTH the requested date AND the bundle's
    ``tail_date`` so the operator can decide whether to refresh the
    bundle or pull back the date window.
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
    """

    provider_uri: str
    tail_date: date
    instrument_count: int
    built_at: str


def _manifest_path(provider_uri: str | Path) -> Path:
    """Return the conventional location of the manifest file."""
    return Path(provider_uri) / MANIFEST_FILENAME


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

    return BundleManifest(
        provider_uri=provider_uri_raw,
        tail_date=tail_date,
        instrument_count=instrument_count_raw,
        built_at=built_at_raw,
    )


def save_manifest(
    provider_uri: str | Path,
    *,
    tail_date: str | date,
    instrument_count: int,
    built_at: str | None = None,
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

    Returns
    -------
    pathlib.Path
        The resolved path the manifest was written to.

    Raises
    ------
    BundleManifestError
        If ``tail_date`` is a malformed string, or ``instrument_count``
        is not a non-bool integer.

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

    payload = {
        "provider_uri": str(provider_uri),
        "tail_date": tail_iso,
        "instrument_count": int(instrument_count),
        "built_at": built_at_iso,
    }

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
    BundleStaleError
        When ``test_end > manifest.tail_date`` and ``soft=False``.
    BundleManifestError
        When the manifest exists but is malformed (propagated from
        :func:`load_manifest`).
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
