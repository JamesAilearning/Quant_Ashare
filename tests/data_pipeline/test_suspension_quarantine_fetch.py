"""Synthetic exact-incident quarantine tests; no vendor or production inputs."""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

from src.data.tushare.aggregate_response import AggregateResponseError
from src.data.tushare.fetch_manifest import (
    MANIFEST_FILENAME,
    EndpointCoverage,
    FetchManifest,
    build_manifest,
    merge_manifest,
    read_manifest,
    write_manifest,
)
from src.data.tushare.fetcher import TushareFetcher, TushareFetcherConfig, TushareFetcherError
from src.data.tushare.suspension_quarantine import verify_quarantine_evidence

POLICY = "suspend-688766-20251127-20251209"
START, END = "20251101", "20251231"
APPROVED_DATES = (
    "20251127", "20251128", "20251201", "20251202",
    "20251203", "20251204", "20251205", "20251209",
)
FIELDS = ("ts_code", "trade_date", "suspend_timing", "suspend_type")


def _row(day="20251105", code="600000.SH", kind="S", timing=None):
    return dict(zip(FIELDS, (code, day, timing, kind), strict=True))


def _complete():
    return pd.DataFrame([
        _row(),
        *[_row(day, "688766.SH", "R" if day == "20251209" else "S")
          for day in APPROVED_DATES],
    ], columns=FIELDS)


def _seed(root, frame=None):
    path = root / "suspend_d.parquet"
    (frame if frame is not None else _complete()).to_parquet(path, index=False)
    write_manifest(root / MANIFEST_FILENAME, FetchManifest(
        1, "2026-09-23T00:00:00+00:00", {
            "suspend_d": EndpointCoverage("complete", START, END, 1, ()),
        },
    ))
    return path


def _client(candidate):
    client = MagicMock()

    def call(endpoint, **params):
        assert endpoint == "suspend_d"
        return candidate.loc[candidate["trade_date"].between(
            params["start_date"], params["end_date"],
        )].copy(deep=True)

    client.call.side_effect = call
    return client


def _fetch(root, candidate, *, policy=POLICY, **kwargs):
    options = dict(output_dir=root, endpoints=("suspend_d",), start_date=START, end_date=END,
                   rate_limit_sleep_ms=0, refresh_current=True, suspension_quarantine=policy)
    options.update(kwargs)
    return TushareFetcher(_client(candidate), TushareFetcherConfig(**options))


def _persist(root, fetcher, results):
    path = root / MANIFEST_FILENAME
    config = fetcher._config
    manifest = merge_manifest(read_manifest(path), build_manifest(
        results, fetcher.holes, config.start_date, config.end_date,
        endpoint_start_dates=config.aggregate_start_dates(),
    ))
    write_manifest(path, manifest)
    return read_manifest(path)


def test_selected_known_loss_publishes_only_vendor_rows_and_a_structured_hole(tmp_path):
    path = _seed(tmp_path)
    old_bytes = path.read_bytes()
    candidate = pd.DataFrame([_row()], columns=FIELDS)
    fetcher = _fetch(tmp_path, candidate)

    results = fetcher.fetch()

    assert results[0].files_written == 1
    pd.testing.assert_frame_equal(pd.read_parquet(path), candidate)
    hole, = fetcher.holes
    assert (hole.endpoint, hole.unit, hole.reason_class) == (
        "suspend_d", "file", "quarantined_history",
    )
    evidence = hole.quarantine
    assert evidence is not None and evidence.policy_id == POLICY
    assert evidence.missing_dates == APPROVED_DATES
    assert evidence.candidate_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    original_hash = hashlib.sha256(old_bytes).hexdigest()
    assert evidence.reference_sha256 == evidence.retained_sha256 == original_hash
    assert (tmp_path / "_suspension_quarantine" / f"{original_hash}.parquet").read_bytes() == old_bytes
    manifest = _persist(tmp_path, fetcher, results)
    assert manifest.schema_version == 2
    assert manifest.endpoints["suspend_d"].status == "holes"
    assert manifest.endpoints["suspend_d"].holes[0].quarantine == evidence


