"""Independent literal regressions for the genuine original R+S evidence shape."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pandas as pd
import pytest

from src.data.pit.quarantine_gate import validate_scoped_quarantine
from src.data.tushare import quarantine_transaction as transaction
from src.data.tushare import suspension_quarantine as acquisition
from src.data.tushare.fetch_manifest import build_manifest, read_manifest, write_manifest
from src.data.tushare.fetch_types import FetchHole, TushareFetchResult

POLICY = "suspend-688005-20260116-conflict"
START, END = "20151001", "20261008"
FIELDS = ["ts_code", "trade_date", "suspend_timing", "suspend_type"]
ORIGINAL_R = ("688005.SH", "20260116", None, "R")
ORIGINAL_S = ("688005.SH", "20260116", "09:30-09:30", "S")
# Deliberately literal: these inputs must not derive from the implementation's
# approved/reference/conflict constants, which missed the actual source pair.
UNRELATED_EIGHT = (
    ("688766.SH", "20251127", None, "S"),
    ("688766.SH", "20251128", None, "S"),
    ("688766.SH", "20251201", None, "S"),
    ("688766.SH", "20251202", None, "S"),
    ("688766.SH", "20251203", None, "S"),
    ("688766.SH", "20251204", None, "S"),
    ("688766.SH", "20251205", None, "S"),
    ("688766.SH", "20251209", None, "R"),
)


def _frame(*rows):
    return pd.DataFrame(rows, columns=FIELDS)


def _publish(raw, frame, *, prior=None, policy=POLICY):
    return acquisition.publish_suspension_candidate(
        raw, frame, prior=prior, start_date=START, end_date=END, policy=policy,
    )


def test_first_conflict_from_original_pair_preserves_both_reference_rows(tmp_path):
    path = tmp_path / "suspend_d.parquet"
    original = _frame(*UNRELATED_EIGHT, ORIGINAL_R, ORIGINAL_S)
    original.to_parquet(path, index=False)
    original_bytes = path.read_bytes()
    original_sha = hashlib.sha256(original_bytes).hexdigest()
    candidate = _frame(*UNRELATED_EIGHT, ORIGINAL_S)

    evidence = _publish(tmp_path, candidate)

    assert evidence is not None
    assert evidence.policy_id == POLICY
    assert evidence.missing_dates == ("20260116",)
    assert evidence.reference_sha256 == evidence.retained_sha256 == original_sha
    preserved = tmp_path / "_suspension_quarantine" / f"{original_sha}.parquet"
    assert preserved.read_bytes() == original_bytes
    pd.testing.assert_frame_equal(pd.read_parquet(preserved), original)
    pd.testing.assert_frame_equal(pd.read_parquet(path), candidate)
    acquisition.verify_quarantine_evidence(tmp_path, evidence)


def _seed(raw):
    path = raw / "suspend_d.parquet"
    _frame(*UNRELATED_EIGHT, ORIGINAL_R, ORIGINAL_S).to_parquet(path, index=False)
    return path


def _commit(raw, evidence):
    hole = FetchHole("suspend_d", "file", "quarantined_history", 1, "conflict", evidence)
    manifest = build_manifest([TushareFetchResult("suspend_d", 1, 9)], [hole], START, END)
    write_manifest(raw / "fetch_manifest.json", manifest)
    return read_manifest(raw / "fetch_manifest.json")


def _established(raw):
    path = _seed(raw)
    original = path.read_bytes()
    evidence = _publish(raw, _frame(*UNRELATED_EIGHT, ORIGINAL_S))
    _commit(raw, evidence)
    return path, original, evidence


def _files(raw):
    return {str(path.relative_to(raw)): path.read_bytes() for path in raw.rglob("*") if path.is_file()}


def test_repeat_from_paired_reference_keeps_original_bytes_and_matching_provider_policy(tmp_path):
    path, original, first = _established(tmp_path)
    first_candidate = path.read_bytes()
    for _ in range(2):
        repeated = _publish(tmp_path, _frame(*UNRELATED_EIGHT, ORIGINAL_S), prior=first)
        assert repeated.reference_sha256 == first.reference_sha256 == hashlib.sha256(original).hexdigest()
        assert repeated.retained_sha256 == hashlib.sha256(first_candidate).hexdigest()
        assert repeated.missing_dates == ("20260116",)
        manifest = _commit(tmp_path, repeated)
        assert manifest.schema_version == 2
        assert manifest.endpoints["suspend_d"].status == "holes"
        assert validate_scoped_quarantine(manifest, POLICY, tmp_path, required_endpoints=("suspend_d",)) == repeated
        reference = tmp_path / "_suspension_quarantine" / f"{first.reference_sha256}.parquet"
        assert reference.read_bytes() == original
        pd.testing.assert_frame_equal(pd.read_parquet(reference), _frame(*UNRELATED_EIGHT, ORIGINAL_R, ORIGINAL_S))
        for policy in (None, "suspend-688766-20251127-20251209", "suspend-688005-688766-observed-20261008"):
            with pytest.raises(ValueError):
                validate_scoped_quarantine(manifest, policy, tmp_path, required_endpoints=("suspend_d",))
        first, first_candidate = repeated, path.read_bytes()


@pytest.mark.parametrize("established", [False, True])
@pytest.mark.parametrize("incident_rows", [
    (), (ORIGINAL_R,), (ORIGINAL_R, ORIGINAL_S),
    (("688005.SH", "20260116", "09:30-10:00", "S"),),
    (("688005.SH", "20260116", "09:30-09:30", "R"),),
    (ORIGINAL_S, ("688005.SH", "20260116", "09:31-09:31", "S")),
])
def test_candidate_cannot_restore_clear_retime_or_expand_pair_conflict(tmp_path, established, incident_rows):
    if established:
        _, _, prior = _established(tmp_path)
    else:
        _seed(tmp_path)
        prior = None
    before = _files(tmp_path)
    with pytest.raises(ValueError):
        _publish(tmp_path, _frame(*UNRELATED_EIGHT, *incident_rows), prior=prior)
    assert _files(tmp_path) == before
    assert not transaction.pending_quarantine_exists(tmp_path)


@pytest.mark.parametrize("established", [False, True])
@pytest.mark.parametrize("omitted", range(8))
def test_each_unrelated_688766_key_remains_required_not_authorized_by_single_policy(tmp_path, established, omitted):
    if established:
        _, _, prior = _established(tmp_path)
    else:
        _seed(tmp_path)
        prior = None
    before = _files(tmp_path)
    candidate = _frame(*(row for index, row in enumerate(UNRELATED_EIGHT) if index != omitted), ORIGINAL_S)
    with pytest.raises(ValueError):
        _publish(tmp_path, candidate, prior=prior)
    assert _files(tmp_path) == before
    assert not transaction.pending_quarantine_exists(tmp_path)


def test_matching_interrupted_publication_restores_complete_pair_and_successor_preserves_it(tmp_path):
    path = _seed(tmp_path)
    original = path.read_bytes()
    first = _publish(tmp_path, _frame(*UNRELATED_EIGHT, ORIGINAL_S))
    assert transaction.pending_quarantine_exists(tmp_path)
    assert transaction.recover_quarantine_publication(tmp_path, policy=POLICY, start_date=START, end_date=END, enabled=True)
    assert path.read_bytes() == original
    assert transaction.pending_quarantine_exists(tmp_path)
    with pytest.raises(ValueError):
        transaction.assert_no_pending_quarantine(tmp_path)
    successor = _publish(tmp_path, _frame(*UNRELATED_EIGHT, ORIGINAL_S))
    assert successor.reference_sha256 == first.reference_sha256
    manifest = _commit(tmp_path, successor)
    assert not transaction.pending_quarantine_exists(tmp_path)
    assert validate_scoped_quarantine(manifest, POLICY, tmp_path, required_endpoints=("suspend_d",)) == successor
    assert (tmp_path / "_suspension_quarantine" / f"{first.reference_sha256}.parquet").read_bytes() == original


@pytest.mark.parametrize("policy", [None, "suspend-688766-20251127-20251209", "suspend-688005-688766-observed-20261008"])
def test_cross_policy_recovery_preserves_pair_publication_journal_and_candidate(tmp_path, policy):
    _seed(tmp_path)
    _publish(tmp_path, _frame(*UNRELATED_EIGHT, ORIGINAL_S))
    before = _files(tmp_path)
    with pytest.raises(ValueError):
        transaction.recover_quarantine_publication(tmp_path, policy=policy, start_date=START, end_date=END, enabled=True)
    assert _files(tmp_path) == before


@pytest.mark.parametrize("policy", ["suspend-688766-20251127-20251209", "suspend-688005-688766-observed-20261008"])
def test_paired_reference_prior_cannot_be_reused_by_other_policy(tmp_path, policy):
    _, _, evidence = _established(tmp_path)
    before = _files(tmp_path)
    with pytest.raises(ValueError, match="matching prior"):
        _publish(tmp_path, _frame(*UNRELATED_EIGHT, ORIGINAL_S), prior=evidence, policy=policy)
    assert _files(tmp_path) == before


@pytest.mark.parametrize("target", ["reference", "retained", "candidate"])
def test_provider_rechecks_complete_paired_evidence_bytes_and_rejects_tamper(tmp_path, target):
    path, _, evidence = _established(tmp_path)
    # A repeated write gives retained S-only evidence distinct from the paired
    # reference, so these are three actual byte-bound consumer roles.
    evidence = _publish(tmp_path, _frame(*UNRELATED_EIGHT, ORIGINAL_S), prior=evidence)
    _commit(tmp_path, evidence)
    manifest = read_manifest(tmp_path / "fetch_manifest.json")
    target_path = path if target == "candidate" else (
        tmp_path / "_suspension_quarantine" / f"{getattr(evidence, target + '_sha256')}.parquet"
    )
    before = target_path.read_bytes()
    target_path.write_bytes(before + b"tampered")
    with pytest.raises(ValueError):
        validate_scoped_quarantine(manifest, POLICY, tmp_path, required_endpoints=("suspend_d",))
    assert target_path.read_bytes() == before + b"tampered"


@pytest.mark.parametrize("role,incident_rows", [
    ("reference", ()), ("reference", (ORIGINAL_S,)),
    ("reference", (ORIGINAL_R, ORIGINAL_S, ("688005.SH", "20260116", "09:31-09:31", "S"))),
    ("retained", ()),
    ("retained", (("688005.SH", "20260116", "09:31-09:31", "S"),)),
    ("retained", (ORIGINAL_R, ORIGINAL_S, ("688005.SH", "20260116", None, "S"))),
])
def test_correctly_hashed_wrong_reference_or_retained_shape_still_refuses(tmp_path, role, incident_rows):
    _, _, evidence = _established(tmp_path)
    replacement = tmp_path / "replacement.parquet"
    _frame(*UNRELATED_EIGHT, *incident_rows).to_parquet(replacement, index=False)
    replacement_bytes = replacement.read_bytes()
    replacement_sha = hashlib.sha256(replacement_bytes).hexdigest()
    preserved = tmp_path / "_suspension_quarantine" / f"{replacement_sha}.parquet"
    preserved.write_bytes(replacement_bytes)
    altered = replace(evidence, **{role + "_sha256": replacement_sha})
    before = _files(tmp_path)
    with pytest.raises(ValueError):
        acquisition.verify_quarantine_evidence(tmp_path, altered)
    assert _files(tmp_path) == before


@pytest.mark.parametrize("reference_rows", [(ORIGINAL_R,), (ORIGINAL_R, ORIGINAL_S)])
@pytest.mark.parametrize("retained_rows", [(ORIGINAL_R,), (ORIGINAL_S,), (ORIGINAL_R, ORIGINAL_S)])
def test_single_conflict_history_has_only_the_explicit_finite_reference_and_retained_shapes(reference_rows, retained_rows):
    assert acquisition._validate_history(
        _frame(*UNRELATED_EIGHT, ORIGINAL_S),
        _frame(*UNRELATED_EIGHT, *reference_rows),
        _frame(*UNRELATED_EIGHT, *retained_rows),
        policy=POLICY,
    ) == ("20260116",)


def test_provider_does_not_accept_other_holes_alongside_pair_evidence(tmp_path):
    _, _, evidence = _established(tmp_path)
    holes = [
        FetchHole("suspend_d", "file", "quarantined_history", 1, "conflict", evidence),
        FetchHole("daily", "20261008", "unusable_response", 1, "another hole"),
    ]
    manifest = build_manifest([TushareFetchResult("suspend_d", 1, 9), TushareFetchResult("daily", 0, 0)], holes, START, END)
    with pytest.raises(ValueError, match="no other holes"):
        validate_scoped_quarantine(manifest, POLICY, tmp_path, required_endpoints=("suspend_d",))


def test_paired_reference_journal_rejects_policy_tamper_without_repairing_it(tmp_path):
    _seed(tmp_path)
    _publish(tmp_path, _frame(*UNRELATED_EIGHT, ORIGINAL_S))
    path = tmp_path / transaction.PENDING_FILENAME
    journal = json.loads(path.read_text(encoding="utf-8"))
    journal["policy_id"] = "suspend-688005-688766-observed-20261008"
    path.write_text(json.dumps(journal), encoding="utf-8")
    before = _files(tmp_path)
    with pytest.raises(ValueError):
        transaction.recover_quarantine_publication(tmp_path, policy=POLICY, start_date=START, end_date=END, enabled=True)
    assert _files(tmp_path) == before


def test_real_fetch_cli_pair_input_reports_three_and_keeps_matching_provider_gate(tmp_path, monkeypatch):
    from tests.data_pipeline.test_suspension_quarantine_fetch import _cli, _client

    path = _seed(tmp_path)
    original = path.read_bytes()
    write_manifest(tmp_path / "fetch_manifest.json", build_manifest(
        [TushareFetchResult("suspend_d", 1, 10)], (), START, END,
    ))
    cli = _cli()
    client = _client(_frame(*UNRELATED_EIGHT, ORIGINAL_S))
    monkeypatch.setattr(cli.TushareClient, "from_environment", lambda: client)
    args = ["--output-dir", str(tmp_path), "--start-date", START, "--end-date", END,
        "--endpoints", "suspend_d", "--refresh-current", "--rate-limit-sleep-ms", "0",
        "--suspension-quarantine", POLICY]
    for _ in range(2):
        assert cli.main(args) == 3
        manifest = read_manifest(tmp_path / "fetch_manifest.json")
        evidence = validate_scoped_quarantine(manifest, POLICY, tmp_path, required_endpoints=("suspend_d",))
        assert evidence.reference_sha256 == hashlib.sha256(original).hexdigest()
        pd.testing.assert_frame_equal(pd.read_parquet(path), _frame(*UNRELATED_EIGHT, ORIGINAL_S))
    assert client.call.call_count >= 2
