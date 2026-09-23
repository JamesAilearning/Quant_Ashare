"""Synthetic exact-incident evidence and lossless manifest/stamp regressions."""

from __future__ import annotations

import importlib
import json
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.data.pit import bundle_integrity as bi
from src.data.tushare import fetch_manifest as fm
from src.data.tushare.fetch_types import FetchHole, TushareFetchResult

POLICY = "suspend-688766-20251127-20251209"
DATES = (
    "20251127", "20251128", "20251201", "20251202",
    "20251203", "20251204", "20251205", "20251209",
)
NOW = datetime(2026, 9, 23, tzinfo=timezone.utc)


def _contract():
    # Lazy import keeps the pre-implementation reader regressions runnable.
    return importlib.import_module("src.contracts.suspension_quarantine")


def _evidence_payload():
    return {
        "policy_id": POLICY,
        "missing_dates": list(DATES),
        "reference_sha256": "a" * 64,
        "retained_sha256": "b" * 64,
        "candidate_sha256": "c" * 64,
        "query_start_date": "20151001",
        "query_end_date": "20260922",
    }


def _evidence(**changes):
    payload = _evidence_payload()
    payload["missing_dates"] = tuple(payload["missing_dates"])
    payload.update(changes)
    return _contract().SuspensionQuarantine(**payload)


def _quarantine_hole(**changes):
    fields = dict(
        endpoint="suspend_d", unit="file", reason_class="quarantined_history",
        attempts=1, last_error="approved history absent", quarantine=_evidence(),
    )
    fields.update(changes)
    return FetchHole(**fields)


def _ordinary_hole(endpoint="daily", unit="ts_code=600000.SH year=2026"):
    return FetchHole(endpoint, unit, "transient", 2, "synthetic unavailable")


def _raw_hole(*, metadata=True):
    hole = {
        "endpoint": "suspend_d", "unit": "file",
        "reason_class": "quarantined_history", "attempts": 1,
        "last_error": "approved history absent",
    }
    if metadata:
        hole["quarantine"] = _evidence_payload()
    return hole


def _raw_document(kind, *, version=2, metadata=True):
    hole = _raw_hole(metadata=metadata)
    if kind == "stamp":
        return {
            "schema_version": version, "built_from_holey_fetch": True,
            "built_at": NOW.isoformat(), "holes": [hole],
        }
    hole.pop("endpoint")
    return {
        "schema_version": version, "fetched_at": NOW.isoformat(),
        "endpoints": {"suspend_d": {
            "status": "holes", "coverage_start_date": "20151001",
            "coverage_end_date": "20260922", "units_written": 1,
            "units_verified": 0, "holes": [hole],
        }},
    }


def _read_document(tmp_path, kind, payload):
    name = bi.INTEGRITY_FILENAME if kind == "stamp" else fm.MANIFEST_FILENAME
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return bi.read_bundle_integrity(tmp_path) if kind == "stamp" else fm.read_manifest(path)


def _document_hole(payload, kind):
    return (payload["holes"] if kind == "stamp"
            else payload["endpoints"]["suspend_d"]["holes"])[0]


def _error(kind):
    return bi.BundleIntegrityError if kind == "stamp" else fm.FetchManifestError


def _manifest(holes=(), *, endpoint="suspend_d", written=1, verified=0):
    return fm.build_manifest(
        [TushareFetchResult(endpoint, written, 1, units_verified=verified)],
        holes, "20151001", "20260922", now=NOW,
    )


def test_exact_policy_and_business_keys_are_not_a_security_count_tolerance():
    contract = _contract()
    assert contract.POLICY_ID == POLICY
    assert contract.QUARANTINE_REASON == "quarantined_history"
    assert contract.TS_CODE == "688766.SH"
    assert contract.INSTRUMENT == "SH688766"
    assert contract.APPROVED_KEYS == frozenset(
        ("688766.SH", day, None, "R" if day == "20251209" else "S") for day in DATES
    )
    assert contract.validate_policy(None) is None
    assert contract.validate_policy(POLICY) == POLICY


