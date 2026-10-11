"""Literal synthetic coverage for evidence-derived security isolation."""

from __future__ import annotations

import hashlib
from dataclasses import replace

import pandas as pd
import pytest

from src.contracts.suspension_quarantine import SuspensionQuarantine
from src.data.pit.quarantine_gate import validate_scoped_quarantine
from src.data.tushare import quarantine_transaction as transaction
from src.data.tushare import suspension_quarantine as acquisition
from src.data.tushare.fetch_manifest import FetchManifestError, build_manifest, read_manifest, write_manifest
from src.data.tushare.fetch_types import FetchHole, TushareFetchResult

POLICY = "suspend-security-anomalies-v1"
START, END = "20151001", "20261008"
FIELDS = ["ts_code", "trade_date", "suspend_timing", "suspend_type"]
R = ("688005.SH", "20260116", None, "R")
S = ("688005.SH", "20260116", "09:30-09:30", "S")
# Literal inputs deliberately do not derive from the incident implementation.
EIGHT = (
    ("688766.SH", "20251127", None, "S"),
    ("688766.SH", "20251128", None, "S"),
    ("688766.SH", "20251201", None, "S"),
    ("688766.SH", "20251202", None, "S"),
    ("688766.SH", "20251203", None, "S"),
    ("688766.SH", "20251204", None, "S"),
    ("688766.SH", "20251205", None, "S"),
    ("688766.SH", "20251209", None, "R"),
)
HEALTHY = (
    ("600000.SH", "20260115", None, "R"),
    ("600001.SH", "20260115", None, "R"),
    ("600002.SH", "20260115", None, "R"),
)
MORE_HEALTHY = (
    ("600003.SH", "20260115", None, "R"),
    ("600004.SH", "20260115", None, "R"),
)


def _frame(*rows):
    return pd.DataFrame(rows, columns=FIELDS)


def _seed(raw, *rows):
    path = raw / "suspend_d.parquet"
    _frame(*(rows or (*HEALTHY, R, S, *EIGHT))).to_parquet(path, index=False)
    return path


def _publish(raw, candidate, *, prior=None, policy=POLICY, start=START, end=END):
    return acquisition.publish_suspension_candidate(
        raw, candidate, prior=prior, start_date=start, end_date=end, policy=policy,
    )


def _commit(raw, evidence):
    holes = () if evidence is None else (
        FetchHole("suspend_d", "file", "quarantined_history", 1, "security history loss", evidence),
    )
    write_manifest(raw / "fetch_manifest.json", build_manifest(
        [TushareFetchResult("suspend_d", 1, len(pd.read_parquet(raw / "suspend_d.parquet")))],
        holes, START, END,
    ))
    return read_manifest(raw / "fetch_manifest.json")


def _files(raw):
    return {str(path.relative_to(raw)): path.read_bytes() for path in raw.rglob("*") if path.is_file()}


def test_literal_original_pair_and_eight_omissions_isolate_only_two_securities(tmp_path):
    path = tmp_path / "suspend_d.parquet"
    reference = _frame(*HEALTHY, R, S, *EIGHT)
    reference.to_parquet(path, index=False)
    original_bytes = path.read_bytes()
    original_sha = hashlib.sha256(original_bytes).hexdigest()
    candidate = _frame(*HEALTHY, S)

    evidence = acquisition.publish_suspension_candidate(
        tmp_path, candidate, prior=None, start_date=START, end_date=END, policy=POLICY,
    )

    assert evidence is not None
    assert evidence.policy_id == POLICY
    assert evidence.quarantined_ts_codes == ("688005.SH", "688766.SH")
    assert evidence.missing_dates == (
        "20251127", "20251128", "20251201", "20251202", "20251203",
        "20251204", "20251205", "20251209", "20260116",
    )
    assert evidence.reference_sha256 == evidence.retained_sha256 == original_sha
    assert evidence.candidate_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    preserved = tmp_path / "_suspension_quarantine" / f"{original_sha}.parquet"
    assert preserved.read_bytes() == original_bytes
    pd.testing.assert_frame_equal(pd.read_parquet(path), candidate)
    acquisition.verify_quarantine_evidence(tmp_path, evidence)


@pytest.mark.parametrize("policy", [
    None, "suspend-688766-20251127-20251209", "suspend-688005-20260116-conflict",
    "suspend-688005-688766-observed-20261008",
])
def test_dynamic_shape_is_not_accepted_by_default_or_any_old_policy(tmp_path, policy):
    _seed(tmp_path)
    before = _files(tmp_path)
    with pytest.raises(ValueError):
        _publish(tmp_path, _frame(*HEALTHY, S), policy=policy)
    assert _files(tmp_path) == before


