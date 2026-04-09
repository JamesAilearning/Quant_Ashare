"""Shared, stateless validator helpers for V2 data contracts.

This module exists to collapse the copy-pasted validation patterns shared by
``benchmark_data_contract``, ``universe_data_contract``, and
``taxonomy_data_contract``. It deliberately does **not** define any abstract
base class: the three contracts have distinct Input/Profile/Status dataclasses,
and nothing is gained by forcing them under a common inheritance tree.

Design invariants
-----------------
1. **No error-code constants.** Each contract owns its own error-code strings
   (e.g. ``ISSUE_TEMPORAL_ISSUE`` vs ``ISSUE_TEMPORAL_LEAKAGE``). Every helper
   accepts the desired code(s) as keyword-only arguments.
2. **No contract-specific state.** All helpers are pure functions on the
   minimum ``Protocol`` they need from a profile. If a helper starts needing
   a new profile attribute, the Protocol must be widened explicitly.
3. **No exceptions, with one exception.** ``parse_iso_date`` raises via a
   caller-supplied ``error_cls`` on malformed input, matching the behavior
   of the per-contract ``_as_date`` helpers it replaces. Every other helper
   returns plain ``list[str]`` of error/warning codes to append.
4. **Call-order parity.** Callers are expected to invoke helpers in the same
   order the per-contract implementations used prior to this refactor, so
   that the *order* of codes in ``errors`` / ``warnings`` stays identical
   and no existing test needs to change.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Mapping, Optional, Protocol, Type


# ---------------------------------------------------------------------------
# Minimal Protocols
#
# Each protocol declares only the attributes the helper below actually reads.
# This keeps the contract Profile dataclasses completely free of inheritance
# while still giving type checkers a way to catch drift.
# ---------------------------------------------------------------------------


class _HasPresenceFlags(Protocol):
    artifact_present: bool
    manifest_present: bool


class _HasMetadata(Protocol):
    metadata: Mapping[str, Any]


class _HasStaleness(Protocol):
    stale_days: Optional[int]


class _HasCoverage(Protocol):
    coverage_ratio: Optional[float]


class _HasSnapshotEnd(Protocol):
    snapshot_end: Optional[str]


class _HasSnapshotAtMismatch(Protocol):
    has_snapshot_at_mismatch: bool


# ---------------------------------------------------------------------------
# Date / collection primitives
# ---------------------------------------------------------------------------


def parse_iso_date(value: Optional[str], *, error_cls: Type[Exception]) -> Optional[date]:
    """Parse an optional ISO date string.

    ``None`` or empty strings return ``None``. Non-empty strings that cannot
    be parsed raise ``error_cls`` with a message matching the per-contract
    behavior established by the legacy ``_as_date`` helpers. This raising
    behavior is intentional: the three contracts' ``validate_input_boundary``
    relies on it to reject malformed ``reference_date`` inputs.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise error_cls(f"Invalid ISO date value: '{text}'.") from exc


def normalize_columns(values: Iterable[Any]) -> tuple[str, ...]:
    """Return a tuple of lowercased, stripped, non-empty column names."""
    return tuple(str(column).strip().lower() for column in values if str(column).strip())


def dedupe(values: Iterable[str]) -> tuple[str, ...]:
    """Stable-deduplicate a sequence of strings while preserving insertion order."""
    return tuple(dict.fromkeys(values))


# ---------------------------------------------------------------------------
# Presence / metadata / columns
# ---------------------------------------------------------------------------


def check_presence(
    profile: _HasPresenceFlags,
    *,
    missing_artifact_code: str,
    missing_manifest_code: str,
) -> list[str]:
    """Return the error codes triggered by missing artifact / manifest files."""
    errors: list[str] = []
    if not profile.artifact_present:
        errors.append(missing_artifact_code)
    if not profile.manifest_present:
        errors.append(missing_manifest_code)
    return errors


def check_metadata_fields(
    profile: _HasMetadata,
    required_fields: tuple[str, ...],
    *,
    schema_mismatch_code: str,
) -> tuple[tuple[str, ...], tuple[str, ...], list[str]]:
    """Validate required metadata fields.

    Returns a triple ``(present_fields, missing_fields, errors_to_append)``
    so callers can include ``present`` / ``missing`` in their status payload
    while also accumulating the ``schema_mismatch`` error.
    """
    metadata = profile.metadata or {}
    present_fields = tuple(
        key for key in required_fields if str(metadata.get(key, "")).strip()
    )
    missing_fields = tuple(key for key in required_fields if key not in present_fields)
    errors: list[str] = []
    if missing_fields:
        errors.append(schema_mismatch_code)
    return present_fields, missing_fields, errors


def check_required_columns(
    normalized_columns: tuple[str, ...],
    required_columns: tuple[str, ...],
    *,
    schema_mismatch_code: str,
) -> list[str]:
    """Return a schema_mismatch code if any required column is absent."""
    missing = [col for col in required_columns if col not in normalized_columns]
    if missing:
        return [schema_mismatch_code]
    return []


# ---------------------------------------------------------------------------
# Staleness / coverage
# ---------------------------------------------------------------------------


def check_staleness(
    profile: _HasStaleness,
    threshold: int,
    *,
    stale_code: str,
) -> list[str]:
    """Warn when ``profile.stale_days`` exceeds the threshold."""
    if profile.stale_days is not None and profile.stale_days > threshold:
        return [stale_code]
    return []


def check_coverage(
    profile: _HasCoverage,
    min_ratio: float,
    *,
    incomplete_coverage_code: str,
) -> list[str]:
    """Warn when ``profile.coverage_ratio`` is below the configured minimum."""
    if profile.coverage_ratio is not None and profile.coverage_ratio < min_ratio:
        return [incomplete_coverage_code]
    return []


# ---------------------------------------------------------------------------
# Temporal checks
# ---------------------------------------------------------------------------


def check_temporal_basic(
    *,
    snapshot_end: Optional[str],
    reference_date: Optional[str],
    has_future_data_flags: tuple[bool, ...],
    temporal_code: str,
    error_cls: Type[Exception],
) -> list[str]:
    """Return a temporal error when the profile leaks future data.

    Two modes of leakage:
      1. Any of the caller-supplied boolean flags is True (e.g.
         ``has_future_data``, ``has_future_effective_data``,
         ``has_future_known_metadata``); emit exactly one code.
      2. Otherwise, if both ``reference_date`` and ``snapshot_end`` parse
         as ISO dates and the snapshot end is strictly after the reference,
         emit the same code.

    The ordering and mutually-exclusive branching mirror the legacy
    per-contract implementations to preserve test expectations.
    """
    if any(has_future_data_flags):
        return [temporal_code]

    reference = parse_iso_date(reference_date, error_cls=error_cls)
    end = parse_iso_date(snapshot_end, error_cls=error_cls)
    if reference is not None and end is not None and end > reference:
        return [temporal_code]
    return []


def check_snapshot_at_mismatch(
    profile: _HasSnapshotAtMismatch,
    *,
    temporal_code: str,
) -> list[str]:
    """Return the temporal error code when ``has_snapshot_at_mismatch`` is True."""
    if profile.has_snapshot_at_mismatch:
        return [temporal_code]
    return []


# ---------------------------------------------------------------------------
# Health aggregation
# ---------------------------------------------------------------------------


def aggregate_health(errors: tuple[str, ...], warnings: tuple[str, ...]) -> str:
    """Derive the three-valued contract health label from errors and warnings."""
    if errors:
        return "error"
    if warnings:
        return "warning"
    return "ok"
