"""Synthetic regression for the exact two-security observed incident."""

from __future__ import annotations

import hashlib
import json
from unittest.mock import MagicMock

import pandas as pd
import pytest

from src.contracts.suspension_quarantine import (
    APPROVED_KEYS,
    CONFLICT_POLICY_ID,
    POLICY_ID,
    SuspensionQuarantine,
)
from src.data.pit.qlib_bin_builder import BUNDLE_REQUIRED_ENDPOINTS
from src.data.pit.quarantine_gate import validate_scoped_quarantine
from src.data.tushare import quarantine_transaction as transaction
from src.data.tushare import suspension_quarantine as acquisition
from src.data.tushare.fetch_manifest import (
    FetchManifestError,
    build_manifest,
    read_manifest,
    write_manifest,
)
from src.data.tushare.fetch_types import FetchHole, TushareFetchResult
from src.data.tushare.fetcher import TushareFetcher, TushareFetcherConfig, TushareFetcherError
from tests.data_pipeline.test_suspension_quarantine_fetch import _cli, _client

POLICY = "suspend-688005-688766-observed-20261008"
START, END = "20151001", "20260922"
FIELDS = ["ts_code", "trade_date", "suspend_timing", "suspend_type"]
ORIGINAL = ("688005.SH", "20260116", None, "R")
OBSERVED = ("688005.SH", "20260116", "09:30-09:30", "S")
OTHER = ("600000.SH", "20260115", None, "S")
LEGACY_DATES = tuple(sorted(key[1] for key in APPROVED_KEYS))


def _frame(*rows):
    return pd.DataFrame(rows, columns=FIELDS)


def _reference():
    return _frame(OTHER, *sorted(APPROVED_KEYS), ORIGINAL)


def _candidate():
    return _frame(OTHER, ORIGINAL, OBSERVED)


def _publish(raw, candidate, prior=None, *, policy=POLICY, start=START, end=END):
    return acquisition.publish_suspension_candidate(
        raw, candidate, prior=prior, start_date=start, end_date=end, policy=policy,
    )


def _seed(raw, frame=None):
    raw.mkdir(parents=True, exist_ok=True)
    path = raw / "suspend_d.parquet"
    reference = _reference() if frame is None else frame
    reference.to_parquet(path, index=False)
    write_manifest(raw / "fetch_manifest.json", build_manifest(
        [TushareFetchResult("suspend_d", 1, len(reference))], (), START, END,
    ))
    return path


def _commit(raw, evidence, *, core=False):
    holes = (FetchHole("suspend_d", "file", "quarantined_history", 1, "combined incident", evidence),)
    results = [TushareFetchResult("suspend_d", 1, len(pd.read_parquet(raw / "suspend_d.parquet")))]
    if core:
        results.extend(TushareFetchResult(endpoint, 1, 1) for endpoint in BUNDLE_REQUIRED_ENDPOINTS)
    write_manifest(raw / "fetch_manifest.json", build_manifest(results, holes, START, END))
    return read_manifest(raw / "fetch_manifest.json")


def _quarantined(raw, *, core=False):
    _seed(raw)
    evidence = _publish(raw, _candidate())
    _commit(raw, evidence, core=core)
    return evidence


def _bytes(raw):
    return {str(path.relative_to(raw)): path.read_bytes() for path in raw.rglob("*") if path.is_file()}


def _evidence(**changes):
    values = dict(
        policy_id=POLICY, missing_dates=LEGACY_DATES,
        reference_sha256="a" * 64, retained_sha256="b" * 64, candidate_sha256="c" * 64,
        query_start_date=START, query_end_date=END,
    )
    values.update(changes)
    return SuspensionQuarantine(**values)


def test_combined_exact_observation_has_separate_explicit_authorization(tmp_path):
    path = tmp_path / "suspend_d.parquet"
    _reference().to_parquet(path, index=False)
    original_bytes = path.read_bytes()
    evidence = _publish(tmp_path, _candidate())
    assert evidence.policy_id == POLICY
    assert evidence.missing_dates == LEGACY_DATES
    assert evidence.reference_sha256 == hashlib.sha256(original_bytes).hexdigest()
    pd.testing.assert_frame_equal(pd.read_parquet(path), _candidate())


