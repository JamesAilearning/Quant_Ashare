"""Synthetic regression for the separately approved 688005 conflict."""

from __future__ import annotations

import hashlib
import importlib
import json

import pandas as pd
import pytest

from src.contracts.suspension_quarantine import SuspensionQuarantine
from src.data.tushare import quarantine_transaction as transaction
from src.data.tushare import suspension_quarantine as acquisition
from src.data.tushare.fetch_manifest import build_manifest, write_manifest
from src.data.tushare.fetch_types import FetchHole, TushareFetchResult

POLICY = "suspend-688005-20260116-conflict"
START, END = "20151001", "20260922"
FIELDS = ["ts_code", "trade_date", "suspend_timing", "suspend_type"]
ORIGINAL = ("688005.SH", "20260116", None, "R")
OBSERVED = ("688005.SH", "20260116", "09:30-09:30", "S")
OTHER = ("600000.SH", "20260115", None, "S")


def _frame(*rows):
    return pd.DataFrame(rows, columns=FIELDS)


def _publish(raw, frame, prior=None, policy=POLICY):
    return acquisition.publish_suspension_candidate(
        raw, frame, prior=prior, start_date=START, end_date=END, policy=policy,
    )


def _commit(raw, evidence):
    hole = FetchHole("suspend_d", "file", "quarantined_history", 1, "conflict", evidence)
    manifest = build_manifest([TushareFetchResult("suspend_d", 1, 2)], [hole], START, END)
    write_manifest(raw / "fetch_manifest.json", manifest)


def test_exact_conflict_keeps_original_bytes_and_repeated_reference(tmp_path):
    path = tmp_path / "suspend_d.parquet"
    _frame(OTHER, ORIGINAL).to_parquet(path, index=False)
    original_bytes = path.read_bytes()
    original_sha = hashlib.sha256(original_bytes).hexdigest()
    evidence = _publish(tmp_path, _frame(OTHER, OBSERVED))
    assert evidence.policy_id == POLICY
    assert evidence.missing_dates == ("20260116",)
    assert evidence.reference_sha256 == original_sha
    assert (tmp_path / "_suspension_quarantine" / f"{original_sha}.parquet").read_bytes() == original_bytes
    pd.testing.assert_frame_equal(pd.read_parquet(path), _frame(OTHER, OBSERVED))
    _commit(tmp_path, evidence)
    repeated = _publish(tmp_path, _frame(OTHER, OBSERVED), prior=evidence)
    assert repeated.reference_sha256 == original_sha
    _commit(tmp_path, repeated)
    acquisition.verify_quarantine_evidence(tmp_path, repeated)


def test_conflict_contract_has_separate_explicit_identity():
    evidence = SuspensionQuarantine(
        policy_id=POLICY, missing_dates=("20260116",), reference_sha256="a" * 64,
        retained_sha256="b" * 64, candidate_sha256="c" * 64,
        query_start_date=START, query_end_date=END,
    )
    assert SuspensionQuarantine.from_dict(evidence.to_dict()) == evidence


@pytest.mark.parametrize("rows", [
    (OTHER,), (OBSERVED,), (OTHER, ORIGINAL, OBSERVED),
    (OTHER, ("688005.SH", "20260116", "09:30-10:00", "S")),
    (OTHER, ("688005.SH", "20260117", "09:30-09:30", "S")),
])
def test_unapproved_loss_or_payload_preserves_raw(tmp_path, rows):
    path = tmp_path / "suspend_d.parquet"
    _frame(OTHER, ORIGINAL).to_parquet(path, index=False)
    before = path.read_bytes()
    with pytest.raises(ValueError):
        _publish(tmp_path, _frame(*rows))
    assert path.read_bytes() == before
    assert not transaction.pending_quarantine_exists(tmp_path)


def test_restored_r_does_not_silently_clear_established_conflict(tmp_path):
    path = tmp_path / "suspend_d.parquet"
    _frame(OTHER, ORIGINAL).to_parquet(path, index=False)
    evidence = _publish(tmp_path, _frame(OTHER, OBSERVED))
    _commit(tmp_path, evidence)
    before = path.read_bytes()
    with pytest.raises(ValueError):
        _publish(tmp_path, _frame(OTHER, ORIGINAL), prior=evidence)
    assert path.read_bytes() == before


def test_cross_policy_recovery_preserves_pending_and_raw(tmp_path):
    path = tmp_path / "suspend_d.parquet"
    _frame(OTHER, ORIGINAL).to_parquet(path, index=False)
    _publish(tmp_path, _frame(OTHER, OBSERVED))
    pending = tmp_path / transaction.PENDING_FILENAME
    before = (path.read_bytes(), pending.read_bytes())
    with pytest.raises(ValueError):
        transaction.recover_quarantine_publication(
            tmp_path, policy="suspend-688766-20251127-20251209",
            start_date=START, end_date=END, enabled=True,
        )
    assert (path.read_bytes(), pending.read_bytes()) == before


def test_matching_recovery_restores_original_then_commits_real_successor(tmp_path):
    path = tmp_path / "suspend_d.parquet"
    _frame(OTHER, ORIGINAL).to_parquet(path, index=False)
    original = path.read_bytes()
    _publish(tmp_path, _frame(OTHER, OBSERVED))
    assert transaction.recover_quarantine_publication(
        tmp_path, policy=POLICY, start_date=START, end_date=END, enabled=True,
    )
    assert path.read_bytes() == original
    assert transaction.pending_quarantine_exists(tmp_path)
    with pytest.raises(ValueError):
        transaction.assert_no_pending_quarantine(tmp_path)
    successor = _publish(tmp_path, _frame(OTHER, OBSERVED))
    _commit(tmp_path, successor)
    assert not transaction.pending_quarantine_exists(tmp_path)
    acquisition.verify_quarantine_evidence(tmp_path, successor)