@pytest.mark.parametrize("value", ["", "688766.SH", POLICY + " ", True, 1, [], {}])
def test_unknown_policy_refuses_without_coercing(value):
    with pytest.raises(ValueError):
        _contract().validate_policy(value)


def test_typed_evidence_is_frozen_and_json_roundtrips_without_aliasing():
    evidence = _evidence()
    payload = evidence.to_dict()
    assert payload == _evidence_payload()
    restored = _contract().SuspensionQuarantine.from_dict(payload)
    assert restored == evidence
    payload["missing_dates"].clear()
    assert evidence.missing_dates == DATES
    assert restored.missing_dates == DATES
    with pytest.raises(FrozenInstanceError):
        evidence.policy_id = "other"


@pytest.mark.parametrize("missing", [(DATES[0],), DATES[2:5], (DATES[-1],)])
def test_partial_return_keeps_only_the_still_missing_approved_dates(missing):
    evidence = _evidence(missing_dates=missing)
    assert _contract().SuspensionQuarantine.from_dict(evidence.to_dict()) == evidence


@pytest.mark.parametrize("missing", [
    (), list(DATES), (DATES[1], DATES[0]), (DATES[0], DATES[0]),
    ("20251208",), ("2025-11-27",), (20251127,), (None,), (True,),
])
def test_missing_dates_cannot_expand_reorder_duplicate_or_coerce(missing):
    with pytest.raises(ValueError):
        _evidence(missing_dates=missing)


@pytest.mark.parametrize("field", ["reference_sha256", "retained_sha256", "candidate_sha256"])
@pytest.mark.parametrize("value", ["", "a" * 63, "g" * 64, "A" * 64, None, 1, True])
def test_all_evidence_hashes_are_strict_sha256(field, value):
    with pytest.raises(ValueError):
        _evidence(**{field: value})


@pytest.mark.parametrize("field,value", [
    ("policy_id", "other"), ("policy_id", None),
    ("query_start_date", "20251128"), ("query_end_date", "20251205"),
    ("query_start_date", "20260923"), ("query_end_date", "20151001"),
    ("query_start_date", "20250230"), ("query_end_date", "20261301"),
    ("query_start_date", "２０１５１００１"), ("query_end_date", "2026-09-22"),
    ("query_start_date", True), ("query_end_date", 20260922),
])
def test_evidence_requires_real_full_incident_query_bounds(field, value):
    with pytest.raises(ValueError):
        _evidence(**{field: value})


@pytest.mark.parametrize("mutation", ["missing", "extra", "tuple_dates", "null", "array"])
def test_evidence_json_requires_exact_fields_and_array_dates(mutation):
    payload = _evidence_payload()
    if mutation == "missing":
        payload.pop("reference_sha256")
    elif mutation == "extra":
        payload["allow_other_stocks"] = True
    elif mutation == "tuple_dates":
        payload["missing_dates"] = DATES
    elif mutation == "null":
        payload = None
    else:
        payload = []
    with pytest.raises(ValueError):
        _contract().SuspensionQuarantine.from_dict(payload)


def test_one_validated_hole_is_qualified_only_with_explicit_matching_policy():
    contract = _contract()
    hole = _quarantine_hole()
    contract.validate_hole_quarantine(hole)
    assert contract.has_quarantine((hole,))
    assert contract.qualified_quarantine((hole,), POLICY) == hole.quarantine
    assert contract.qualified_quarantine((hole,), None) is None
    assert contract.qualified_quarantine((), POLICY) is None
    assert contract.qualified_quarantine((_ordinary_hole(),), POLICY) is None
    assert not contract.has_quarantine((_ordinary_hole(),))
    assert not contract.has_quarantine(())


@pytest.mark.parametrize("mode", ["other_endpoint", "same_unit_failure", "duplicate_quarantine"])
def test_mixed_or_duplicate_holes_are_never_qualified(mode):
    hole = _quarantine_hole()
    other = (hole if mode == "duplicate_quarantine" else
             _ordinary_hole("suspend_d", "file") if mode == "same_unit_failure"
             else _ordinary_hole())
    assert _contract().qualified_quarantine((hole, other), POLICY) is None