@pytest.mark.parametrize("extra", [
    S, ("600000.SH", "20200103", None, "S"), ("000777.SZ", "20260115", None, "R"),
])
def test_legal_same_day_types_historical_backfill_and_new_security_additions_are_not_loss(tmp_path, extra):
    original = (*HEALTHY, R, *EIGHT)
    _seed(tmp_path, *original)
    candidate = _frame(*original, extra)
    before = candidate.copy(deep=True)
    assert _publish(tmp_path, candidate) is None
    pd.testing.assert_frame_equal(candidate, before)
    pd.testing.assert_frame_equal(pd.read_parquet(tmp_path / "suspend_d.parquet"), before)
    assert not transaction.pending_quarantine_exists(tmp_path)


@pytest.mark.parametrize("case", [
    "empty", "missing_column", "extra_column", "invalid_date", "outside_query",
    "invalid_code", "non_ascii_code", "code_whitespace", "invalid_reference_code", "invalid_retained_code",
])
def test_ambiguous_global_or_malformed_inputs_cannot_be_recast_as_security_loss(tmp_path, case):
    _seed(tmp_path)
    candidate = _frame(*HEALTHY, S)
    if case == "empty":
        candidate = _frame()
    elif case == "missing_column":
        candidate = candidate.drop(columns="suspend_type")
    elif case == "extra_column":
        candidate["unknown"] = 1
    elif case == "invalid_date":
        candidate.loc[0, "trade_date"] = "20260230"
    elif case == "outside_query":
        candidate.loc[0, "trade_date"] = "20261009"
    elif case in {"invalid_code", "non_ascii_code", "code_whitespace"}:
        candidate.loc[0, "ts_code"] = {
            "invalid_code": "600000.XX", "non_ascii_code": "６０００００.SH", "code_whitespace": "600000.SH ",
        }[case]
    else:
        original = pd.read_parquet(tmp_path / "suspend_d.parquet")
        original.loc[0, "ts_code"] = "malformed"
        original.to_parquet(tmp_path / "suspend_d.parquet", index=False)
    before = _files(tmp_path)
    with pytest.raises(ValueError):
        _publish(tmp_path, candidate)
    assert _files(tmp_path) == before


@pytest.mark.parametrize("start,end", [("20251201", END), (START, "20260115"), (END, START)])
def test_query_must_cover_entire_reference_and_retained_history(tmp_path, start, end):
    _seed(tmp_path)
    before = _files(tmp_path)
    with pytest.raises(ValueError):
        _publish(tmp_path, _frame(*HEALTHY, S), start=start, end=end)
    assert _files(tmp_path) == before


@pytest.mark.parametrize("n_reference,n_missing", [(4, 2), (5, 3), (23, 11)])
def test_half_or_more_of_baseline_or_more_than_ten_securities_is_a_hard_failure(tmp_path, n_reference, n_missing):
    original = tuple((f"{600000 + i:06}.SH", "20260115", None, "R") for i in range(n_reference))
    _seed(tmp_path, *original)
    before = _files(tmp_path)
    with pytest.raises(ValueError, match="systemic|minority|count"):
        _publish(tmp_path, _frame(*original[n_missing:]))
    assert _files(tmp_path) == before


def test_exact_ten_isolated_securities_can_be_a_strict_minority(tmp_path):
    original = tuple((f"{600000 + i:06}.SH", "20260115", None, "R") for i in range(21))
    _seed(tmp_path, *original)
    evidence = _publish(tmp_path, _frame(*original[10:]))
    assert evidence.quarantined_ts_codes == tuple(row[0] for row in original[:10])
    assert evidence.missing_dates == ("20260115",)
    assert evidence.history_sha256 == ()
    acquisition.verify_quarantine_evidence(tmp_path, evidence)


def test_candidate_only_new_codes_cannot_inflate_the_loss_denominator(tmp_path):
    _seed(tmp_path, *HEALTHY[:2])
    candidate = _frame(HEALTHY[0], ("000777.SZ", "20260115", None, "S"))
    before = _files(tmp_path)
    with pytest.raises(ValueError, match="minority"):
        _publish(tmp_path, candidate)
    assert _files(tmp_path) == before


