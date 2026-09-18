"""Synthetic full name-history acquisition; no vendor or production access."""

from __future__ import annotations

import importlib.util
from dataclasses import replace
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.data.tushare import fetcher as fetcher_module
from src.data.tushare.client import KIND_AUTH, KIND_NETWORK, KIND_PARAM, TushareClientError
from src.data.tushare.fetch_manifest import (
    MANIFEST_FILENAME,
    EndpointCoverage,
    FetchManifest,
    build_manifest,
    merge_manifest,
    read_manifest,
    write_manifest,
)
from src.data.tushare.fetch_types import FetchHole
from src.data.tushare.fetcher import TushareFetcher, TushareFetcherConfig, TushareFetcherError
from src.data_pipeline.daily_update import DailyUpdateConfig, build_plan

RUN_DATE = date(2026, 9, 18)
START = "20180101"
END = "20260917"
NAME_FIELDS = "ts_code,name,start_date,end_date,ann_date,change_reason"


def _name(code="000001.SZ", **changes):
    return {
        "ts_code": code,
        "name": "Synthetic name",
        "start_date": "20180105",
        "end_date": None,
        "ann_date": "20180105",
        "change_reason": "更名",
        **changes,
    }


def _stocks(codes, status):
    frame = pd.DataFrame({
        "ts_code": list(codes),
        "symbol": [code.split(".", 1)[0] for code in codes],
        "name": [f"Synthetic {code}" for code in codes],
        "area": ["上海"] * len(codes),
        "industry": ["银行"] * len(codes),
        "market": ["主板"] * len(codes),
        "list_date": ["20000101"] * len(codes),
        "delist_date": ["20220101" if status == "D" else None] * len(codes),
        "list_status": [status] * len(codes),
        "curr_type": ["CNY"] * len(codes),
        "snapshot_date": [RUN_DATE.strftime("%Y%m%d")] * len(codes),
    })
    assert set(frame.columns) == set(fetcher_module.STOCK_BASIC_FIELDS.split(",")) | {"snapshot_date"}
    return frame


def _seed(root, *, active=("000001.SZ",), delisted=("600003.SH",), retained=None):
    _stocks(active, "L").to_parquet(root / "active_stocks.parquet", index=False)
    _stocks(delisted, "D").to_parquet(root / "delisted_stocks.parquet", index=False)
    coverage = {"stock_basic": EndpointCoverage("complete", START, END, 2, ())}
    if retained is not None:
        pd.DataFrame(retained).to_parquet(root / "all_namechanges.parquet", index=False)
        coverage["namechange"] = EndpointCoverage("complete", START, END, 1, ())
    write_manifest(root / MANIFEST_FILENAME, FetchManifest(
        1, "2026-09-18T00:00:00+00:00", coverage,
    ))
    return root / "all_namechanges.parquet"


def _fetcher(root, response, **overrides):
    client = MagicMock()

    def call(api, **params):
        assert api == "namechange"
        return response(params).copy(deep=True)

    client.call.side_effect = call
    config = {
        "output_dir": root,
        "endpoints": ("namechange",),
        "start_date": START,
        "end_date": END,
        "now": RUN_DATE,
        "rate_limit_sleep_ms": 0,
        "refresh_current": True,
        "namechange_mode": "per_security_full",
        **overrides,
    }
    return TushareFetcher(client, TushareFetcherConfig(**config))


def _persist_manifest(root, fetcher, results):
    config = fetcher._config
    path = root / MANIFEST_FILENAME
    current = build_manifest(
        results, fetcher.holes, config.start_date, config.end_date,
        endpoint_start_dates=config.aggregate_start_dates(),
    )
    merged = merge_manifest(read_manifest(path), current)
    write_manifest(path, merged)
    return merged.endpoints["namechange"]


def test_historical_id_and_ordinary_code_refresh_and_query_as_distinct_identities(tmp_path, monkeypatch):
    historical = "T600018.SH"
    ordinary = "600018.SH"
    _seed(tmp_path, active=(ordinary,), retained=[_name(historical)])

    def response(api, **params):
        if api == "stock_basic":
            status = params["list_status"]
            code = ordinary if status == "L" else historical
            return _stocks((code,), status).drop(columns="snapshot_date")
        return pd.DataFrame([_name(params["ts_code"])])

    cli, client, writer, args = _stock_name_cli(tmp_path, monkeypatch, response)

    assert cli.main(args + ["--refresh-current"]) == 0

    assert [request.args[1].name for request in writer.call_args_list] == [
        "active_stocks.parquet", "delisted_stocks.parquet", "all_namechanges.parquet",
    ]
    for filename, code, status in (
        ("active_stocks.parquet", ordinary, "L"),
        ("delisted_stocks.parquet", historical, "D"),
    ):
        pd.testing.assert_frame_equal(pd.read_parquet(tmp_path / filename), _stocks((code,), status))
    assert [request.kwargs for request in client.call.call_args_list if request.args[0] == "namechange"] == [
        {"ts_code": code, "fields": NAME_FIELDS} for code in (ordinary, historical)
    ]
    assert set(pd.read_parquet(tmp_path / "all_namechanges.parquet")["ts_code"]) == {ordinary, historical}
    for endpoint in ("stock_basic", "namechange"):
        coverage = read_manifest(tmp_path / MANIFEST_FILENAME).endpoints[endpoint]
        assert coverage.status == "complete" and coverage.holes == ()


@pytest.mark.parametrize("in_delisted", [True, False])
def test_historical_id_in_saved_snapshot_or_retained_only_is_queried_unchanged(tmp_path, in_delisted):
    path = _seed(
        tmp_path, active=("600018.SH",),
        delisted=("T600018.SH",) if in_delisted else ("600003.SH",),
        retained=[_name("T600018.SH")],
    )
    fetcher = _fetcher(tmp_path, lambda params: pd.DataFrame([_name(params["ts_code"])]))

    result, = fetcher.fetch()

    expected = ["600018.SH", "T600018.SH"] if in_delisted else ["600003.SH", "600018.SH", "T600018.SH"]
    assert result.files_written == 1 and fetcher.holes == ()
    assert [request.kwargs["ts_code"] for request in fetcher._client.call.call_args_list] == expected
    assert set(pd.read_parquet(path)["ts_code"]) == set(expected)


def test_historical_id_in_raw_listed_bucket_preserves_pair_and_blocks_name_calls(tmp_path, monkeypatch):
    def response(api, **params):
        if api == "stock_basic" and params["list_status"] == "L":
            return _stocks(("T600018.SH",), "L").drop(columns="snapshot_date")
        return _stock_and_names(api, **params)

    _assert_cli_preserves_snapshots_after_unusable_stock_response(tmp_path, monkeypatch, response)


def test_historical_id_in_saved_listed_bucket_blocks_before_name_queries(tmp_path):
    path = _seed(tmp_path, active=("T600018.SH",), retained=[_name()])
    before = path.read_bytes()
    fetcher = _fetcher(tmp_path, lambda params: pd.DataFrame([_name(params["ts_code"])]))

    _assert_unusable(fetcher, path, before)

    fetcher._client.call.assert_not_called()


