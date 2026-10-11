"""Explicit, evidence-backed suspension isolation, never a general waiver.

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
CONFLICT_POLICY_ID = "suspend-688005-20260116-conflict"
COMBINED_POLICY_ID = "suspend-688005-688766-observed-20261008"
SECURITY_POLICY_ID = "suspend-security-anomalies-v1"
MAX_QUARANTINED_SECURITIES = 10
MAX_HISTORY_WITNESSES = 32
POLICY_IDS = (POLICY_ID, CONFLICT_POLICY_ID, COMBINED_POLICY_ID, SECURITY_POLICY_ID)
BusinessKey = tuple[str, str, str | None, str]


@dataclass(frozen=True)
class SuspensionIncident:
    """Closed, immutable reviewed policy; never loaded from operator config."""

    policy_id: str
    ts_codes: tuple[str, ...]
    instruments: tuple[str, ...]
    reference_keys: frozenset[BusinessKey]
    conflict_keys: frozenset[BusinessKey] = frozenset()

    @property
    def dates(self) -> frozenset[str]:
        """Required query dates, including non-missing conflicting records."""
        return frozenset(key[1] for key in self.reference_keys)

    @property
    def missing_dates(self) -> frozenset[str]:
        """Permitted missing-date domain, not a shortcut for query coverage."""
        if self.policy_id == COMBINED_POLICY_ID:
            return frozenset(key[1] for key in APPROVED_KEYS)
        return self.dates

    @property
    def affected_days(self) -> frozenset[tuple[str, str]]:
        return frozenset((key[0], key[1]) for key in self.reference_keys)


_INCIDENTS = (
    SuspensionIncident(POLICY_ID, (TS_CODE,), (INSTRUMENT,), APPROVED_KEYS),
    SuspensionIncident(
        CONFLICT_POLICY_ID, ("688005.SH",), ("SH688005",),
        frozenset({("688005.SH", "20260116", None, "R")}),
        frozenset({("688005.SH", "20260116", "09:30-09:30", "S")}),
    ),
    SuspensionIncident(
        COMBINED_POLICY_ID, ("688005.SH", TS_CODE), ("SH688005", INSTRUMENT),
        APPROVED_KEYS | frozenset({("688005.SH", "20260116", None, "R")}),
        frozenset({("688005.SH", "20260116", "09:30-09:30", "S")}),
    ),
)
_EVIDENCE_FIELDS = frozenset({
    "policy_id", "missing_dates", "reference_sha256", "retained_sha256",
    "candidate_sha256", "query_start_date", "query_end_date",
})
_SECURITY_EVIDENCE_FIELDS = _EVIDENCE_FIELDS | {"quarantined_ts_codes", "history_sha256", "predecessor_sha256"}


def validate_policy(value: object) -> str | None:
    """None means no authorization; select one explicitly approved policy."""
    if value is None:
        return None
    if not isinstance(value, str) or value not in POLICY_IDS:
        raise ValueError(f"unknown suspension quarantine policy {value!r}")
    return value


def incident_for_policy(value: object) -> SuspensionIncident:
    """Resolve an explicit policy, refusing absent/unknown authorization."""
    selected = validate_policy(value)
    for incident in _INCIDENTS:
        if incident.policy_id == selected:
            return incident
    raise ValueError("suspension quarantine requires an explicit policy")


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
        incident = incident_for_policy(self.policy_id)
        dates = self.missing_dates
        if (not isinstance(dates, tuple) or not dates
                or any(not isinstance(day, str) or day not in incident.missing_dates for day in dates)
                or dates != tuple(sorted(set(dates)))):
            raise ValueError(
                "suspension quarantine missing_dates must be a nonempty sorted unique "
                "tuple drawn from the selected incident's approved dates"
            )
        if (self.policy_id == COMBINED_POLICY_ID
                and dates != tuple(sorted(incident.missing_dates))):
            raise ValueError("combined suspension quarantine requires exactly all eight missing dates")
        for field in ("reference_sha256", "retained_sha256", "candidate_sha256"):
            value = getattr(self, field)
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                raise ValueError(f"suspension quarantine {field} must be lowercase SHA256")
        start = _date(self.query_start_date, "query_start_date")
        end = _date(self.query_end_date, "query_end_date")
        if start > min(incident.dates) or end < max(incident.dates):
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
        if (isinstance(value, dict) and isinstance(value.get("policy_id"), str)
                and value["policy_id"] == SECURITY_POLICY_ID):
            if (set(value) != _SECURITY_EVIDENCE_FIELDS
                    or not isinstance(value["missing_dates"], list)
                    or not isinstance(value["quarantined_ts_codes"], list)
                    or not isinstance(value["history_sha256"], list)):
                raise ValueError("security suspension quarantine evidence requires exactly its ten typed fields")
            return SecuritySuspensionQuarantine(
                policy_id=value["policy_id"], missing_dates=tuple(value["missing_dates"]),
                reference_sha256=value["reference_sha256"], retained_sha256=value["retained_sha256"],
                candidate_sha256=value["candidate_sha256"], query_start_date=value["query_start_date"],
                query_end_date=value["query_end_date"],
                quarantined_ts_codes=tuple(value["quarantined_ts_codes"]),
                history_sha256=tuple(value["history_sha256"]),
                predecessor_sha256=value["predecessor_sha256"],
            )
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


@dataclass(frozen=True)
class SecuritySuspensionQuarantine(SuspensionQuarantine):
    """Derived security identities; earlier actual files prove later-added loss.

    The extra required fields exist only for the new policy. The immutable old
    seven-field DTO and the old policies' incident-specific semantics remain
    unchanged. Raw verification, not this structural parser, proves key loss.
    """

    quarantined_ts_codes: tuple[str, ...]
    history_sha256: tuple[str, ...]
    predecessor_sha256: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.policy_id, str) or self.policy_id != SECURITY_POLICY_ID:
            raise ValueError("security suspension quarantine requires its explicit policy")
        codes = self.quarantined_ts_codes
        if (not isinstance(codes, tuple) or not 1 <= len(codes) <= MAX_QUARANTINED_SECURITIES
                or any(not isinstance(code, str) or re.fullmatch(r"[0-9]{6}\.(SH|SZ|BJ)", code) is None
                       for code in codes)
                or codes != tuple(sorted(set(codes)))):
            raise ValueError("security suspension quarantine requires sorted unique valid stock codes within its limit")
        dates = self.missing_dates
        if (not isinstance(dates, tuple) or not dates
                or any(not isinstance(day, str) for day in dates)
                or dates != tuple(sorted(set(dates)))):
            raise ValueError("security suspension quarantine requires sorted unique missing dates")
        for day in dates:
            _date(day, "missing_dates")
        for field in ("reference_sha256", "retained_sha256", "candidate_sha256"):
            digest = getattr(self, field)
            if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                raise ValueError(f"suspension quarantine {field} must be lowercase SHA256")
        history = self.history_sha256
        if (not isinstance(history, tuple) or len(history) > MAX_HISTORY_WITNESSES
                or any(not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                       for digest in history)
                or history != tuple(sorted(set(history)))
                or set(history) & {self.reference_sha256, self.retained_sha256, self.candidate_sha256}):
            raise ValueError("security suspension quarantine history requires bounded unique nonredundant SHA256 witnesses")
        predecessor = self.predecessor_sha256
        if predecessor is not None and (
            not isinstance(predecessor, str) or re.fullmatch(r"[0-9a-f]{64}", predecessor) is None
        ):
            raise ValueError("security suspension quarantine predecessor must be null or lowercase SHA256")
        start = _date(self.query_start_date, "query_start_date")
        end = _date(self.query_end_date, "query_end_date")
        if start > min(dates) or end < max(dates) or start > end:
            raise ValueError("security suspension quarantine query must cover all missing dates")

    def to_dict(self) -> dict[str, Any]:
        result = super().to_dict()
        result["quarantined_ts_codes"] = list(self.quarantined_ts_codes)
        result["history_sha256"] = list(self.history_sha256)
        result["predecessor_sha256"] = self.predecessor_sha256
        return result


def quarantine_ts_codes(evidence: SuspensionQuarantine) -> tuple[str, ...]:
    """Resolve identities from typed evidence, never from a dynamic policy ID."""
    evidence.__post_init__()
    if isinstance(evidence, SecuritySuspensionQuarantine):
        return evidence.quarantined_ts_codes
    return incident_for_policy(evidence.policy_id).ts_codes


def quarantine_instruments(evidence: SuspensionQuarantine) -> tuple[str, ...]:
    """Use the existing six-digit exchange-prefixed qlib security identity."""
    return tuple(code[-2:] + code[:6] for code in quarantine_ts_codes(evidence))


def quarantine_context(evidence: SuspensionQuarantine) -> dict[str, Any]:
    """Construct the policy's exact serving shape, retaining old single-ID JSON."""
    evidence.__post_init__()
    instruments = quarantine_instruments(evidence)
    identity: dict[str, Any] = (
        {"instrument": instruments[0]} if len(instruments) == 1 and evidence.policy_id != SECURITY_POLICY_ID
        else {"instruments": list(instruments)}
    )
    return {"policy_id": evidence.policy_id, **identity,
            "built_from_holey_fetch": True, "evidence": evidence.to_dict()}


