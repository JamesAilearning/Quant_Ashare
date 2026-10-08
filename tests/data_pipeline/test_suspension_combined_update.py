"""Synthetic combined-incident build and post-validation publication gates."""

import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from src.contracts.suspension_quarantine import CONFLICT_POLICY_ID, POLICY_ID
from src.data.pit.bundle_integrity import INTEGRITY_FILENAME, read_bundle_integrity, write_bundle_integrity
from src.data.pit.qlib_bin_builder import BUNDLE_REQUIRED_ENDPOINTS, QlibBinBuilder, QlibBinBuilderError
from src.data.tushare.fetch_manifest import build_manifest, write_manifest
from src.data.tushare.fetch_types import FetchHole, TushareFetchResult
from src.data_pipeline.bundle_swap import bak_dir, new_dir
from src.data_pipeline.daily_update import (
    EXIT_FETCH_HOLES,
    EXIT_VALIDATE,
    DailyUpdateConfig,
    build_plan,
    default_status_path,
    run_daily_update,
)
from tests.data_pipeline.test_daily_update import _mk_bundle, _Recorder, _write_snapshot
from tests.data_pipeline.test_suspension_combined import END, POLICY, _candidate, _publish, _reference
from tests.data_pipeline.test_suspension_quarantine_update import _assert_no_publication, _seed_prices

START = "20200101"
RUN_DAY = date(2026, 10, 8)


def _config(tmp_path, *, policy=POLICY, broad=False, **kwargs):
    values = dict(
        tushare_dir=tmp_path / "raw", provider_dir=tmp_path / "provider",
        delisted_registry=tmp_path / "raw/registry.parquet",
        reference_cases=tmp_path / "reference.yaml", now=RUN_DAY,
        start_date=START, end_date=END, suspend_d_start_date=START,
        suspension_quarantine=policy, allow_holey_fetch=broad,
    )
    values.update(kwargs)
    return DailyUpdateConfig(**values)


def _manifest(raw, *, extra=(), omit=(), start=START):
    """Use actual publication and pending commit, never fake byte evidence."""
    raw.mkdir(parents=True, exist_ok=True)
    _reference().to_parquet(raw / "suspend_d.parquet", index=False)
    endpoints = (*BUNDLE_REQUIRED_ENDPOINTS, "suspend_d")
    results = [TushareFetchResult(endpoint, 1, 1) for endpoint in endpoints]
    write_manifest(raw / "fetch_manifest.json", build_manifest(results, (), start, END))
    evidence = _publish(raw, _candidate(), start=start)
    hole = FetchHole("suspend_d", "file", "quarantined_history", 1, "combined incident", evidence)
    # A real successful suspend write must be committed before constructing
    # synthetic other-stage failures or absent core coverage for refusal tests.
    write_manifest(raw / "fetch_manifest.json", build_manifest(results, (hole,), start, END))
    if extra or omit:
        results = [result for result in results if result.endpoint not in omit]
        write_manifest(raw / "fetch_manifest.json", build_manifest(results, (hole, *extra), start, END))
    return hole


def _builder(tmp_path, *, policy=POLICY, broad=False):
    return QlibBinBuilder(
        tushare_dir=tmp_path / "raw", delisted_registry_path=tmp_path / "raw/registry.parquet",
        output_dir=tmp_path / "provider", suspension_quarantine=policy, allow_holey_fetch=broad,
    )


def _staged_update(tmp_path, *, broad=False):
    """Mock expensive stages only; use actual raw evidence, stamps and swap."""
    cfg = _config(tmp_path, broad=broad)
    _write_snapshot(cfg.tushare_dir, RUN_DAY.strftime("%Y%m%d"))
    hole = _manifest(cfg.tushare_dir)
    _mk_bundle(cfg.provider_dir, "OLD")
    rec = _Recorder({"fetch": 3})
    runners = rec.all()

    def bins(argv):
        rec.calls.append("bins")
        staging = Path(argv[argv.index("--output-dir") + 1])
        _mk_bundle(staging, "NEW")
        write_bundle_integrity(staging, built_from_holey_fetch=True, holes=(hole,))
        return 0

    runners["bins"] = bins
    return cfg, hole, rec, runners