@pytest.mark.parametrize("requested,returned", [
    ("T600018.SH", "600018.SH"), ("600018.SH", "T600018.SH"),
])
def test_historical_and_ordinary_responses_cannot_substitute_for_each_other(tmp_path, requested, returned):
    path = _seed(tmp_path, active=("600018.SH",), delisted=("T600018.SH",), retained=[_name(requested)])
    before = path.read_bytes()
    fetcher = _fetcher(tmp_path, lambda params: pd.DataFrame([
        _name(returned if params["ts_code"] == requested else params["ts_code"]),
    ]))

    _assert_unusable(fetcher, path, before)

    assert requested in [request.kwargs["ts_code"] for request in fetcher._client.call.call_args_list]
    assert "response contains another security code" in fetcher.holes[0].last_error


@pytest.mark.parametrize("has_retained", [True, False])
def test_historical_empty_response_cannot_be_filled_by_ordinary_identity(tmp_path, has_retained):
    path = _seed(
        tmp_path, active=("600018.SH",), delisted=("T600018.SH",),
        retained=[_name("T600018.SH")] if has_retained else None,
    )
    before = path.read_bytes() if has_retained else None
    fetcher = _fetcher(tmp_path, lambda params: (
        pd.DataFrame(columns=NAME_FIELDS.split(",")) if params["ts_code"] == "T600018.SH"
        else pd.DataFrame([_name(params["ts_code"])])
    ))

    results = fetcher.fetch()

    assert [request.kwargs["ts_code"] for request in fetcher._client.call.call_args_list] == [
        "600018.SH", "T600018.SH",
    ]
    if has_retained:
        assert results[0].files_written == 0 and path.read_bytes() == before
        assert "loses 1 retained business keys" in fetcher.holes[0].last_error
    else:
        assert results[0].files_written == 1 and fetcher.holes == ()
        assert list(pd.read_parquet(path)["ts_code"]) == ["600018.SH"]


@pytest.mark.parametrize("code", [
    "T600019.SH", "T600018.SZ", "T600018.BJ", "T00018.SH", "t600018.SH",
    "T600018.sh", " T600018.SH", "T600018.SH ", "T６０００１８.SH", "T600018.SH\n",
])
def test_unregistered_historical_ids_reject_in_all_full_history_contexts(code):
    from src.data.tushare.aggregate_response import AggregateResponseError
    from src.data.tushare.namechange_history import (
        collect_namechange_history,
        namechange_security_universe,
        validate_stock_basic_snapshot,
    )

    for status in ("L", "D"):
        frame = _stocks((code,), status)
        for stamp in (None, RUN_DATE.strftime("%Y%m%d")):
            with pytest.raises(AggregateResponseError, match="invalid security code"):
                validate_stock_basic_snapshot(frame, status=status, snapshot_date=stamp)
    with pytest.raises(AggregateResponseError, match="invalid security code"):
        namechange_security_universe(
            _stocks(("600018.SH",), "L"), _stocks(("T600018.SH",), "D"),
            pd.DataFrame([_name(code)]), snapshot_date=RUN_DATE.strftime("%Y%m%d"),
        )
    call = MagicMock()
    with pytest.raises(AggregateResponseError, match="invalid security code"):
        collect_namechange_history((code,), call=call)
    call.assert_not_called()


def test_full_mode_queries_sorted_active_delisted_and_retained_union_without_dates(tmp_path):
    retained = _name("800001.BJ")
    path = _seed(
        tmp_path, active=("600002.SH", "000001.SZ"), retained=[retained],
    )
    fetcher = _fetcher(tmp_path, lambda params: pd.DataFrame([_name(params["ts_code"])]))

    result, = fetcher.fetch()

    assert result.files_written == 1 and result.rows_total == 4
    assert fetcher.holes == ()
    assert [request.kwargs for request in fetcher._client.call.call_args_list] == [
        {"ts_code": code, "fields": NAME_FIELDS}
        for code in ("000001.SZ", "600002.SH", "600003.SH", "800001.BJ")
    ]
    assert set(pd.read_parquet(path)["ts_code"]) == {
        "000001.SZ", "600002.SH", "600003.SH", "800001.BJ",
    }


def test_full_history_preserves_null_announcements_and_rows_outside_requested_envelope(tmp_path):
    path = _seed(tmp_path)
    rows = [
        _name(name="Null announcement", start_date="19900101", ann_date=None),
        _name(name="Before envelope", start_date="20000101", ann_date="19991231"),
        _name(name="After envelope", start_date="20261001", ann_date="20260920"),
    ]
    candidate = pd.DataFrame(rows)
    fetcher = _fetcher(tmp_path, lambda params: (
        candidate if params["ts_code"] == "000001.SZ"
        else pd.DataFrame(columns=NAME_FIELDS.split(","))
    ))

    results = fetcher.fetch()

    assert results[0].files_written == 1 and results[0].rows_total == 3
    pd.testing.assert_frame_equal(pd.read_parquet(path), candidate)
    assert pd.isna(pd.read_parquet(path).loc[0, "ann_date"])
    coverage = _persist_manifest(tmp_path, fetcher, results)
    assert (coverage.coverage_start_date, coverage.coverage_end_date) == (START, END)
    assert coverage.status == "complete"
    assert [request.kwargs for request in fetcher._client.call.call_args_list] == [
        {"ts_code": code, "fields": NAME_FIELDS} for code in ("000001.SZ", "600003.SH")
    ]


def test_late_wrong_code_response_keeps_old_bytes_and_coverage_without_partial_write(
    tmp_path, monkeypatch,
):
    old = _name()
    path = _seed(tmp_path, retained=[old])
    before = path.read_bytes()
    writer = MagicMock(wraps=fetcher_module.atomic_write_parquet)
    monkeypatch.setattr(fetcher_module, "atomic_write_parquet", writer)

    def response(params):
        return pd.DataFrame([old if params["ts_code"] == "000001.SZ" else _name("999999.SZ")])

    fetcher = _fetcher(tmp_path, response, end_date="20260918")
    results = fetcher.fetch()

    assert results[0].files_written == results[0].rows_total == results[0].units_verified == 0
    assert path.read_bytes() == before
    writer.assert_not_called()
    assert fetcher._client.call.call_count == 2
    hole, = fetcher.holes
    assert (hole.endpoint, hole.unit, hole.reason_class) == (
        "namechange", "file", "unusable_response",
    )
    coverage = _persist_manifest(tmp_path, fetcher, results)
    assert coverage.status == "holes"
    assert (coverage.coverage_start_date, coverage.coverage_end_date) == (START, END)


def test_existing_aggregate_cannot_blind_resume_into_full_mode_without_refresh(tmp_path):
    path = _seed(tmp_path, retained=[_name()])
    before = path.read_bytes()
    manifest_before = (tmp_path / MANIFEST_FILENAME).read_bytes()
    fetcher = _fetcher(
        tmp_path, lambda params: pd.DataFrame([_name(params["ts_code"])]),
        refresh_current=False,
    )

    with pytest.raises(TushareFetcherError, match="refresh"):
        fetcher.fetch()

    fetcher._client.call.assert_not_called()
    assert path.read_bytes() == before
    assert (tmp_path / MANIFEST_FILENAME).read_bytes() == manifest_before


