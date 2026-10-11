"""Canonical JSON ancestors cannot launder a genuine foreign raw archive."""

from __future__ import annotations

import hashlib
from dataclasses import replace

import pandas as pd
import pytest

from src.data.tushare import suspension_quarantine as acquisition
from tests.data_pipeline.test_security_quarantine_lineage import (
    ADDITIONS,
    FOREIGN_OTHER,
    _record,
    _two_generations,
)
from tests.data_pipeline.test_security_suspension_quarantine import (
    EIGHT,
    R,
    _commit,
    _files,
    _frame,
    _publish,
    _seed,
)


def _keys(frame):
    return {
        (str(code), str(day), None if pd.isna(timing) else str(timing), str(kind))
        for code, day, timing, kind in frame[
            ["ts_code", "trade_date", "suspend_timing", "suspend_type"]
        ].itertuples(index=False, name=None)
    }


def _loss_facts(candidate, *baselines):
    required = set().union(*(_keys(frame) for frame in baselines))
    missing = required - _keys(candidate)
    return (
        tuple(sorted({key[1] for key in missing})),
        tuple(sorted({key[0] for key in missing})),
    )


@pytest.mark.parametrize("foreign_policy", [
    "suspend-688766-20251127-20251209", "suspend-security-anomalies-v1",
], ids=["foreign-legacy-policy", "foreign-dynamic-chain"])
def test_synthetic_canonical_ancestors_cannot_launder_a_foreign_archive(tmp_path, foreign_policy):
    target = tmp_path / "target"
    foreign = tmp_path / "foreign"
    target.mkdir()
    foreign.mkdir()
    _, evidence = _two_generations(target)
    acquisition.verify_quarantine_evidence(target, evidence)
    original_files = _files(target)

    # F is a real immutable reference archive from a separate successful
    # publication. No malformed Parquet or unavailable assets cause rejection.
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
    payload = source.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == witness_sha
    with destination.open("xb") as output:
        output.write(payload)

    archive = target / "_suspension_quarantine"
    reference = pd.read_parquet(archive / f"{evidence.reference_sha256}.parquet")
    retained = pd.read_parquet(archive / f"{evidence.retained_sha256}.parquet")
    candidate = pd.read_parquet(target / "suspend_d.parquet")
    witness = pd.read_parquet(destination)
    assert evidence.retained_sha256 == evidence.candidate_sha256
    assert witness_sha not in {evidence.reference_sha256, evidence.retained_sha256}

    # Construct root A->F and parent F->B, where genuine current retained/raw
    # are B. The root's missing codes/dates are accurate, but its 4/4 systemic
    # loss was never a legal publication. Metadata-only traversal misses this.
    root_dates, root_codes = _loss_facts(witness, reference)
    assert root_codes == tuple(sorted({key[0] for key in _keys(reference)}))
    root = replace(
        evidence, retained_sha256=evidence.reference_sha256,
        candidate_sha256=witness_sha, history_sha256=(), predecessor_sha256=None,
        missing_dates=root_dates, quarantined_ts_codes=root_codes,
    )
    root_sha = _record(target, root)
    parent_dates, parent_codes = _loss_facts(retained, reference, witness)
    parent = replace(
        evidence, retained_sha256=witness_sha, candidate_sha256=evidence.retained_sha256,
        history_sha256=(), predecessor_sha256=root_sha,
        missing_dates=parent_dates, quarantined_ts_codes=parent_codes,
    )
    parent_sha = _record(target, parent)

    # Every current fact agrees with actual raw keys: the forged ancestor adds
    # three lost securities and expands the denominator to make 4/10 admissible.
    missing_dates, codes = _loss_facts(candidate, reference, retained, witness)
    baseline_codes = {key[0] for frame in (reference, retained, witness) for key in _keys(frame)}
    assert codes == ("600000.SH", "600009.SH", "688005.SH", "688766.SH")
    assert len(codes) == 4 and len(baseline_codes) == 10
    forged = replace(
        evidence, history_sha256=(witness_sha,), predecessor_sha256=parent_sha,
        missing_dates=missing_dates, quarantined_ts_codes=codes,
    )
    _record(target, forged)
    before = _files(target)
    assert all(before[name] == value for name, value in original_files.items())

    with pytest.raises(ValueError):
        acquisition.verify_quarantine_evidence(target, forged)

    assert _files(target) == before


