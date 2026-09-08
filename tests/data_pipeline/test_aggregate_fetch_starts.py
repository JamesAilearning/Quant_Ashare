"""Explicit aggregate ranges: synthetic CLI, coverage and preservation regressions."""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.data.tushare.client import KIND_NETWORK, TushareClientError
from src.data.tushare.fetch_manifest import (
    MANIFEST_FILENAME,
    EndpointCoverage,
    FetchManifest,
    FetchManifestError,
    build_manifest,
    merge_manifest,
    read_manifest,
    write_manifest,
)
from src.data.tushare.fetch_types import FetchHole, TushareFetchResult
from src.data.tushare.fetcher import (
    TRADE_CAL_START_DATE,
    TushareFetcher,
    TushareFetcherConfig,
    TushareFetcherError,
)
from src.data_pipeline.daily_update import DailyUpdateConfig, build_plan

STARTS = {
    "namechange": "19900101",
    "suspend_d": "20151001",
    "index_weight": "20000101",
}
TARGETS = {
    "namechange": "all_namechanges.parquet",
    "suspend_d": "suspend_d.parquet",
    "index_weight": "index_weight/000906.SH.parquet",
}
COMMON_START = "20180101"
PRIOR_END = "20180131"
END = "20180215"


def _cli():
    path = Path(__file__).resolve().parents[2] / "scripts/data_pipeline/01_fetch_tushare.py"
    spec = importlib.util.spec_from_file_location("aggregate_fetch_starts_cli", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _flags(starts):
    return [item for endpoint, start in starts.items()
            for item in (f"--{endpoint.replace('_', '-')}-start-date", start)]


def _fields(starts):
    return {f"{endpoint}_start_date": start for endpoint, start in starts.items()}


def _unit(endpoint):
    return "index=000906.SH" if endpoint == "index_weight" else "file"


def _seed(root, starts, *, holey=(), end=PRIOR_END):
    endpoints = {}
    for endpoint, start in starts.items():
        path = root / TARGETS[endpoint]
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"old_history": [1, 2]}).to_parquet(path, index=False)
        holes = ((FetchHole(endpoint, _unit(endpoint), "transient", 2, "prior failure"),)
                 if endpoint in holey else ())
        endpoints[endpoint] = EndpointCoverage(
            "holes" if holes else "complete", start, end, 1, holes,
        )
    previous = FetchManifest(1, "2018-02-01T00:00:00+00:00", endpoints)
    write_manifest(root / MANIFEST_FILENAME, previous)
    return previous


def _client(*, fail_endpoint=None):
    def call(api, **params):
        if api == fail_endpoint:
            raise TushareClientError("synthetic network failure", kind=KIND_NETWORK)
        if api == "stock_basic":
            codes = ["600000.SH"] if params["list_status"] == "L" else []
            return pd.DataFrame({
                "ts_code": codes, "list_date": ["19900101"] * len(codes),
                "delist_date": [None] * len(codes),
            })
        if api == "trade_cal":
            dates = pd.date_range(params["start_date"], params["end_date"])
            return pd.DataFrame({
                "exchange": "SSE", "cal_date": dates.strftime("%Y%m%d"),
                "is_open": (dates.dayofweek < 5).astype(int),
            })
        if api == "index_weight":
            return pd.DataFrame({
                "index_code": [params["index_code"]], "con_code": ["600000.SH"],
                "trade_date": [params["start_date"]], "weight": [100.0],
            })
        if api in {"daily", "adj_factor", "daily_basic"}:
            dates = pd.bdate_range(params["start_date"], params["end_date"])
            return pd.DataFrame({
                "ts_code": "600000.SH", "trade_date": dates.strftime("%Y%m%d"),
                "close": 10.0, "adj_factor": 1.0,
            })
        if api == "namechange":
            return pd.DataFrame({"ts_code": ["600000.SH"], "start_date": [params["start_date"]]})
        if api == "suspend_d":
            return pd.DataFrame({"ts_code": ["600000.SH"], "trade_date": [params["start_date"]]})
        raise AssertionError(f"Unexpected synthetic API {api!r}")

    client = MagicMock()
    client.call.side_effect = call
    return client


