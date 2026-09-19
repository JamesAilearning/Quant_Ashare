"""A proven name-only vendor identity must never become a stock or price input."""

import pandas as pd
import pytest

from src.data.tushare.aggregate_response import AggregateResponseError
from src.data.tushare.fetch_manifest import MANIFEST_FILENAME, read_manifest
from src.data.tushare.fetcher import ENDPOINTS
from src.data.tushare.namechange_history import validate_stock_basic_snapshot
from tests.data_pipeline.test_historical_generic_guard import (
    END,
    GENERIC,
    HISTORICAL,
    ORDINARY,
    START,
    _bytes,
    _generic,
    _prior_hole,
    _refuses_without_generic_effects,
    _response,
    _snapshot,
)
from tests.data_pipeline.test_namechange_per_security import (
    NAME_FIELDS,
    RUN_DATE,
    _assert_cli_preserves_snapshots_after_unusable_stock_response,
    _assert_unusable,
    _fetcher,
    _name,
    _seed,
    _stock_name_cli,
    _stocks,
)

NAME_ONLY = "X19363.SH"


@pytest.mark.parametrize("correct_end", [False, True])
def test_retained_only_identity_is_queried_unchanged_and_preserves_each_key(tmp_path, correct_end):
    retained = [_name(NAME_ONLY), _name(NAME_ONLY, start_date="19990101", ann_date=None)]
    path = _seed(tmp_path, active=(ORDINARY,), delisted=(HISTORICAL,), retained=retained)

    def response(params):
        rows = retained if params["ts_code"] == NAME_ONLY else [_name(params["ts_code"])]
        return pd.DataFrame(rows).assign(end_date="20200101") if correct_end else pd.DataFrame(rows)

    fetcher = _fetcher(tmp_path, response)
    result, = fetcher.fetch()

    assert result.files_written == 1 and fetcher.holes == ()
    assert [call.kwargs for call in fetcher._client.call.call_args_list] == [
        {"ts_code": code, "fields": NAME_FIELDS} for code in (ORDINARY, HISTORICAL, NAME_ONLY)
    ]
    actual = pd.read_parquet(path)
    expected = pd.DataFrame(retained).assign(end_date="20200101") if correct_end else pd.DataFrame(retained)
    pd.testing.assert_frame_equal(actual.loc[actual.ts_code.eq(NAME_ONLY)].reset_index(drop=True), expected)


@pytest.mark.parametrize("status", ["L", "D"])
@pytest.mark.parametrize("stamp", [None, RUN_DATE.strftime("%Y%m%d")])
def test_name_only_identity_is_not_valid_in_any_stock_snapshot(status, stamp):
    frame = _stocks((NAME_ONLY,), status)
    if stamp is None:
        frame = frame.drop(columns="snapshot_date")
    with pytest.raises(AggregateResponseError, match="invalid security code"):
        validate_stock_basic_snapshot(frame, status=status, snapshot_date=stamp)


@pytest.mark.parametrize("status", ["L", "D"])
def test_name_only_identity_in_raw_stock_response_preserves_pair(tmp_path, monkeypatch, status):
    def response(api, **params):
        if api == "stock_basic" and params["list_status"] == status:
            return _stocks((NAME_ONLY,), status).drop(columns="snapshot_date")
        return _response(api, **params)

    _assert_cli_preserves_snapshots_after_unusable_stock_response(tmp_path, monkeypatch, response)


@pytest.mark.parametrize("status", ["L", "D"])
def test_name_only_identity_in_saved_stock_snapshot_blocks_before_name_calls(tmp_path, status):
    path = _seed(tmp_path, active=(NAME_ONLY if status == "L" else ORDINARY,),
                 delisted=(NAME_ONLY if status == "D" else HISTORICAL,), retained=[_name()])
    fetcher = _fetcher(tmp_path, lambda params: pd.DataFrame([_name(params["ts_code"])]))
    _assert_unusable(fetcher, path, path.read_bytes())
    fetcher._client.call.assert_not_called()


@pytest.mark.parametrize("defect", ["empty", "missing_key", "ordinary", "historical", "conflicting"])
def test_name_only_response_defects_preserve_prior_history_without_fallback(tmp_path, defect):
    retained = [_name(NAME_ONLY), _name(NAME_ONLY, start_date="19990101", ann_date=None)]
    path = _seed(tmp_path, active=(ORDINARY,), delisted=(HISTORICAL,), retained=retained)

    def response(params):
        if params["ts_code"] != NAME_ONLY:
            return pd.DataFrame([_name(params["ts_code"])])
        if defect == "empty":
            return pd.DataFrame(columns=NAME_FIELDS.split(","))
        if defect == "missing_key":
            return pd.DataFrame(retained[:1])
        if defect == "conflicting":
            return pd.DataFrame([*retained, {**retained[0], "end_date": "20200101"}])
        return pd.DataFrame([_name(ORDINARY if defect == "ordinary" else HISTORICAL)])

    fetcher = _fetcher(tmp_path, response)
    _assert_unusable(fetcher, path, path.read_bytes())
    assert [call.kwargs["ts_code"] for call in fetcher._client.call.call_args_list][-1] == NAME_ONLY


