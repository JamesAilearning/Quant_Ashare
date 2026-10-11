"""Foreign actual archives cannot authorize another security quarantine chain."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from src.data.tushare import quarantine_lineage as lineage
from src.data.tushare import quarantine_transaction as transaction
from src.data.tushare import suspension_quarantine as acquisition
from src.data.tushare.fetch_manifest import FetchManifestError
from tests.data_pipeline.test_security_suspension_quarantine import (
    EIGHT,
    R,
    _commit,
    _files,
    _frame,
    _publish,
    _seed,
)

ORIGINAL = (
    ("600000.SH", "20260115", None, "R"),
    ("600001.SH", "20260115", None, "R"),
    ("600002.SH", "20260115", None, "R"),
    ("600003.SH", "20260115", None, "R"),
)
ADDITIONS = (
    ("000101.SZ", "20260115", None, "S"),
    ("000102.SZ", "20260115", None, "S"),
    ("000103.SZ", "20260115", None, "S"),
)
FOREIGN_OTHER = ("600009.SH", "20260115", None, "R")


def _record_bytes(raw, payload):
    sha = hashlib.sha256(payload).hexdigest()
    path = lineage.lineage_path(raw, sha)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        assert path.read_bytes() == payload
    else:
        with path.open("xb") as output:
            output.write(payload)
    return sha


def _record(raw, evidence):
    return _record_bytes(raw, lineage.lineage_payload(evidence))


def _established(raw):
    _seed(raw, *ORIGINAL)
    candidate = _frame(*ORIGINAL[1:], *ADDITIONS)
    evidence = _publish(raw, candidate)
    _commit(raw, evidence)
    acquisition.verify_quarantine_evidence(raw, evidence)
    return evidence, candidate


def _two_generations(raw):
    first, candidate = _established(raw)
    second = _publish(raw, candidate, prior=first)
    _commit(raw, second)
    assert second.predecessor_sha256 == _record(raw, first)
    acquisition.verify_quarantine_evidence(raw, second)
    return first, second


def _assert_refused_without_mutation(raw, evidence):
    # Supplying an internally canonical current record must not bypass the
    # root, parent edge or exact witness-pool provenance verification.
    _record(raw, evidence)
    before = _files(raw)
    with pytest.raises(ValueError):
        acquisition.verify_quarantine_evidence(raw, evidence)
    assert _files(raw) == before


@pytest.mark.parametrize("foreign_policy", [
    "suspend-688766-20251127-20251209", "suspend-security-anomalies-v1",
], ids=["foreign-legacy-policy", "foreign-dynamic-chain"])
def test_actual_foreign_archive_cannot_expand_a_genuine_quarantine_chain(tmp_path, foreign_policy):
    target = tmp_path / "target"
    foreign = tmp_path / "foreign"
    target.mkdir()
    foreign.mkdir()

    # The target has one legitimate lost security out of four. Three valid
    # candidate-only additions do not count toward the evidence denominator.
    _seed(target, *ORIGINAL)
    evidence = _publish(target, _frame(*ORIGINAL[1:], *ADDITIONS))
    _commit(target, evidence)
    assert evidence.quarantined_ts_codes == ("600000.SH",)
    assert evidence.history_sha256 == ()
    acquisition.verify_quarantine_evidence(target, evidence)

    # Produce the borrowed archive through a genuine independent publication,
    # not a malformed or fabricated Parquet. The old policy loses only its
    # exact eight keys; the independent dynamic chain loses only 600009.SH.
    _seed(foreign, *ADDITIONS, R, FOREIGN_OTHER, *EIGHT)
    foreign_candidate = (
        _frame(*ADDITIONS, R, FOREIGN_OTHER) if foreign_policy.startswith("suspend-688766-")
        else _frame(*ADDITIONS, R, *EIGHT)
    )
    foreign_evidence = _publish(foreign, foreign_candidate, policy=foreign_policy)
    _commit(foreign, foreign_evidence)
    acquisition.verify_quarantine_evidence(foreign, foreign_evidence)
    witness_sha = foreign_evidence.reference_sha256
    source = foreign / "_suspension_quarantine" / f"{witness_sha}.parquet"
    destination = target / "_suspension_quarantine" / f"{witness_sha}.parquet"
    with destination.open("xb") as output:
        output.write(source.read_bytes())

    # Before the lineage fix, this extra archive supplied three fabricated
    # losses relative to the target AND three matching candidate-only codes:
    # the forged evidence became 4 affected securities / 10 baseline codes.
    # All SHA/key/date facts are correct, but the archive is not a predecessor.
    forged = replace(
        evidence,
        history_sha256=(witness_sha,),
        quarantined_ts_codes=("600000.SH", "600009.SH", "688005.SH", "688766.SH"),
        missing_dates=(
            "20251127", "20251128", "20251201", "20251202", "20251203",
            "20251204", "20251205", "20251209", "20260115", "20260116",
        ),
    )
    _assert_refused_without_mutation(target, forged)


def test_canonical_root_record_cannot_claim_a_different_retained_snapshot(tmp_path):
    evidence, _ = _established(tmp_path)
    assert evidence.predecessor_sha256 is None
    raw = tmp_path / "suspend_d.parquet"
    archive = tmp_path / "_suspension_quarantine" / f"{evidence.candidate_sha256}.parquet"
    with archive.open("xb") as output:
        output.write(raw.read_bytes())
    forged = replace(evidence, retained_sha256=evidence.candidate_sha256)
    _assert_refused_without_mutation(tmp_path, forged)


def test_canonical_child_record_must_retain_the_actual_parent_candidate(tmp_path):
    first, second = _two_generations(tmp_path)
    assert second.retained_sha256 == first.candidate_sha256
    forged = replace(second, retained_sha256=first.reference_sha256)
    _assert_refused_without_mutation(tmp_path, forged)


def test_genuine_other_chain_parent_is_not_authority_for_the_current_reference(tmp_path):
    target = tmp_path / "target"
    foreign = tmp_path / "foreign"
    target.mkdir()
    foreign.mkdir()
    _, second = _two_generations(target)
    _seed(foreign, *ADDITIONS, R, FOREIGN_OTHER, *EIGHT)
    foreign_evidence = _publish(foreign, _frame(*ADDITIONS, R, *EIGHT))
    _commit(foreign, foreign_evidence)
    parent_sha = _record(foreign, foreign_evidence)
    _record_bytes(target, lineage.lineage_path(foreign, parent_sha).read_bytes())
    # Supply the foreign chain's genuine byte assets too: rejection cannot be
    # attributed merely to an unavailable foreign reference/candidate file.
    for source in (
        *sorted((foreign / "_suspension_quarantine").glob("*.parquet")),
        foreign / "suspend_d.parquet",
    ):
        payload = source.read_bytes()
        destination = target / "_suspension_quarantine" / f"{hashlib.sha256(payload).hexdigest()}.parquet"
        if destination.exists():
            assert destination.read_bytes() == payload
        else:
            with destination.open("xb") as output:
                output.write(payload)
    forged = replace(second, predecessor_sha256=parent_sha)
    _assert_refused_without_mutation(target, forged)


def test_deleting_a_still_necessary_witness_cannot_forge_a_partial_repair(tmp_path):
    from tests.data_pipeline.test_security_suspension_quarantine import HEALTHY, MORE_HEALTHY, S

    _seed(tmp_path, *HEALTHY, *MORE_HEALTHY, R, S, *EIGHT)
    added = ("600000.SH", "20200103", None, "S")
    first = _publish(tmp_path, _frame(*HEALTHY, *MORE_HEALTHY, S, added))
    _commit(tmp_path, first)
    second = _publish(tmp_path, _frame(*HEALTHY, *MORE_HEALTHY, S), prior=first)
    _commit(tmp_path, second)
    third = _publish(tmp_path, _frame(*HEALTHY, *MORE_HEALTHY, S), prior=second)
    _commit(tmp_path, third)
    assert third.history_sha256 == (second.retained_sha256,)
    forged = replace(
        third, history_sha256=(), quarantined_ts_codes=("688005.SH", "688766.SH"),
        missing_dates=(
            "20251127", "20251128", "20251201", "20251202", "20251203",
            "20251204", "20251205", "20251209", "20260116",
        ),
    )
    _assert_refused_without_mutation(tmp_path, forged)


@pytest.mark.parametrize("record_role", ["current", "parent"])
@pytest.mark.parametrize("damage", ["missing", "changed"])
def test_missing_or_tampered_actual_lineage_records_cannot_be_rebased(tmp_path, record_role, damage):
    first, second = _two_generations(tmp_path)
    sha = _record(tmp_path, second if record_role == "current" else first)
    path = lineage.lineage_path(tmp_path, sha)
    if damage == "missing":
        path.unlink()
    else:
        path.write_bytes(path.read_bytes() + b" ")
    before = _files(tmp_path)
    with pytest.raises(ValueError):
        acquisition.verify_quarantine_evidence(tmp_path, second)
    assert _files(tmp_path) == before


@pytest.mark.parametrize("damage", ["duplicate_key", "oversized_record", "noncanonical_json"])
def test_hash_correct_parent_bytes_still_require_strict_bounded_canonical_json(tmp_path, damage):
    first, second = _two_generations(tmp_path)
    value = first.to_dict()
    if damage == "duplicate_key":
        payload = lineage.lineage_payload(first)
        assert payload.lstrip().startswith(b"{")
        brace = payload.index(b"{")
        payload = (payload[:brace + 1] + b'"policy_id":"suspend-security-anomalies-v1",'
                   + payload[brace + 1:])
    elif damage == "oversized_record":
        payload = lineage.lineage_payload(first) + b" " * lineage.MAX_LINEAGE_RECORD_BYTES
    else:
        payload = json.dumps(value, indent=4, ensure_ascii=False).encode("utf-8")
        assert payload != lineage.lineage_payload(first)
    altered_parent_sha = _record_bytes(tmp_path, payload)
    forged = replace(second, predecessor_sha256=altered_parent_sha)
    _assert_refused_without_mutation(tmp_path, forged)


@pytest.mark.parametrize("budget", ["records", "record_bytes", "total_bytes"])
def test_complete_valid_chain_is_rejected_when_any_cumulative_budget_is_exhausted(tmp_path, monkeypatch, budget):
    first, second = _two_generations(tmp_path)
    first_size = len(lineage.lineage_payload(first))
    second_size = len(lineage.lineage_payload(second))
    if budget == "records":
        monkeypatch.setattr(lineage, "MAX_LINEAGE_RECORDS", 1)
    elif budget == "record_bytes":
        monkeypatch.setattr(lineage, "MAX_LINEAGE_RECORD_BYTES", max(first_size, second_size) - 1)
    else:
        monkeypatch.setattr(lineage, "MAX_LINEAGE_BYTES", first_size + second_size - 1)
    before = _files(tmp_path)
    with pytest.raises(ValueError):
        acquisition.verify_quarantine_evidence(tmp_path, second)
    assert _files(tmp_path) == before


def test_corrupt_prior_record_refuses_publication_before_any_candidate_or_record_write(tmp_path):
    first, candidate = _established(tmp_path)
    path = lineage.lineage_path(tmp_path, _record(tmp_path, first))
    path.write_bytes(path.read_bytes() + b"corrupt")
    before = _files(tmp_path)
    with pytest.raises(ValueError):
        _publish(tmp_path, candidate, prior=first)
    assert _files(tmp_path) == before


def test_valid_lineage_records_are_immutable_and_old_policies_do_not_gain_a_new_field(tmp_path):
    first, second = _two_generations(tmp_path)
    for evidence in (first, second):
        payload = lineage.lineage_payload(evidence)
        path = lineage.lineage_path(tmp_path, hashlib.sha256(payload).hexdigest())
        assert path.read_bytes() == payload
        assert json.loads(payload)["predecessor_sha256"] == evidence.predecessor_sha256
    assert first.predecessor_sha256 is None
    assert second.predecessor_sha256 == hashlib.sha256(lineage.lineage_payload(first)).hexdigest()
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    _seed(legacy, *ADDITIONS, R, FOREIGN_OTHER, *EIGHT)
    old = _publish(legacy, _frame(*ADDITIONS, R, FOREIGN_OTHER), policy="suspend-688766-20251127-20251209")
    _commit(legacy, old)
    assert "predecessor_sha256" not in old.to_dict()
    assert not (legacy / "_suspension_quarantine" / "lineage").exists()


def test_actual_published_manifest_is_required_before_archiving_a_successor(tmp_path):
    first, candidate = _established(tmp_path)
    (tmp_path / "fetch_manifest.json").rename(tmp_path / "archived-manifest.json")
    before = _files(tmp_path)
    with pytest.raises(ValueError, match="published manifest prior"):
        _publish(tmp_path, candidate, prior=first)
    assert _files(tmp_path) == before


def test_corrupt_current_lineage_keeps_pending_before_manifest_publication(tmp_path):
    _seed(tmp_path, *ORIGINAL)
    evidence = _publish(tmp_path, _frame(*ORIGINAL[1:], *ADDITIONS))
    record = lineage.lineage_path(tmp_path, _record(tmp_path, evidence))
    record.write_bytes(record.read_bytes() + b"corrupt")
    before = _files(tmp_path)
    with pytest.raises(FetchManifestError, match="lineage"):
        _commit(tmp_path, evidence)
    assert transaction.pending_quarantine_exists(tmp_path)
    assert _files(tmp_path) == before


@pytest.mark.parametrize("record_role", ["current", "parent"])
def test_committed_recovery_cannot_discharge_pending_with_corrupt_lineage(tmp_path, monkeypatch, record_role):
    first, candidate = _established(tmp_path)
    second = _publish(tmp_path, candidate, prior=first)
    with monkeypatch.context() as failing:
        def interrupted_remove(*args):
            raise transaction.QuarantineTransactionError("synthetic interruption after committed manifest")
        failing.setattr(transaction, "_remove_pending", interrupted_remove)
        with pytest.raises(FetchManifestError, match="synthetic interruption"):
            _commit(tmp_path, second)
    assert transaction.pending_quarantine_exists(tmp_path)
    record = lineage.lineage_path(tmp_path, _record(tmp_path, second if record_role == "current" else first))
    original = record.read_bytes()
    record.write_bytes(original + b"corrupt")
    before = _files(tmp_path)
    with pytest.raises(transaction.QuarantineTransactionError, match="lineage"):
        transaction.recover_quarantine_publication(
            tmp_path, policy=second.policy_id, start_date="20151001", end_date="20261008", enabled=True,
        )
    assert transaction.pending_quarantine_exists(tmp_path)
    assert _files(tmp_path) == before
    # Restoring the test's actual original receipt permits the existing
    # committed recovery, which still requires the next genuine refresh.
    record.write_bytes(original)
    assert transaction.recover_quarantine_publication(
        tmp_path, policy=second.policy_id, start_date="20151001", end_date="20261008", enabled=True,
    )
    assert not transaction.pending_quarantine_exists(tmp_path)
