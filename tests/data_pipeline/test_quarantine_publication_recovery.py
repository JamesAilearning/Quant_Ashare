"""Small real-file crash/retry regressions for quarantine publication."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from src.contracts.suspension_quarantine import (
    APPROVED_KEYS,
    POLICY_ID,
    SuspensionQuarantine,
)
from src.data.pit.qlib_bin_builder import QlibBinBuilder, QlibBinBuilderError
from src.data.tushare import fetch_manifest as manifest_module
from src.data.tushare import quarantine_transaction as transaction
from src.data.tushare.client import KIND_AUTH, KIND_ENVIRONMENT, TushareClientError
from src.data.tushare.fetch_manifest import (
    MANIFEST_FILENAME,
    FetchManifestError,
    build_manifest,
    clear_manifest,
    merge_manifest,
    read_manifest,
    write_manifest,
)
from src.data.tushare.fetch_types import FetchHole, TushareFetchResult
from src.data.tushare.fetcher import TushareFetcher, TushareFetcherConfig, TushareFetcherError

START, END = "20251101", "20251231"
FIELDS = ["ts_code", "trade_date", "suspend_timing", "suspend_type"]
MISSING_DATES = tuple(sorted(key[1] for key in APPROVED_KEYS))


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _manifest(evidence=None):
    holes = () if evidence is None else (
        FetchHole("suspend_d", "file", "quarantined_history", 1, "known incident", evidence),
    )
    return build_manifest(
        [TushareFetchResult(endpoint, 1, 1) for endpoint in
         ("stock_basic", "daily", "adj_factor", "suspend_d")],
        holes, START, END,
    )


def _evidence(reference: bytes, retained: bytes, candidate: bytes):
    return SuspensionQuarantine(
        policy_id=POLICY_ID, missing_dates=MISSING_DATES,
        reference_sha256=_sha(reference), retained_sha256=_sha(retained),
        candidate_sha256=_sha(candidate), query_start_date=START, query_end_date=END,
    )


def _case(tmp_path, *, full_recovery=False):
    raw = tmp_path / "raw"
    raw.mkdir()
    complete = pd.DataFrame([
        ("600000.SH", "20251105", None, "S"), *sorted(APPROVED_KEYS),
    ], columns=FIELDS)
    short = complete.iloc[:1].copy()
    reference_path, short_path = tmp_path / "reference.parquet", tmp_path / "short.parquet"
    complete.to_parquet(reference_path, index=False)
    short.to_parquet(short_path, index=False)
    reference, shortened = reference_path.read_bytes(), short_path.read_bytes()
    before = shortened if full_recovery else reference
    candidate = reference if full_recovery else shortened
    directory = raw / "_suspension_quarantine"
    directory.mkdir()
    for value in (reference, before):
        (directory / f"{_sha(value)}.parquet").write_bytes(value)
    path = raw / "suspend_d.parquet"
    path.write_bytes(before)
    old_evidence = _evidence(reference, reference, shortened) if full_recovery else None
    before_manifest = _manifest(old_evidence)
    write_manifest(raw / MANIFEST_FILENAME, before_manifest)
    return SimpleNamespace(
        raw=raw, path=path, reference=reference, complete=complete, short=short, before=before, candidate=candidate,
        before_manifest=before_manifest, before_manifest_bytes=(raw / MANIFEST_FILENAME).read_bytes(),
        evidence_after=None if full_recovery else _evidence(reference, before, candidate),
        pending=raw / transaction.PENDING_FILENAME,
    )


def _begin(case):
    transaction.begin_quarantine_publication(
        case.raw, retained_sha256=_sha(case.before), candidate_sha256=_sha(case.candidate),
        quarantine_after=case.evidence_after, query_start_date=START, query_end_date=END,
    )
    assert transaction.pending_quarantine_exists(case.raw)


def _replace_candidate(case):
    temporary = case.raw / ".synthetic-candidate.parquet"
    temporary.write_bytes(case.candidate)
    temporary.replace(case.path)


def _recover(case, **overrides):
    options = dict(policy=POLICY_ID, start_date=START, end_date=END, enabled=True)
    options.update(overrides)
    return transaction.recover_quarantine_publication(case.raw, **options)


def _refresh_manifest(case, **overrides):
    options = dict(policy=POLICY_ID, start_date=START, end_date=END, enabled=True)
    options.update(overrides)
    # Deliberately resolved at call time: pre-fix RED must not prevent collection.
    return manifest_module.read_manifest_for_quarantine_refresh(case.raw / MANIFEST_FILENAME, **options)


def _bytes_under(raw):
    return {path.relative_to(raw).as_posix(): path.read_bytes()
            for path in raw.rglob("*") if path.is_file()}


def _fail_marker_cleanup(raw_dir, journal):
    raise OSError("synthetic marker cleanup failure")


def _committed_with_pending(case, monkeypatch):
    _replace_candidate(case)
    with monkeypatch.context() as scoped:
        scoped.setattr(transaction, "_remove_pending", _fail_marker_cleanup)
        with pytest.raises(OSError, match="marker cleanup"):
            write_manifest(case.raw / MANIFEST_FILENAME, _manifest(case.evidence_after))
    assert case.pending.exists()
    assert case.path.read_bytes() == case.candidate


@pytest.mark.parametrize("manifest_missing", [False, True])
def test_ordinary_manifest_read_without_pending_does_not_apply_transaction_path_rules(
    tmp_path, monkeypatch, manifest_missing,
):
    case = _case(tmp_path)
    manifest_path = case.raw / MANIFEST_FILENAME
    if manifest_missing:
        manifest_path.unlink()
    before = _bytes_under(case.raw)
    strict_check = Mock(side_effect=AssertionError("ordinary reads must not check transaction paths"))
    monkeypatch.setattr(transaction, "_check_path", strict_check)

    parsed = read_manifest(manifest_path)

    if manifest_missing:
        assert parsed is None
    else:
        assert parsed == case.before_manifest
    strict_check.assert_not_called()
    assert _bytes_under(case.raw) == before


def test_manifest_read_with_pending_still_enforces_strict_transaction_path_rules(tmp_path, monkeypatch):
    case = _case(tmp_path)
    _begin(case)
    before = _bytes_under(case.raw)
    strict_check = Mock(side_effect=transaction.QuarantineTransactionError("pending strict-path refusal"))
    monkeypatch.setattr(transaction, "_check_path", strict_check)

    with pytest.raises(FetchManifestError, match="pending strict-path refusal"):
        read_manifest(case.raw / MANIFEST_FILENAME)

    strict_check.assert_called()
    assert _bytes_under(case.raw) == before


@pytest.mark.parametrize("raw_replaced", [False, True])
def test_interrupted_publication_restores_old_pair_but_keeps_durable_retry(tmp_path, raw_replaced):
    case = _case(tmp_path)
    _begin(case)
    if raw_replaced:
        _replace_candidate(case)
    with pytest.raises(FetchManifestError, match="pending"):
        read_manifest(case.raw / MANIFEST_FILENAME)
    assert _recover(case) is True
    assert case.path.read_bytes() == case.before
    assert (case.raw / MANIFEST_FILENAME).read_bytes() == case.before_manifest_bytes
    assert case.pending.exists()
    assert json.loads(case.pending.read_text(encoding="utf-8"))["phase"] == "retry_required"
    with pytest.raises(FetchManifestError, match="pending"):
        read_manifest(case.raw / MANIFEST_FILENAME)
    journal = case.pending.read_bytes()
    assert _recover(case) is True
    assert case.pending.read_bytes() == journal
    assert _refresh_manifest(case) == case.before_manifest
    assert case.pending.read_bytes() == journal


@pytest.mark.parametrize("outcome", ["rollback", "commit"])
def test_initially_absent_manifest_is_bound_as_absence_through_recovery(tmp_path, monkeypatch, outcome):
    case = _case(tmp_path)
    manifest_path = case.raw / MANIFEST_FILENAME
    manifest_path.unlink()
    _begin(case)
    assert json.loads(case.pending.read_text(encoding="utf-8"))["before_manifest_sha256"] is None
    if outcome == "commit":
        _committed_with_pending(case, monkeypatch)
        committed_bytes = manifest_path.read_bytes()
    else:
        _replace_candidate(case)
    with pytest.raises(FetchManifestError, match="pending"):
        read_manifest(manifest_path)

    assert _recover(case) is True

    if outcome == "commit":
        assert not case.pending.exists()
        assert case.path.read_bytes() == case.candidate
        assert manifest_path.read_bytes() == committed_bytes
        assert read_manifest(manifest_path).endpoints["suspend_d"].holes[0].quarantine == case.evidence_after
    else:
        assert case.pending.exists()
        assert case.path.read_bytes() == case.before
        assert not manifest_path.exists()
        with pytest.raises(FetchManifestError, match="pending"):
            read_manifest(manifest_path)
        assert _refresh_manifest(case) is None
        assert (case.raw / "_suspension_quarantine" / f"{_sha(case.candidate)}.parquet").read_bytes() == case.candidate
    assert (case.raw / "_suspension_quarantine" / f"{_sha(case.reference)}.parquet").read_bytes() == case.reference


def test_removing_a_previously_bound_manifest_cannot_be_reinterpreted_as_initial_absence(tmp_path):
    case = _case(tmp_path)
    _begin(case)
    _replace_candidate(case)
    (case.raw / MANIFEST_FILENAME).unlink()
    before = _bytes_under(case.raw)

    with pytest.raises(ValueError, match="unknown raw/manifest bytes"):
        _recover(case)

    assert _bytes_under(case.raw) == before
    assert case.path.read_bytes() == case.candidate
    assert case.pending.exists()


@pytest.mark.parametrize("full_recovery", [False, True])
def test_manifest_commit_with_failed_marker_cleanup_is_recognized_without_rollback(
    tmp_path, monkeypatch, full_recovery,
):
    case = _case(tmp_path, full_recovery=full_recovery)
    _begin(case)
    _committed_with_pending(case, monkeypatch)
    manifest_bytes = (case.raw / MANIFEST_FILENAME).read_bytes()
    with pytest.raises(FetchManifestError, match="pending"):
        read_manifest(case.raw / MANIFEST_FILENAME)
    assert _recover(case) is True
    assert case.path.read_bytes() == case.candidate
    assert (case.raw / MANIFEST_FILENAME).read_bytes() == manifest_bytes
    assert not case.pending.exists()
    parsed = read_manifest(case.raw / MANIFEST_FILENAME)
    assert parsed.schema_version == (1 if full_recovery else 2)
    assert bool(parsed.endpoints["suspend_d"].holes) is not full_recovery
    assert (case.raw / "_suspension_quarantine" / f"{_sha(case.reference)}.parquet").read_bytes() == case.reference


def test_rollback_never_removes_retry_marker_and_repeated_recovery_is_idempotent(tmp_path, monkeypatch):
    case = _case(tmp_path)
    _begin(case)
    _replace_candidate(case)
    cleanup = Mock(side_effect=AssertionError("rollback must not discharge the retry obligation"))
    monkeypatch.setattr(transaction, "_remove_pending", cleanup)
    assert _recover(case) is True
    assert case.path.read_bytes() == case.before
    assert (case.raw / MANIFEST_FILENAME).read_bytes() == case.before_manifest_bytes
    journal_after = case.pending.read_bytes()
    assert json.loads(journal_after)["phase"] == "retry_required"
    assert _recover(case) is True
    assert case.pending.read_bytes() == journal_after
    cleanup.assert_not_called()
    assert case.path.read_bytes() == case.before


@pytest.mark.parametrize("changed_input", ["raw", "manifest", "journal"])
def test_recovery_rechecks_inputs_after_archiving_before_overwriting_raw(tmp_path, monkeypatch, changed_input):
    case = _case(tmp_path)
    _begin(case)
    _replace_candidate(case)
    copy_fsynced = transaction._copy_fsynced
    copies = []
    after_change = None

    def archive_then_change_input(source, target, digest, *, exclusive):
        nonlocal after_change
        copies.append((source, target, exclusive))
        copy_fsynced(source, target, digest, exclusive=exclusive)
        if exclusive and after_change is None:
            assert source == case.path
            assert target.read_bytes() == case.candidate
            if changed_input == "raw":
                case.path.write_bytes(case.candidate + b"unknown concurrent raw bytes")
            elif changed_input == "manifest":
                (case.raw / MANIFEST_FILENAME).write_bytes(case.before_manifest_bytes + b"\n")
            else:
                journal = json.loads(case.pending.read_text(encoding="utf-8"))
                journal["candidate_sha256"] = "d" * 64
                case.pending.write_text(json.dumps(journal), encoding="utf-8")
            after_change = _bytes_under(case.raw)

    monkeypatch.setattr(transaction, "_copy_fsynced", archive_then_change_input)
    with pytest.raises(ValueError):
        _recover(case)

    assert after_change is not None
    assert _bytes_under(case.raw) == after_change
    assert len(copies) == 1 and copies[0][2] is True
    assert case.pending.exists()
    assert (case.raw / "_suspension_quarantine" / f"{_sha(case.candidate)}.parquet").read_bytes() == case.candidate


def test_interrupted_full_history_recovery_preserves_old_quarantine_for_fresh_retry(tmp_path):
    case = _case(tmp_path, full_recovery=True)
    _begin(case)
    _replace_candidate(case)
    assert _recover(case) is True
    assert case.path.read_bytes() == case.before
    assert (case.raw / MANIFEST_FILENAME).read_bytes() == case.before_manifest_bytes
    with pytest.raises(FetchManifestError, match="pending"):
        read_manifest(case.raw / MANIFEST_FILENAME)
    restored = _refresh_manifest(case)
    assert restored.schema_version == 2
    assert restored.endpoints["suspend_d"].holes[0].quarantine.reference_sha256 == _sha(case.reference)


@pytest.mark.parametrize("damage", ["before_manifest", "candidate", "retained_archive", "before_sha"])
def test_unknown_or_corrupted_precommit_bytes_refuse_without_mutation(tmp_path, damage):
    case = _case(tmp_path)
    _begin(case)
    _replace_candidate(case)
    if damage == "before_manifest":
        (case.raw / MANIFEST_FILENAME).write_bytes(case.before_manifest_bytes + b"\n")
    elif damage == "candidate":
        case.path.write_bytes(case.candidate + b"corrupt")
    elif damage == "retained_archive":
        (case.raw / "_suspension_quarantine" / f"{_sha(case.before)}.parquet").write_bytes(b"corrupt")
    else:
        journal = json.loads(case.pending.read_text(encoding="utf-8"))
        journal["before_manifest_sha256"] = "d" * 64
        case.pending.write_text(json.dumps(journal), encoding="utf-8")
    before = _bytes_under(case.raw)
    with pytest.raises(ValueError):
        _recover(case)
    assert _bytes_under(case.raw) == before


@pytest.mark.parametrize("damage", ["manifest_bytes", "after_sha", "candidate_sha"])
def test_changed_committed_identity_is_not_accepted_as_recorded_after_state(tmp_path, monkeypatch, damage):
    case = _case(tmp_path)
    _begin(case)
    _committed_with_pending(case, monkeypatch)
    if damage == "manifest_bytes":
        manifest = case.raw / MANIFEST_FILENAME
        manifest.write_bytes(manifest.read_bytes() + b"\n")
    else:
        journal = json.loads(case.pending.read_text(encoding="utf-8"))
        journal["after_manifest_sha256" if damage == "after_sha" else "candidate_sha256"] = "d" * 64
        case.pending.write_text(json.dumps(journal), encoding="utf-8")
    before = _bytes_under(case.raw)
    with pytest.raises(ValueError):
        _recover(case)
    assert _bytes_under(case.raw) == before


@pytest.mark.parametrize("manifest_committed", [False, True])
def test_byte_identical_repeated_quarantine_uses_manifest_identity_to_recover(
    tmp_path, monkeypatch, manifest_committed,
):
    case = _case(tmp_path, full_recovery=True)
    case.candidate = case.before
    case.evidence_after = _evidence(case.reference, case.before, case.candidate)
    _begin(case)
    if manifest_committed:
        _committed_with_pending(case, monkeypatch)
    else:
        _replace_candidate(case)
    expected_manifest = (case.raw / MANIFEST_FILENAME).read_bytes()
    assert _recover(case) is True
    assert case.path.read_bytes() == case.before == case.candidate
    assert (case.raw / MANIFEST_FILENAME).read_bytes() == expected_manifest
    parsed = read_manifest(case.raw / MANIFEST_FILENAME) if manifest_committed else _refresh_manifest(case)
    assert parsed.schema_version == 2
    assert parsed.endpoints["suspend_d"].holes[0].quarantine.reference_sha256 == _sha(case.reference)
    assert case.pending.exists() is not manifest_committed


@pytest.mark.parametrize("damage", ["broken_json", "wrong_schema", "oversized", "unknown_field",
                                   "missing_phase", "wrong_phase", "nonstring_phase"])
def test_bad_or_oversized_journal_is_fail_closed_without_changing_evidence(tmp_path, damage):
    case = _case(tmp_path)
    _begin(case)
    if damage == "broken_json":
        case.pending.write_bytes(b"{")
    elif damage == "oversized":
        case.pending.write_bytes(b" " * (transaction.MAX_JOURNAL_BYTES + 1))
    else:
        journal = json.loads(case.pending.read_text(encoding="utf-8"))
        if damage == "missing_phase":
            journal.pop("phase", None)
        elif damage in {"wrong_phase", "nonstring_phase"}:
            journal["phase"] = "clean" if damage == "wrong_phase" else True
        else:
            journal["schema_version" if damage == "wrong_schema" else "unknown_field"] = 99
        case.pending.write_text(json.dumps(journal), encoding="utf-8")
    before = _bytes_under(case.raw)
    assert transaction.pending_quarantine_exists(case.raw)
    with pytest.raises(ValueError):
        transaction.assert_no_pending_quarantine(case.raw)
    with pytest.raises(ValueError):
        _recover(case)
    assert _bytes_under(case.raw) == before


def test_second_begin_cannot_overwrite_an_unfinished_transaction(tmp_path):
    case = _case(tmp_path)
    _begin(case)
    before = _bytes_under(case.raw)
    with pytest.raises(ValueError):
        _begin(case)
    assert _bytes_under(case.raw) == before


def test_retry_begin_cannot_adopt_manifest_changed_after_restored_pair_check(tmp_path, monkeypatch):
    case = _case(tmp_path)
    _begin(case)
    _replace_candidate(case)
    assert _recover(case) is True
    journal_before = case.pending.read_bytes()
    raw_before = case.path.read_bytes()
    manifest_path = case.raw / MANIFEST_FILENAME
    changed_manifest = case.before_manifest_bytes + b"\n"
    original_digest = transaction._digest
    manifest_reads = 0

    def change_before_new_manifest_binding(path, *, limit, optional=False):
        nonlocal manifest_reads
        if path == manifest_path:
            manifest_reads += 1
            if manifest_reads == 2:
                # The first read proved the restored pair. The next read must
                # not adopt unknown bytes as the successor's before-manifest.
                manifest_path.write_bytes(changed_manifest)
        return original_digest(path, limit=limit, optional=optional)

    monkeypatch.setattr(transaction, "_digest", change_before_new_manifest_binding)
    with pytest.raises(ValueError):
        transaction.begin_quarantine_publication(
            case.raw, retained_sha256=_sha(case.before), candidate_sha256=_sha(case.candidate),
            quarantine_after=case.evidence_after, query_start_date=START, query_end_date=END,
        )

    assert manifest_reads >= 2
    assert manifest_path.read_bytes() == changed_manifest
    assert case.path.read_bytes() == raw_before
    assert case.pending.read_bytes() == journal_before


def test_begin_publishes_complete_regular_journal_without_temporary_links(tmp_path):
    case = _case(tmp_path)
    _begin(case)
    assert case.pending.is_file() and not case.pending.is_symlink()
    assert case.pending.stat().st_nlink == 1
    journal = json.loads(case.pending.read_text(encoding="utf-8"))
    assert journal["schema_version"] == transaction.JOURNAL_SCHEMA_VERSION
    assert journal["phase"] == "publishing"
    assert journal["retained_sha256"] == _sha(case.before)
    assert journal["candidate_sha256"] == _sha(case.candidate)
    assert journal["before_manifest_sha256"] == _sha(case.before_manifest_bytes)
    assert journal["after_manifest_sha256"] is None
    assert not list(case.raw.glob(".quarantine-journal-*.tmp"))
    assert case.path.read_bytes() == case.before
    assert (case.raw / MANIFEST_FILENAME).read_bytes() == case.before_manifest_bytes


@pytest.mark.parametrize("options", [
    {"policy": None}, {"policy": "other-policy"}, {"enabled": False},
    {"start_date": "20251102"}, {"end_date": "20251230"},
])
def test_recovery_requires_selected_enabled_policy_and_whole_original_query(tmp_path, options):
    case = _case(tmp_path)
    _begin(case)
    _replace_candidate(case)
    before = _bytes_under(case.raw)
    with pytest.raises(ValueError):
        _recover(case, **options)
    assert _bytes_under(case.raw) == before


def test_recovery_allows_larger_explicit_query_without_claiming_new_coverage(tmp_path):
    case = _case(tmp_path)
    _begin(case)
    _replace_candidate(case)
    assert _recover(case, start_date="20251001", end_date="20260131") is True
    assert case.path.read_bytes() == case.before
    assert (case.raw / MANIFEST_FILENAME).read_bytes() == case.before_manifest_bytes


def test_clear_manifest_cannot_erase_an_unfinished_publication(tmp_path):
    case = _case(tmp_path)
    _begin(case)
    before = _bytes_under(case.raw)
    with pytest.raises(FetchManifestError, match="pending"):
        clear_manifest(case.raw / MANIFEST_FILENAME)
    assert _bytes_under(case.raw) == before


@pytest.mark.parametrize("manifest_missing", [False, True])
@pytest.mark.parametrize("policy", [None, POLICY_ID])
def test_builder_cannot_bypass_pending_transaction_with_broad_hole_override(tmp_path, manifest_missing, policy):
    case = _case(tmp_path)
    _begin(case)
    _replace_candidate(case)
    if manifest_missing:
        (case.raw / MANIFEST_FILENAME).unlink()
    before = _bytes_under(case.raw)
    provider = tmp_path / "provider"
    builder = QlibBinBuilder(
        tushare_dir=case.raw, delisted_registry_path=case.raw / "unused_registry.parquet",
        output_dir=provider, allow_holey_fetch=True, suspension_quarantine=policy,
    )
    with pytest.raises(QlibBinBuilderError, match="pending"):
        builder.build()
    assert not provider.exists()
    assert _bytes_under(case.raw) == before


def _cli():
    script = Path(__file__).resolve().parents[2] / "scripts/data_pipeline/01_fetch_tushare.py"
    spec = importlib.util.spec_from_file_location("quarantine_transaction_test_cli", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _cli_args(case):
    return ["--output-dir", str(case.raw), "--start-date", START, "--end-date", END,
            "--endpoints", "suspend_d", "--rate-limit-sleep-ms", "0"]


@pytest.mark.parametrize("mode", ["default", "dry_run", "subset", "reset_manifest"])
def test_cli_cannot_recover_implicitly_or_mutate_pending_data_before_refusal(tmp_path, monkeypatch, mode):
    case = _case(tmp_path)
    _begin(case)
    _replace_candidate(case)
    cli = _cli()
    client = Mock(side_effect=AssertionError("client must not be constructed"))
    monkeypatch.setattr(cli.TushareClient, "from_environment", client)
    args = _cli_args(case)
    if mode != "default":
        args += ["--suspension-quarantine", POLICY_ID]
    if mode == "dry_run":
        args += ["--dry-run"]
    elif mode == "subset":
        args[args.index("--endpoints") + 1] = "namechange"
    elif mode == "reset_manifest":
        args += ["--reset-manifest"]
    before = _bytes_under(case.raw)
    assert cli.main(args) == 1
    client.assert_not_called()
    assert _bytes_under(case.raw) == before


def _client(case, *, frame=None):
    client = Mock()
    response_frame = case.short if frame is None else frame

    def response(endpoint, **params):
        assert endpoint == "suspend_d"
        return response_frame.loc[response_frame["trade_date"].between(
            params["start_date"], params["end_date"],
        )].copy()

    client.call.side_effect = response
    return client


@pytest.mark.parametrize("entrypoint", ["library", "cli"])
def test_selected_recovery_forces_actual_refetch_even_without_refresh_flag(tmp_path, monkeypatch, entrypoint):
    case = _case(tmp_path)
    _begin(case)
    _replace_candidate(case)
    client = _client(case)
    if entrypoint == "library":
        fetcher = TushareFetcher(client, TushareFetcherConfig(
            output_dir=case.raw, endpoints=("suspend_d",), start_date=START, end_date=END,
            rate_limit_sleep_ms=0, suspension_quarantine=POLICY_ID,
        ))
        result = fetcher.fetch()
        assert result[0].files_written == 1
        current = build_manifest(result, fetcher.holes, START, END)
        write_manifest(case.raw / MANIFEST_FILENAME, merge_manifest(case.before_manifest, current))
    else:
        cli = _cli()
        monkeypatch.setattr(cli.TushareClient, "from_environment", lambda: client)
        assert cli.main(_cli_args(case) + ["--suspension-quarantine", POLICY_ID]) == 3
    assert client.call.call_count > 0
    manifest = read_manifest(case.raw / MANIFEST_FILENAME)
    evidence = manifest.endpoints["suspend_d"].holes[0].quarantine
    assert manifest.schema_version == 2
    assert evidence.missing_dates == MISSING_DATES
    assert evidence.reference_sha256 == _sha(case.reference)
    assert evidence.candidate_sha256 == _sha(case.path.read_bytes())
    pd.testing.assert_frame_equal(pd.read_parquet(case.path), case.short)
    assert not case.pending.exists()


def _assert_retry_remains_blocked(case, monkeypatch):
    assert case.pending.exists()
    assert json.loads(case.pending.read_text(encoding="utf-8"))["phase"] == "retry_required"
    before = _bytes_under(case.raw)
    with pytest.raises(FetchManifestError, match="pending"):
        read_manifest(case.raw / MANIFEST_FILENAME)
    with pytest.raises(FetchManifestError, match="pending"):
        clear_manifest(case.raw / MANIFEST_FILENAME)
    default_client = Mock()
    with pytest.raises(TushareFetcherError, match="pending"):
        TushareFetcher(default_client, TushareFetcherConfig(
            output_dir=case.raw, endpoints=("suspend_d",), start_date=START, end_date=END,
            rate_limit_sleep_ms=0,
        )).fetch()
    default_client.call.assert_not_called()
    cli = _cli()
    factory = Mock(side_effect=AssertionError("default CLI cannot construct a client while retry is required"))
    monkeypatch.setattr(cli.TushareClient, "from_environment", factory)
    assert cli.main(_cli_args(case)) == 1
    factory.assert_not_called()
    for policy in (None, POLICY_ID):
        provider = case.raw.parent / "refused_provider"
        builder = QlibBinBuilder(
            tushare_dir=case.raw, delisted_registry_path=case.raw / "unused_registry.parquet",
            output_dir=provider, allow_holey_fetch=True, suspension_quarantine=policy,
        )
        with pytest.raises(QlibBinBuilderError, match="pending"):
            builder.build()
        assert not provider.exists()
    assert _bytes_under(case.raw) == before


def test_cli_token_construction_failure_after_rollback_keeps_durable_retry(tmp_path, monkeypatch):
    case = _case(tmp_path)
    _begin(case)
    _replace_candidate(case)
    cli = _cli()
    factory = Mock(side_effect=TushareClientError("synthetic token environment missing", kind=KIND_ENVIRONMENT))
    monkeypatch.setattr(cli.TushareClient, "from_environment", factory)

    assert cli.main(_cli_args(case) + ["--suspension-quarantine", POLICY_ID]) == 1

    factory.assert_called_once()
    assert case.path.read_bytes() == case.before
    assert (case.raw / MANIFEST_FILENAME).read_bytes() == case.before_manifest_bytes
    _assert_retry_remains_blocked(case, monkeypatch)


@pytest.mark.parametrize("entrypoint", ["library", "cli"])
@pytest.mark.parametrize("fully_restored", [False, True])
def test_nonretry_failure_keeps_obligation_until_selected_real_refresh_commits(
    tmp_path, monkeypatch, entrypoint, fully_restored,
):
    case = _case(tmp_path)
    _begin(case)
    _replace_candidate(case)
    failing = Mock()
    failing.call.side_effect = TushareClientError("synthetic permission refusal", kind=KIND_AUTH)
    config = TushareFetcherConfig(
        output_dir=case.raw, endpoints=("suspend_d",), start_date=START, end_date=END,
        rate_limit_sleep_ms=0, suspension_quarantine=POLICY_ID,
    )
    if entrypoint == "library":
        with pytest.raises(TushareClientError, match="permission refusal"):
            TushareFetcher(failing, config).fetch()
    else:
        cli = _cli()
        monkeypatch.setattr(cli.TushareClient, "from_environment", lambda: failing)
        assert cli.main(_cli_args(case) + ["--suspension-quarantine", POLICY_ID]) == 1
    assert failing.call.call_count == 1
    assert case.path.read_bytes() == case.before
    assert (case.raw / MANIFEST_FILENAME).read_bytes() == case.before_manifest_bytes
    _assert_retry_remains_blocked(case, monkeypatch)

    frame = case.complete if fully_restored else case.short
    successful = _client(case, frame=frame)
    if entrypoint == "library":
        fetcher = TushareFetcher(successful, config)
        result = fetcher.fetch()
        assert result[0].files_written == 1
        # Fetch alone cannot release the obligation, including the full-eight
        # path where both original clean-manifest prior and new evidence are None.
        assert case.pending.exists()
        journal = json.loads(case.pending.read_text(encoding="utf-8"))
        assert journal["phase"] == "publishing"
        assert (journal["quarantine_after"] is None) is fully_restored
        with pytest.raises(FetchManifestError, match="pending"):
            read_manifest(case.raw / MANIFEST_FILENAME)
        current = build_manifest(result, fetcher.holes, START, END)
        write_manifest(case.raw / MANIFEST_FILENAME, merge_manifest(case.before_manifest, current))
    else:
        cli = _cli()
        monkeypatch.setattr(cli.TushareClient, "from_environment", lambda: successful)
        assert cli.main(_cli_args(case) + ["--suspension-quarantine", POLICY_ID]) == (0 if fully_restored else 3)
    assert successful.call.call_count > 0
    assert not case.pending.exists()
    parsed = read_manifest(case.raw / MANIFEST_FILENAME)
    assert parsed.schema_version == (1 if fully_restored else 2)
    assert bool(parsed.endpoints["suspend_d"].holes) is not fully_restored
    pd.testing.assert_frame_equal(pd.read_parquet(case.path), frame)
    assert (case.raw / "_suspension_quarantine" / f"{_sha(case.reference)}.parquet").read_bytes() == case.reference


@pytest.mark.parametrize("options", [
    {"policy": None}, {"policy": "other-policy"}, {"enabled": False},
    {"start_date": "20251102"}, {"end_date": "20251230"},
])
def test_refresh_only_reader_does_not_waive_explicit_retry_authorization(tmp_path, options):
    case = _case(tmp_path)
    _begin(case)
    _replace_candidate(case)
    assert _recover(case) is True
    before = _bytes_under(case.raw)
    with pytest.raises((FetchManifestError, ValueError)):
        _refresh_manifest(case, **options)
    assert _bytes_under(case.raw) == before


@pytest.mark.parametrize("state", ["publishing_old", "publishing_new", "changed_raw", "changed_manifest"])
def test_refresh_only_reader_requires_retry_phase_and_exact_restored_pair(tmp_path, state):
    case = _case(tmp_path)
    _begin(case)
    if state != "publishing_old":
        _replace_candidate(case)
    if state.startswith("changed"):
        assert _recover(case) is True
        if state == "changed_raw":
            case.path.write_bytes(case.before + b"unknown newer raw")
        else:
            (case.raw / MANIFEST_FILENAME).write_bytes(case.before_manifest_bytes + b"\n")
    before = _bytes_under(case.raw)
    with pytest.raises((FetchManifestError, ValueError)):
        _refresh_manifest(case)
    assert _bytes_under(case.raw) == before


@pytest.mark.parametrize("replacement", ["old_complete", "failed_hole", "claimed_success"])
def test_retry_phase_cannot_be_discharged_by_manifest_only_write(tmp_path, replacement):
    case = _case(tmp_path, full_recovery=True)
    # Equal before/after bytes make the phase essential: hashes alone would
    # permit the old already-quarantined file to satisfy a fresh success claim.
    case.candidate = case.before
    case.evidence_after = _evidence(case.reference, case.before, case.candidate)
    _begin(case)
    _replace_candidate(case)
    assert _recover(case) is True
    before = _bytes_under(case.raw)
    if replacement == "old_complete":
        proposed = case.before_manifest
    elif replacement == "failed_hole":
        current = build_manifest(
            [TushareFetchResult("suspend_d", 0, 0)],
            (FetchHole("suspend_d", "file", "transient", 1, "failed retry"),), START, END,
        )
        proposed = merge_manifest(case.before_manifest, current)
    else:
        proposed = _manifest(case.evidence_after)
    with pytest.raises(FetchManifestError):
        write_manifest(case.raw / MANIFEST_FILENAME, proposed)
    assert _bytes_under(case.raw) == before