def test_pruned_history_cannot_inflate_the_final_evidence_minority(tmp_path):
    original = tuple((f"{600000 + i:06}.SH", "20260115", None, "R") for i in range(7))
    newcomers = tuple((f"{777 + i:06}.SZ", "20260115", None, "S") for i in range(3))
    _seed(tmp_path, *original)
    first = _publish(tmp_path, _frame(*original[1:], *newcomers))
    _commit(tmp_path, first)
    # All three genuinely retained newer securities may be isolated: 4/10.
    second = _publish(tmp_path, _frame(*original[1:]), prior=first)
    assert len(second.quarantined_ts_codes) == 4
    _commit(tmp_path, second)
    before = _files(tmp_path)
    # Restoring the newer securities prunes their old witness. They are now
    # candidate-only, so losing four original securities is 4/7, not 4/10.
    with pytest.raises(ValueError, match="minority"):
        _publish(tmp_path, _frame(*original[4:], *newcomers), prior=second)
    assert _files(tmp_path) == before


def test_repeat_additional_loss_partial_repair_and_true_complete_repair_keep_original_evidence(tmp_path):
    _seed(tmp_path, *HEALTHY, *MORE_HEALTHY, R, S, *EIGHT)
    original_bytes = (tmp_path / "suspend_d.parquet").read_bytes()
    first = _publish(tmp_path, _frame(*HEALTHY, *MORE_HEALTHY, S))
    _commit(tmp_path, first)
    second_candidate = _frame(*HEALTHY[1:], *MORE_HEALTHY, S)
    second = _publish(tmp_path, second_candidate, prior=first)
    assert second.quarantined_ts_codes == ("600000.SH", "688005.SH", "688766.SH")
    _commit(tmp_path, second)
    third = _publish(tmp_path, _frame(*HEALTHY, *MORE_HEALTHY, S, *EIGHT), prior=second)
    assert third.quarantined_ts_codes == ("688005.SH",)
    assert third.missing_dates == ("20260116",)
    _commit(tmp_path, third)
    complete = _frame(*HEALTHY, *MORE_HEALTHY, R, S, *EIGHT)
    assert _publish(tmp_path, complete, prior=third) is None
    manifest = _commit(tmp_path, None)
    assert manifest.schema_version == 1
    assert manifest.endpoints["suspend_d"].status == "complete"
    assert (tmp_path / "_suspension_quarantine" / f"{first.reference_sha256}.parquet").read_bytes() == original_bytes
    pd.testing.assert_frame_equal(pd.read_parquet(tmp_path / "suspend_d.parquet"), complete)


@pytest.mark.parametrize("new_code", ["600000.SH", "000777.SZ"])
def test_later_added_history_remains_quarantined_across_updates_until_real_restoration(tmp_path, new_code):
    _seed(tmp_path, *HEALTHY, *MORE_HEALTHY, R, S, *EIGHT)
    added = (new_code, "20200103", None, "S")
    with_x = _frame(*HEALTHY, *MORE_HEALTHY, S, added)
    without_x = _frame(*HEALTHY, *MORE_HEALTHY, S)
    first = _publish(tmp_path, with_x)
    _commit(tmp_path, first)
    second = _publish(tmp_path, without_x, prior=first)
    assert new_code in second.quarantined_ts_codes
    assert second.history_sha256 == ()
    _commit(tmp_path, second)
    third = _publish(tmp_path, without_x, prior=second)
    assert new_code in third.quarantined_ts_codes
    assert third.history_sha256 == (second.retained_sha256,)
    assert "20200103" in third.missing_dates
    _commit(tmp_path, third)
    acquisition.verify_quarantine_evidence(tmp_path, third)
    fourth = _publish(tmp_path, with_x, prior=third)
    assert new_code not in fourth.quarantined_ts_codes
    assert fourth.quarantined_ts_codes == ("688005.SH", "688766.SH")
    assert fourth.history_sha256 == ()
    assert fourth.reference_sha256 == first.reference_sha256
    acquisition.verify_quarantine_evidence(tmp_path, fourth)


@pytest.mark.parametrize("field", ["codes", "dates", "reference_sha", "candidate_sha"])
def test_verification_recomputes_exact_scope_from_raw_bytes_not_metadata(tmp_path, field):
    _seed(tmp_path)
    evidence = _publish(tmp_path, _frame(*HEALTHY, S))
    if field == "codes":
        changed = replace(evidence, quarantined_ts_codes=("600000.SH",))
    elif field == "dates":
        changed = replace(evidence, missing_dates=("20260116",))
    elif field == "reference_sha":
        changed = replace(evidence, reference_sha256="a" * 64)
    else:
        changed = replace(evidence, candidate_sha256="a" * 64)
    with pytest.raises(ValueError):
        acquisition.verify_quarantine_evidence(tmp_path, changed)