def _assert_fetch_refused(cfg, rec):
    assert rec.calls == ["fetch"]
    assert (cfg.provider_dir / "calendars/day.txt").read_text(encoding="utf-8") == "OLD"
    assert not new_dir(cfg.provider_dir).exists()
    assert not bak_dir(cfg.provider_dir).exists()
    status = json.loads(default_status_path(cfg.provider_dir).read_text(encoding="utf-8"))
    assert status["state"] == "finished"
    assert status["exit_code"] == EXIT_FETCH_HOLES
    assert status["failed_stage"] == "fetch"


def test_combined_policy_is_forwarded_to_fetch_and_bins_without_generic_override(tmp_path):
    plan = build_plan(_config(tmp_path))
    for argv in (plan.fetch, plan.bins):
        assert argv[argv.index("--suspension-quarantine") + 1] == POLICY
        assert "--allow-holey-fetch" not in argv
    assert plan.fetch[plan.fetch.index("--snapshot-date") + 1] == "20261008"
    assert plan.fetch[plan.fetch.index("--end-date") + 1] == END


def test_real_tiny_price_builder_retains_combined_incomplete_stamp_and_coverage_anchor(tmp_path):
    raw = _seed_prices(tmp_path)
    hole = _manifest(raw)
    result = _builder(tmp_path).build()
    assert result.calendar_days == 2
    assert result.ticker_count == 1
    stamp = read_bundle_integrity(tmp_path / "provider")
    assert stamp.schema_version == 2
    assert stamp.built_from_holey_fetch is True
    assert stamp.holes == (hole,)
    assert stamp.holes[0].quarantine.policy_id == POLICY
    assert stamp.data_coverage_start == "2020-01-01"
    assert (tmp_path / "provider/calendars/day.txt").read_text(encoding="utf-8").splitlines() == [
        "2020-01-02", "2020-01-03",
    ]


@pytest.mark.parametrize("policy", [None, POLICY_ID, CONFLICT_POLICY_ID])
@pytest.mark.parametrize("broad", [False, True])
def test_real_builder_requires_exact_combined_authority_even_with_generic_override(tmp_path, policy, broad):
    raw = _seed_prices(tmp_path)
    _manifest(raw)
    with pytest.raises(QlibBinBuilderError, match="quarantine"):
        _builder(tmp_path, policy=policy, broad=broad).build()
    assert not (tmp_path / "provider").exists()


@pytest.mark.parametrize("broad", [False, True])
def test_real_builder_rejects_combined_plus_unrelated_hole_under_generic_override(tmp_path, broad):
    raw = _seed_prices(tmp_path)
    extra = FetchHole("daily", "ts_code=600000.SH year=2020", "unusable_response", 1, "other failure")
    _manifest(raw, extra=(extra,))
    with pytest.raises(QlibBinBuilderError, match="quarantine"):
        _builder(tmp_path, broad=broad).build()
    assert not (tmp_path / "provider").exists()


def test_real_builder_cannot_use_combined_authority_to_skip_missing_core_coverage(tmp_path):
    raw = _seed_prices(tmp_path)
    _manifest(raw, omit=("adj_factor",))
    with pytest.raises(QlibBinBuilderError, match="missing core coverage"):
        _builder(tmp_path).build()
    assert not (tmp_path / "provider").exists()


def test_combined_exception_never_disables_price_leading_coverage_guard(tmp_path):
    raw = _seed_prices(tmp_path, dates=("20200120", "20200121"))
    _manifest(raw)
    with pytest.raises(QlibBinBuilderError, match="coverage|calendar|gap|first"):
        _builder(tmp_path).build()
    assert not (tmp_path / "provider").exists()


