"""Explicit aggregate request starts shared by config and manifest producers.

Dependency-light: reading/building a manifest must not import the fetch client.
None at configuration boundaries means the existing common request start; no
history is inferred from disk and no requested interval is widened implicitly.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

AGGREGATE_START_ENDPOINTS = ("namechange", "suspend_d", "index_weight")
NAMECHANGE_MODES = ("date_range", "per_security_full")


def validate_namechange_mode(value: str) -> None:
    """Reject unknown acquisition modes before any producer side effect."""
    if not isinstance(value, str) or value not in NAMECHANGE_MODES:
        raise ValueError(f"namechange_mode must be one of {NAMECHANGE_MODES}, got {value!r}")


def _require_date(value: str, label: str) -> None:
    if (not isinstance(value, str) or len(value) != 8
            or not value.isascii() or not value.isdigit()):
        raise ValueError(f"{label} must be a real ASCII YYYYMMDD date, got {value!r}")
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError as exc:
        raise ValueError(
            f"{label} must be a real ASCII YYYYMMDD date, got {value!r}"
        ) from exc


def validate_aggregate_start_dates(
    values: Mapping[str, str], end_date: str,
) -> dict[str, str]:
    """Validate and copy explicit overrides, raising ValueError on bad input.

    Empty overrides preserve the legacy common-range validation contract.
    The three supported names are not a general per-endpoint override API.
    """
    if not isinstance(values, Mapping):
        raise ValueError("endpoint_start_dates must be a mapping")
    if not values:
        return {}
    _require_date(end_date, "end_date")
    result: dict[str, str] = {}
    for endpoint, start in values.items():
        if endpoint not in AGGREGATE_START_ENDPOINTS:
            raise ValueError(f"Unsupported aggregate start endpoint {endpoint!r}")
        _require_date(start, f"{endpoint}_start_date")
        if start > end_date:
            raise ValueError(f"{endpoint}_start_date {start} > end_date {end_date}")
        result[endpoint] = start
    return result


def resolve_aggregate_start_dates(
    *, end_date: str, namechange_start_date: str | None = None,
    suspend_d_start_date: str | None = None,
    index_weight_start_date: str | None = None,
) -> dict[str, str]:
    """Return only supplied, validated starts; None never becomes provenance."""
    values = {
        "namechange": namechange_start_date,
        "suspend_d": suspend_d_start_date,
        "index_weight": index_weight_start_date,
    }
    return validate_aggregate_start_dates(
        {endpoint: start for endpoint, start in values.items() if start is not None},
        end_date,
    )