def test_one_typed_hole_preserves_new_evidence_through_manifest_and_provider_gate(tmp_path):
    _seed(tmp_path)
    evidence = _publish(tmp_path, _frame(*HEALTHY, S))
    encoded = evidence.to_dict()
    assert set(encoded) == {
        "policy_id", "missing_dates", "reference_sha256", "retained_sha256", "candidate_sha256",
        "query_start_date", "query_end_date", "quarantined_ts_codes", "history_sha256", "predecessor_sha256",
    }
    assert SuspensionQuarantine.from_dict(encoded) == evidence
    manifest = _commit(tmp_path, evidence)
    assert manifest.schema_version == 2
    assert len(manifest.endpoints["suspend_d"].holes) == 1
    assert validate_scoped_quarantine(manifest, POLICY, tmp_path, required_endpoints=("suspend_d",)) == evidence
    for policy in (None, "suspend-688005-20260116-conflict", "suspend-688005-688766-observed-20261008"):
        with pytest.raises(ValueError):
            validate_scoped_quarantine(manifest, policy, tmp_path, required_endpoints=("suspend_d",))
    mixed = build_manifest(
        [TushareFetchResult("suspend_d", 1, 4), TushareFetchResult("daily", 0, 0)],
        [FetchHole("suspend_d", "file", "quarantined_history", 1, "isolated", evidence),
         FetchHole("daily", "20261008", "transient", 1, "unrelated")], START, END,
    )
    with pytest.raises(ValueError, match="no other holes"):
        validate_scoped_quarantine(mixed, POLICY, tmp_path, required_endpoints=("suspend_d",))


def test_real_fetch_cli_repeats_qualified_three_then_true_repair_returns_zero(tmp_path, monkeypatch):
    from tests.data_pipeline.test_suspension_quarantine_fetch import _cli, _client

    original = _frame(*HEALTHY, R, S, *EIGHT)
    _seed(tmp_path)
    _commit(tmp_path, None)
    client = _client(_frame(*HEALTHY, S))
    cli = _cli()
    monkeypatch.setattr(cli.TushareClient, "from_environment", lambda: client)
    args = ["--output-dir", str(tmp_path), "--start-date", START, "--end-date", END,
            "--endpoints", "suspend_d", "--refresh-current", "--rate-limit-sleep-ms", "0",
            "--suspension-quarantine", POLICY]
    for _ in range(2):
        assert cli.main(args) == 3
        manifest = read_manifest(tmp_path / "fetch_manifest.json")
        evidence = validate_scoped_quarantine(manifest, POLICY, tmp_path, required_endpoints=("suspend_d",))
        assert evidence.quarantined_ts_codes == ("688005.SH", "688766.SH")
        assert manifest.endpoints["suspend_d"].status == "holes"
        pd.testing.assert_frame_equal(pd.read_parquet(tmp_path / "suspend_d.parquet"), _frame(*HEALTHY, S))
    repaired_client = _client(original)
    monkeypatch.setattr(cli.TushareClient, "from_environment", lambda: repaired_client)
    assert cli.main(args) == 0
    restored = read_manifest(tmp_path / "fetch_manifest.json")
    assert restored.schema_version == 1
    assert restored.endpoints["suspend_d"].holes == ()
    assert restored.endpoints["suspend_d"].status == "complete"
    pd.testing.assert_frame_equal(
        pd.read_parquet(tmp_path / "suspend_d.parquet"),
        original.sort_values("trade_date", kind="stable").reset_index(drop=True),
    )


def test_pending_dynamic_publication_remains_closed_until_matching_manifest_commit(tmp_path):
    _seed(tmp_path)
    _commit(tmp_path, None)
    evidence = _publish(tmp_path, _frame(*HEALTHY, S))
    assert transaction.pending_quarantine_exists(tmp_path)
    with pytest.raises(FetchManifestError):
        read_manifest(tmp_path / "fetch_manifest.json")
    before = _files(tmp_path)
    with pytest.raises(ValueError):
        transaction.recover_quarantine_publication(
            tmp_path, policy="suspend-688005-20260116-conflict", start_date=START, end_date=END, enabled=True,
        )
    assert _files(tmp_path) == before
    _commit(tmp_path, evidence)
    assert not transaction.pending_quarantine_exists(tmp_path)
    acquisition.verify_quarantine_evidence(tmp_path, evidence)


