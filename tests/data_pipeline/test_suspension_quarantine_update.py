"""Scoped quarantine never grants a general incomplete-data build/update."""

import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from src.data.pit.bundle_integrity import (
    INTEGRITY_FILENAME,
    read_bundle_integrity,
    write_bundle_integrity,
)
from src.data.pit.qlib_bin_builder import QlibBinBuilder, QlibBinBuilderError
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
from tests.data_pipeline.test_qlib_bin_builder import _write_active, _write_daily_year, _write_registry

POLICY = "suspend-688766-20251127-20251209"


def _config(tmp_path, **kwargs):
    return DailyUpdateConfig(
        tushare_dir=tmp_path / "raw", provider_dir=tmp_path / "provider",
        delisted_registry=tmp_path / "raw/registry.parquet",
        reference_cases=tmp_path / "reference.yaml", now=date(2026, 9, 23),
        end_date="20260922", suspension_quarantine=POLICY, **kwargs,
    )


def _evidence(raw):
    """Real tiny vendor/reference files, not a mocked evidence verifier."""
    import hashlib

    from src.contracts.suspension_quarantine import APPROVED_KEYS, SuspensionQuarantine

    cols = ["ts_code", "trade_date", "suspend_timing", "suspend_type"]
    retained = pd.DataFrame(sorted(APPROVED_KEYS), columns=cols)
    retained.loc[len(retained)] = ["600000.SH", "20251210", None, "S"]
    directory = raw / "_suspension_quarantine"
    directory.mkdir(parents=True, exist_ok=True)
    reference = directory / "seed.parquet"
    retained.to_parquet(reference, index=False)
    digest = hashlib.sha256(reference.read_bytes()).hexdigest()
    reference.rename(directory / f"{digest}.parquet")
    retained.iloc[-1:].to_parquet(raw / "suspend_d.parquet", index=False)
    candidate = hashlib.sha256((raw / "suspend_d.parquet").read_bytes()).hexdigest()
    return SuspensionQuarantine(
        policy_id=POLICY, missing_dates=tuple(sorted(k[1] for k in APPROVED_KEYS)),
        reference_sha256=digest, retained_sha256=digest, candidate_sha256=candidate,
        query_start_date="20200101", query_end_date="20260922",
    )


def _manifest(raw, *, extra=(), omit=(), start="20200101"):
    evidence = _evidence(raw)
    hole = FetchHole("suspend_d", "file", "quarantined_history", 1, "known incident", quarantine=evidence)
    manifest = build_manifest(
        [TushareFetchResult(ep, 1, 0) for ep in
         ("stock_basic", "daily", "adj_factor", "suspend_d") if ep not in omit],
        (hole, *extra), start, "20260922",
    )
    write_manifest(raw / "fetch_manifest.json", manifest)
    return hole


def _builder(tmp_path, *, quarantine=POLICY, **kwargs):
    return QlibBinBuilder(
        tushare_dir=tmp_path / "raw", delisted_registry_path=tmp_path / "raw/registry.parquet",
        output_dir=tmp_path / "provider", suspension_quarantine=quarantine, **kwargs,
    )


def _seed_prices(tmp_path, dates=("20200102", "20200103")):
    raw = tmp_path / "raw"
    _write_active(raw / "active_stocks.parquet", ["600000.SH"])
    _write_registry(raw / "registry.parquet", [])
    _write_daily_year(raw, 2020, "600000.SH", list(dates))
    return raw


def test_policy_is_forwarded_without_enabling_broad_hole_permission(tmp_path):
    plan = build_plan(_config(tmp_path))
    for argv in (plan.fetch, plan.bins):
        assert argv[argv.index("--suspension-quarantine") + 1] == POLICY
        assert "--allow-holey-fetch" not in argv


@pytest.mark.parametrize("policy", ["", "other", True, 1])
def test_unknown_policy_refuses_config_before_writes(tmp_path, policy):
    with pytest.raises(ValueError, match="quarantine"):
        DailyUpdateConfig(tmp_path / "raw", tmp_path / "provider", tmp_path / "reg", tmp_path / "refs",
                          suspension_quarantine=policy)
    assert list(tmp_path.iterdir()) == []


def test_selected_policy_does_not_accept_exit_three_without_manifest(tmp_path):
    cfg = _config(tmp_path)
    rec = _Recorder({"fetch": 3})
    assert run_daily_update(cfg, runners=rec.all()) == EXIT_FETCH_HOLES
    assert rec.calls == ["fetch"]