def test_combined_publication_preserves_original_and_repeated_byte_lineage(tmp_path):
    path = _seed(tmp_path)
    original_bytes = path.read_bytes()
    evidence = _publish(tmp_path, _candidate())
    assert evidence.reference_sha256 == evidence.retained_sha256 == hashlib.sha256(original_bytes).hexdigest()
    assert (tmp_path / "_suspension_quarantine" / f"{evidence.reference_sha256}.parquet").read_bytes() == original_bytes
    _commit(tmp_path, evidence)
    acquisition.verify_quarantine_evidence(tmp_path, evidence)
    prior_candidate = path.read_bytes()
    repeated = _publish(tmp_path, _candidate(), prior=evidence)
    assert repeated.reference_sha256 == evidence.reference_sha256
    assert repeated.retained_sha256 == evidence.candidate_sha256
    _commit(tmp_path, repeated)
    assert (tmp_path / "_suspension_quarantine" / f"{repeated.retained_sha256}.parquet").read_bytes() == prior_candidate
    assert repeated.missing_dates == LEGACY_DATES
    acquisition.verify_quarantine_evidence(tmp_path, repeated)


@pytest.mark.parametrize("rows", [
    (OTHER, OBSERVED),
    (OTHER, ORIGINAL),
    (OTHER,),
    (ORIGINAL, OBSERVED),
    (OTHER, ORIGINAL, OBSERVED, ("688005.SH", "20260116", "09:30-10:00", "S")),
    (OTHER, ORIGINAL, OBSERVED, ("688766.SH", "20251127", "09:30-09:30", "S")),
])
def test_combined_missing_record_extra_affected_payload_or_other_loss_preserves_raw(tmp_path, rows):
    path = _seed(tmp_path)
    before = _bytes(tmp_path)
    with pytest.raises(ValueError):
        _publish(tmp_path, _frame(*rows))
    assert _bytes(tmp_path) == before
    assert not transaction.pending_quarantine_exists(tmp_path)
    pd.testing.assert_frame_equal(pd.read_parquet(path), _reference())


@pytest.mark.parametrize("returned", sorted(APPROVED_KEYS))
def test_combined_candidate_cannot_return_even_one_legacy_key(tmp_path, returned):
    _seed(tmp_path)
    before = _bytes(tmp_path)
    with pytest.raises(ValueError):
        _publish(tmp_path, _frame(OTHER, ORIGINAL, OBSERVED, returned))
    assert _bytes(tmp_path) == before


@pytest.mark.parametrize("case", ["missing_legacy", "missing_r", "extra_s", "wrong_legacy_timing", "observed_only"])
def test_combined_first_reference_requires_exact_original_nine_affected_keys(tmp_path, case):
    legacy = sorted(APPROVED_KEYS)
    rows = [OTHER, *legacy, ORIGINAL]
    if case == "missing_legacy":
        rows.remove(legacy[0])
    elif case == "missing_r":
        rows.remove(ORIGINAL)
    elif case == "extra_s":
        rows.append(OBSERVED)
    elif case == "wrong_legacy_timing":
        rows.remove(legacy[0])
        rows.append((legacy[0][0], legacy[0][1], "09:30-10:00", legacy[0][3]))
    else:
        rows = [OTHER, ORIGINAL, OBSERVED]
    _seed(tmp_path, _frame(*rows))
    before = _bytes(tmp_path)
    with pytest.raises(ValueError):
        _publish(tmp_path, _candidate())
    assert _bytes(tmp_path) == before


def test_selected_combined_policy_does_not_take_clean_first_acquisition_shortcut(tmp_path):
    _seed(tmp_path)
    before = _bytes(tmp_path)
    with pytest.raises(ValueError):
        _publish(tmp_path, _reference())
    assert _bytes(tmp_path) == before