def _run_cli(cli, client, argv):
    with patch.object(cli, "setup_logging"), \
            patch.object(cli.TushareClient, "from_environment", return_value=client), \
            patch("src.data.tushare.fetcher.time.sleep"):
        return cli.main(argv)


def _fetch_args(root, endpoints, starts, *, end=END):
    return [
        "--output-dir", str(root), "--start-date", COMMON_START, "--end-date", end,
        "--endpoints", ",".join(endpoints), "--indices", "000906.SH",
        "--refresh-current", "--rate-limit-sleep-ms", "0", *_flags(starts),
    ]


@pytest.mark.parametrize("holey", [tuple(STARTS), ("index_weight",)])
def test_cli_refresh_uses_explicit_ranges_in_requests_and_manifest(tmp_path, holey):
    _seed(tmp_path, STARTS, holey=holey)
    client = _client()
    assert _run_cli(_cli(), client, _fetch_args(tmp_path, STARTS, STARTS)) == 0
    manifest = read_manifest(tmp_path / MANIFEST_FILENAME)
    assert manifest is not None and manifest.schema_version == 1
    for endpoint, start in STARTS.items():
        calls = [call for call in client.call.call_args_list if call.args[0] == endpoint]
        assert calls[0].kwargs["start_date"] == start
        assert calls[-1].kwargs["end_date"] == END
        coverage = manifest.endpoints[endpoint]
        assert (coverage.coverage_start_date, coverage.coverage_end_date) == (start, END)
        assert coverage.status == "complete" and coverage.holes == ()
        assert coverage.units_written == 1
        assert "old_history" not in pd.read_parquet(tmp_path / TARGETS[endpoint]).columns


def _daily_config(root, **kwargs):
    kwargs.setdefault("now", date(2018, 2, 15))
    return DailyUpdateConfig(
        tushare_dir=root / "raw", provider_dir=root / "provider",
        delisted_registry=root / "registry.parquet",
        reference_cases=root / "reference.yaml", rate_limit_sleep_ms=0,
        **kwargs,
    )


def _arg_value(argv, flag):
    return argv[argv.index(flag) + 1]


def test_daily_plan_reaches_real_fetch_without_widening_prices_or_benchmarks(tmp_path):
    config = _daily_config(tmp_path, **_fields(STARTS))
    _seed(config.tushare_dir, STARTS, holey=tuple(STARTS))
    plan = build_plan(config)
    for endpoint, start in STARTS.items():
        flag = f"--{endpoint.replace('_', '-')}-start-date"
        assert _arg_value(plan.fetch, flag) == start
        assert flag not in plan.benchmark
    for argv in (plan.fetch, plan.benchmark):
        assert _arg_value(argv, "--start-date") == COMMON_START
        assert _arg_value(argv, "--end-date") == END
    client = _client()
    assert _run_cli(_cli(), client, plan.fetch + ["--indices", "000906.SH"]) == 0
    manifest = read_manifest(config.tushare_dir / MANIFEST_FILENAME)
    assert manifest is not None
    for endpoint in ("daily", "adj_factor", "daily_basic"):
        calls = [call for call in client.call.call_args_list if call.args[0] == endpoint]
        assert len(calls) == 1
        assert (calls[0].kwargs["start_date"], calls[0].kwargs["end_date"]) == (COMMON_START, END)
        coverage = manifest.endpoints[endpoint]
        assert (coverage.coverage_start_date, coverage.coverage_end_date) == (COMMON_START, END)
        assert {path.name for path in (config.tushare_dir / endpoint).iterdir()} == {"2018"}
    for endpoint, start in STARTS.items():
        assert manifest.endpoints[endpoint].coverage_start_date == start
    calendar_calls = [call for call in client.call.call_args_list
                      if call.args[0] == "trade_cal" and "exchange" in call.kwargs]
    assert calendar_calls and calendar_calls[0].kwargs["start_date"] == TRADE_CAL_START_DATE