def _assert_unusable(fetcher, path, before):
    result = fetcher.fetch()[-1]
    assert result.endpoint == "namechange"
    assert result.files_written == result.rows_total == result.units_verified == 0
    assert path.read_bytes() == before
    holes = [hole for hole in fetcher.holes if hole.endpoint == "namechange"]
    assert [(hole.unit, hole.reason_class) for hole in holes] == [("file", "unusable_response")]


def _stock_hole(root):
    manifest_path = root / MANIFEST_FILENAME
    manifest = read_manifest(manifest_path)
    assert manifest is not None
    manifest.endpoints["stock_basic"] = EndpointCoverage(
        "holes", START, END, 0,
        (FetchHole("stock_basic", "list_status=D (delisted_stocks)", "transient", 3, "offline"),),
    )
    write_manifest(manifest_path, manifest)


@pytest.mark.parametrize("bucket,problem", [
    ("active_stocks", "stale"), ("delisted_stocks", "stale"),
    ("active_stocks", "mixed_date"), ("active_stocks", "null_date"),
    ("active_stocks", "missing_date"), ("active_stocks", "wrong_status"),
    ("delisted_stocks", "wrong_status"), ("active_stocks", "null_code"),
    ("active_stocks", "invalid_code"), ("active_stocks", "duplicate_code"),
    ("active_stocks", "unicode_code"), ("active_stocks", "empty"),
    ("delisted_stocks", "overlap"),
])
def test_invalid_stock_snapshot_refuses_before_name_queries(tmp_path, bucket, problem):
    path = _seed(tmp_path, active=("000001.SZ", "600002.SH"), retained=[_name()])
    before = path.read_bytes()
    snapshot_path = tmp_path / f"{bucket}.parquet"
    frame = pd.read_parquet(snapshot_path)
    if problem == "stale":
        frame["snapshot_date"] = "20260917"
    elif problem == "mixed_date":
        frame.loc[0, "snapshot_date"] = "20260917"
    elif problem == "null_date":
        frame.loc[0, "snapshot_date"] = None
    elif problem == "missing_date":
        frame = frame.drop(columns=["snapshot_date"])
    elif problem == "wrong_status":
        frame["list_status"] = "P"
    elif problem in {"null_code", "invalid_code", "unicode_code"}:
        frame.loc[0, "ts_code"] = {
            "null_code": None, "invalid_code": "600001.HK", "unicode_code": "０００００１.SZ",
        }[problem]
    elif problem == "duplicate_code":
        frame.loc[1, "ts_code"] = frame.loc[0, "ts_code"]
    elif problem == "empty":
        frame = frame.iloc[:0]
    else:
        frame.loc[0, "ts_code"] = "000001.SZ"
    frame.to_parquet(snapshot_path, index=False)
    fetcher = _fetcher(tmp_path, lambda params: pd.DataFrame([_name(params["ts_code"])]))

    _assert_unusable(fetcher, path, before)

    fetcher._client.call.assert_not_called()


@pytest.mark.parametrize("bucket", ["active_stocks", "delisted_stocks"])
@pytest.mark.parametrize("missing_field", fetcher_module.STOCK_BASIC_FIELDS.split(","))
def test_missing_stock_producer_field_refuses_names_without_write_or_coverage_advance(
    tmp_path, monkeypatch, bucket, missing_field,
):
    path = _seed(tmp_path, retained=[_name()])
    before = path.read_bytes()
    manifest_before = (tmp_path / MANIFEST_FILENAME).read_bytes()
    snapshot = tmp_path / f"{bucket}.parquet"
    pd.read_parquet(snapshot).drop(columns=[missing_field]).to_parquet(snapshot, index=False)
    writer = MagicMock(wraps=fetcher_module.atomic_write_parquet)
    monkeypatch.setattr(fetcher_module, "atomic_write_parquet", writer)
    fetcher = _fetcher(
        tmp_path, lambda params: pd.DataFrame([_name(params["ts_code"])]), end_date="20260918",
    )

    results = fetcher.fetch()

    result, = results
    assert result.files_written == result.rows_total == result.units_verified == 0
    fetcher._client.call.assert_not_called()
    writer.assert_not_called()
    assert path.read_bytes() == before
    assert (tmp_path / MANIFEST_FILENAME).read_bytes() == manifest_before
    assert [(hole.endpoint, hole.unit, hole.reason_class) for hole in fetcher.holes] == [
        ("namechange", "file", "unusable_response"),
    ]
    coverage = _persist_manifest(tmp_path, fetcher, results)
    assert coverage.status == "holes" and coverage.units_written == 0
    assert (coverage.coverage_start_date, coverage.coverage_end_date) == (START, END)


@pytest.mark.parametrize("bucket", ["active_stocks", "delisted_stocks"])
@pytest.mark.parametrize("problem", ["missing", "unreadable"])
def test_unreadable_stock_prerequisite_hard_aborts_without_name_queries(tmp_path, bucket, problem):
    path = _seed(tmp_path, retained=[_name()])
    before = path.read_bytes()
    snapshot = tmp_path / f"{bucket}.parquet"
    if problem == "missing":
        snapshot.unlink()
    else:
        snapshot.write_bytes(b"invalid parquet")
    fetcher = _fetcher(tmp_path, lambda params: pd.DataFrame([_name(params["ts_code"])]))

    with pytest.raises(TushareFetcherError, match="stock_basic"):
        fetcher.fetch()

    assert path.read_bytes() == before and fetcher.holes == ()
    fetcher._client.call.assert_not_called()


@pytest.mark.parametrize("problem", ["missing", "corrupt", "missing_endpoint", "empty_coverage"])
def test_unproven_stock_snapshots_cannot_create_first_full_history(tmp_path, problem):
    path = _seed(tmp_path)
    manifest_path = tmp_path / MANIFEST_FILENAME
    if problem == "missing":
        manifest_path.unlink()
    elif problem == "corrupt":
        manifest_path.write_text("not json", encoding="utf-8")
    else:
        manifest = read_manifest(manifest_path)
        assert manifest is not None
        if problem == "missing_endpoint":
            manifest.endpoints.clear()
        else:
            manifest.endpoints["stock_basic"] = EndpointCoverage("complete", "", "", 0, ())
        write_manifest(manifest_path, manifest)
    before = manifest_path.read_bytes() if manifest_path.exists() else None
    fetcher = _fetcher(tmp_path, lambda params: pd.DataFrame([_name(params["ts_code"])]))

    with pytest.raises(TushareFetcherError, match="provenance"):
        fetcher.fetch()

    assert not path.exists()
    assert (manifest_path.read_bytes() if manifest_path.exists() else None) == before
    fetcher._client.call.assert_not_called()


def test_prior_stock_hole_blocks_even_current_dated_snapshot_files(tmp_path):
    path = _seed(tmp_path, retained=[_name()])
    _stock_hole(tmp_path)
    before = path.read_bytes()
    fetcher = _fetcher(tmp_path, lambda params: pd.DataFrame([_name(params["ts_code"])]))

    _assert_unusable(fetcher, path, before)

    fetcher._client.call.assert_not_called()


def _stock_and_names(api, **params):
    if api == "stock_basic":
        codes = ("000001.SZ",) if params["list_status"] == "L" else ("600003.SH",)
        return _stocks(codes, params["list_status"]).drop(columns=["snapshot_date"])
    assert api == "namechange"
    return pd.DataFrame([_name(params["ts_code"])])