@pytest.mark.parametrize("rows", [(OTHER, ORIGINAL), (OTHER, OBSERVED), (OTHER, ORIGINAL, OBSERVED, *sorted(APPROVED_KEYS))])
def test_established_combination_never_automatically_clears_on_changed_payload(tmp_path, rows):
    evidence = _quarantined(tmp_path)
    before = _bytes(tmp_path)
    with pytest.raises(ValueError):
        _publish(tmp_path, _frame(*rows), prior=evidence)
    assert _bytes(tmp_path) == before


@pytest.mark.parametrize("retained_rows", [
    (OTHER, ORIGINAL),
    (OTHER, ORIGINAL, *sorted(APPROVED_KEYS)[1:]),
    (OTHER, ORIGINAL, OBSERVED, sorted(APPROVED_KEYS)[0]),
    (ORIGINAL, OBSERVED),
])
def test_byte_bound_evidence_still_refuses_unapproved_retained_key_sets(tmp_path, retained_rows):
    directory = tmp_path / "_suspension_quarantine"
    directory.mkdir()
    reference = directory / "reference.parquet"
    retained = directory / "retained.parquet"
    candidate = tmp_path / "suspend_d.parquet"
    _reference().to_parquet(reference, index=False)
    _frame(*retained_rows).to_parquet(retained, index=False)
    _candidate().to_parquet(candidate, index=False)
    reference_sha = hashlib.sha256(reference.read_bytes()).hexdigest()
    retained_sha = hashlib.sha256(retained.read_bytes()).hexdigest()
    reference.rename(directory / f"{reference_sha}.parquet")
    retained.rename(directory / f"{retained_sha}.parquet")
    evidence = _evidence(
        reference_sha256=reference_sha, retained_sha256=retained_sha,
        candidate_sha256=hashlib.sha256(candidate.read_bytes()).hexdigest(),
    )
    with pytest.raises(ValueError):
        acquisition.verify_quarantine_evidence(tmp_path, evidence)


def test_combined_evidence_keeps_exact_seven_field_wire_shape():
    evidence = _evidence()
    encoded = evidence.to_dict()
    assert set(encoded) == {
        "policy_id", "missing_dates", "reference_sha256", "retained_sha256",
        "candidate_sha256", "query_start_date", "query_end_date",
    }
    assert encoded["missing_dates"] == list(LEGACY_DATES)
    assert SuspensionQuarantine.from_dict(encoded) == evidence


@pytest.mark.parametrize("dates", [
    (), LEGACY_DATES[:-1], (LEGACY_DATES[0],), (*LEGACY_DATES, "20260116"),
    tuple(reversed(LEGACY_DATES)), (*LEGACY_DATES, LEGACY_DATES[-1]), list(LEGACY_DATES),
])
def test_combined_evidence_rejects_subset_extra_unsorted_duplicate_or_wrong_type_dates(dates):
    with pytest.raises(ValueError):
        _evidence(missing_dates=dates)


@pytest.mark.parametrize("start,end", [("20251128", END), (START, "20251231"), (START, "20260115")])
def test_combined_query_contract_and_journal_cover_nonmissing_conflict_date(start, end):
    with pytest.raises(ValueError):
        _evidence(query_start_date=start, query_end_date=end)
    with pytest.raises(ValueError):
        acquisition.validate_quarantine_query(start, end, policy=POLICY)
    with pytest.raises(ValueError):
        transaction._bounds(start, end, policy=POLICY)


@pytest.mark.parametrize("policy", [POLICY_ID, CONFLICT_POLICY_ID])
def test_cross_policy_prior_cannot_relabel_combined_reference(tmp_path, policy):
    evidence = _quarantined(tmp_path)
    before = _bytes(tmp_path)
    with pytest.raises(ValueError, match="matching prior"):
        _publish(tmp_path, _candidate(), prior=evidence, policy=policy)
    assert _bytes(tmp_path) == before