@pytest.mark.parametrize("artifact", ["candidate", "reference"])
def test_real_builder_rechecks_combined_raw_bytes_before_output(tmp_path, artifact):
    raw = _seed_prices(tmp_path)
    hole = _manifest(raw)
    path = (raw / "suspend_d.parquet" if artifact == "candidate" else
            raw / "_suspension_quarantine" / f"{hole.quarantine.reference_sha256}.parquet")
    path.write_bytes(b"changed before synthetic build")
    with pytest.raises(QlibBinBuilderError, match="SHA"):
        _builder(tmp_path).build()
    assert not (tmp_path / "provider").exists()


def test_exact_combined_fetch_three_and_matching_real_stamp_complete_update(tmp_path):
    cfg, hole, rec, runners = _staged_update(tmp_path)
    before_raw = (cfg.tushare_dir / "suspend_d.parquet").read_bytes()
    before_reference = (cfg.tushare_dir / "_suspension_quarantine" / f"{hole.quarantine.reference_sha256}.parquet").read_bytes()
    assert run_daily_update(cfg, runners=runners) == 0
    assert rec.calls == ["fetch", "registry", "bins", "membership", "universe", "benchmark", "validate"]
    assert (cfg.provider_dir / "calendars/day.txt").read_text(encoding="utf-8") == "NEW"
    assert (bak_dir(cfg.provider_dir) / "calendars/day.txt").read_text(encoding="utf-8") == "OLD"
    assert not new_dir(cfg.provider_dir).exists()
    stamp = read_bundle_integrity(cfg.provider_dir)
    assert stamp.holes == (hole,) and stamp.built_from_holey_fetch is True
    assert (cfg.tushare_dir / "suspend_d.parquet").read_bytes() == before_raw
    assert (cfg.tushare_dir / "_suspension_quarantine" / f"{hole.quarantine.reference_sha256}.parquet").read_bytes() == before_reference
    status = json.loads(default_status_path(cfg.provider_dir).read_text(encoding="utf-8"))
    assert status["exit_code"] == 0 and status["failed_stage"] is None
    assert status["run_date"] == "2026-10-08"
    assert POLICY in status["detail"] and "data remains incomplete" in status["detail"]


@pytest.mark.parametrize("broad", [False, True])
@pytest.mark.parametrize("mismatch", ["exit", "start", "end"])
def test_actual_fetch_exit_and_combined_query_must_match_update_request(tmp_path, broad, mismatch):
    cfg = _config(tmp_path, broad=broad)
    _write_snapshot(cfg.tushare_dir, RUN_DAY.strftime("%Y%m%d"))
    _manifest(cfg.tushare_dir)
    _mk_bundle(cfg.provider_dir, "OLD")
    if mismatch == "start":
        cfg = replace(cfg, suspend_d_start_date="20210101")
    elif mismatch == "end":
        cfg = replace(cfg, end_date="20260923")
    rec = _Recorder({"fetch": 0 if mismatch == "exit" else 3})
    assert run_daily_update(cfg, runners=rec.all()) == EXIT_FETCH_HOLES
    _assert_fetch_refused(cfg, rec)
    status = json.loads(default_status_path(cfg.provider_dir).read_text(encoding="utf-8"))
    assert "does not match this update's requested interval" in status["detail"]


@pytest.mark.parametrize("policy", [None, POLICY_ID, CONFLICT_POLICY_ID])
@pytest.mark.parametrize("broad", [False, True])
def test_combined_update_cannot_rebuild_with_missing_or_other_policy(tmp_path, policy, broad):
    cfg = _config(tmp_path, policy=policy, broad=broad)
    _write_snapshot(cfg.tushare_dir, RUN_DAY.strftime("%Y%m%d"))
    _manifest(cfg.tushare_dir)
    _mk_bundle(cfg.provider_dir, "OLD")
    rec = _Recorder({"fetch": 3})
    assert run_daily_update(cfg, runners=rec.all()) == EXIT_FETCH_HOLES
    _assert_fetch_refused(cfg, rec)