@pytest.mark.parametrize("selected", STARTS)
def test_one_explicit_override_keeps_other_aggregate_requests_and_coverage_at_common_start(tmp_path, selected):
    explicit = {selected: STARTS[selected]}
    effective = {endpoint: explicit.get(endpoint, COMMON_START) for endpoint in STARTS}
    _seed(tmp_path, effective, holey=tuple(STARTS))
    plan = build_plan(_daily_config(tmp_path, **_fields(explicit)))
    for endpoint in STARTS:
        flag = f"--{endpoint.replace('_', '-')}-start-date"
        assert (flag in plan.fetch) == (endpoint == selected)
    client = _client()
    assert _run_cli(_cli(), client, _fetch_args(tmp_path, STARTS, explicit)) == 0
    manifest = read_manifest(tmp_path / MANIFEST_FILENAME)
    assert manifest is not None
    for endpoint, start in effective.items():
        calls = [call for call in client.call.call_args_list if call.args[0] == endpoint]
        assert calls[0].kwargs["start_date"] == start
        assert calls[-1].kwargs["end_date"] == END
        coverage = manifest.endpoints[endpoint]
        assert (coverage.coverage_start_date, coverage.coverage_end_date) == (start, END)
        assert coverage.holes == ()


def test_omitted_overrides_keep_common_ranges_and_do_not_infer_prior_history(tmp_path):
    config = TushareFetcherConfig(output_dir=tmp_path, start_date=COMMON_START, end_date=END)
    assert config.aggregate_start_dates() == {}
    assert all(config.effective_start_date(endpoint) == COMMON_START for endpoint in STARTS)
    plan = build_plan(_daily_config(tmp_path))
    assert not any(flag in plan.fetch for flag in _flags(STARTS)[::2])
    _seed(tmp_path, {"namechange": STARTS["namechange"]})
    before = (tmp_path / TARGETS["namechange"]).read_bytes()
    client = _client()
    assert _run_cli(_cli(), client, _fetch_args(tmp_path, ("namechange",), {})) == 3
    client.call.assert_not_called()
    assert (tmp_path / TARGETS["namechange"]).read_bytes() == before
    coverage = read_manifest(tmp_path / MANIFEST_FILENAME).endpoints["namechange"]
    assert coverage.coverage_start_date == STARTS["namechange"]
    assert coverage.holes[0].reason_class == "unsafe_overwrite"


@pytest.mark.parametrize("endpoint", STARTS)
@pytest.mark.parametrize("bad", ["", "2018-01-01", "20180230", "２０１８０１０１", "20180216", 20180101])
def test_invalid_override_is_rejected_by_both_configs(tmp_path, endpoint, bad):
    fields = _fields({endpoint: bad})
    with pytest.raises(TushareFetcherError):
        TushareFetcherConfig(output_dir=tmp_path, end_date=END, **fields)
    with pytest.raises((TypeError, ValueError)):
        _daily_config(tmp_path, **fields)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("endpoint", STARTS)
@pytest.mark.parametrize("start", ["20160229", END])
def test_real_leap_day_and_end_date_are_valid_explicit_starts(tmp_path, endpoint, start):
    config = TushareFetcherConfig(output_dir=tmp_path, end_date=END, **_fields({endpoint: start}))
    assert config.aggregate_start_dates() == {endpoint: start}
    assert config.effective_start_date(endpoint) == start


@pytest.mark.parametrize("endpoint", STARTS)
@pytest.mark.parametrize("bad", ["20180230", "２０１８０１０１", "20180216"])
def test_invalid_fetch_cli_override_preserves_manifest_before_reset_or_client(tmp_path, endpoint, bad):
    _seed(tmp_path, {endpoint: STARTS[endpoint]})
    paths = (tmp_path / MANIFEST_FILENAME, tmp_path / TARGETS[endpoint])
    before = {path: path.read_bytes() for path in paths}
    cli = _cli()
    with patch.object(cli, "setup_logging"), patch.object(cli, "clear_manifest") as clear, \
            patch.object(cli.TushareClient, "from_environment") as factory:
        assert cli.main(_fetch_args(tmp_path, (endpoint,), {endpoint: bad}) + ["--reset-manifest"]) == 2
    clear.assert_not_called()
    factory.assert_not_called()
    assert {path: path.read_bytes() for path in paths} == before