def test_default_loss_still_preserves_the_entire_retained_file(tmp_path):
    path = _seed(tmp_path)
    before = path.read_bytes()
    fetcher = _fetch(tmp_path, pd.DataFrame([_row()], columns=FIELDS), policy=None)
    assert fetcher.fetch()[0].files_written == 0
    assert path.read_bytes() == before
    assert fetcher.holes[0].reason_class == "unusable_response"
    assert not (tmp_path / "_suspension_quarantine").exists()


def test_unknown_quarantine_is_a_config_error_before_files_or_api(tmp_path):
    with pytest.raises(TushareFetcherError, match="quarantine|policy"):
        _fetch(tmp_path / "not-created", _complete(), policy="another-stock")
    assert not (tmp_path / "not-created").exists()


def _cli():
    script = Path(__file__).resolve().parents[2] / "scripts/data_pipeline/01_fetch_tushare.py"
    spec = importlib.util.spec_from_file_location("quarantine_fetch_cli", script)
    assert spec is not None and spec.loader is not None
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    return cli


def test_cli_selected_known_loss_remains_exit_three_not_clean_success(tmp_path, monkeypatch):
    _seed(tmp_path)
    cli = _cli()
    monkeypatch.setattr(cli.TushareClient, "from_environment", lambda: _client(
        pd.DataFrame([_row()], columns=FIELDS),
    ))
    assert cli.main([
        "--output-dir", str(tmp_path), "--start-date", START, "--end-date", END,
        "--endpoints", "suspend_d", "--refresh-current", "--rate-limit-sleep-ms", "0",
        "--suspension-quarantine", POLICY,
    ]) == 3
    manifest = read_manifest(tmp_path / MANIFEST_FILENAME)
    assert manifest.endpoints["suspend_d"].status == "holes"


def _quarantined(root):
    _seed(root)
    fetcher = _fetch(root, pd.DataFrame([_row()], columns=FIELDS))
    _persist(root, fetcher, fetcher.fetch())
    return fetcher.holes[0].quarantine


def test_repeat_omission_forces_a_real_refresh_and_cannot_claim_self_healing(tmp_path):
    original = _quarantined(tmp_path)
    fetcher = _fetch(tmp_path, pd.DataFrame([_row()], columns=FIELDS), refresh_current=False)
    results = fetcher.fetch()
    assert fetcher._client.call.call_count == 2
    assert results[0].files_written == 1
    repeated = fetcher.holes[0].quarantine
    assert repeated.missing_dates == APPROVED_DATES
    assert repeated.reference_sha256 == original.reference_sha256
    assert repeated.retained_sha256 == original.candidate_sha256
    manifest = _persist(tmp_path, fetcher, results)
    assert manifest.schema_version == 2
    verify_quarantine_evidence(tmp_path, repeated)


def test_partial_then_full_recovery_requires_all_original_keys_and_keeps_evidence(tmp_path):
    original = _quarantined(tmp_path)
    partial = _complete().loc[lambda frame: frame["trade_date"] != "20251209"].reset_index(drop=True)
    fetcher = _fetch(tmp_path, partial)
    _persist(tmp_path, fetcher, fetcher.fetch())
    recovering = fetcher.holes[0].quarantine
    assert recovering.missing_dates == ("20251209",)
    assert recovering.reference_sha256 == original.reference_sha256
    verify_quarantine_evidence(tmp_path, recovering)
    fetcher = _fetch(tmp_path, _complete())
    manifest = _persist(tmp_path, fetcher, fetcher.fetch())
    assert fetcher.holes == ()
    assert manifest.schema_version == 1
    assert manifest.endpoints["suspend_d"].status == "complete"
    assert (tmp_path / "_suspension_quarantine" / f"{original.reference_sha256}.parquet").exists()
    assert (tmp_path / "_suspension_quarantine" / f"{recovering.candidate_sha256}.parquet").exists()


