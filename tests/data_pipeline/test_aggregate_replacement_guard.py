"""Synthetic acquisition-range protection; no vendor access or production data."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.data.tushare.fetch_manifest import (
    MANIFEST_FILENAME,
    EndpointCoverage,
    FetchManifest,
    read_manifest,
    write_manifest,
)
from src.data.tushare.fetch_types import FetchHole
from src.data.tushare.fetcher import (
    TRADE_CAL_START_DATE,
    TushareFetcher,
    TushareFetcherConfig,
    TushareFetcherError,
)

TARGETS = {
    "namechange": "all_namechanges.parquet",
    "suspend_d": "suspend_d.parquet",
    "trade_cal": "trade_cal.parquet",
    "index_weight": "index_weight/000906.SH.parquet",
}


def _unit(endpoint):
    return "index=000906.SH" if endpoint == "index_weight" else "file"


def _seed_file(root, endpoint, *, empty=False):
    path = root / TARGETS[endpoint]
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"old_history": [] if empty else [1, 2]}).to_parquet(path, index=False)
    return path


def _seed_manifest(root, endpoints, *, start="20250101", end="20251231", holey=False):
    manifest = FetchManifest(1, "2026-01-01T00:00:00+00:00", {
        ep: EndpointCoverage(
            "holes" if holey else "complete", start, end, 1,
            (FetchHole(ep, _unit(ep), "transient", 1, "test failure"),) if holey else (),
        ) for ep in endpoints
    })
    write_manifest(root / MANIFEST_FILENAME, manifest)


def _client():
    def call(api, **params):
        if api == "trade_cal":
            dates = pd.date_range(TRADE_CAL_START_DATE, params["end_date"])
            return pd.DataFrame({
                "exchange": "SSE", "cal_date": dates.strftime("%Y%m%d"),
                "is_open": (dates.dayofweek < 5).astype(int),
            })
        return pd.DataFrame({"new_history": [1]})

    client = MagicMock()
    client.call.side_effect = call
    return client


def _fetcher(root, endpoint, *, start="20250101", end="20251231", **kwargs):
    kwargs.setdefault("force_retry_units", frozenset({(endpoint, _unit(endpoint))}))
    return TushareFetcher(_client(), TushareFetcherConfig(
        output_dir=root, endpoints=(endpoint,), indices=("000906.SH",),
        start_date=start, end_date=end, rate_limit_sleep_ms=0, **kwargs,
    ))


@pytest.mark.parametrize("endpoint", TARGETS)
@pytest.mark.parametrize("holey", [False, True])
@pytest.mark.parametrize("end", ["20251130", "20241231"])
def test_narrow_aggregate_retry_preserves_declared_history(tmp_path, endpoint, holey, end):
    path = _seed_file(tmp_path, endpoint)
    before = path.read_bytes()
    _seed_manifest(tmp_path, [endpoint], holey=holey)
    manifest_before = (tmp_path / MANIFEST_FILENAME).read_bytes()
    fetcher = _fetcher(tmp_path, endpoint, start="20240101", end=end)
    result, = fetcher.fetch()
    assert path.read_bytes() == before
    assert (tmp_path / MANIFEST_FILENAME).read_bytes() == manifest_before
    fetcher._client.call.assert_not_called()
    assert result.files_written == result.rows_total == result.units_verified == result.skipped == 0
    hole, = fetcher.holes
    assert (hole.endpoint, hole.unit, hole.reason_class, hole.attempts) == (
        endpoint, _unit(endpoint), "unsafe_overwrite", 0,
    )
    assert "20250101..20251231" in hole.last_error


@pytest.mark.parametrize("endpoint", ["namechange", "suspend_d", "index_weight"])
def test_later_request_start_cannot_erase_aggregate_head(tmp_path, endpoint):
    path = _seed_file(tmp_path, endpoint)
    before = path.read_bytes()
    _seed_manifest(tmp_path, [endpoint])
    fetcher = _fetcher(tmp_path, endpoint, start="20250701", end="20261231")
    result = getattr(fetcher, f"_fetch_{endpoint}")()
    assert path.read_bytes() == before
    assert result.files_written == 0
    assert fetcher.holes[0].reason_class == "unsafe_overwrite"
    fetcher._client.call.assert_not_called()


@pytest.mark.parametrize("endpoint", ["namechange", "suspend_d", "trade_cal"])
def test_refresh_current_also_guards_known_range(tmp_path, endpoint):
    path = _seed_file(tmp_path, endpoint)
    before = path.read_bytes()
    _seed_manifest(tmp_path, [endpoint])
    fetcher = _fetcher(tmp_path, endpoint, end="20251130", refresh_current=True,
                       force_retry_units=frozenset())
    result, = fetcher.fetch()
    assert result.files_written == 0
    assert path.read_bytes() == before
    assert fetcher.holes[0].reason_class == "unsafe_overwrite"
    fetcher._client.call.assert_not_called()


@pytest.mark.parametrize("endpoint", TARGETS)
@pytest.mark.parametrize("empty", [False, True])
def test_existing_aggregate_without_provenance_hard_fails(tmp_path, endpoint, empty):
    path = _seed_file(tmp_path, endpoint, empty=empty)
    before = path.read_bytes()
    fetcher = _fetcher(tmp_path, endpoint)
    with pytest.raises(TushareFetcherError, match="provenance"):
        fetcher.fetch()
    assert path.read_bytes() == before
    assert fetcher.holes == ()  # A hole-only run must not establish false provenance.
    fetcher._client.call.assert_not_called()


@pytest.mark.parametrize("bad", [
    "corrupt", "missing_endpoint", "empty_start", "empty_end", "nonstring_start",
    "nonstring_end", "impossible_date", "unicode_date", "reversed", "bad_status",
    "list_status", "schema_bool", "schema_float", "schema_unknown", "missing_field",
])
def test_unusable_manifest_never_authorizes_existing_replacement(tmp_path, bad):
    path = _seed_file(tmp_path, "namechange")
    before = path.read_bytes()
    _seed_manifest(tmp_path, ["namechange"])
    manifest_path = tmp_path / MANIFEST_FILENAME
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    cov = raw["endpoints"]["namechange"]
    if bad == "missing_endpoint":
        raw["endpoints"] = {}
    elif bad in {"schema_bool", "schema_float", "schema_unknown"}:
        raw["schema_version"] = {"schema_bool": True, "schema_float": 1.0,
                                 "schema_unknown": 99}[bad]
    elif bad == "missing_field":
        del cov["coverage_start_date"]
    elif bad != "corrupt":
        field, value = {
            "empty_start": ("coverage_start_date", ""),
            "empty_end": ("coverage_end_date", ""),
            "nonstring_start": ("coverage_start_date", 20250101),
            "nonstring_end": ("coverage_end_date", None),
            "impossible_date": ("coverage_start_date", "20250230"),
            "unicode_date": ("coverage_start_date", "２０２５０１０１"),
            "reversed": ("coverage_start_date", "20260101"),
            "bad_status": ("status", "unknown"),
            "list_status": ("status", []),
        }[bad]
        cov[field] = value
    manifest_path.write_text("{" if bad == "corrupt" else json.dumps(raw), encoding="utf-8")
    manifest_before = manifest_path.read_bytes()
    fetcher = _fetcher(tmp_path, "namechange")
    with pytest.raises(TushareFetcherError, match="provenance"):
        fetcher.fetch()
    assert fetcher.holes == ()
    assert path.read_bytes() == before
    assert manifest_path.read_bytes() == manifest_before
    fetcher._client.call.assert_not_called()


@pytest.mark.parametrize("endpoint", TARGETS)
@pytest.mark.parametrize("kind", ["missing_target", "same_range", "covering_range"])
def test_safe_acquisition_keeps_requested_bounds(tmp_path, endpoint, kind):
    if kind != "missing_target":
        _seed_file(tmp_path, endpoint)
        _seed_manifest(tmp_path, [endpoint])
    start = "20240101" if kind == "covering_range" else "20250101"
    end = "20260101" if kind == "covering_range" else "20251231"
    fetcher = _fetcher(tmp_path, endpoint, start=start, end=end)
    result, = fetcher.fetch()
    assert result.files_written == 1
    assert fetcher.holes == ()
    calls = fetcher._client.call.call_args_list
    assert calls[0].kwargs["start_date"] == (TRADE_CAL_START_DATE if endpoint == "trade_cal" else start)
    assert calls[-1].kwargs["end_date"] == end


@pytest.mark.parametrize("prior_start", [TRADE_CAL_START_DATE, "19800101"])
def test_calendar_compares_actual_fixed_start_not_cli_start(tmp_path, prior_start):
    _seed_file(tmp_path, "trade_cal")
    _seed_manifest(tmp_path, ["trade_cal"], start=prior_start)
    fetcher = _fetcher(tmp_path, "trade_cal", start="20250701")
    assert fetcher.fetch()[0].files_written == 1
    assert fetcher.holes == ()


@pytest.mark.parametrize("endpoint", TARGETS)
@pytest.mark.parametrize("dry_run", [False, True])
def test_no_write_paths_do_not_load_provenance(tmp_path, endpoint, dry_run):
    path = _seed_file(tmp_path, endpoint)
    before = path.read_bytes()
    fetcher = _fetcher(tmp_path, endpoint, dry_run=dry_run,
                       force_retry_units=(frozenset({(endpoint, _unit(endpoint))})
                                          if dry_run else frozenset()))
    with patch("src.data.tushare.fetcher.read_manifest", side_effect=AssertionError("unexpected read")):
        result, = fetcher.fetch()
    assert result.files_written == 0
    assert path.read_bytes() == before
    fetcher._client.call.assert_not_called()


def test_manifest_snapshot_is_shared_then_reset_for_next_fetch(tmp_path):
    for ep in ("namechange", "suspend_d"):
        _seed_file(tmp_path, ep)
    _seed_manifest(tmp_path, ["namechange", "suspend_d"])
    fetcher = TushareFetcher(_client(), TushareFetcherConfig(
        output_dir=tmp_path, endpoints=("namechange", "suspend_d"),
        start_date="20250101", end_date="20251231", rate_limit_sleep_ms=0,
        refresh_current=True,
    ))
    with patch("src.data.tushare.fetcher.read_manifest", wraps=read_manifest) as reader:
        assert all(r.files_written == 1 for r in fetcher.fetch())
        assert reader.call_count == 1
        _seed_manifest(tmp_path, ["namechange", "suspend_d"], end="20260101")
        assert all(r.files_written == 0 for r in fetcher.fetch())
        assert reader.call_count == 2
    assert len(fetcher.holes) == 2


@pytest.mark.parametrize("endpoint", ["namechange", "suspend_d", "index_weight"])
def test_covering_empty_response_retains_existing_publication_contract(tmp_path, endpoint):
    path = _seed_file(tmp_path, endpoint)
    _seed_manifest(tmp_path, [endpoint])
    fetcher = _fetcher(tmp_path, endpoint)
    fetcher._client.call.side_effect = lambda *args, **kwargs: pd.DataFrame()
    assert fetcher.fetch()[0].files_written == 1
    assert pd.read_parquet(path).empty
    assert fetcher.holes == ()


def test_covering_calendar_still_rejects_unusable_vendor_response(tmp_path):
    path = _seed_file(tmp_path, "trade_cal")
    before = path.read_bytes()
    _seed_manifest(tmp_path, ["trade_cal"])
    fetcher = _fetcher(tmp_path, "trade_cal")
    fetcher._client.call.side_effect = lambda *args, **kwargs: pd.DataFrame()
    assert fetcher.fetch()[0].files_written == 0
    assert path.read_bytes() == before
    fetcher._client.call.assert_called_once()
    assert fetcher.holes[0].reason_class == "unusable_response"


def test_known_index_refusal_continues_first_write_for_other_index(tmp_path):
    path = _seed_file(tmp_path, "index_weight")
    before = path.read_bytes()
    _seed_manifest(tmp_path, ["index_weight"])
    fetcher = TushareFetcher(_client(), TushareFetcherConfig(
        output_dir=tmp_path, endpoints=("index_weight",),
        indices=("000906.SH", "000300.SH"), start_date="20250701", end_date="20250731",
        rate_limit_sleep_ms=0,
        force_retry_units=frozenset({("index_weight", "index=000906.SH")}),
    ))
    assert fetcher.fetch()[0].files_written == 1
    assert path.read_bytes() == before
    assert (path.parent / "000300.SH.parquet").exists()
    fetcher._client.call.assert_called_once()
    assert fetcher._client.call.call_args.kwargs["index_code"] == "000300.SH"
    assert fetcher.holes[0].unit == "index=000906.SH"


def _cli():
    path = Path(__file__).resolve().parents[2] / "scripts/data_pipeline/01_fetch_tushare.py"
    spec = importlib.util.spec_from_file_location("aggregate_guard_cli", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("provenance", ["absent", "missing_endpoint", "empty_coverage", "known", "holey"])
def test_repeated_cli_refusal_preserves_raw_and_cannot_launder_unknown_coverage(tmp_path, provenance):
    path = _seed_file(tmp_path, "namechange")
    before = path.read_bytes()
    if provenance != "absent":
        _seed_manifest(tmp_path, [] if provenance == "missing_endpoint" else ["namechange"],
                       start="" if provenance == "empty_coverage" else "20250101",
                       end="" if provenance == "empty_coverage" else "20251231",
                       holey=provenance == "holey")
    manifest_path = tmp_path / MANIFEST_FILENAME
    original = manifest_path.read_bytes() if manifest_path.exists() else None
    cli = _cli()
    client = _client()
    with patch.object(cli, "setup_logging"), patch.object(cli.TushareClient, "from_environment", return_value=client):
        for _ in range(2):
            rc = cli.main([
                "--output-dir", str(tmp_path), "--endpoints", "namechange",
                "--start-date", "20250701", "--end-date", "20250801",
                "--refresh-current", "--rate-limit-sleep-ms", "0",
            ])
            assert rc != 0
            assert path.read_bytes() == before
            client.call.assert_not_called()
            if provenance == "known":
                cov = read_manifest(manifest_path).endpoints["namechange"]
                assert (cov.coverage_start_date, cov.coverage_end_date) == ("20250101", "20251231")
                assert cov.holes[0].reason_class == "unsafe_overwrite"
            else:
                assert rc == 1
                assert (manifest_path.read_bytes() if manifest_path.exists() else None) == original


def test_explicit_manifest_reset_cannot_authorize_existing_file_replacement(tmp_path):
    path = _seed_file(tmp_path, "namechange")
    before = path.read_bytes()
    _seed_manifest(tmp_path, ["namechange"])
    cli = _cli()
    client = _client()
    with patch.object(cli, "setup_logging"), patch.object(cli.TushareClient, "from_environment", return_value=client):
        assert cli.main([
            "--output-dir", str(tmp_path), "--endpoints", "namechange",
            "--start-date", "20250701", "--end-date", "20250801",
            "--refresh-current", "--reset-manifest",
        ]) == 1
    assert path.read_bytes() == before
    assert not (tmp_path / MANIFEST_FILENAME).exists()
    client.call.assert_not_called()