@pytest.mark.parametrize("policy", [POLICY_ID, CONFLICT_POLICY_ID])
def test_combined_publication_cannot_take_a_prior_from_either_single_policy(tmp_path, policy):
    _seed(tmp_path)
    old_candidate = (_frame(OTHER, ORIGINAL) if policy == POLICY_ID else
                     _frame(OTHER, *sorted(APPROVED_KEYS), OBSERVED))
    prior = _publish(tmp_path, old_candidate, policy=policy)
    _commit(tmp_path, prior)
    before = _bytes(tmp_path)
    with pytest.raises(ValueError, match="matching prior"):
        _publish(tmp_path, _candidate(), prior=prior)
    assert _bytes(tmp_path) == before


@pytest.mark.parametrize("policy", [POLICY_ID, CONFLICT_POLICY_ID])
def test_cross_policy_recovery_preserves_combined_pending_and_raw(tmp_path, policy):
    _seed(tmp_path)
    _publish(tmp_path, _candidate())
    before = _bytes(tmp_path)
    with pytest.raises(ValueError, match="matching policy"):
        transaction.recover_quarantine_publication(
            tmp_path, policy=policy, start_date=START, end_date=END, enabled=True,
        )
    assert _bytes(tmp_path) == before


@pytest.mark.parametrize("start,end", [("20200101", END), (START, "20260116")])
def test_matching_policy_cannot_narrow_an_interrupted_query(tmp_path, start, end):
    _seed(tmp_path)
    _publish(tmp_path, _candidate())
    before = _bytes(tmp_path)
    with pytest.raises(ValueError, match="cannot narrow"):
        transaction.recover_quarantine_publication(
            tmp_path, policy=POLICY, start_date=start, end_date=end, enabled=True,
        )
    assert _bytes(tmp_path) == before


def test_combined_journal_cannot_use_missing_dates_as_shorter_query_domain(tmp_path):
    _seed(tmp_path)
    _publish(tmp_path, _candidate())
    journal = json.loads((tmp_path / transaction.PENDING_FILENAME).read_text(encoding="utf-8"))
    journal["query_end_date"] = "20251231"
    journal["quarantine_after"]["query_end_date"] = "20251231"
    with pytest.raises(ValueError):
        transaction._validate_journal(journal)


def test_combined_journal_embedded_evidence_cannot_select_another_policy(tmp_path):
    _seed(tmp_path)
    _publish(tmp_path, _candidate())
    journal = json.loads((tmp_path / transaction.PENDING_FILENAME).read_text(encoding="utf-8"))
    journal["policy_id"] = POLICY_ID
    with pytest.raises(ValueError, match="bind the publication"):
        transaction._validate_journal(journal)


def test_matching_interrupted_recovery_restores_original_and_requires_real_successor(tmp_path):
    path = _seed(tmp_path)
    original = path.read_bytes()
    interrupted = _publish(tmp_path, _candidate())
    with pytest.raises(FetchManifestError, match="pending"):
        read_manifest(tmp_path / "fetch_manifest.json")
    assert transaction.recover_quarantine_publication(
        tmp_path, policy=POLICY, start_date=START, end_date=END, enabled=True,
    )
    assert path.read_bytes() == original
    assert transaction.pending_quarantine_exists(tmp_path)
    with pytest.raises(ValueError, match="pending"):
        transaction.assert_no_pending_quarantine(tmp_path)
    successor = _publish(tmp_path, _candidate())
    assert successor.reference_sha256 == interrupted.reference_sha256
    _commit(tmp_path, successor)
    assert not transaction.pending_quarantine_exists(tmp_path)
    acquisition.verify_quarantine_evidence(tmp_path, successor)
    assert (tmp_path / "_suspension_quarantine" / f"{interrupted.candidate_sha256}.parquet").exists()


def test_combined_pending_marker_blocks_scoped_gate_even_with_old_clean_manifest(tmp_path):
    _seed(tmp_path)
    previous = read_manifest(tmp_path / "fetch_manifest.json")
    _publish(tmp_path, _candidate())
    with pytest.raises(ValueError, match="pending"):
        validate_scoped_quarantine(previous, POLICY, tmp_path, required_endpoints=("suspend_d",))