def test_two_fresh_stock_buckets_supersede_prior_stock_holes(tmp_path):
    path = _seed(tmp_path, retained=[_name()])
    _stock_hole(tmp_path)
    fetcher = _fetcher(tmp_path, lambda params: None, endpoints=("stock_basic", "namechange"))
    fetcher._client.call.side_effect = _stock_and_names

    results = fetcher.fetch()

    assert [result.files_written for result in results] == [2, 1]
    assert fetcher.holes == ()
    assert set(pd.read_parquet(path)["ts_code"]) == {"000001.SZ", "600003.SH"}
    assert [request.args[0] for request in fetcher._client.call.call_args_list] == [
        "stock_basic", "stock_basic", "namechange", "namechange",
    ]


def test_one_refreshed_bucket_cannot_hide_prior_stock_hole(tmp_path):
    path = _seed(tmp_path, retained=[_name()])
    _stock_hole(tmp_path)
    before = path.read_bytes()
    fetcher = _fetcher(
        tmp_path, lambda params: None, endpoints=("stock_basic", "namechange"),
        refresh_current=False,
        force_retry_units=frozenset({("stock_basic", "list_status=L (active_stocks)"),
                                     ("namechange", "file")}),
    )
    fetcher._client.call.side_effect = _stock_and_names

    _assert_unusable(fetcher, path, before)

    assert [request.args[0] for request in fetcher._client.call.call_args_list] == ["stock_basic"]
    assert fetcher._stock_basic_refreshed == {"L"}  # The skipped D bucket is not fresh evidence.


def test_current_stock_failure_blocks_names_despite_complete_prior_provenance(tmp_path, monkeypatch):
    path = _seed(tmp_path, retained=[_name()])
    before = path.read_bytes()
    fetcher = _fetcher(tmp_path, lambda params: None, endpoints=("stock_basic", "namechange"))
    monkeypatch.setattr(fetcher_module.time, "sleep", lambda _: None)

    def response(api, **params):
        assert api == "stock_basic"
        if params["list_status"] == "D":
            raise TushareClientError("synthetic offline", kind=KIND_NETWORK)
        return _stock_and_names(api, **params)

    fetcher._client.call.side_effect = response

    _assert_unusable(fetcher, path, before)

    assert any(hole.endpoint == "stock_basic" and hole.reason_class == "transient"
               for hole in fetcher.holes)
    assert all(request.args[0] == "stock_basic" for request in fetcher._client.call.call_args_list)


def test_reused_fetcher_cannot_carry_successful_stock_refresh_into_next_run(tmp_path):
    _seed(tmp_path, retained=[_name()])
    _stock_hole(tmp_path)
    fetcher = _fetcher(tmp_path, lambda params: None, endpoints=("stock_basic", "namechange"))
    fetcher._client.call.side_effect = _stock_and_names
    assert fetcher.fetch()[-1].files_written == 1
    path = tmp_path / "all_namechanges.parquet"
    before = path.read_bytes()
    fetcher._config = replace(fetcher._config, endpoints=("namechange",))
    fetcher._client.call.reset_mock()

    _assert_unusable(fetcher, path, before)

    fetcher._client.call.assert_not_called()


@pytest.mark.parametrize("problem", ["schema", "invalid_code", "conflicting_key", "invalid_date"])
def test_malformed_retained_history_is_refused_before_any_name_query(tmp_path, problem):
    rows = [_name()]
    if problem == "schema":
        rows = [{"old_history": "unknown"}]
    elif problem == "invalid_code":
        rows = [_name("invalid")]
    elif problem == "conflicting_key":
        rows += [_name(end_date="20200101")]
    else:
        rows = [_name(ann_date="20260230")]
    path = _seed(tmp_path, retained=rows)
    before = path.read_bytes()
    fetcher = _fetcher(tmp_path, lambda params: pd.DataFrame([_name(params["ts_code"])]))

    _assert_unusable(fetcher, path, before)

    fetcher._client.call.assert_not_called()


def test_unreadable_retained_history_hard_aborts_before_name_queries(tmp_path):
    path = _seed(tmp_path, retained=[_name()])
    path.write_bytes(b"invalid parquet")
    before = path.read_bytes()
    fetcher = _fetcher(tmp_path, lambda params: pd.DataFrame([_name(params["ts_code"])]))

    with pytest.raises(TushareFetcherError, match="unreadable aggregate"):
        fetcher.fetch()

    assert path.read_bytes() == before
    fetcher._client.call.assert_not_called()


@pytest.mark.parametrize("retained", [False, True])
def test_empty_responses_only_publish_when_no_retained_history_is_lost(tmp_path, retained):
    path = _seed(tmp_path, retained=[_name()] if retained else None)
    empty = pd.DataFrame(columns=NAME_FIELDS.split(","))
    fetcher = _fetcher(tmp_path, lambda params: empty)
    if retained:
        _assert_unusable(fetcher, path, path.read_bytes())
    else:
        result, = fetcher.fetch()
        assert result.files_written == 1 and result.rows_total == 0
        pd.testing.assert_frame_equal(pd.read_parquet(path), empty)
    assert fetcher._client.call.call_count == 2


def test_end_date_correction_and_exact_duplicates_preserve_nullable_business_keys(tmp_path):
    old = _name(ann_date=None, change_reason=None)
    path = _seed(tmp_path, retained=[old])
    corrected = {**old, "end_date": "20200101"}
    fetcher = _fetcher(tmp_path, lambda params: (
        pd.DataFrame([corrected, corrected]) if params["ts_code"] == "000001.SZ"
        else pd.DataFrame(columns=NAME_FIELDS.split(","))
    ))

    result, = fetcher.fetch()

    assert result.files_written == 1 and result.rows_total == 1
    pd.testing.assert_frame_equal(pd.read_parquet(path), pd.DataFrame([corrected]))
    assert fetcher._client.call.call_count == 2


@pytest.mark.parametrize("problem", ["schema", "bad_date", "conflicting_key", "changed_key", "non_frame"])
def test_late_invalid_response_never_publishes_earlier_codes(tmp_path, problem):
    old = _name("600003.SH")
    path = _seed(tmp_path, retained=[old])
    before = path.read_bytes()
    fetcher = _fetcher(tmp_path, lambda params: None)

    def response(api, **params):
        if params["ts_code"] == "000001.SZ":
            return pd.DataFrame([_name()])
        if problem == "schema":
            return pd.DataFrame({"ts_code": ["600003.SH"]})
        if problem == "bad_date":
            return pd.DataFrame([{**old, "start_date": "20260230"}])
        if problem == "conflicting_key":
            return pd.DataFrame([old, {**old, "end_date": "20200101"}])
        if problem == "changed_key":
            return pd.DataFrame([{**old, "change_reason": "different reason"}])
        return None

    fetcher._client.call.side_effect = response

    _assert_unusable(fetcher, path, before)

    assert fetcher._client.call.call_count == 2