@pytest.mark.parametrize("refresh", [False, True])
def test_default_prior_quarantine_refuses_before_any_endpoint_or_resume(tmp_path, refresh):
    _quarantined(tmp_path)
    path = tmp_path / "suspend_d.parquet"
    before = path.read_bytes()
    fetcher = _fetch(tmp_path, _complete(), policy=None, refresh_current=refresh,
                     endpoints=("stock_basic", "suspend_d"))
    with pytest.raises(TushareFetcherError, match="quarantine"):
        fetcher.fetch()
    fetcher._client.call.assert_not_called()
    assert path.read_bytes() == before


@pytest.mark.parametrize("case", ["other_ticker", "other_date", "timing", "type", "empty"])
def test_policy_does_not_admit_unapproved_loss_or_affected_date_replacements(tmp_path, case):
    retained = pd.concat([_complete(), pd.DataFrame([_row("20251210", "688766.SH")])], ignore_index=True)
    path = _seed(tmp_path, retained)
    before = path.read_bytes()
    candidate = retained.loc[~retained["trade_date"].isin(APPROVED_DATES)].copy()
    if case == "other_ticker":
        candidate = candidate.loc[candidate["ts_code"] != "600000.SH"]
    elif case == "other_date":
        candidate = candidate.loc[candidate["trade_date"] != "20251210"]
    elif case in {"timing", "type"}:
        replacement = _row("20251127", "688766.SH", "R" if case == "type" else "S",
                           "09:30-10:30" if case == "timing" else None)
        candidate = pd.concat([candidate, pd.DataFrame([replacement])], ignore_index=True)
    else:
        candidate = pd.DataFrame(columns=FIELDS)
    fetcher = _fetch(tmp_path, candidate)
    assert fetcher.fetch()[0].files_written == 0
    assert fetcher.holes[0].reason_class == "unusable_response"
    assert path.read_bytes() == before


def test_first_quarantine_requires_complete_original_reference(tmp_path):
    short = pd.DataFrame([_row()], columns=FIELDS)
    path = _seed(tmp_path, short)
    before = path.read_bytes()
    fetcher = _fetch(tmp_path, short)
    assert fetcher.fetch()[0].files_written == 0
    assert "all eight" in fetcher.holes[0].last_error
    assert path.read_bytes() == before


@pytest.mark.parametrize("start,end", [("20251128", END), (START, "20251208")])
def test_policy_cannot_publish_a_request_that_excludes_an_incident_key(tmp_path, start, end):
    path = _seed(tmp_path)
    before = path.read_bytes()
    fetcher = _fetch(tmp_path, _complete(), start_date=start, end_date=end)
    with pytest.raises(TushareFetcherError, match="eight"):
        fetcher.fetch()
    fetcher._client.call.assert_not_called()
    assert path.read_bytes() == before


@pytest.mark.parametrize("target", ["reference", "retained", "candidate"])
def test_consumers_and_retries_reject_each_tampered_evidence_file(tmp_path, target):
    _quarantined(tmp_path)
    partial = _complete().loc[lambda frame: frame["trade_date"] != "20251209"].reset_index(drop=True)
    fetcher = _fetch(tmp_path, partial)
    _persist(tmp_path, fetcher, fetcher.fetch())
    evidence = fetcher.holes[0].quarantine
    path = (tmp_path / "suspend_d.parquet" if target == "candidate" else
            tmp_path / "_suspension_quarantine" / f"{getattr(evidence, target + '_sha256')}.parquet")
    path.write_bytes(path.read_bytes() + b"tampered")
    before = (tmp_path / "suspend_d.parquet").read_bytes()
    with pytest.raises(AggregateResponseError, match="SHA"):
        verify_quarantine_evidence(tmp_path, evidence)
    retry = _fetch(tmp_path, _complete())
    with pytest.raises(TushareFetcherError, match="SHA"):
        retry.fetch()
    retry._client.call.assert_not_called()
    assert (tmp_path / "suspend_d.parquet").read_bytes() == before