@pytest.mark.parametrize("broad", [False, True])
@pytest.mark.parametrize("endpoint", ["daily", "suspend_d"])
def test_combined_update_stops_other_fetch_holes_before_rebuild_under_broad_override(tmp_path, broad, endpoint):
    cfg = _config(tmp_path, broad=broad)
    _write_snapshot(cfg.tushare_dir, RUN_DAY.strftime("%Y%m%d"))
    unit = "file" if endpoint == "suspend_d" else "ts_code=600000.SH year=2020"
    _manifest(cfg.tushare_dir, extra=(FetchHole(endpoint, unit, "unusable_response", 1, "another failure"),))
    _mk_bundle(cfg.provider_dir, "OLD")
    rec = _Recorder({"fetch": 3})
    assert run_daily_update(cfg, runners=rec.all()) == EXIT_FETCH_HOLES
    _assert_fetch_refused(cfg, rec)


def test_combined_fetch_three_without_manifest_cannot_rebuild(tmp_path):
    cfg = _config(tmp_path)
    _mk_bundle(cfg.provider_dir, "OLD")
    rec = _Recorder({"fetch": 3})
    assert run_daily_update(cfg, runners=rec.all()) == EXIT_FETCH_HOLES
    _assert_fetch_refused(cfg, rec)


@pytest.mark.parametrize("broad", [False, True])
@pytest.mark.parametrize("mutation", [
    "missing", "clean", "legacy_policy", "conflict_policy", "changed_hash",
    "extra_hole", "corrupt", "false_incomplete",
])
def test_successful_validator_cannot_publish_missing_or_changed_combined_stamp(tmp_path, broad, mutation):
    cfg, hole, rec, runners = _staged_update(tmp_path, broad=broad)

    def validate(argv):
        rec.calls.append("validate")
        staging = Path(argv[argv.index("--provider-dir") + 1])
        stamp = staging / INTEGRITY_FILENAME
        if mutation == "missing":
            stamp.unlink()
        elif mutation == "clean":
            write_bundle_integrity(staging, built_from_holey_fetch=False)
        elif mutation == "legacy_policy":
            evidence = replace(hole.quarantine, policy_id=POLICY_ID)
            write_bundle_integrity(staging, built_from_holey_fetch=True, holes=(replace(hole, quarantine=evidence),))
        elif mutation == "conflict_policy":
            evidence = replace(hole.quarantine, policy_id=CONFLICT_POLICY_ID, missing_dates=("20260116",))
            write_bundle_integrity(staging, built_from_holey_fetch=True, holes=(replace(hole, quarantine=evidence),))
        elif mutation == "changed_hash":
            evidence = replace(hole.quarantine, candidate_sha256="f" * 64)
            write_bundle_integrity(staging, built_from_holey_fetch=True, holes=(replace(hole, quarantine=evidence),))
        elif mutation == "extra_hole":
            extra = FetchHole("daily", "600000.SH", "unusable_response", 1, "late ordinary failure")
            write_bundle_integrity(staging, built_from_holey_fetch=True, holes=(hole, extra))
        elif mutation == "false_incomplete":
            payload = json.loads(stamp.read_text(encoding="utf-8"))
            payload["built_from_holey_fetch"] = False
            stamp.write_text(json.dumps(payload), encoding="utf-8")
        else:
            stamp.write_text("{not-json", encoding="utf-8")
        return 0

    runners["validate"] = validate
    assert run_daily_update(cfg, runners=runners) == EXIT_VALIDATE
    _assert_no_publication(cfg, rec)


@pytest.mark.parametrize("broad", [False, True])
@pytest.mark.parametrize("artifact", ["candidate", "reference"])
def test_combined_evidence_changed_after_validation_cannot_swap_provider(tmp_path, broad, artifact):
    cfg, hole, rec, runners = _staged_update(tmp_path, broad=broad)

    def validate(argv):
        rec.calls.append("validate")
        path = (cfg.tushare_dir / "suspend_d.parquet" if artifact == "candidate" else
                cfg.tushare_dir / "_suspension_quarantine" / f"{hole.quarantine.reference_sha256}.parquet")
        path.write_bytes(b"changed after initial real verification")
        return 0

    runners["validate"] = validate
    assert run_daily_update(cfg, runners=runners) == EXIT_VALIDATE
    _assert_no_publication(cfg, rec)