@pytest.mark.parametrize("endpoint", GENERIC)
@pytest.mark.parametrize("status", ["L", "D"])
def test_name_only_stock_identity_cannot_reach_any_generic_endpoint(tmp_path, endpoint, status):
    _snapshot(tmp_path)
    path = tmp_path / ("active_stocks.parquet" if status == "L" else "delisted_stocks.parquet")
    pd.read_parquet(path).assign(ts_code=NAME_ONLY).to_parquet(path, index=False)
    _refuses_without_generic_effects(tmp_path, _generic(tmp_path, endpoint))


@pytest.mark.parametrize("requested", [ORDINARY, HISTORICAL])
def test_name_only_response_cannot_substitute_for_a_stock_identity(tmp_path, requested):
    path = _seed(tmp_path, active=(ORDINARY,), delisted=(HISTORICAL,), retained=[_name(NAME_ONLY)])
    fetcher = _fetcher(tmp_path, lambda params: pd.DataFrame([
        _name(NAME_ONLY if params["ts_code"] == requested else params["ts_code"]),
    ]))
    _assert_unusable(fetcher, path, path.read_bytes())
    assert fetcher._client.call.call_args_list[-1].kwargs["ts_code"] == requested
    assert "response contains another security code" in fetcher.holes[0].last_error


@pytest.mark.parametrize("endpoint", GENERIC)
@pytest.mark.parametrize("source", ["forced", "manifest", "cli"])
@pytest.mark.parametrize("year", ["2026", "1999"])
def test_absent_name_only_generic_hole_is_never_silently_healed(tmp_path, monkeypatch, endpoint, source, year):
    _snapshot(tmp_path)  # Valid L/D; X deliberately absent, no earlier stock rejection.
    unit = f"ts_code={NAME_ONLY} year={year}"
    if source != "forced":
        _prior_hole(tmp_path, endpoint, unit)
    if source == "cli":
        cli, client, _, args = _stock_name_cli(tmp_path, monkeypatch, _response)
        args[args.index("--endpoints") + 1] = endpoint
        before = _bytes(tmp_path)
        directories = {path for path in tmp_path.rglob("*") if path.is_dir()}
        assert cli.main(args) == 1
        client.call.assert_not_called()
        assert _bytes(tmp_path) == before
        assert {path for path in tmp_path.rglob("*") if path.is_dir()} == directories
    else:
        forced = frozenset({(endpoint, unit)}) if source == "forced" else frozenset()
        _refuses_without_generic_effects(tmp_path, _generic(tmp_path, endpoint, force_retry_units=forced))


@pytest.mark.parametrize("pending_hole", [False, True])
@pytest.mark.parametrize("empty_historical", [False, True])
def test_default_eight_endpoint_cli_keeps_name_only_identity_out_of_prices(
    tmp_path, monkeypatch, pending_hole, empty_historical,
):
    retained = _name(NAME_ONLY)
    _seed(tmp_path, active=(ORDINARY,), delisted=(HISTORICAL,), retained=[retained])
    if pending_hole:
        _prior_hole(tmp_path, "daily", f"ts_code={NAME_ONLY} year=2026")
    before = (tmp_path / MANIFEST_FILENAME).read_bytes()
    def response(api, **params):
        if empty_historical and api == "namechange" and params["ts_code"] == HISTORICAL:
            return pd.DataFrame(columns=NAME_FIELDS.split(","))
        return _response(api, **params)

    cli, client, _, args = _stock_name_cli(tmp_path, monkeypatch, response)
    index = args.index("--endpoints")
    del args[index:index + 2]
    args[args.index("--start-date") + 1] = START
    args[args.index("--end-date") + 1] = END
    assert cli.main(args + ["--refresh-current", "--namechange-start-date", "20180101"]) == (
        1 if pending_hole else 0
    )
    assert [call.kwargs for call in client.call.call_args_list if call.args[0] == "namechange"] == [
        {"ts_code": code, "fields": NAME_FIELDS} for code in (ORDINARY, HISTORICAL, NAME_ONLY)
    ]
    actual = pd.read_parquet(tmp_path / "all_namechanges.parquet")
    assert actual.loc[actual.ts_code.eq(NAME_ONLY)].to_dict("records") == [retained]
    generic = [call for call in client.call.call_args_list if call.args[0] in GENERIC]
    if pending_hole:
        assert generic == []
        assert (tmp_path / MANIFEST_FILENAME).read_bytes() == before
        assert all(not (tmp_path / endpoint).exists() for endpoint in GENERIC)
    else:
        assert [(call.args[0], call.kwargs["ts_code"]) for call in generic] == [(ep, ORDINARY) for ep in GENERIC]
        manifest = read_manifest(tmp_path / MANIFEST_FILENAME)
        assert set(manifest.endpoints) == set(ENDPOINTS)
        assert all(ep.status == "complete" and not ep.holes for ep in manifest.endpoints.values())
        for endpoint in GENERIC:
            assert not (tmp_path / endpoint / "2026" / f"{NAME_ONLY}.parquet").exists()
            assert manifest.endpoints[endpoint].units_written == 1