def test_real_builder_keeps_incomplete_stamp_and_strict_coverage_anchor(tmp_path):
    raw = _seed_prices(tmp_path)
    hole = _manifest(raw)
    _builder(tmp_path).build()
    stamp = read_bundle_integrity(tmp_path / "provider")
    assert stamp.schema_version == 2
    assert stamp.built_from_holey_fetch is True
    assert stamp.holes == (hole,)
    assert stamp.data_coverage_start == "2020-01-01"


def test_quarantine_cannot_skip_price_leading_coverage_check(tmp_path):
    raw = _seed_prices(tmp_path, dates=("20200120", "20200121"))
    _manifest(raw)
    with pytest.raises(QlibBinBuilderError, match="coverage|calendar|gap|first"):
        _builder(tmp_path).build()
    assert not (tmp_path / "provider").exists()


@pytest.mark.parametrize("broad", [False, True])
def test_extra_hole_never_qualifies_for_scoped_build(tmp_path, broad):
    raw = _seed_prices(tmp_path)
    _manifest(raw, extra=(FetchHole("daily", "600000.SH", "transient", 1, "failed"),))
    with pytest.raises(QlibBinBuilderError):
        _builder(tmp_path, allow_holey_fetch=broad).build()
    assert not (tmp_path / "provider").exists()


def test_missing_core_coverage_never_qualifies_for_scoped_build(tmp_path):
    raw = _seed_prices(tmp_path)
    _manifest(raw, omit=("adj_factor",))
    with pytest.raises(QlibBinBuilderError):
        _builder(tmp_path).build()


def test_raw_evidence_mutation_refuses_before_any_bundle_write(tmp_path):
    raw = _seed_prices(tmp_path)
    _manifest(raw)
    (raw / "suspend_d.parquet").write_bytes(b"changed")
    with pytest.raises(QlibBinBuilderError):
        _builder(tmp_path).build()
    assert not (tmp_path / "provider").exists()


def test_scoped_update_verifies_manifest_and_discloses_qualified_completion(tmp_path):
    cfg = _config(tmp_path, suspend_d_start_date="20200101")
    _write_snapshot(cfg.tushare_dir, "20260923")
    hole = _manifest(cfg.tushare_dir)
    rec = _Recorder({"fetch": 3})
    runners = rec.all()

    def bins(argv):
        rec.calls.append("bins")
        staging = Path(argv[argv.index("--output-dir") + 1])
        _mk_bundle(staging, "NEW")
        write_bundle_integrity(staging, built_from_holey_fetch=True, holes=(hole,))
        return 0

    runners["bins"] = bins
    assert run_daily_update(cfg, runners=runners) == 0
    assert read_bundle_integrity(cfg.provider_dir).holes == (hole,)
    status = json.loads((tmp_path / "provider.daily_update_status.json").read_text(encoding="utf-8"))
    assert "quarantin" in status["detail"].lower()


def _staged_update(tmp_path, *, broad=False):
    """Real ledger/evidence and old provider; fake only the expensive stages."""
    cfg = _config(tmp_path, suspend_d_start_date="20200101", allow_holey_fetch=broad)
    _write_snapshot(cfg.tushare_dir, "20260923")
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


def _assert_no_publication(cfg, rec):
    assert rec.calls == ["fetch", "registry", "bins", "membership", "universe", "benchmark", "validate"]
    assert (cfg.provider_dir / "calendars/day.txt").read_text(encoding="utf-8") == "OLD"
    assert (new_dir(cfg.provider_dir) / "calendars/day.txt").read_text(encoding="utf-8") == "NEW"
    assert not bak_dir(cfg.provider_dir).exists()
    status = json.loads(default_status_path(cfg.provider_dir).read_text(encoding="utf-8"))
    assert "staged suspension quarantine refused" in status["detail"]