def test_existing_corrupt_content_addressed_evidence_is_never_overwritten(tmp_path):
    path = _seed(tmp_path)
    before = path.read_bytes()
    directory = tmp_path / "_suspension_quarantine"
    directory.mkdir()
    saved = directory / f"{hashlib.sha256(before).hexdigest()}.parquet"
    saved.write_bytes(b"wrong evidence")
    fetcher = _fetch(tmp_path, pd.DataFrame([_row()], columns=FIELDS))
    assert fetcher.fetch()[0].files_written == 0
    assert saved.read_bytes() == b"wrong evidence"
    assert path.read_bytes() == before


@pytest.mark.parametrize("limit", ["rows", "bytes"])
def test_evidence_is_bounded_before_retained_parquet_decoding(tmp_path, monkeypatch, limit):
    from src.data.tushare import aggregate_response as aggregate
    path = _seed(tmp_path)
    before = path.read_bytes()
    if limit == "rows":
        monkeypatch.setattr(aggregate, "MAX_CANDIDATE_ROWS", 5)
    else:
        monkeypatch.setattr(aggregate, "MAX_CANDIDATE_BYTES", len(before) - 1)
    fetcher = _fetch(tmp_path, pd.DataFrame([_row()], columns=FIELDS))
    assert fetcher.fetch()[0].files_written == 0
    assert "budget" in fetcher.holes[0].last_error
    assert path.read_bytes() == before


def test_failed_retry_keeps_original_quarantine_and_adds_blocking_ordinary_hole(tmp_path):
    original = _quarantined(tmp_path)
    path = tmp_path / "suspend_d.parquet"
    before = path.read_bytes()
    fetcher = _fetch(tmp_path, pd.DataFrame(columns=FIELDS))
    results = fetcher.fetch()
    assert results[0].files_written == 0
    manifest = _persist(tmp_path, fetcher, results)
    holes = manifest.endpoints["suspend_d"].holes
    assert {hole.reason_class for hole in holes} == {"quarantined_history", "unusable_response"}
    assert [hole.quarantine for hole in holes if hole.quarantine is not None] == [original]
    assert path.read_bytes() == before
    retry = _fetch(tmp_path, _complete())
    healed = _persist(tmp_path, retry, retry.fetch())
    assert healed.endpoints["suspend_d"].holes == ()
    assert healed.schema_version == 1


def test_saturated_partition_cannot_hide_even_an_approved_key(tmp_path, monkeypatch):
    from src.data.tushare import aggregate_response as aggregate
    path = _seed(tmp_path)
    before = path.read_bytes()
    monkeypatch.setitem(aggregate.RESPONSE_ROW_GUARDS, "suspend_d", 2)
    fetcher = _fetch(tmp_path, pd.DataFrame([_row()], columns=FIELDS))

    def response(endpoint, **params):
        if params["start_date"] == "20251101" and params["end_date"] == "20251130":
            return pd.DataFrame([_row(), _row("20251127", "688766.SH")], columns=FIELDS)
        return _client(pd.DataFrame([_row()], columns=FIELDS)).call(endpoint, **params)

    fetcher._client.call.side_effect = response
    assert fetcher.fetch()[0].files_written == 0
    assert "saturated parent" in fetcher.holes[0].last_error
    assert path.read_bytes() == before


def test_reset_cannot_remove_the_original_quarantine_reference(tmp_path, monkeypatch):
    _quarantined(tmp_path)
    path = tmp_path / MANIFEST_FILENAME
    before = path.read_bytes()
    cli = _cli()
    client_factory = MagicMock()
    monkeypatch.setattr(cli.TushareClient, "from_environment", client_factory)
    assert cli.main([
        "--output-dir", str(tmp_path), "--start-date", START, "--end-date", END,
        "--endpoints", "suspend_d", "--reset-manifest", "--suspension-quarantine", POLICY,
    ]) == 1
    client_factory.assert_not_called()
    assert path.read_bytes() == before