def test_real_synthetic_fetch_cli_repeats_exit_three_and_gate_requires_matching_only(tmp_path, monkeypatch):
    _seed(tmp_path)
    cli = _cli()
    client = _client(_candidate())
    monkeypatch.setattr(cli.TushareClient, "from_environment", lambda: client)
    argv = ["--output-dir", str(tmp_path), "--start-date", START, "--end-date", END,
            "--endpoints", "suspend_d", "--refresh-current", "--rate-limit-sleep-ms", "0",
            "--suspension-quarantine", POLICY]
    previous_calls = 0
    for _ in range(2):
        assert cli.main(argv) == 3
        assert client.call.call_count > previous_calls
        previous_calls = client.call.call_count
        manifest = read_manifest(tmp_path / "fetch_manifest.json")
        assert manifest.schema_version == 2
        assert manifest.endpoints["suspend_d"].status == "holes"
        evidence = validate_scoped_quarantine(manifest, POLICY, tmp_path, required_endpoints=("suspend_d",))
        assert evidence.missing_dates == LEGACY_DATES
        assert evidence.policy_id == POLICY
        for wrong in (None, POLICY_ID, CONFLICT_POLICY_ID):
            with pytest.raises(ValueError):
                validate_scoped_quarantine(manifest, wrong, tmp_path, required_endpoints=("suspend_d",))
    pd.testing.assert_frame_equal(pd.read_parquet(tmp_path / "suspend_d.parquet"), _candidate())


def test_combined_scoped_gate_requires_core_coverage_and_refuses_mixed_holes(tmp_path):
    evidence = _quarantined(tmp_path)
    manifest = read_manifest(tmp_path / "fetch_manifest.json")
    with pytest.raises(ValueError, match="missing core coverage"):
        validate_scoped_quarantine(manifest, POLICY, tmp_path, required_endpoints=BUNDLE_REQUIRED_ENDPOINTS)
    manifest = _commit(tmp_path, evidence, core=True)
    assert validate_scoped_quarantine(manifest, POLICY, tmp_path, required_endpoints=BUNDLE_REQUIRED_ENDPOINTS) == evidence
    extra = FetchHole("daily", "ts_code=600000.SH year=2026", "unusable_response", 1, "unrelated hole")
    results = [TushareFetchResult("suspend_d", 1, 3), *(
        TushareFetchResult(endpoint, 1, 1) for endpoint in BUNDLE_REQUIRED_ENDPOINTS
    )]
    incident = FetchHole("suspend_d", "file", "quarantined_history", 1, "combined incident", evidence)
    manifest = build_manifest(results, (incident, extra), START, END)
    write_manifest(tmp_path / "fetch_manifest.json", manifest)
    with pytest.raises(ValueError, match="no other holes"):
        validate_scoped_quarantine(manifest, POLICY, tmp_path, required_endpoints=BUNDLE_REQUIRED_ENDPOINTS)


@pytest.mark.parametrize("target", ["reference", "retained", "candidate"])
def test_combined_byte_hash_failure_refuses_provider_gate_and_retry_before_api(tmp_path, target):
    first = _quarantined(tmp_path)
    repeated = _publish(tmp_path, _candidate(), prior=first)
    manifest = _commit(tmp_path, repeated, core=True)
    path = (tmp_path / "suspend_d.parquet" if target == "candidate" else
            tmp_path / "_suspension_quarantine" / f"{getattr(repeated, target + '_sha256')}.parquet")
    path.write_bytes(path.read_bytes() + b"tampered")
    before = _bytes(tmp_path)
    with pytest.raises(ValueError, match="SHA"):
        validate_scoped_quarantine(manifest, POLICY, tmp_path, required_endpoints=BUNDLE_REQUIRED_ENDPOINTS)
    client = _client(_candidate())
    fetcher = TushareFetcher(client, TushareFetcherConfig(
        output_dir=tmp_path, endpoints=("suspend_d",), start_date=START, end_date=END,
        refresh_current=True, rate_limit_sleep_ms=0, suspension_quarantine=POLICY,
    ))
    with pytest.raises(TushareFetcherError, match="SHA"):
        fetcher.fetch()
    client.call.assert_not_called()
    assert _bytes(tmp_path) == before


