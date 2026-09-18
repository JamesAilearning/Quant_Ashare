"""Historical name identity must never leak into unsupported price requests."""

from dataclasses import replace
from unittest.mock import MagicMock

import pandas as pd
import pytest

from src.data.tushare.fetch_manifest import (
    MANIFEST_FILENAME,
    EndpointCoverage,
    read_manifest,
    write_manifest,
)
from src.data.tushare.fetch_types import FetchHole
from src.data.tushare.fetcher import ENDPOINTS, TushareFetcher, TushareFetcherConfig, TushareFetcherError
from tests.data_pipeline.test_namechange_per_security import (
    RUN_DATE,
    _name,
    _seed,
    _stock_name_cli,
    _stocks,
)

HISTORICAL = "T600018.SH"
ORDINARY = "600018.SH"
GENERIC = ("daily", "adj_factor", "daily_basic")
START, END = "20260916", "20260917"


def _snapshot(root, *, historical=True):
    _seed(root, active=(ORDINARY,), delisted=(HISTORICAL if historical else "600003.SH",))
    path = root / "delisted_stocks.parquet"
    frame = pd.read_parquet(path).assign(list_date="20000719", delist_date="20061020")
    frame.to_parquet(path, index=False)


def _response(api, **params):
    if api == "stock_basic":
        status = params["list_status"]
        frame = _stocks((ORDINARY if status == "L" else HISTORICAL,), status)
        if status == "D":
            frame = frame.assign(list_date="20000719", delist_date="20061020")
        return frame.drop(columns="snapshot_date")
    if api == "namechange":
        return pd.DataFrame([_name(params["ts_code"])])
    if api == "trade_cal":
        dates = pd.date_range(params["start_date"], params["end_date"])
        return pd.DataFrame({
            "exchange": "SSE", "cal_date": dates.strftime("%Y%m%d"),
            "is_open": (dates.weekday < 5).astype(int),
        })
    if api in {"suspend_d", "index_weight"}:
        return pd.DataFrame(columns=params["fields"].split(","))
    assert api in GENERIC
    dates = [params["start_date"], params["end_date"]]
    return pd.DataFrame({
        field: [params["ts_code"]] * 2 if field == "ts_code" else dates if field == "trade_date" else [1.0] * 2
        for field in params["fields"].split(",")
    })


def _generic(root, endpoint, **overrides):
    client = MagicMock()
    client.call.side_effect = _response
    return TushareFetcher(client, TushareFetcherConfig(**{
        "output_dir": root, "endpoints": (endpoint,), "start_date": START, "end_date": END,
        "now": RUN_DATE, "rate_limit_sleep_ms": 0, "namechange_mode": "per_security_full", **overrides,
    }))