def test_selected_dry_run_keeps_prior_data_and_evidence_without_api_calls(tmp_path):
    original = _quarantined(tmp_path)
    before = {path.name: path.read_bytes() for path in (tmp_path / "_suspension_quarantine").iterdir()}
    fetcher = _fetch(tmp_path, _complete(), dry_run=True)
    assert fetcher.fetch()[0].files_written == 0
    fetcher._client.call.assert_not_called()
    assert before == {path.name: path.read_bytes() for path in (tmp_path / "_suspension_quarantine").iterdir()}
    verify_quarantine_evidence(tmp_path, original)


def test_empty_schema_bearing_response_can_only_remove_the_exact_incident_rows(tmp_path):
    retained = _complete().loc[lambda frame: frame["ts_code"] == "688766.SH"].reset_index(drop=True)
    _seed(tmp_path, retained)
    fetcher = _fetch(tmp_path, pd.DataFrame(columns=FIELDS))
    assert fetcher.fetch()[0].files_written == 1
    assert fetcher.holes[0].quarantine.missing_dates == APPROVED_DATES
    assert pd.read_parquet(tmp_path / "suspend_d.parquet").empty
    verify_quarantine_evidence(tmp_path, fetcher.holes[0].quarantine)


def test_full_first_response_needs_no_quarantine_or_saved_reference(tmp_path):
    fetcher = _fetch(tmp_path, _complete())
    manifest = _persist(tmp_path, fetcher, fetcher.fetch())
    assert manifest.schema_version == 1
    assert fetcher.holes == ()
    assert not (tmp_path / "_suspension_quarantine").exists()


def test_prepare_failure_keeps_original_parquet_and_publishes_no_quarantine(tmp_path, monkeypatch):
    from src.data.tushare import suspension_quarantine as quarantine_io
    path = _seed(tmp_path)
    before = path.read_bytes()

    def failed_prepare(*args, **kwargs):
        raise OSError("synthetic unavailable disk")

    monkeypatch.setattr(quarantine_io, "atomic_write_parquet", failed_prepare)
    fetcher = _fetch(tmp_path, pd.DataFrame([_row()], columns=FIELDS))
    assert fetcher.fetch()[0].files_written == 0
    assert fetcher.holes[0].quarantine is None
    assert path.read_bytes() == before
    assert not list(tmp_path.glob(".suspend_d.*"))


def test_late_partition_failure_cannot_publish_an_early_valid_candidate(tmp_path):
    path = _seed(tmp_path)
    before = path.read_bytes()
    fetcher = _fetch(tmp_path, pd.DataFrame([_row()], columns=FIELDS))

    def response(endpoint, **params):
        if params["start_date"] == "20251201":
            return pd.DataFrame({"wrong_schema": []})
        return pd.DataFrame([_row()], columns=FIELDS)

    fetcher._client.call.side_effect = response
    assert fetcher.fetch()[0].files_written == 0
    assert fetcher.holes[0].reason_class == "unusable_response"
    assert path.read_bytes() == before
    assert not (tmp_path / "_suspension_quarantine").exists()


def test_subset_fetch_still_returns_incomplete_when_existing_quarantine_is_not_selected(tmp_path, monkeypatch):
    original = _quarantined(tmp_path)
    pd.DataFrame(columns=["ts_code", "name", "start_date", "end_date", "ann_date", "change_reason"]).to_parquet(
        tmp_path / "all_namechanges.parquet", index=False,
    )
    cli = _cli()
    client = MagicMock()
    monkeypatch.setattr(cli.TushareClient, "from_environment", lambda: client)
    assert cli.main([
        "--output-dir", str(tmp_path), "--start-date", START, "--end-date", END,
        "--endpoints", "namechange", "--rate-limit-sleep-ms", "0",
        "--suspension-quarantine", POLICY,
    ]) == 3
    client.call.assert_not_called()
    manifest = read_manifest(tmp_path / MANIFEST_FILENAME)
    assert manifest.endpoints["suspend_d"].holes[0].quarantine == original
    verify_quarantine_evidence(tmp_path, original)