@pytest.mark.parametrize("kind", [KIND_NETWORK, KIND_AUTH, KIND_PARAM])
def test_late_api_errors_keep_existing_classification_and_never_fall_back(tmp_path, monkeypatch, kind):
    path = _seed(tmp_path, retained=[_name()])
    before = path.read_bytes()
    monkeypatch.setattr(fetcher_module.time, "sleep", lambda _: None)
    fetcher = _fetcher(tmp_path, lambda params: None)

    def response(api, **params):
        assert set(params) == {"ts_code", "fields"}
        if params["ts_code"] == "000001.SZ":
            return pd.DataFrame([_name()])
        raise TushareClientError("synthetic classified failure", kind=kind)

    fetcher._client.call.side_effect = response
    if kind == KIND_NETWORK:
        result, = fetcher.fetch()
        assert result.files_written == 0
        assert [(hole.unit, hole.reason_class) for hole in fetcher.holes] == [("file", "transient")]
    else:
        with pytest.raises((TushareFetcherError, TushareClientError)):
            fetcher.fetch()
        assert fetcher.holes == ()
        assert fetcher._client.call.call_count == 2
    assert path.read_bytes() == before


def test_raw_response_cap_is_checked_before_duplicate_elimination(tmp_path, monkeypatch):
    from src.data.tushare import aggregate_response

    path = _seed(tmp_path, retained=[_name()])
    before = path.read_bytes()
    monkeypatch.setitem(aggregate_response.RESPONSE_ROW_GUARDS, "namechange", 2)
    fetcher = _fetcher(tmp_path, lambda params: pd.DataFrame([_name(), _name()]))

    _assert_unusable(fetcher, path, before)

    fetcher._client.call.assert_called_once()


@pytest.mark.parametrize("budget", ["MAX_NAMECHANGE_SECURITIES", "STOCK_BASIC_ROW_GUARD"])
def test_frozen_universe_limits_refuse_before_name_calls(tmp_path, monkeypatch, budget):
    from src.data.tushare import namechange_history

    path = _seed(tmp_path, retained=[_name()])
    before = path.read_bytes()
    monkeypatch.setattr(namechange_history, budget, 1)
    fetcher = _fetcher(tmp_path, lambda params: pd.DataFrame([_name(params["ts_code"])]))

    _assert_unusable(fetcher, path, before)

    fetcher._client.call.assert_not_called()


@pytest.mark.parametrize("budget", ["MAX_CANDIDATE_ROWS", "MAX_CANDIDATE_BYTES"])
def test_cumulative_candidate_budget_rejects_second_response_without_partial_result(monkeypatch, budget):
    from src.data.tushare import aggregate_response, namechange_history

    first, second = pd.DataFrame([_name()]), pd.DataFrame([_name("600003.SH")])
    if budget == "MAX_CANDIDATE_ROWS":
        limit = 1
    else:
        limit = max(int(frame.memory_usage(index=True, deep=True).sum()) for frame in (first, second))
    monkeypatch.setattr(aggregate_response, budget, limit)
    call = MagicMock(side_effect=[first, second])

    with pytest.raises(aggregate_response.AggregateResponseError, match="budget"):
        namechange_history.collect_namechange_history(("000001.SZ", "600003.SH"), call=call)

    assert call.call_count == 2


@pytest.mark.parametrize("refresh", [False, True])
def test_full_mode_dry_run_leaves_existing_data_and_manifest_untouched(tmp_path, refresh):
    path = _seed(tmp_path, retained=[_name()])
    before = {file: file.read_bytes() for file in tmp_path.iterdir()}
    fetcher = _fetcher(tmp_path, lambda params: None, dry_run=True, refresh_current=refresh)

    result, = fetcher.fetch()

    assert result.files_written == 0 and result.skipped == int(not refresh)
    assert path.exists()
    assert {file: file.read_bytes() for file in tmp_path.iterdir()} == before
    fetcher._client.call.assert_not_called()


def test_new_full_mode_dry_run_needs_no_prerequisites_and_creates_no_directory(tmp_path):
    root = tmp_path / "absent"
    fetcher = _fetcher(root, lambda params: None, dry_run=True)
    assert fetcher.fetch()[0].files_written == 0
    assert not root.exists()
    fetcher._client.call.assert_not_called()


def test_legacy_date_range_still_blind_skips_without_full_mode_prerequisites(tmp_path):
    path = tmp_path / "all_namechanges.parquet"
    path.write_bytes(b"legacy existence checkpoint")
    fetcher = _fetcher(tmp_path, lambda params: None, namechange_mode="date_range", refresh_current=False)
    result, = fetcher.fetch()
    assert result.skipped == 1 and result.files_written == 0
    assert path.read_bytes() == b"legacy existence checkpoint"
    fetcher._client.call.assert_not_called()


def _fetch_cli():
    script = Path(__file__).resolve().parents[2] / "scripts/data_pipeline/01_fetch_tushare.py"
    spec = importlib.util.spec_from_file_location("namechange_per_security_cli", script)
    assert spec is not None and spec.loader is not None
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    return cli


def _fetch_args(root):
    return ["--output-dir", str(root), "--endpoints", "namechange",
            "--start-date", START, "--end-date", "20260918", "--snapshot-date", "20260918",
            "--namechange-mode", "per_security_full", "--rate-limit-sleep-ms", "0"]


def _daily_config(root, **kwargs):
    return DailyUpdateConfig(
        tushare_dir=root / "raw", provider_dir=root / "provider",
        delisted_registry=root / "registry.parquet", reference_cases=root / "reference.yaml",
        now=RUN_DATE, end_date=END, **kwargs,
    )


def _daily_args(root):
    return ["--tushare-dir", str(root / "raw"), "--provider-dir", str(root / "provider"),
            "--delisted-registry", str(root / "registry.parquet"),
            "--reference-cases", str(root / "reference.yaml"), "--end-date", END]


def test_cli_failed_refresh_keeps_coverage_then_saved_hole_forces_complete_retry(tmp_path, monkeypatch):
    old = _name("800001.BJ", ann_date=None)
    path = _seed(tmp_path, retained=[old])
    before = path.read_bytes()
    cli = _fetch_cli()
    client = MagicMock()
    client.call.return_value = pd.DataFrame(columns=NAME_FIELDS.split(","))
    monkeypatch.setattr(cli, "setup_logging", lambda: None)
    monkeypatch.setattr(cli.TushareClient, "from_environment", lambda: client)
    writer = MagicMock(wraps=fetcher_module.atomic_write_parquet)
    monkeypatch.setattr(fetcher_module, "atomic_write_parquet", writer)
    args = _fetch_args(tmp_path)
    for _ in range(2):
        assert cli.main(args + ["--refresh-current"]) == 3
        assert path.read_bytes() == before
        coverage = read_manifest(tmp_path / MANIFEST_FILENAME).endpoints["namechange"]
        assert coverage.status == "holes" and coverage.units_written == 0
        assert (coverage.coverage_start_date, coverage.coverage_end_date) == (START, END)
        assert [(hole.unit, hole.reason_class) for hole in coverage.holes] == [
            ("file", "unusable_response"),
        ]
    writer.assert_not_called()
    client.reset_mock()
    client.call.side_effect = lambda api, **params: pd.DataFrame([
        old if params["ts_code"] == "800001.BJ" else _name(params["ts_code"]),
    ])

    assert cli.main(args) == 0  # Prior file hole forces retry without refresh-current.

    writer.assert_called_once()
    coverage = read_manifest(tmp_path / MANIFEST_FILENAME).endpoints["namechange"]
    assert coverage.status == "complete" and coverage.holes == ()
    assert (coverage.coverage_start_date, coverage.coverage_end_date) == (START, "20260918")
    assert [request.kwargs["ts_code"] for request in client.call.call_args_list] == [
        "000001.SZ", "600003.SH", "800001.BJ",
    ]