def test_unknown_policy_refuses_even_without_any_quarantine_holes():
    with pytest.raises(ValueError):
        _contract().qualified_quarantine((), "other")


@pytest.mark.parametrize("changes", [
    {"endpoint": "namechange"}, {"unit": "other"}, {"reason_class": "transient"},
    {"quarantine": None}, {"quarantine": {}}, {"quarantine": True},
])
@pytest.mark.parametrize("policy", [None, POLICY])
def test_malformed_hole_is_not_waived_by_missing_policy_or_other_valid_holes(changes, policy):
    good = _quarantine_hole()
    fields = dict(
        endpoint=good.endpoint, unit=good.unit, reason_class=good.reason_class,
        attempts=good.attempts, last_error=good.last_error, quarantine=good.quarantine,
    )
    fields.update(changes)
    malformed = SimpleNamespace(**fields)
    with pytest.raises(ValueError):
        _contract().validate_hole_quarantine(malformed)
    with pytest.raises(ValueError):
        _contract().has_quarantine((good, malformed))
    with pytest.raises(ValueError):
        _contract().qualified_quarantine((good, malformed), policy)


@pytest.mark.parametrize("kind", ["manifest", "stamp"])
@pytest.mark.parametrize("metadata", [True, False])
def test_legacy_reader_cannot_silently_erase_quarantine_metadata_or_reason(tmp_path, kind, metadata):
    # Meaningful RED on the old source: both readers accepted these v1 documents.
    payload = _raw_document(kind, version=1, metadata=metadata)
    with pytest.raises(_error(kind)):
        _read_document(tmp_path, kind, payload)


@pytest.mark.parametrize("kind", ["manifest", "stamp"])
def test_new_version_reads_structured_hole_without_discarding_identity(tmp_path, kind):
    got = _read_document(tmp_path, kind, _raw_document(kind))
    assert got.schema_version == 2
    holes = got.holes if kind == "stamp" else fm.all_holes(got)
    assert holes[0].quarantine.to_dict() == _evidence_payload()


@pytest.mark.parametrize("kind", ["manifest", "stamp"])
@pytest.mark.parametrize("mutation", [
    "no_quarantine", "null_quarantine", "empty_quarantine", "bad_hash", "missing_field",
    "unknown_field", "wrong_unit", "wrong_reason", "clean", "bool_version", "float_version",
])
def test_corrupt_versioned_quarantine_is_always_domain_error(tmp_path, kind, mutation):
    payload = _raw_document(kind)
    hole = _document_hole(payload, kind)
    if mutation == "no_quarantine":
        hole.pop("quarantine")
        hole["reason_class"] = "transient"
    elif mutation == "null_quarantine":
        hole["quarantine"] = None
    elif mutation == "empty_quarantine":
        hole["quarantine"] = {}
    elif mutation == "bad_hash":
        hole["quarantine"]["candidate_sha256"] = "bad"
    elif mutation == "missing_field":
        hole["quarantine"].pop("missing_dates")
    elif mutation == "unknown_field":
        hole["quarantine"]["extra"] = True
    elif mutation == "wrong_unit":
        hole["unit"] = "688766.SH"
    elif mutation == "wrong_reason":
        hole["reason_class"] = "transient"
    elif mutation == "clean":
        if kind == "stamp":
            payload["built_from_holey_fetch"] = False
        else:
            payload["endpoints"]["suspend_d"]["status"] = "complete"
    else:
        payload["schema_version"] = True if mutation == "bool_version" else 2.0
    with pytest.raises(_error(kind)):
        _read_document(tmp_path, kind, payload)


@pytest.mark.parametrize("kind", ["manifest", "stamp"])
def test_explicit_null_metadata_is_not_treated_as_a_legacy_absent_field(tmp_path, kind):
    payload = _raw_document(kind, version=1)
    hole = _document_hole(payload, kind)
    hole["reason_class"] = "transient"
    hole["quarantine"] = None
    with pytest.raises(_error(kind)):
        _read_document(tmp_path, kind, payload)


