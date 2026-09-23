"""One explicitly approved suspension-history incident, never a general waiver.

This module validates evidence structure only. Raw byte/key verification belongs
to the acquisition/build boundary; selecting this policy is not proof that those
checks ran, nor permission to reuse it for historical performance certification.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

POLICY_ID = "suspend-688766-20251127-20251209"
QUARANTINE_REASON = "quarantined_history"
TS_CODE = "688766.SH"
INSTRUMENT = "SH688766"
APPROVED_KEYS: frozenset[tuple[str, str, None, str]] = frozenset({
    (TS_CODE, "20251127", None, "S"),
    (TS_CODE, "20251128", None, "S"),
    (TS_CODE, "20251201", None, "S"),
    (TS_CODE, "20251202", None, "S"),
    (TS_CODE, "20251203", None, "S"),
    (TS_CODE, "20251204", None, "S"),
    (TS_CODE, "20251205", None, "S"),
    (TS_CODE, "20251209", None, "R"),
})
_APPROVED_DATES = frozenset(key[1] for key in APPROVED_KEYS)
_EVIDENCE_FIELDS = frozenset({
    "policy_id", "missing_dates", "reference_sha256", "retained_sha256",
    "candidate_sha256", "query_start_date", "query_end_date",
})


def validate_policy(value: object) -> str | None:
    """None means no authorization; only the reviewed exact ID is selectable."""
    if value is None:
        return None
    if not isinstance(value, str) or value != POLICY_ID:
        raise ValueError(f"unknown suspension quarantine policy {value!r}")
    return value


def _date(value: object, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{8}", value):
        raise ValueError(f"suspension quarantine {label} must be ASCII YYYYMMDD")
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError as exc:
        raise ValueError(f"suspension quarantine {label} must be a real date") from exc
    return value


@dataclass(frozen=True)
class SuspensionQuarantine:
    """Immutable, bounded facts about the still-missing approved business keys."""

    policy_id: str
    missing_dates: tuple[str, ...]
    reference_sha256: str
    retained_sha256: str
    candidate_sha256: str
    query_start_date: str
    query_end_date: str

    def __post_init__(self) -> None:
        if validate_policy(self.policy_id) is None:
            raise ValueError("suspension quarantine evidence requires its policy_id")
        dates = self.missing_dates
        if (not isinstance(dates, tuple) or not dates
                or any(not isinstance(day, str) or day not in _APPROVED_DATES for day in dates)
                or dates != tuple(sorted(set(dates)))):
            raise ValueError(
                "suspension quarantine missing_dates must be a nonempty sorted unique "
                "tuple drawn from the eight approved dates"
            )
        for field in ("reference_sha256", "retained_sha256", "candidate_sha256"):
            value = getattr(self, field)
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                raise ValueError(f"suspension quarantine {field} must be lowercase SHA256")
        start = _date(self.query_start_date, "query_start_date")
        end = _date(self.query_end_date, "query_end_date")
        if start > min(_APPROVED_DATES) or end < max(_APPROVED_DATES):
            raise ValueError("suspension quarantine query must cover the entire approved incident")

    def to_dict(self) -> dict[str, Any]:
        """Fresh JSON-ready values; no mutable alias to the evidence instance."""
        return {
            "policy_id": self.policy_id,
            "missing_dates": list(self.missing_dates),
            "reference_sha256": self.reference_sha256,
            "retained_sha256": self.retained_sha256,
            "candidate_sha256": self.candidate_sha256,
            "query_start_date": self.query_start_date,
            "query_end_date": self.query_end_date,
        }

    @classmethod
    def from_dict(cls, value: object) -> SuspensionQuarantine:
        """Reject missing/unknown keys, null evidence and scalar coercion."""
        if not isinstance(value, dict) or set(value) != _EVIDENCE_FIELDS:
            raise ValueError("suspension quarantine evidence must contain exactly its seven fields")
        if not isinstance(value["missing_dates"], list):
            raise ValueError("suspension quarantine missing_dates JSON field must be an array")
        return cls(
            policy_id=value["policy_id"], missing_dates=tuple(value["missing_dates"]),
            reference_sha256=value["reference_sha256"],
            retained_sha256=value["retained_sha256"],
            candidate_sha256=value["candidate_sha256"],
            query_start_date=value["query_start_date"], query_end_date=value["query_end_date"],
        )


class _Hole(Protocol):
    @property
    def endpoint(self) -> str: ...

    @property
    def unit(self) -> str: ...

    @property
    def reason_class(self) -> str: ...

    @property
    def quarantine(self) -> SuspensionQuarantine | None: ...


def validate_hole_quarantine(hole: _Hole) -> None:
    """The reason and structured evidence must agree, even without an opt-in."""
    evidence = hole.quarantine
    if evidence is None:
        if hole.reason_class == QUARANTINE_REASON:
            raise ValueError("quarantined_history hole is missing quarantine evidence")
        return
    if not isinstance(evidence, SuspensionQuarantine):
        raise ValueError("hole quarantine must be typed SuspensionQuarantine evidence")
    if (hole.endpoint != "suspend_d" or hole.unit != "file"
            or hole.reason_class != QUARANTINE_REASON):
        raise ValueError("quarantine requires suspend_d/file/quarantined_history")
    # Revalidate at trust boundaries, including manually forged in-memory DTOs.
    evidence.__post_init__()


def has_quarantine(holes: Iterable[_Hole]) -> bool:
    """Validate ALL holes; an early valid incident cannot conceal later corruption."""
    found = False
    for hole in holes:
        validate_hole_quarantine(hole)
        found = found or hole.quarantine is not None
    return found


def qualified_quarantine(
    holes: Iterable[_Hole], policy: object,
) -> SuspensionQuarantine | None:
    """Only one validated incident and matching opt-in qualify; mixed gaps do not."""
    selected = validate_policy(policy)
    items = tuple(holes)
    present = has_quarantine(items)
    if present and len(items) == 1 and selected == POLICY_ID:
        return items[0].quarantine
    return None  # No qualifying authorization, never a completeness fallback.