@pytest.mark.parametrize("broad", [False, True])
@pytest.mark.parametrize("mutation", ["missing", "clean", "different_evidence", "extra_hole", "corrupt"])
def test_successful_validation_cannot_publish_a_lost_or_changed_quarantine_stamp(tmp_path, broad, mutation):
    cfg, hole, rec, runners = _staged_update(tmp_path, broad=broad)

    def validate(argv):
        rec.calls.append("validate")
        staging = Path(argv[argv.index("--provider-dir") + 1])
        stamp = staging / INTEGRITY_FILENAME
        if mutation == "missing":
            stamp.unlink()
        elif mutation == "clean":
            write_bundle_integrity(staging, built_from_holey_fetch=False)
        elif mutation == "different_evidence":
            # Well-formed schema v2, but no longer the evidence verified after fetch.
            different = replace(hole.quarantine, missing_dates=hole.quarantine.missing_dates[:1])
            write_bundle_integrity(
                staging, built_from_holey_fetch=True,
                holes=(replace(hole, quarantine=different),),
            )
        elif mutation == "extra_hole":
            extra = FetchHole("daily", "600000.SH", "transient", 1, "late ordinary failure")
            write_bundle_integrity(staging, built_from_holey_fetch=True, holes=(hole, extra))
        else:
            stamp.write_text("{not-json", encoding="utf-8")
        return 0  # A successful 06 is not sufficient authorization to swap.

    runners["validate"] = validate
    assert run_daily_update(cfg, runners=runners) == EXIT_VALIDATE
    _assert_no_publication(cfg, rec)


@pytest.mark.parametrize("broad", [False, True])
@pytest.mark.parametrize("artifact", ["candidate", "reference"])
def test_raw_evidence_is_rechecked_after_successful_validation_before_publication(tmp_path, broad, artifact):
    cfg, hole, rec, runners = _staged_update(tmp_path, broad=broad)

    def validate(argv):
        rec.calls.append("validate")
        if artifact == "candidate":
            path = cfg.tushare_dir / "suspend_d.parquet"
        else:
            path = cfg.tushare_dir / "_suspension_quarantine" / f"{hole.quarantine.reference_sha256}.parquet"
        path.write_bytes(b"changed after initial verification")
        return 0

    runners["validate"] = validate
    assert run_daily_update(cfg, runners=runners) == EXIT_VALIDATE
    _assert_no_publication(cfg, rec)


@pytest.mark.parametrize("broad", [False, True])
@pytest.mark.parametrize("endpoint", ["daily", "suspend_d"])
def test_other_fetch_failure_stops_scoped_update_before_rebuilding_even_with_broad_override(tmp_path, broad, endpoint):
    cfg = _config(tmp_path, suspend_d_start_date="20200101", allow_holey_fetch=broad)
    _write_snapshot(cfg.tushare_dir, "20260923")
    _mk_bundle(cfg.provider_dir, "OLD")
    unit = "file" if endpoint == "suspend_d" else "600000.SH"
    _manifest(cfg.tushare_dir, extra=(FetchHole(endpoint, unit, "transient", 1, "other failure"),))
    rec = _Recorder({"fetch": 3})
    assert run_daily_update(cfg, runners=rec.all()) == EXIT_FETCH_HOLES
    assert rec.calls == ["fetch"]
    assert (cfg.provider_dir / "calendars/day.txt").read_text(encoding="utf-8") == "OLD"
    assert not new_dir(cfg.provider_dir).exists()


@pytest.mark.parametrize("broad", [False, True])
@pytest.mark.parametrize("mismatch", ["start", "end", "exit"])
def test_valid_quarantine_evidence_must_match_this_update_request_and_holey_exit(tmp_path, broad, mismatch):
    cfg = _config(tmp_path, suspend_d_start_date="20200101", allow_holey_fetch=broad)
    _write_snapshot(cfg.tushare_dir, "20260923")
    _mk_bundle(cfg.provider_dir, "OLD")
    _manifest(cfg.tushare_dir)
    if mismatch == "start":
        cfg = replace(cfg, suspend_d_start_date="20210101")
    elif mismatch == "end":
        cfg = replace(cfg, end_date="20260923")
    rec = _Recorder({"fetch": 0 if mismatch == "exit" else 3})
    assert run_daily_update(cfg, runners=rec.all()) == EXIT_FETCH_HOLES
    assert rec.calls == ["fetch"]
    assert (cfg.provider_dir / "calendars/day.txt").read_text(encoding="utf-8") == "OLD"
    assert not new_dir(cfg.provider_dir).exists()
    status = json.loads(default_status_path(cfg.provider_dir).read_text(encoding="utf-8"))
    assert "does not match this update's requested interval" in status["detail"]