@pytest.mark.parametrize("kind", ["manifest", "stamp"])
def test_ordinary_v1_documents_remain_readable_and_without_quarantine_field(tmp_path, kind):
    hole = _ordinary_hole()
    if kind == "manifest":
        original = _manifest((hole,), endpoint="daily")
        fm.write_manifest(tmp_path / fm.MANIFEST_FILENAME, original)
        result = fm.read_manifest(tmp_path / fm.MANIFEST_FILENAME)
        assert result == original
        path = tmp_path / fm.MANIFEST_FILENAME
    else:
        bi.write_bundle_integrity(tmp_path, built_from_holey_fetch=True, holes=(hole,), now=NOW)
        result = bi.read_bundle_integrity(tmp_path)
        assert result.holes == (hole,)
        path = tmp_path / bi.INTEGRITY_FILENAME
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert "quarantine" not in path.read_text(encoding="utf-8")


def test_manifest_to_stamp_roundtrip_preserves_all_existing_fields(tmp_path):
    hole = _quarantine_hole()
    original = _manifest((hole,))
    assert original.schema_version == 2
    assert original.endpoints["suspend_d"].status == "holes"
    assert not fm.is_complete(original)
    path = tmp_path / fm.MANIFEST_FILENAME
    fm.write_manifest(path, original)
    restored = fm.read_manifest(path)
    assert restored == original
    identity = bi.BundleIdentity("2026-09-22", "d" * 64, 5909, "2015-10-08", "2026-09-22")
    bi.write_bundle_integrity(
        tmp_path, built_from_holey_fetch=True, holes=fm.all_holes(restored), identity=identity,
        data_coverage_start="2015-10-01", expected_first_session="2015-10-08", now=NOW,
    )
    stamp = bi.read_bundle_integrity(tmp_path)
    assert stamp.schema_version == 2
    assert stamp.built_from_holey_fetch
    assert stamp.holes == (hole,)
    assert stamp.identity == identity
    assert stamp.built_at == NOW.isoformat()
    assert stamp.data_coverage_start == "2015-10-01"
    assert stamp.expected_first_session == "2015-10-08"


@pytest.mark.parametrize("mode", ["other_endpoint", "blind_skip"])
def test_manifest_merge_preserves_unattempted_quarantine_and_version(mode):
    hole = _quarantine_hole()
    previous = _manifest((hole,))
    current = _manifest(endpoint="daily") if mode == "other_endpoint" else _manifest(written=0)
    merged = fm.merge_manifest(previous, current)
    assert merged.schema_version == 2
    assert merged.endpoints["suspend_d"] == previous.endpoints["suspend_d"]
    assert not fm.is_complete(merged)


def test_repeat_quarantine_accumulates_attempts_but_uses_current_evidence():
    previous = _manifest((_quarantine_hole(),))
    fresh = _evidence(missing_dates=(DATES[-1],), candidate_sha256="d" * 64)
    current = _manifest((_quarantine_hole(quarantine=fresh, attempts=3),))
    merged = fm.merge_manifest(previous, current)
    hole, = fm.all_holes(merged)
    assert hole.attempts == 4
    assert hole.quarantine == fresh
    assert merged.schema_version == 2


def test_failed_refresh_preserves_prior_reference_and_new_failure_separately():
    previous = _manifest((_quarantine_hole(),))
    failure = _ordinary_hole("suspend_d", "file")
    current = _manifest((failure,), written=0)
    merged = fm.merge_manifest(previous, current)
    holes = fm.all_holes(merged)
    assert len(holes) == 2
    assert holes[0].reason_class == "transient"
    assert holes[0].attempts == 2
    assert holes[1] == fm.all_holes(previous)[0]
    assert merged.schema_version == 2
    assert _contract().qualified_quarantine(holes, POLICY) is None