def _assert_cli_preserves_snapshots_after_unusable_stock_response(tmp_path, monkeypatch, response):
    _seed(tmp_path, retained=[_name()])
    protected = ("active_stocks.parquet", "delisted_stocks.parquet", "all_namechanges.parquet")
    before = {name: (tmp_path / name).read_bytes() for name in protected}
    cli = _fetch_cli()
    client = MagicMock()
    client.call.side_effect = response
    monkeypatch.setattr(cli, "setup_logging", lambda: None)
    monkeypatch.setattr(cli.TushareClient, "from_environment", lambda: client)
    writer = MagicMock(wraps=fetcher_module.atomic_write_parquet)
    monkeypatch.setattr(fetcher_module, "atomic_write_parquet", writer)
    args = _fetch_args(tmp_path)
    args[args.index("--endpoints") + 1] = "stock_basic,namechange"

    assert cli.main(args + ["--refresh-current"]) == 3

    assert {name: (tmp_path / name).read_bytes() for name in protected} == before
    writer.assert_not_called()
    assert all(request.args[0] == "stock_basic" for request in client.call.call_args_list)
    manifest = read_manifest(tmp_path / MANIFEST_FILENAME)
    assert manifest is not None
    for endpoint in ("stock_basic", "namechange"):
        coverage = manifest.endpoints[endpoint]
        assert coverage.status == "holes" and coverage.holes
        assert coverage.units_written == coverage.units_verified == 0
        assert (coverage.coverage_start_date, coverage.coverage_end_date) == (START, END)


@pytest.mark.parametrize("status", ["L", "D"])
@pytest.mark.parametrize("problem", [
    "empty", "saturated", "wrong_status", "duplicate_code", "invalid_code", "null_code",
    *[f"missing:{field}" for field in fetcher_module.STOCK_BASIC_FIELDS.split(",")],
])
def test_full_cli_invalid_stock_response_preserves_both_snapshots_and_records_holes(
    tmp_path, monkeypatch, status, problem,
):
    from src.data.tushare import namechange_history

    if problem == "saturated":
        monkeypatch.setattr(namechange_history, "STOCK_BASIC_ROW_GUARD", 2)

    def response(api, **params):
        frame = _stock_and_names(api, **params)
        if api != "stock_basic" or params["list_status"] != status:
            return frame
        if problem == "empty":
            return frame.iloc[:0]
        if problem == "saturated":
            codes = ("000001.SZ", "000002.SZ") if status == "L" else ("600003.SH", "600004.SH")
            return _stocks(codes, status).drop(columns="snapshot_date")
        if problem == "wrong_status":
            return frame.assign(list_status="P")
        if problem == "duplicate_code":
            return pd.concat([frame, frame], ignore_index=True)
        if problem in {"invalid_code", "null_code"}:
            return frame.assign(ts_code=None if problem == "null_code" else "000001.HK")
        return frame.drop(columns=problem.split(":", 1)[1])

    _assert_cli_preserves_snapshots_after_unusable_stock_response(tmp_path, monkeypatch, response)


def test_full_cli_overlapping_stock_buckets_preserve_old_pair_and_cannot_be_complete(
    tmp_path, monkeypatch,
):
    def response(api, **params):
        if api == "stock_basic" and params["list_status"] == "D":
            return _stocks(("000001.SZ",), "D").drop(columns="snapshot_date")
        return _stock_and_names(api, **params)

    _assert_cli_preserves_snapshots_after_unusable_stock_response(tmp_path, monkeypatch, response)


def _stock_name_cli(root, monkeypatch, response):
    cli = _fetch_cli()
    client = MagicMock()
    client.call.side_effect = response
    monkeypatch.setattr(cli, "setup_logging", lambda: None)
    monkeypatch.setattr(cli.TushareClient, "from_environment", lambda: client)
    writer = MagicMock(wraps=fetcher_module.atomic_write_parquet)
    monkeypatch.setattr(fetcher_module, "atomic_write_parquet", writer)
    args = _fetch_args(root)
    args[args.index("--endpoints") + 1] = "stock_basic,namechange"
    return cli, client, writer, args


@pytest.mark.parametrize("failed_status", ["L", "D"])
def test_cli_stock_api_hole_withholds_pair_then_retries_both_without_refresh(
    tmp_path, monkeypatch, failed_status,
):
    _seed(tmp_path, retained=[_name()])
    protected = ("active_stocks.parquet", "delisted_stocks.parquet", "all_namechanges.parquet")
    before = {name: (tmp_path / name).read_bytes() for name in protected}
    monkeypatch.setattr(fetcher_module.time, "sleep", lambda _: None)

    def response(api, **params):
        if api == "stock_basic" and params["list_status"] == failed_status:
            raise TushareClientError("synthetic offline", kind=KIND_NETWORK)
        return _stock_and_names(api, **params)

    cli, client, writer, args = _stock_name_cli(tmp_path, monkeypatch, response)

    assert cli.main(args + ["--refresh-current"]) == 3

    writer.assert_not_called()
    assert {name: (tmp_path / name).read_bytes() for name in protected} == before
    coverage = read_manifest(tmp_path / MANIFEST_FILENAME).endpoints["stock_basic"]
    assert coverage.status == "holes" and coverage.units_written == coverage.units_verified == 0
    holes = {hole.unit: hole for hole in coverage.holes}
    assert set(holes) == {"list_status=L (active_stocks)", "list_status=D (delisted_stocks)"}
    for status, label in (("L", "active_stocks"), ("D", "delisted_stocks")):
        hole = holes[f"list_status={status} ({label})"]
        if status == failed_status:
            assert hole.reason_class == "transient"
            assert hole.attempts == fetcher_module.MAX_RATE_LIMIT_RETRIES
            assert "synthetic offline" in hole.last_error
        else:
            assert hole.reason_class == "unusable_response" and hole.attempts == 0
    assert all(request.args[0] == "stock_basic" for request in client.call.call_args_list)
    assert sum(request.kwargs["list_status"] == failed_status
               for request in client.call.call_args_list) == fetcher_module.MAX_RATE_LIMIT_RETRIES
    name_coverage = read_manifest(tmp_path / MANIFEST_FILENAME).endpoints["namechange"]
    assert name_coverage.status == "holes" and name_coverage.units_written == 0
    client.reset_mock()
    client.call.side_effect = _stock_and_names

    assert cli.main(args) == 0  # Saved holes must force BOTH stock files past exists-skip.

    assert [(request.args[0], request.kwargs.get("list_status", request.kwargs.get("ts_code")))
            for request in client.call.call_args_list] == [
        ("stock_basic", "L"), ("stock_basic", "D"),
        ("namechange", "000001.SZ"), ("namechange", "600003.SH"),
    ]
    assert [request.args[1].name for request in writer.call_args_list] == list(protected)
    manifest = read_manifest(tmp_path / MANIFEST_FILENAME)
    for endpoint, written in (("stock_basic", 2), ("namechange", 1)):
        coverage = manifest.endpoints[endpoint]
        assert coverage.status == "complete" and coverage.holes == ()
        assert coverage.units_written == written and coverage.units_verified == 0
        assert (coverage.coverage_start_date, coverage.coverage_end_date) == (START, "20260918")
    assert set(pd.read_parquet(tmp_path / "all_namechanges.parquet")["ts_code"]) == {
        "000001.SZ", "600003.SH",
    }