@pytest.mark.parametrize("endpoint", STARTS)
def test_invalid_daily_cli_override_cannot_lock_or_start_status_writes(tmp_path, endpoint):
    from scripts import daily_update as cli

    status = tmp_path / "status.json"
    status.write_text("previous status", encoding="utf-8")
    with patch.object(cli, "setup_logging"), patch.object(cli, "single_flight") as lock, \
            patch.object(cli, "run_daily_update") as run:
        assert cli.main([
            "--tushare-dir", str(tmp_path / "raw"), "--provider-dir", str(tmp_path / "provider"),
            "--delisted-registry", str(tmp_path / "registry.parquet"),
            "--reference-cases", str(tmp_path / "reference.yaml"),
            "--status-path", str(status), "--end-date", END,
            *_flags({endpoint: "20180216"}),
        ]) == 2
    lock.assert_not_called()
    run.assert_not_called()
    assert status.read_text(encoding="utf-8") == "previous status"
    assert list(tmp_path.iterdir()) == [status]


def test_daily_plan_revalidates_override_against_injected_execution_date(tmp_path):
    config = _daily_config(tmp_path, namechange_start_date=END)
    with pytest.raises(ValueError):
        build_plan(config, run_date=date(2018, 2, 14))
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("dry_run", [False, True])
def test_daily_run_revalidates_frozen_date_before_status_ledger_repair_or_stages(tmp_path, dry_run):
    from src.data_pipeline import daily_update as orchestration

    with patch.object(orchestration, "date") as clock:
        clock.today.return_value = date(2018, 2, 15)
        config = _daily_config(tmp_path, now=None, dry_run=dry_run, namechange_start_date=END)
        status_path = orchestration.default_status_path(config.provider_dir)
        ledger_path = orchestration.default_ledger_path(config.provider_dir)
        status_path.write_text("previous status", encoding="utf-8")
        ledger_path.write_text("previous ledger", encoding="utf-8")
        clock.today.return_value = date(2018, 2, 14)
        with patch.object(orchestration, "_record_status") as status, \
                patch.object(orchestration, "_append_ledger") as ledger, \
                patch.object(orchestration, "check_and_repair") as repair, \
                patch.object(orchestration, "_load_script_main") as load_stage:
            assert orchestration.run_daily_update(config) == orchestration.EXIT_CONFIG
    status.assert_not_called()
    ledger.assert_not_called()
    repair.assert_not_called()
    load_stage.assert_not_called()
    assert status_path.read_text(encoding="utf-8") == "previous status"
    assert ledger_path.read_text(encoding="utf-8") == "previous ledger"
    assert set(tmp_path.iterdir()) == {status_path, ledger_path}


def test_daily_cli_dry_run_displays_overrides_without_locks_stages_or_artifacts(tmp_path):
    from scripts import daily_update as cli
    from src.data_pipeline import daily_update as orchestration

    with patch.object(cli, "setup_logging"), patch.object(cli, "single_flight") as lock, \
            patch.object(orchestration, "_logger") as logger, \
            patch.object(orchestration, "_load_script_main") as load_stage, \
            patch.object(orchestration, "_record_status") as status:
        assert cli.main([
            "--tushare-dir", str(tmp_path / "raw"), "--provider-dir", str(tmp_path / "provider"),
            "--delisted-registry", str(tmp_path / "registry.parquet"),
            "--reference-cases", str(tmp_path / "reference.yaml"),
            "--end-date", END, "--dry-run", *_flags(STARTS),
        ]) == 0
    lock.assert_not_called()
    load_stage.assert_not_called()
    status.assert_not_called()
    plans = {call.args[1]: call.args[2] for call in logger.info.call_args_list
             if call.args[0] == "  [dry-run] %s: %s"}
    for endpoint, start in STARTS.items():
        assert f"--{endpoint.replace('_', '-')}-start-date {start}" in plans["fetch"]
    assert "--start-date 20180101" in plans["benchmark"]
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("endpoint", STARTS)
@pytest.mark.parametrize("narrow_end", [False, True])
def test_explicit_but_noncovering_retry_keeps_raw_and_prior_holes(tmp_path, endpoint, narrow_end):
    _seed(tmp_path, {endpoint: STARTS[endpoint]}, holey=(endpoint,))
    raw = tmp_path / TARGETS[endpoint]
    manifest_path = tmp_path / MANIFEST_FILENAME
    raw_before, manifest_before = raw.read_bytes(), manifest_path.read_bytes()
    starts = {endpoint: STARTS[endpoint] if narrow_end else COMMON_START}
    client = _client()
    rc = _run_cli(_cli(), client, _fetch_args(
        tmp_path, (endpoint,), starts, end="20180115" if narrow_end else END,
    ))
    assert rc == 1  # The original hole cannot be healed by a narrower-scope merge.
    client.call.assert_not_called()
    assert raw.read_bytes() == raw_before
    assert manifest_path.read_bytes() == manifest_before