def test_repeated_failed_refresh_accumulates_only_matching_failure_reason():
    original = _manifest((_quarantine_hole(),))
    failure = _manifest((_ordinary_hole("suspend_d", "file"),), written=0)
    previous = fm.merge_manifest(original, failure)
    merged = fm.merge_manifest(previous, failure)
    holes = fm.all_holes(merged)
    assert len(holes) == 2
    assert next(h for h in holes if h.reason_class == "transient").attempts == 4
    assert next(h for h in holes if h.reason_class == "quarantined_history").attempts == 1


def test_verified_without_write_does_not_retire_quarantine():
    previous = _manifest((_quarantine_hole(),))
    merged = fm.merge_manifest(previous, _manifest(written=0, verified=1))
    assert fm.all_holes(merged) == fm.all_holes(previous)
    assert merged.schema_version == 2
    assert not fm.is_complete(merged)


def test_no_write_cannot_replace_reference_or_candidate_evidence():
    previous = _manifest((_quarantine_hole(),))
    changed = _quarantine_hole(quarantine=_evidence(candidate_sha256="d" * 64))
    with pytest.raises(fm.FetchManifestError, match="successful file write"):
        fm.merge_manifest(previous, _manifest((changed,), written=0))


def test_quarantine_hole_without_an_endpoint_result_is_not_silently_dropped():
    with pytest.raises(fm.FetchManifestError, match="matching endpoint result"):
        fm.build_manifest([], (_quarantine_hole(),), "20151001", "20260922", now=NOW)


def test_actual_clean_replacement_recovers_to_legacy_clean_version():
    previous = _manifest((_quarantine_hole(),))
    merged = fm.merge_manifest(previous, _manifest())
    assert merged.schema_version == 1
    assert fm.all_holes(merged) == ()
    assert fm.is_complete(merged)


@pytest.mark.parametrize("kind", ["manifest", "stamp"])
def test_writer_refuses_clean_quarantine_without_creating_output(tmp_path, kind):
    target = tmp_path / "not_created"
    hole = _quarantine_hole()
    with pytest.raises(_error(kind)):
        if kind == "stamp":
            bi.write_bundle_integrity(target, built_from_holey_fetch=False, holes=(hole,), now=NOW)
        else:
            valid = _manifest((hole,))
            invalid = replace(valid, endpoints={
                "suspend_d": replace(valid.endpoints["suspend_d"], status="complete"),
            })
            fm.write_manifest(target / fm.MANIFEST_FILENAME, invalid)
    assert not target.exists()


def test_manifest_writer_refuses_v1_quarantine_instead_of_silently_upgrading(tmp_path):
    target = tmp_path / "not_created" / fm.MANIFEST_FILENAME
    invalid = replace(_manifest((_quarantine_hole(),)), schema_version=1)
    with pytest.raises(fm.FetchManifestError):
        fm.write_manifest(target, invalid)
    assert not target.parent.exists()


def test_historical_guard_rejects_quarantine_but_preserves_legacy_missing_stamp(tmp_path):
    guard = bi.assert_no_suspension_quarantine
    assert guard(tmp_path) is None
    bi.write_bundle_integrity(tmp_path, built_from_holey_fetch=False, now=NOW)
    assert guard(tmp_path) is None
    bi.write_bundle_integrity(
        tmp_path, built_from_holey_fetch=True, holes=(_ordinary_hole(),), now=NOW,
    )
    assert guard(tmp_path) is None
    bi.write_bundle_integrity(
        tmp_path, built_from_holey_fetch=True, holes=(_quarantine_hole(),), now=NOW,
    )
    with pytest.raises(bi.BundleIntegrityError, match="quarantine"):
        guard(tmp_path)


def test_historical_guard_does_not_treat_corrupt_stamp_as_absent(tmp_path):
    (tmp_path / bi.INTEGRITY_FILENAME).write_text("not-json", encoding="utf-8")
    with pytest.raises(bi.BundleIntegrityError):
        bi.assert_no_suspension_quarantine(tmp_path)