@pytest.mark.parametrize("problem", ["overlap", "stale_skipped"])
def test_cli_pending_bucket_must_validate_retained_partner_before_publishing(
    tmp_path, monkeypatch, problem,
):
    _seed(tmp_path, retained=[_name()])
    _stock_hole(tmp_path)  # Only D is pending; L remains an exists-skipped snapshot.
    manifest_path = tmp_path / MANIFEST_FILENAME
    manifest = read_manifest(manifest_path)
    manifest.endpoints["namechange"] = EndpointCoverage(
        "holes", START, END, 0,
        (FetchHole("namechange", "file", "unusable_response", 1, "prior stock hole"),),
    )
    write_manifest(manifest_path, manifest)
    if problem == "stale_skipped":
        _stocks(("000001.SZ",), "L").assign(snapshot_date="20260917").to_parquet(
            tmp_path / "active_stocks.parquet", index=False,
        )
    protected = ("active_stocks.parquet", "delisted_stocks.parquet", "all_namechanges.parquet")
    before = {name: (tmp_path / name).read_bytes() for name in protected}

    def response(api, **params):
        if api == "stock_basic" and problem == "overlap":
            return _stocks(("000001.SZ",), "D").drop(columns="snapshot_date")
        return _stock_and_names(api, **params)

    cli, client, writer, args = _stock_name_cli(tmp_path, monkeypatch, response)

    assert cli.main(args) == 3

    writer.assert_not_called()
    assert {name: (tmp_path / name).read_bytes() for name in protected} == before
    assert [(request.args[0], request.kwargs["list_status"])
            for request in client.call.call_args_list] == [("stock_basic", "D")]
    coverage = read_manifest(manifest_path).endpoints["stock_basic"]
    assert coverage.status == "holes" and coverage.units_written == coverage.units_verified == 0
    assert {hole.unit for hole in coverage.holes} == {
        "list_status=L (active_stocks)", "list_status=D (delisted_stocks)",
    }
    assert all(hole.reason_class == "unusable_response" for hole in coverage.holes)


@pytest.mark.parametrize("invalid_status", ["L", "D"])
def test_cli_first_stock_pair_with_invalid_bucket_publishes_neither_file(
    tmp_path, monkeypatch, invalid_status,
):
    root = tmp_path / "fresh"

    def response(api, **params):
        frame = _stock_and_names(api, **params)
        return frame.iloc[:0] if api == "stock_basic" and params["list_status"] == invalid_status else frame

    cli, client, writer, args = _stock_name_cli(root, monkeypatch, response)

    assert cli.main(args) == 3

    writer.assert_not_called()
    assert not any((root / filename).exists() for filename in (
        "active_stocks.parquet", "delisted_stocks.parquet", "all_namechanges.parquet",
    ))
    assert all(request.args[0] == "stock_basic" for request in client.call.call_args_list)
    manifest = read_manifest(root / MANIFEST_FILENAME)
    for endpoint, count in (("stock_basic", 2), ("namechange", 1)):
        coverage = manifest.endpoints[endpoint]
        assert coverage.status == "holes" and len(coverage.holes) == count
        assert coverage.units_written == coverage.units_verified == 0


def test_second_stock_publication_failure_hard_aborts_without_claiming_pair_refresh(tmp_path, monkeypatch):
    _seed(tmp_path, retained=[_name()])
    protected = ("active_stocks.parquet", "delisted_stocks.parquet", "all_namechanges.parquet", MANIFEST_FILENAME)
    before = {name: (tmp_path / name).read_bytes() for name in protected}
    fetcher = _fetcher(tmp_path, lambda params: None, endpoints=("stock_basic", "namechange"))

    def response(api, **params):
        frame = _stock_and_names(api, **params)
        return frame.assign(name="Fresh stock snapshot") if api == "stock_basic" else frame

    fetcher._client.call.side_effect = response
    real_write = fetcher_module.atomic_write_parquet

    def fail_second_write(frame, path):
        if path.name == "delisted_stocks.parquet":
            raise OSError("synthetic second publication failure")
        return real_write(frame, path)

    writer = MagicMock(side_effect=fail_second_write)
    monkeypatch.setattr(fetcher_module, "atomic_write_parquet", writer)

    with pytest.raises(OSError, match="synthetic second publication failure"):
        fetcher.fetch()

    assert [request.args[1].name for request in writer.call_args_list] == list(protected[:2])
    # Per-file atomic publication is NOT a two-file transaction: L already changed.
    assert (tmp_path / "active_stocks.parquet").read_bytes() != before["active_stocks.parquet"]
    assert {name: (tmp_path / name).read_bytes() for name in protected[1:]} == {
        name: before[name] for name in protected[1:]
    }
    assert fetcher._stock_basic_refreshed == set()
    assert all(request.args[0] == "stock_basic" for request in fetcher._client.call.call_args_list)


@pytest.mark.parametrize("mode", ["date_range", "per_security_full"])
@pytest.mark.parametrize("dry_run", [False, True])
def test_stock_blind_skip_and_dry_run_do_not_become_verified_snapshot_reads(tmp_path, monkeypatch, mode, dry_run):
    for filename in ("active_stocks.parquet", "delisted_stocks.parquet"):
        (tmp_path / filename).write_bytes(b"unverified legacy checkpoint")
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    fetcher = _fetcher(
        tmp_path, lambda params: None, endpoints=("stock_basic",),
        namechange_mode=mode, refresh_current=dry_run, dry_run=dry_run,
    )
    reader = MagicMock(side_effect=AssertionError("blind skip must not read the old parquet"))
    writer = MagicMock(side_effect=AssertionError("blind skip and dry-run must not write"))
    monkeypatch.setattr(fetcher_module.pd, "read_parquet", reader)
    monkeypatch.setattr(fetcher_module, "atomic_write_parquet", writer)

    result, = fetcher.fetch()

    assert result.files_written == result.rows_total == result.units_verified == 0
    assert result.skipped == (0 if dry_run else 2)
    assert fetcher.holes == () and fetcher._stock_basic_refreshed == set()
    fetcher._client.call.assert_not_called()
    reader.assert_not_called()
    writer.assert_not_called()
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before


@pytest.mark.parametrize("bad", ["unknown", "PER_SECURITY_FULL", "", None, 1, True, []])
def test_public_configs_reject_invalid_mode_before_creating_artifacts(tmp_path, bad):
    with pytest.raises(TushareFetcherError, match="namechange_mode"):
        TushareFetcherConfig(output_dir=tmp_path / "raw", namechange_mode=bad)
    with pytest.raises(ValueError, match="namechange_mode"):
        _daily_config(tmp_path, namechange_mode=bad)
    assert not list(tmp_path.iterdir())