def validate_quarantine_context(value: object) -> SuspensionQuarantine:
    """Reject cross-policy, singular/plural and nullable/coerced identities."""
    if not isinstance(value, dict) or "evidence" not in value:
        raise ValueError("quarantine metadata has an invalid shape")
    evidence = SuspensionQuarantine.from_dict(value["evidence"])
    expected = quarantine_context(evidence)
    if set(value) != set(expected):
        raise ValueError("quarantine metadata has an invalid shape")
    if (not isinstance(value["policy_id"], str) or value["policy_id"] != evidence.policy_id
            or value["built_from_holey_fetch"] is not True):
        raise ValueError("quarantine metadata must identify the exact incomplete incident")
    identity = "instrument" if "instrument" in expected else "instruments"
    codes = value[identity]
    if identity == "instrument":
        valid = isinstance(codes, str) and codes == expected[identity]
    else:
        valid = (isinstance(codes, list) and all(isinstance(code, str) for code in codes)
                 and codes == expected[identity])
    if not valid:
        raise ValueError("quarantine metadata must identify the exact incomplete incident")
    return evidence


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
    if present and len(items) == 1:
        evidence = items[0].quarantine
        if evidence is not None and selected == evidence.policy_id:
            return evidence
    return None  # No qualifying authorization, never a completeness fallback.