@pytest.mark.parametrize("kind", ["compressed", "footer", "decoded"])
def test_ancestor_work_budget_is_cumulative_even_for_individually_bounded_reads(kind):
    budget = {"compressed": 0, "footer": 0, "decoded": 0}
    limit = (acquisition.MAX_LINEAGE_RAW_BYTES if kind == "compressed"
             else acquisition.MAX_LINEAGE_DECODED_BYTES)
    acquisition._consume_lineage_budget(budget, kind, limit)
    with pytest.raises(ValueError, match="cumulative lineage"):
        acquisition._consume_lineage_budget(budget, kind, 1)


def test_real_ancestor_reads_cannot_bypass_cumulative_compressed_budget(tmp_path, monkeypatch):
    _, evidence = _two_generations(tmp_path)
    original = tmp_path / "_suspension_quarantine" / f"{evidence.reference_sha256}.parquet"
    live = tmp_path / "suspend_d.parquet"
    # Both initial independent files fit; the required actual ancestor file
    # read crosses the cumulative limit although each file is individually small.
    monkeypatch.setattr(acquisition, "MAX_LINEAGE_RAW_BYTES", original.stat().st_size + live.stat().st_size)
    before = _files(tmp_path)
    with pytest.raises(ValueError, match="cumulative lineage compressed"):
        acquisition.verify_quarantine_evidence(tmp_path, evidence)
    assert _files(tmp_path) == before


@pytest.mark.parametrize("end,calls_expected", [("20261009", 2), ("20261007", 3)])
def test_identical_transition_verdict_reuse_requires_query_to_cover_verified_bounds(
    tmp_path, monkeypatch, end, calls_expected,
):
    _, second = _two_generations(tmp_path)
    candidate = pd.read_parquet(tmp_path / "suspend_d.parquet")
    third = _publish(tmp_path, candidate, prior=second, end=end)
    transitions = []
    reads = []
    transition = acquisition._security_transition
    reader = acquisition._read_frame

    def observed_transition(*args, **kwargs):
        transitions.append((kwargs["start_date"], kwargs["end_date"]))
        return transition(*args, **kwargs)

    def observed_read(path, *args, **kwargs):
        reads.append(path)
        return reader(path, *args, **kwargs)

    monkeypatch.setattr(acquisition, "_security_transition", observed_transition)
    monkeypatch.setattr(acquisition, "_read_frame", observed_read)
    acquisition.verify_quarantine_evidence(tmp_path, third)
    assert len(transitions) == calls_expected
    assert reads.count(tmp_path / "suspend_d.parquet") == 1


@pytest.mark.parametrize("field", ["codes", "dates", "history"])
def test_cached_semantic_transition_still_rejects_changed_current_facts(tmp_path, field):
    _, second = _two_generations(tmp_path)
    candidate = pd.read_parquet(tmp_path / "suspend_d.parquet")
    third = _publish(tmp_path, candidate, prior=second)
    if field == "codes":
        altered = replace(third, quarantined_ts_codes=("600001.SH",))
    elif field == "dates":
        altered = replace(third, missing_dates=("20260116",))
    else:
        path = tmp_path / "additional.parquet"
        _frame(FOREIGN_OTHER).to_parquet(path, index=False)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        path.rename(tmp_path / "_suspension_quarantine" / f"{digest}.parquet")
        altered = replace(third, history_sha256=(digest,))
    _record(tmp_path, altered)
    before = _files(tmp_path)
    with pytest.raises(ValueError):
        acquisition.verify_quarantine_evidence(tmp_path, altered)
    assert _files(tmp_path) == before


def test_live_bytes_are_rechecked_on_every_independent_verification(tmp_path):
    _, evidence = _two_generations(tmp_path)
    acquisition.verify_quarantine_evidence(tmp_path, evidence)
    path = tmp_path / "suspend_d.parquet"
    path.write_bytes(path.read_bytes() + b"changed after the previous verification")
    before = _files(tmp_path)
    with pytest.raises(ValueError, match="SHA mismatch"):
        acquisition.verify_quarantine_evidence(tmp_path, evidence)
    assert _files(tmp_path) == before