def test_selected_policy_does_not_waive_legacy_eight_keys(tmp_path):
    from src.contracts.suspension_quarantine import APPROVED_KEYS

    path = tmp_path / "suspend_d.parquet"
    _frame(OTHER, ORIGINAL, *sorted(APPROVED_KEYS)).to_parquet(path, index=False)
    before = path.read_bytes()
    with pytest.raises(ValueError):
        _publish(tmp_path, _frame(OTHER, OBSERVED))
    assert path.read_bytes() == before


def test_complete_r_without_prior_conflict_remains_clean(tmp_path):
    path = tmp_path / "suspend_d.parquet"
    frame = _frame(OTHER, ORIGINAL)
    frame.to_parquet(path, index=False)
    assert _publish(tmp_path, frame) is None
    assert not transaction.pending_quarantine_exists(tmp_path)


def test_cross_policy_prior_refuses_before_publication(tmp_path):
    path = tmp_path / "suspend_d.parquet"
    _frame(OTHER, ORIGINAL).to_parquet(path, index=False)
    evidence = _publish(tmp_path, _frame(OTHER, OBSERVED))
    _commit(tmp_path, evidence)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="matching prior"):
        _publish(tmp_path, _frame(OTHER, OBSERVED), prior=evidence,
                 policy="suspend-688766-20251127-20251209")
    assert path.read_bytes() == before


def test_real_fetch_cli_keeps_exit_three_and_provider_gate_requires_matching_policy(tmp_path, monkeypatch):
    from src.data.pit.quarantine_gate import validate_scoped_quarantine
    from src.data.tushare.fetch_manifest import read_manifest
    from tests.data_pipeline.test_suspension_quarantine_fetch import _cli, _client

    path = tmp_path / "suspend_d.parquet"
    _frame(OTHER, ORIGINAL).to_parquet(path, index=False)
    cli = _cli()
    write_manifest(tmp_path / "fetch_manifest.json", build_manifest(
        [TushareFetchResult("suspend_d", 1, 2)], (), START, END,
    ))
    client = _client(_frame(OTHER, OBSERVED))
    monkeypatch.setattr(cli.TushareClient, "from_environment", lambda: client)
    args = ["--output-dir", str(tmp_path), "--start-date", START, "--end-date", END,
            "--endpoints", "suspend_d", "--refresh-current", "--rate-limit-sleep-ms", "0",
            "--suspension-quarantine", POLICY]
    for _ in range(2):
        assert cli.main(args) == 3
        manifest = read_manifest(tmp_path / "fetch_manifest.json")
        evidence = validate_scoped_quarantine(manifest, POLICY, tmp_path, required_endpoints=("suspend_d",))
        assert evidence.policy_id == POLICY
        for wrong in (None, "suspend-688766-20251127-20251209"):
            with pytest.raises(ValueError):
                validate_scoped_quarantine(manifest, wrong, tmp_path, required_endpoints=("suspend_d",))
    assert client.call.call_count >= 2


def test_fetcher_cross_policy_prior_refuses_before_vendor_calls(tmp_path):
    from src.data.tushare.fetcher import TushareFetcher, TushareFetcherConfig, TushareFetcherError
    from tests.data_pipeline.test_suspension_quarantine_fetch import _client

    path = tmp_path / "suspend_d.parquet"
    _frame(OTHER, ORIGINAL).to_parquet(path, index=False)
    evidence = _publish(tmp_path, _frame(OTHER, OBSERVED))
    _commit(tmp_path, evidence)
    client = _client(_frame(OTHER, OBSERVED))
    fetcher = TushareFetcher(client, TushareFetcherConfig(
        output_dir=tmp_path, endpoints=("suspend_d",), start_date=START, end_date=END,
        refresh_current=True, rate_limit_sleep_ms=0,
        suspension_quarantine="suspend-688766-20251127-20251209",
    ))
    with pytest.raises(TushareFetcherError, match="matching policy"):
        fetcher.fetch()
    client.call.assert_not_called()


def test_journal_rejects_cross_policy_embedded_evidence(tmp_path):
    _frame(OTHER, ORIGINAL).to_parquet(tmp_path / "suspend_d.parquet", index=False)
    _publish(tmp_path, _frame(OTHER, OBSERVED))
    journal = json.loads((tmp_path / transaction.PENDING_FILENAME).read_text(encoding="utf-8"))
    journal["policy_id"] = "suspend-688766-20251127-20251209"
    with pytest.raises(ValueError, match="bind the publication"):
        transaction._validate_journal(journal)


@pytest.mark.parametrize("module", [
    "scripts.daily_update", "scripts.daily_recommend", "scripts.data_pipeline.05_build_qlib_bins",
])
def test_operator_cli_lists_both_explicit_policies_but_defaults_to_no_waiver(module):
    parser = importlib.import_module(module)._build_arg_parser()
    action = next(item for item in parser._actions if item.dest == "suspension_quarantine")
    assert tuple(action.choices) == ("suspend-688766-20251127-20251209", POLICY)
    assert action.default is None
