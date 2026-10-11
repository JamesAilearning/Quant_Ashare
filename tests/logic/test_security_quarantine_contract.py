"""Strict dynamic evidence must not broaden the old incident wire contracts."""

from dataclasses import FrozenInstanceError, replace

import pytest

from src.contracts.suspension_quarantine import (
    COMBINED_POLICY_ID,
    CONFLICT_POLICY_ID,
    POLICY_ID,
    SECURITY_POLICY_ID,
    SecuritySuspensionQuarantine,
    SuspensionQuarantine,
    incident_for_policy,
    quarantine_context,
    quarantine_instruments,
    quarantine_ts_codes,
    validate_quarantine_context,
)


def _evidence(**changes):
    payload = {
        "policy_id": SECURITY_POLICY_ID, "missing_dates": ("20260116",),
        "reference_sha256": "a" * 64, "retained_sha256": "b" * 64,
        "candidate_sha256": "c" * 64, "query_start_date": "20151001",
        "query_end_date": "20261008", "quarantined_ts_codes": ("300001.SZ", "600036.SH"),
        "history_sha256": (),
        "predecessor_sha256": None,
    }
    payload.update(changes)
    return SecuritySuspensionQuarantine(**payload)


def test_ten_field_variant_roundtrips_without_aliasing_or_fixed_incident():
    evidence = _evidence(history_sha256=("d" * 64,))
    payload = evidence.to_dict()
    assert len(payload) == 10
    assert SuspensionQuarantine.from_dict(payload) == evidence
    assert isinstance(evidence, SuspensionQuarantine)
    payload["quarantined_ts_codes"].append("688005.SH")
    payload["history_sha256"].clear()
    assert quarantine_ts_codes(evidence) == ("300001.SZ", "600036.SH")
    assert evidence.history_sha256 == ("d" * 64,)
    assert quarantine_instruments(evidence) == ("SZ300001", "SH600036")
    with pytest.raises(ValueError):
        incident_for_policy(SECURITY_POLICY_ID)
    with pytest.raises(FrozenInstanceError):
        evidence.quarantined_ts_codes = ()


@pytest.mark.parametrize("changes", [
    {"quarantined_ts_codes": ()}, {"quarantined_ts_codes": ["600036.SH"]},
    {"quarantined_ts_codes": ("600036.SH", "300001.SZ")},
    {"quarantined_ts_codes": ("600036.SH", "600036.SH")},
    {"quarantined_ts_codes": tuple(f"{i:06d}.SH" for i in range(11))},
    {"quarantined_ts_codes": (None,)}, {"quarantined_ts_codes": ("６０００３６.SH",)},
    {"quarantined_ts_codes": ("600036.SH ",)}, {"quarantined_ts_codes": ("SH600036",)},
    {"missing_dates": ()}, {"missing_dates": ("20260116", "20260116")},
    {"missing_dates": ("20260230",)}, {"missing_dates": (None,)},
    {"query_start_date": "20260117"}, {"query_end_date": "20260115"},
    {"reference_sha256": "A" * 64}, {"history_sha256": ["d" * 64]},
    {"history_sha256": ("d" * 64, "d" * 64)}, {"history_sha256": ("d" * 63,)},
    {"history_sha256": ("b" * 64,)}, {"history_sha256": ("a" * 64,)},
    {"history_sha256": ("c" * 64,)},
    {"history_sha256": tuple(f"{i:064x}" for i in range(33))},
    {"predecessor_sha256": "A" * 64}, {"predecessor_sha256": "d" * 63},
    {"predecessor_sha256": []}, {"predecessor_sha256": 1},
    {"policy_id": POLICY_ID},
])
def test_malformed_or_unbounded_dynamic_evidence_is_refused(changes):
    with pytest.raises(ValueError):
        _evidence(**changes)


@pytest.mark.parametrize("mutation", [
    "missing_codes", "missing_history", "missing_predecessor", "extra", "codes_scalar", "history_scalar",
])
def test_json_variant_requires_exact_ten_field_shape(mutation):
    payload = _evidence().to_dict()
    if mutation == "missing_codes":
        del payload["quarantined_ts_codes"]
    elif mutation == "missing_history":
        del payload["history_sha256"]
    elif mutation == "missing_predecessor":
        del payload["predecessor_sha256"]
    elif mutation == "extra":
        payload["allow_holes"] = True
    elif mutation == "codes_scalar":
        payload["quarantined_ts_codes"] = "600036.SH"
    else:
        payload["history_sha256"] = "d" * 64
    with pytest.raises(ValueError):
        SuspensionQuarantine.from_dict(payload)


@pytest.mark.parametrize("policy", [POLICY_ID, CONFLICT_POLICY_ID, COMBINED_POLICY_ID])
def test_old_incident_evidence_keeps_seven_fields_and_refuses_dynamic_extras(policy):
    incident = incident_for_policy(policy)
    dates = tuple(sorted(incident.missing_dates)) if policy == COMBINED_POLICY_ID else (min(incident.dates),)
    evidence = SuspensionQuarantine(policy, dates, "a" * 64, "b" * 64, "c" * 64, "20151001", "20261008")
    payload = evidence.to_dict()
    assert len(payload) == 7
    assert SuspensionQuarantine.from_dict(payload) == evidence
    assert quarantine_ts_codes(evidence) == incident.ts_codes
    assert quarantine_instruments(evidence) == incident.instruments
    for field in ("quarantined_ts_codes", "history_sha256", "predecessor_sha256"):
        extra = {**payload, field: []}
        with pytest.raises(ValueError):
            SuspensionQuarantine.from_dict(extra)


def test_single_dynamic_security_uses_plural_identity_and_rejects_alias_or_forgery():
    evidence = _evidence(quarantined_ts_codes=("600036.SH",))
    context = quarantine_context(evidence)
    assert context["instruments"] == ["SH600036"]
    assert "instrument" not in context
    assert validate_quarantine_context(context) == evidence
    with pytest.raises(ValueError):
        validate_quarantine_context({**context, "instruments": ["SH688005"]})
    with pytest.raises(ValueError):
        validate_quarantine_context({**context, "instrument": "SH600036"})
    with pytest.raises(ValueError):
        replace(evidence, policy_id=CONFLICT_POLICY_ID)


def test_read_only_notice_uses_dynamic_evidence_and_never_hides_zero_scored_matches():
    from web.operator_ui.pages._suspension_quarantine import quarantine_notice

    evidence = _evidence()
    payload = {"meta": {"suspension_quarantine": quarantine_context(evidence)}, "n_quarantined": 0, "picks": []}
    notice = quarantine_notice(payload)
    assert "300001.SZ" in notice and "600036.SH" in notice and "2 只" in notice
    assert "不完整" in notice and "持仓" in notice
    payload["picks"] = [{"stock_code": "600036.SH"}]
    with pytest.raises(ValueError, match="隔离"):
        quarantine_notice(payload)