def _bytes(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def _refuses_without_generic_effects(root, fetcher):
    before = _bytes(root)
    directories = {path for path in root.rglob("*") if path.is_dir()}
    with pytest.raises(TushareFetcherError):
        fetcher.fetch()
    fetcher._client.call.assert_not_called()  # Includes implicit calendar calls.
    assert _bytes(root) == before
    assert {path for path in root.rglob("*") if path.is_dir()} == directories


@pytest.mark.parametrize("endpoint", GENERIC)
@pytest.mark.parametrize("start,end", [(START, END), ("19990104", "19990105")])
@pytest.mark.parametrize("empty_file", [False, True])
def test_disjoint_historical_identity_is_excluded_without_placeholder_or_verification(
    tmp_path, caplog, endpoint, start, end, empty_file,
):
    _snapshot(tmp_path)
    historical_path = tmp_path / endpoint / start[:4] / f"{HISTORICAL}.parquet"
    if empty_file:
        historical_path.parent.mkdir(parents=True)
        pd.DataFrame(columns=["ts_code", "trade_date"]).to_parquet(historical_path, index=False)
    before = historical_path.read_bytes() if empty_file else None
    fetcher = _generic(tmp_path, endpoint, start_date=start, end_date=end)

    result, = fetcher.fetch()

    assert result.files_written == 1 and result.units_verified == result.skipped == 0
    calls = [call.kwargs["ts_code"] for call in fetcher._client.call.call_args_list if call.args[0] == endpoint]
    assert calls == [ORDINARY]
    assert fetcher.holes == ()
    assert HISTORICAL in caplog.text and "excluding" in caplog.text.lower()
    assert historical_path.exists() == empty_file
    if empty_file:
        assert historical_path.read_bytes() == before


@pytest.mark.parametrize("endpoint", GENERIC)
@pytest.mark.parametrize("listed,delisted,start,end", [
    ("20000719", "20061020", "20000101", "20000719"),
    ("20000719", "20061020", "20061020", "20061231"),
    ("20000719", "20061020", "20050101", "20050131"),
    ("20000719", "20061020", "19990101", END),
    (None, "20061020", START, END), ("20000719", None, START, END),
    ("20000230", "20061020", START, END), ("20000719", "20060230", START, END),
    ("20070101", "20061020", START, END), ("２００００７１９", "20061020", START, END),
])
def test_unknown_or_overlapping_lifespan_refuses_before_generic_api_or_files(
    tmp_path, endpoint, listed, delisted, start, end,
):
    _snapshot(tmp_path)
    path = tmp_path / "delisted_stocks.parquet"
    pd.read_parquet(path).assign(list_date=listed, delist_date=delisted).to_parquet(path, index=False)
    _refuses_without_generic_effects(tmp_path, _generic(tmp_path, endpoint, start_date=start, end_date=end))


@pytest.mark.parametrize("endpoint", GENERIC)
@pytest.mark.parametrize("problem", ["listed", "duplicate", "overlap", "unknown", "stale", "missing",
                                       "unreadable", "no_manifest", "stock_hole"])
def test_unproven_snapshots_cannot_authorize_historical_generic_exclusion(tmp_path, endpoint, problem):
    _snapshot(tmp_path)
    active = tmp_path / "active_stocks.parquet"
    path = tmp_path / "delisted_stocks.parquet"
    frame = pd.read_parquet(path)
    if problem == "listed":
        pd.read_parquet(active).assign(ts_code=HISTORICAL).to_parquet(active, index=False)
    elif problem == "duplicate":
        pd.concat([frame, frame]).to_parquet(path, index=False)
    elif problem == "overlap":
        pd.concat([frame, _stocks((ORDINARY,), "D")]).to_parquet(path, index=False)
    elif problem == "unknown":
        frame.assign(ts_code="T600019.SH").to_parquet(path, index=False)
    elif problem == "stale":
        frame.assign(snapshot_date="20260917").to_parquet(path, index=False)
    elif problem == "missing":
        path.unlink()
    elif problem == "unreadable":
        path.write_bytes(b"not parquet")
    elif problem == "no_manifest":
        (tmp_path / MANIFEST_FILENAME).unlink()
    else:
        _prior_hole(tmp_path, "stock_basic", "list_status=D (delisted_stocks)")
    _refuses_without_generic_effects(tmp_path, _generic(tmp_path, endpoint))


@pytest.mark.parametrize("endpoint", GENERIC)
@pytest.mark.parametrize("problem", ["nonempty", "corrupt", "directory"])
@pytest.mark.parametrize("verify_all", [False, True])
def test_existing_historical_artifact_is_not_deleted_rewritten_or_hidden_by_watermark(
    tmp_path, endpoint, problem, verify_all,
):
    _snapshot(tmp_path)
    path = tmp_path / endpoint / "2025" / f"{HISTORICAL}.parquet"
    path.parent.mkdir(parents=True)
    if problem == "nonempty":
        pd.DataFrame({"ts_code": [ORDINARY], "trade_date": ["20250102"]}).to_parquet(path, index=False)
    elif problem == "corrupt":
        path.write_bytes(b"unreadable historical evidence")
    else:
        path.mkdir()
    _refuses_without_generic_effects(tmp_path, _generic(
        tmp_path, endpoint, start_date="20250101", verify_all_years=verify_all,
        assume_verified_ranges={endpoint: ("20200101", END)},
    ))


def _prior_hole(root, endpoint, unit):
    path = root / MANIFEST_FILENAME
    manifest = read_manifest(path)
    coverage = EndpointCoverage("holes", START, END, 0, (
        FetchHole(endpoint, unit, "transient", 5, "synthetic pending retry"),
    ))
    write_manifest(path, replace(manifest, endpoints={**manifest.endpoints, endpoint: coverage}))


@pytest.mark.parametrize("endpoint", GENERIC)
@pytest.mark.parametrize("present", [False, True])
@pytest.mark.parametrize("source", ["forced", "manifest", "cli"])
def test_pending_historical_units_are_not_silently_healed_even_when_identity_disappears(
    tmp_path, monkeypatch, endpoint, present, source,
):
    _snapshot(tmp_path, historical=present)
    unit = f"ts_code={HISTORICAL} year=2026"
    if source != "forced":
        _prior_hole(tmp_path, endpoint, unit)
    if source == "cli":
        cli, client, _, args = _stock_name_cli(tmp_path, monkeypatch, _response)
        args[args.index("--endpoints") + 1] = endpoint
        before = _bytes(tmp_path)
        assert cli.main(args) == 1
        client.call.assert_not_called()
        assert _bytes(tmp_path) == before
    else:
        forced = frozenset({(endpoint, unit)}) if source == "forced" else frozenset()
        _refuses_without_generic_effects(tmp_path, _generic(tmp_path, endpoint, force_retry_units=forced))


@pytest.mark.parametrize("endpoint", GENERIC)
def test_other_endpoint_historical_hole_does_not_block_supported_current_endpoint(tmp_path, endpoint):
    _snapshot(tmp_path)
    other = next(ep for ep in GENERIC if ep != endpoint)
    _prior_hole(tmp_path, other, f"ts_code={HISTORICAL} year=2026")
    before = (tmp_path / MANIFEST_FILENAME).read_bytes()
    result, = _generic(tmp_path, endpoint).fetch()
    assert result.files_written == 1
    assert (tmp_path / MANIFEST_FILENAME).read_bytes() == before


@pytest.mark.parametrize("endpoint", GENERIC)
def test_legacy_generic_requests_are_unchanged(tmp_path, endpoint):
    _snapshot(tmp_path)
    fetcher = _generic(tmp_path, endpoint, namechange_mode="date_range")
    result, = fetcher.fetch()
    assert result.files_written == 2
    assert [call.kwargs["ts_code"] for call in fetcher._client.call.call_args_list
            if call.args[0] == endpoint] == [ORDINARY, HISTORICAL]


@pytest.mark.parametrize("endpoint", GENERIC)
def test_full_mode_generic_dry_run_has_no_api_or_filesystem_effects(tmp_path, endpoint):
    _snapshot(tmp_path)
    before = _bytes(tmp_path)
    fetcher = _generic(tmp_path, endpoint, dry_run=True)
    result, = fetcher.fetch()
    assert result.files_written == result.units_verified == 0
    fetcher._client.call.assert_not_called()
    assert _bytes(tmp_path) == before
    assert not (tmp_path / endpoint).exists()


@pytest.mark.parametrize("overlap", [False, True])
def test_default_all_endpoint_cli_keeps_name_identity_out_of_generic_calls(tmp_path, monkeypatch, overlap):
    _snapshot(tmp_path)
    before = (tmp_path / MANIFEST_FILENAME).read_bytes()

    def response(api, **params):
        frame = _response(api, **params)
        if overlap and api == "stock_basic" and params["list_status"] == "D":
            return frame.assign(delist_date=END)
        return frame

    cli, client, _, args = _stock_name_cli(tmp_path, monkeypatch, response)
    idx = args.index("--endpoints")
    del args[idx:idx + 2]  # Exercise the actual default eight-endpoint order.
    args[args.index("--start-date") + 1] = START
    args[args.index("--end-date") + 1] = END
    assert cli.main(args + ["--refresh-current"]) == (1 if overlap else 0)
    names = [call.kwargs["ts_code"] for call in client.call.call_args_list if call.args[0] == "namechange"]
    assert names == [ORDINARY, HISTORICAL]
    generic = [call for call in client.call.call_args_list if call.args[0] in GENERIC]
    if overlap:
        assert generic == []
        assert (tmp_path / MANIFEST_FILENAME).read_bytes() == before
        assert all(not (tmp_path / endpoint).exists() for endpoint in GENERIC)
    else:
        assert [(call.args[0], call.kwargs["ts_code"]) for call in generic] == [(ep, ORDINARY) for ep in GENERIC]
        manifest = read_manifest(tmp_path / MANIFEST_FILENAME)
        assert set(manifest.endpoints) == set(ENDPOINTS)
        assert all(ep.status == "complete" and not ep.holes for ep in manifest.endpoints.values())
        for endpoint in GENERIC:
            assert (tmp_path / endpoint / "2026" / f"{ORDINARY}.parquet").is_file()
            assert not (tmp_path / endpoint / "2026" / f"{HISTORICAL}.parquet").exists()
            assert manifest.endpoints[endpoint].units_written == 1