def test_unknown_combined_identifier_refuses_before_files_or_api(tmp_path):
    destination = tmp_path / "not-created"
    client = _client(_candidate())
    with pytest.raises(TushareFetcherError, match="policy"):
        TushareFetcher(client, TushareFetcherConfig(
            output_dir=destination, endpoints=("suspend_d",), start_date=START, end_date=END,
            suspension_quarantine=POLICY + "-extra", rate_limit_sleep_ms=0,
        ))
    client.call.assert_not_called()
    assert not destination.exists()


def test_unknown_combined_identifier_cli_refuses_before_client_construction(tmp_path, monkeypatch):
    cli = _cli()
    client_factory = MagicMock()
    monkeypatch.setattr(cli.TushareClient, "from_environment", client_factory)
    destination = tmp_path / "not-created"
    assert cli.main([
        "--output-dir", str(destination), "--start-date", START, "--end-date", END,
        "--endpoints", "suspend_d", "--suspension-quarantine", POLICY + "-extra",
    ]) == 2
    client_factory.assert_not_called()
    assert not destination.exists()


def test_combined_query_rejection_precedes_all_vendor_calls(tmp_path):
    client = _client(_candidate())
    fetcher = TushareFetcher(client, TushareFetcherConfig(
        output_dir=tmp_path, endpoints=("suspend_d",), start_date=START, end_date="20251231",
        suspension_quarantine=POLICY, rate_limit_sleep_ms=0,
    ))
    with pytest.raises(TushareFetcherError, match="cover"):
        fetcher.fetch()
    client.call.assert_not_called()
    assert not (tmp_path / "suspend_d.parquet").exists()


@pytest.mark.parametrize("policy", [None, POLICY_ID, CONFLICT_POLICY_ID])
def test_existing_combined_policy_requires_matching_opt_in_before_any_endpoint(tmp_path, policy):
    _quarantined(tmp_path)
    before = _bytes(tmp_path)
    client = _client(_candidate())
    fetcher = TushareFetcher(client, TushareFetcherConfig(
        output_dir=tmp_path, endpoints=("stock_basic", "suspend_d"), start_date=START, end_date=END,
        suspension_quarantine=policy, refresh_current=True, rate_limit_sleep_ms=0,
    ))
    with pytest.raises(TushareFetcherError, match="explicit matching policy"):
        fetcher.fetch()
    client.call.assert_not_called()
    assert _bytes(tmp_path) == before


def test_reset_cannot_erase_established_combined_reference_or_incomplete_marker(tmp_path, monkeypatch):
    _quarantined(tmp_path)
    before = _bytes(tmp_path)
    cli = _cli()
    client_factory = MagicMock()
    monkeypatch.setattr(cli.TushareClient, "from_environment", client_factory)
    assert cli.main([
        "--output-dir", str(tmp_path), "--start-date", START, "--end-date", END,
        "--endpoints", "suspend_d", "--reset-manifest", "--suspension-quarantine", POLICY,
    ]) == 1
    client_factory.assert_not_called()
    assert _bytes(tmp_path) == before


def test_selected_combined_dry_run_preserves_bytes_and_makes_no_api_call(tmp_path, monkeypatch):
    evidence = _quarantined(tmp_path)
    before = _bytes(tmp_path)
    cli = _cli()
    client = _client(_candidate())
    monkeypatch.setattr(cli.TushareClient, "from_environment", lambda: client)
    assert cli.main([
        "--output-dir", str(tmp_path), "--start-date", START, "--end-date", END,
        "--endpoints", "suspend_d", "--dry-run", "--suspension-quarantine", POLICY,
        "--rate-limit-sleep-ms", "0",
    ]) == 3
    client.call.assert_not_called()
    assert _bytes(tmp_path) == before
    acquisition.verify_quarantine_evidence(tmp_path, evidence)