@pytest.mark.parametrize("endpoint", STARTS)
def test_explicit_range_cannot_authorize_existing_file_without_provenance(tmp_path, endpoint):
    path = tmp_path / TARGETS[endpoint]
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"old_history": [1]}).to_parquet(path, index=False)
    before = path.read_bytes()
    client = _client()
    config = TushareFetcherConfig(
        output_dir=tmp_path, start_date=COMMON_START, end_date=END, endpoints=(endpoint,),
        indices=("000906.SH",), rate_limit_sleep_ms=0,
        force_retry_units=frozenset({(endpoint, _unit(endpoint))}), **_fields(STARTS),
    )
    fetcher = TushareFetcher(client, config)
    with pytest.raises(TushareFetcherError, match="provenance"):
        fetcher.fetch()
    assert path.read_bytes() == before and fetcher.holes == ()
    assert not (tmp_path / MANIFEST_FILENAME).exists()
    client.call.assert_not_called()


def test_covering_retry_carries_failed_hole_and_preserves_unselected_endpoint(tmp_path):
    previous = _seed(tmp_path, STARTS, holey=("namechange", "suspend_d"))
    raw = tmp_path / TARGETS["namechange"]
    before = raw.read_bytes()
    client = _client(fail_endpoint="namechange")
    assert _run_cli(_cli(), client, _fetch_args(tmp_path, ("namechange",), STARTS)) == 3
    manifest = read_manifest(tmp_path / MANIFEST_FILENAME)
    assert manifest is not None
    coverage = manifest.endpoints["namechange"]
    hole, = coverage.holes
    assert (hole.endpoint, hole.unit) == ("namechange", "file")
    assert hole.attempts == 2 + client.call.call_count
    assert (coverage.coverage_start_date, coverage.coverage_end_date) == (STARTS["namechange"], PRIOR_END)
    assert manifest.endpoints["suspend_d"] == previous.endpoints["suspend_d"]
    assert manifest.endpoints["index_weight"] == previous.endpoints["index_weight"]
    assert raw.read_bytes() == before


def test_index_monthly_requests_use_effective_start_and_publish_after_all_months(tmp_path):
    start, end = "20171215", "20180212"
    _seed(tmp_path, {"index_weight": start}, holey=("index_weight",))
    path = tmp_path / TARGETS["index_weight"]
    before = path.read_bytes()
    client = _client()
    response = client.call.side_effect

    def assert_not_published(api, **params):
        assert path.read_bytes() == before
        return response(api, **params)

    client.call.side_effect = assert_not_published
    assert _run_cli(_cli(), client, _fetch_args(
        tmp_path, ("index_weight",), {"index_weight": start}, end=end,
    )) == 0
    assert [(call.kwargs["start_date"], call.kwargs["end_date"])
            for call in client.call.call_args_list] == [
                ("20171215", "20171231"), ("20180101", "20180131"), ("20180201", end),
            ]
    assert len(pd.read_parquet(path)) == 3
    coverage = read_manifest(tmp_path / MANIFEST_FILENAME).endpoints["index_weight"]
    assert (coverage.coverage_start_date, coverage.coverage_end_date) == (start, end)