def test_fetch_cli_invalid_mode_cannot_reset_manifest_or_construct_client(tmp_path):
    _seed(tmp_path, retained=[_name()])
    before = {path: path.read_bytes() for path in tmp_path.iterdir()}
    cli = _fetch_cli()
    args = _fetch_args(tmp_path)
    args[args.index("--namechange-mode") + 1] = "unknown"
    with patch.object(cli, "setup_logging"), patch.object(cli, "clear_manifest") as clear, \
            patch.object(cli.TushareClient, "from_environment") as factory:
        with pytest.raises(SystemExit) as exc:
            cli.main(args + ["--reset-manifest"])
    assert exc.value.code == 2
    clear.assert_not_called()
    factory.assert_not_called()
    assert {path: path.read_bytes() for path in tmp_path.iterdir()} == before


def test_daily_cli_invalid_mode_cannot_lock_or_run_or_write_status(tmp_path):
    from scripts import daily_update as cli

    status = tmp_path / "status.json"
    status.write_text("previous status", encoding="utf-8")
    with patch.object(cli, "setup_logging"), patch.object(cli, "single_flight") as lock, \
            patch.object(cli, "run_daily_update") as run:
        with pytest.raises(SystemExit) as exc:
            cli.main(_daily_args(tmp_path) + [
                "--status-path", str(status), "--namechange-mode", "unknown",
            ])
    assert exc.value.code == 2
    lock.assert_not_called()
    run.assert_not_called()
    assert status.read_text(encoding="utf-8") == "previous status"
    assert list(tmp_path.iterdir()) == [status]


def test_daily_plan_explicit_full_mode_reaches_real_fetch_parser_only(tmp_path):
    plan = build_plan(_daily_config(tmp_path, namechange_mode="per_security_full"))
    parsed = _fetch_cli()._build_arg_parser().parse_args(plan.fetch)
    assert parsed.namechange_mode == "per_security_full"
    assert parsed.snapshot_date == "20260918" and parsed.end_date == END
    assert parsed.start_date == START and parsed.refresh_current is True
    assert "--namechange-mode" not in plan.benchmark
    legacy = build_plan(_daily_config(tmp_path))
    assert "--namechange-mode" not in legacy.fetch
    assert _fetch_cli()._build_arg_parser().parse_args(legacy.fetch).namechange_mode == "date_range"
    assert not list(tmp_path.iterdir())


def test_daily_cli_dry_run_forwards_full_mode_without_running_stages(tmp_path):
    from scripts import daily_update as cli
    from src.data_pipeline import daily_update as orchestration

    with patch.object(cli, "setup_logging"), patch.object(cli, "single_flight") as lock, \
            patch.object(orchestration, "_logger") as logger, \
            patch.object(orchestration, "_load_script_main") as stage, \
            patch.object(orchestration, "_record_status") as status:
        assert cli.main(_daily_args(tmp_path) + [
            "--namechange-mode", "per_security_full", "--dry-run",
        ]) == 0
    lock.assert_not_called()
    stage.assert_not_called()
    status.assert_not_called()
    plans = {call.args[1]: call.args[2] for call in logger.info.call_args_list
             if call.args[0] == "  [dry-run] %s: %s"}
    assert "--namechange-mode per_security_full" in plans["fetch"]
    assert "--namechange-mode" not in plans["benchmark"]
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("overrides", [
    {"start_date": "20180230"},
    {"end_date": "20260230"},
    {"end_date": "20260229"},
])
def test_full_mode_rejects_impossible_operational_dates_before_any_data_operation(tmp_path, overrides):
    with pytest.raises(TushareFetcherError):
        TushareFetcherConfig(
            output_dir=tmp_path / "absent", namechange_mode="per_security_full",
            endpoints=("namechange",), now=RUN_DATE,
            **{"start_date": START, "end_date": END, **overrides},
        )
    assert not list(tmp_path.iterdir())


def test_full_mode_freezes_both_stock_snapshots_at_fetch_start_across_midnight(tmp_path, monkeypatch):
    class Clock(date):
        current = date(2026, 9, 17)

        @classmethod
        def today(cls):
            return cls.current

    monkeypatch.setattr(fetcher_module, "date", Clock)
    fetcher = _fetcher(
        tmp_path, lambda params: None, endpoints=("stock_basic", "namechange"), now=None,
    )
    Clock.current = RUN_DATE  # Fetch starts after construction, on a new day.

    def response(api, **params):
        if api == "stock_basic" and params["list_status"] == "L":
            Clock.current = date(2026, 9, 19)  # First call spans midnight.
        return _stock_and_names(api, **params)

    fetcher._client.call.side_effect = response

    results = fetcher.fetch()

    assert [result.files_written for result in results] == [2, 1]
    assert fetcher.holes == ()
    for filename in ("active_stocks.parquet", "delisted_stocks.parquet"):
        assert set(pd.read_parquet(tmp_path / filename)["snapshot_date"]) == {"20260918"}
    assert [request.kwargs["ts_code"] for request in fetcher._client.call.call_args_list
            if request.args[0] == "namechange"] == ["000001.SZ", "600003.SH"]


def test_empty_delisted_snapshot_cannot_attest_freshness_or_authorize_full_history(tmp_path):
    path = _seed(tmp_path, delisted=(), retained=[_name()])
    before = path.read_bytes()
    fetcher = _fetcher(tmp_path, lambda params: pd.DataFrame([_name(params["ts_code"])]))

    _assert_unusable(fetcher, path, before)

    fetcher._client.call.assert_not_called()


@pytest.mark.parametrize("overrides", [
    {"start_date": "20180230"},
    {"end_date": "20260230"},
    {"end_date": "20260229"},
])
def test_daily_full_mode_rejects_impossible_operational_dates_at_configuration(tmp_path, overrides):
    config = _daily_config(tmp_path, namechange_mode="per_security_full")

    with pytest.raises(ValueError):
        replace(config, **overrides)

    assert not list(tmp_path.iterdir())


def test_current_empty_delisted_response_blocks_name_queries_despite_prior_complete_snapshot(tmp_path):
    path = _seed(tmp_path, retained=[_name()])
    before = path.read_bytes()
    fetcher = _fetcher(tmp_path, lambda params: None, endpoints=("stock_basic", "namechange"))

    def response(api, **params):
        assert api == "stock_basic"
        if params["list_status"] == "D":
            return _stocks((), "D").drop(columns=["snapshot_date"])
        return _stock_and_names(api, **params)

    fetcher._client.call.side_effect = response

    _assert_unusable(fetcher, path, before)

    assert [request.kwargs["list_status"] for request in fetcher._client.call.call_args_list] == ["L", "D"]


def test_exact_security_budget_queries_unique_union_and_publishes_once(tmp_path, monkeypatch):
    from src.data.tushare import namechange_history

    path = _seed(tmp_path, retained=[_name()])
    monkeypatch.setattr(namechange_history, "MAX_NAMECHANGE_SECURITIES", 2)
    writer = MagicMock(wraps=fetcher_module.atomic_write_parquet)
    monkeypatch.setattr(fetcher_module, "atomic_write_parquet", writer)
    fetcher = _fetcher(tmp_path, lambda params: pd.DataFrame([_name(params["ts_code"])]))

    result, = fetcher.fetch()

    assert result.files_written == 1 and result.rows_total == 2
    assert fetcher.holes == ()
    writer.assert_called_once()
    assert [request.kwargs["ts_code"] for request in fetcher._client.call.call_args_list] == [
        "000001.SZ", "600003.SH",
    ]
    assert set(pd.read_parquet(path)["ts_code"]) == {"000001.SZ", "600003.SH"}