@pytest.mark.parametrize("budget", ["rows", "bytes"])
def test_complete_key_union_has_a_budget_even_when_each_input_is_small(monkeypatch, budget):
    if budget == "rows":
        monkeypatch.setattr(acquisition.aggregate, "MAX_CANDIDATE_ROWS", 2)
    else:
        monkeypatch.setattr(acquisition.aggregate, "MAX_CANDIDATE_BYTES", 1)
    with pytest.raises(ValueError, match="combined key budget"):
        acquisition._security_key_sets(_frame(HEALTHY[0]), _frame(HEALTHY[1]), _frame(HEALTHY[2]), {})


def test_same_four_field_keys_in_different_inputs_do_not_consume_unique_row_budget_twice(monkeypatch):
    monkeypatch.setattr(acquisition.aggregate, "MAX_CANDIDATE_ROWS", 1)
    candidate, required = acquisition._security_key_sets(
        _frame(HEALTHY[0]), _frame(HEALTHY[0]), _frame(HEALTHY[0]), {"synthetic": _frame(HEALTHY[0])},
    )
    assert candidate == required == {HEALTHY[0]}


@pytest.mark.parametrize("kind", ["compressed", "footer", "decoded"])
def test_cumulative_history_budget_includes_each_witness_and_never_silently_skips_tail(kind):
    budget = {"compressed": 0, "footer": 0, "decoded": 0}
    acquisition._consume_witness_budget(budget, kind, 1)
    with pytest.raises(ValueError, match="cumulative witness"):
        acquisition._consume_witness_budget(budget, kind, acquisition.aggregate.MAX_CANDIDATE_BYTES)


def test_history_reader_uses_actual_immutable_bytes_and_bounded_accumulation(tmp_path, monkeypatch):
    directory = tmp_path / "_suspension_quarantine"
    directory.mkdir()
    hashes = []
    sizes = []
    for i in range(2):
        path = directory / f"owned{i}.parquet"
        _frame((f"{600000 + i:06}.SH", "20260115", None, "R")).to_parquet(path, index=False)
        payload = path.read_bytes()
        sha = hashlib.sha256(payload).hexdigest()
        path.rename(directory / f"{sha}.parquet")
        hashes.append(sha)
        sizes.append(len(payload))
    loaded = acquisition._read_security_witnesses(tmp_path, tuple(sorted(hashes)))
    assert set(loaded) == set(hashes)
    monkeypatch.setattr(acquisition.aggregate, "MAX_CANDIDATE_BYTES", sum(sizes) - 1)
    with pytest.raises(ValueError, match="cumulative witness compressed"):
        acquisition._read_security_witnesses(tmp_path, tuple(sorted(hashes)))


def test_witness_content_change_is_not_reinterpreted_as_a_new_baseline(tmp_path):
    _seed(tmp_path, *HEALTHY, *MORE_HEALTHY, R, S, *EIGHT)
    added = ("600000.SH", "20200103", None, "S")
    first = _publish(tmp_path, _frame(*HEALTHY, *MORE_HEALTHY, S, added))
    _commit(tmp_path, first)
    second = _publish(tmp_path, _frame(*HEALTHY, *MORE_HEALTHY, S), prior=first)
    _commit(tmp_path, second)
    third = _publish(tmp_path, _frame(*HEALTHY, *MORE_HEALTHY, S), prior=second)
    witness = tmp_path / "_suspension_quarantine" / f"{third.history_sha256[0]}.parquet"
    witness.write_bytes(b"changed")
    with pytest.raises(ValueError, match="SHA mismatch"):
        acquisition.verify_quarantine_evidence(tmp_path, third)


def test_correctly_hashed_but_unpublished_history_cannot_inflate_verified_scope(tmp_path):
    _seed(tmp_path)
    evidence = _publish(tmp_path, _frame(*HEALTHY, S))
    path = tmp_path / "redundant.parquet"
    _frame(HEALTHY[0]).to_parquet(path, index=False)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    path.rename(tmp_path / "_suspension_quarantine" / f"{sha}.parquet")
    altered = replace(evidence, history_sha256=(sha,))
    with pytest.raises(ValueError, match="lineage"):
        acquisition.verify_quarantine_evidence(tmp_path, altered)