def test_index_later_month_failure_keeps_old_file_and_stable_hole_unit(tmp_path):
    start, end = "20171215", "20180212"
    _seed(tmp_path, {"index_weight": start}, holey=("index_weight",))
    path = tmp_path / TARGETS["index_weight"]
    before = path.read_bytes()
    client = _client()
    response = client.call.side_effect

    def fail_second_month(api, **params):
        if params["start_date"] == "20180101":
            raise TushareClientError("synthetic network failure", kind=KIND_NETWORK)
        return response(api, **params)

    client.call.side_effect = fail_second_month
    assert _run_cli(_cli(), client, _fetch_args(
        tmp_path, ("index_weight",), {"index_weight": start}, end=end,
    )) == 3
    assert client.call.call_args_list[0].kwargs["start_date"] == start
    assert path.read_bytes() == before
    coverage = read_manifest(tmp_path / MANIFEST_FILENAME).endpoints["index_weight"]
    hole, = coverage.holes
    assert hole.unit == "index=000906.SH"
    assert hole.attempts == 2 + client.call.call_count - 1
    assert (coverage.coverage_start_date, coverage.coverage_end_date) == (start, PRIOR_END)


def test_index_resume_with_override_never_claims_unfetched_coverage(tmp_path):
    previous = _seed(tmp_path, {"index_weight": COMMON_START})
    path = tmp_path / TARGETS["index_weight"]
    before = path.read_bytes()
    client = _client()
    assert _run_cli(_cli(), client, _fetch_args(
        tmp_path, ("index_weight",), {"index_weight": STARTS["index_weight"]},
    )) == 0
    client.call.assert_not_called()
    assert path.read_bytes() == before
    coverage = read_manifest(tmp_path / MANIFEST_FILENAME).endpoints["index_weight"]
    assert coverage == previous.endpoints["index_weight"]


@pytest.mark.parametrize("outcome", ["written", "verified", "hole", "blind_skip"])
def test_manifest_records_override_only_for_established_endpoint_scope(outcome):
    result = TushareFetchResult(
        "namechange", int(outcome == "written"), 0,
        skipped=int(outcome in {"verified", "blind_skip"}), units_verified=int(outcome == "verified"),
    )
    holes = ((FetchHole("namechange", "file", "transient", 1, "failure"),)
             if outcome == "hole" else ())
    current = build_manifest(
        [result, TushareFetchResult("daily", 1, 1)], holes, COMMON_START, END,
        endpoint_start_dates=STARTS,
    )
    coverage = current.endpoints["namechange"]
    expected = ("", "") if outcome == "blind_skip" else (STARTS["namechange"], END)
    assert (coverage.coverage_start_date, coverage.coverage_end_date) == expected
    assert coverage.holes == holes
    assert current.endpoints["daily"].coverage_start_date == COMMON_START
    assert current.schema_version == 1


@pytest.mark.parametrize("starts", [
    {"daily": "19900101"}, {"trade_cal": "19900101"}, {"unknown": "19900101"},
    {"namechange": "20180230"}, {"suspend_d": "２０１８０１０１"},
    {"index_weight": "20180216"}, {"namechange": ""}, {"namechange": 19900101},
])
def test_manifest_rejects_invalid_endpoint_override_even_without_results(starts):
    with pytest.raises(FetchManifestError):
        build_manifest([], (), COMMON_START, END, endpoint_start_dates=starts)


def test_narrow_price_scope_cannot_clear_prior_price_holes_via_aggregate_override():
    previous = FetchManifest(1, "2018-02-01T00:00:00+00:00", {
        "daily": EndpointCoverage("holes", "20170101", PRIOR_END, 0, (
            FetchHole("daily", "600000.SH/2017", "transient", 1, "prior failure"),
        )),
    })
    current = build_manifest(
        [TushareFetchResult("daily", 1, 1)], (), COMMON_START, END,
        endpoint_start_dates=STARTS,
    )
    with pytest.raises(FetchManifestError, match="narrower-scope"):
        merge_manifest(previous, current)
