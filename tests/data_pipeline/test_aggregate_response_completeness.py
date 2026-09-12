"""Synthetic aggregate-response safety; no vendor access or production data."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

from src.data.tushare import fetcher as fetcher_module
from src.data.tushare.client import KIND_NETWORK, TushareClientError
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

TARGETS = {
    "namechange": "all_namechanges.parquet",
    "suspend_d": "suspend_d.parquet",
}


def _suspension(trade_date="20180105", **changes):
    return {
        "ts_code": "600000.SH",
        "trade_date": trade_date,
        "suspend_timing": "09:30-15:00",
        "suspend_type": "S",
        **changes,
    }


def _namechange(**changes):
    return {
        "ts_code": "600000.SH",
        "name": "Synthetic name",
        "start_date": "20180105",
        "end_date": None,
        "ann_date": "20180105",
        "change_reason": "更名",
        **changes,
    }


def _seed(root, endpoint, rows, *, start="20180101", end="20180131"):
    path = root / TARGETS[endpoint]
    pd.DataFrame(rows).to_parquet(path, index=False)
    write_manifest(root / MANIFEST_FILENAME, FetchManifest(
        1, "2018-02-01T00:00:00+00:00", {
            endpoint: EndpointCoverage("complete", start, end, 1, ()),
        },
    ))
    return path


def _fetcher(root, endpoint, response, *, start="20180101", end="20180131", **kwargs):
    client = MagicMock()

    def call(api, **params):
        assert api == endpoint
        return response(params).copy(deep=True)

    client.call.side_effect = call
    return TushareFetcher(client, TushareFetcherConfig(
        output_dir=root, endpoints=(endpoint,), start_date=start, end_date=end,
        rate_limit_sleep_ms=0, refresh_current=True, **kwargs,
    ))


def _assert_unusable_preserves_file(fetcher, path, before):
    result, = fetcher.fetch()
    assert result.files_written == result.rows_total == result.units_verified == 0
    assert path.read_bytes() == before
    hole, = fetcher.holes
    assert (hole.endpoint, hole.unit, hole.reason_class) == (
        result.endpoint, "file", "unusable_response",
    )
    return result


def test_wider_suspension_request_cannot_replace_2018_keys_with_2015_slice(tmp_path):
    path = _seed(tmp_path, "suspend_d", [_suspension()])
    before = path.read_bytes()
    candidate = pd.DataFrame([_suspension("20151005")])

    def response(params):
        return candidate[
            candidate["trade_date"].between(params["start_date"], params["end_date"])
        ]

    fetcher = _fetcher(
        tmp_path, "suspend_d", response, suspend_d_start_date="20151001",
    )
    _assert_unusable_preserves_file(fetcher, path, before)


@pytest.mark.parametrize("endpoint", ["namechange", "suspend_d"])
def test_schema_bearing_empty_response_cannot_erase_retained_aggregate(tmp_path, endpoint):
    row = _namechange() if endpoint == "namechange" else _suspension()
    path = _seed(tmp_path, endpoint, [row])
    before = path.read_bytes()
    empty = pd.DataFrame(columns=list(row))
    fetcher = _fetcher(tmp_path, endpoint, lambda params: empty)
    _assert_unusable_preserves_file(fetcher, path, before)


def test_namechange_10000_raw_rows_are_rejected_before_deduplication(tmp_path):
    row = _namechange()
    path = _seed(tmp_path, "namechange", [row])
    before = path.read_bytes()
    saturated = pd.DataFrame([row] * 10_000)
    fetcher = _fetcher(tmp_path, "namechange", lambda params: saturated)
    _assert_unusable_preserves_file(fetcher, path, before)
    fetcher._client.call.assert_called_once()


def _date_response(frame, field):
    def response(params):
        return frame[frame[field].between(params["start_date"], params["end_date"])]

    return response


def _persist_fetch_manifest(root, fetcher, results):
    config = fetcher._config
    path = root / MANIFEST_FILENAME
    current = build_manifest(
        results, fetcher.holes, config.start_date, config.end_date,
        endpoint_start_dates=config.aggregate_start_dates(),
    )
    combined = merge_manifest(read_manifest(path), current)
    write_manifest(path, combined)
    return combined.endpoints[results[0].endpoint]


@pytest.mark.parametrize("failure", ["schema", "transient"])
def test_late_partition_failure_preserves_file_and_coverage_then_retry_heals(
    tmp_path, monkeypatch, failure,
):
    endpoint = "suspend_d"
    old = _suspension("20180105")
    path = _seed(tmp_path, endpoint, [old])
    before = path.read_bytes()
    candidate = pd.DataFrame([old, _suspension("20180201")])
    successful_response = _date_response(candidate, "trade_date")
    monkeypatch.setattr("src.data.tushare.fetcher.time.sleep", lambda _: None)
    writer = MagicMock(wraps=fetcher_module.atomic_write_parquet)
    monkeypatch.setattr(fetcher_module, "atomic_write_parquet", writer)

    def response(params):
        # The January partition really succeeded before February fails.
        if params["start_date"] == "20180201":
            if failure == "transient":
                raise TushareClientError("synthetic network failure", kind=KIND_NETWORK)
            return pd.DataFrame({"ts_code": ["600000.SH"]})
        return successful_response(params)

    fetcher = _fetcher(tmp_path, endpoint, response, end="20180202")
    results = fetcher.fetch()
    assert results[0].files_written == results[0].rows_total == 0
    assert path.read_bytes() == before
    writer.assert_not_called()
    hole, = fetcher.holes
    assert (hole.endpoint, hole.unit, hole.reason_class) == (
        endpoint, "file", "transient" if failure == "transient" else "unusable_response",
    )
    requests = fetcher._client.call.call_args_list
    assert requests[0].kwargs["end_date"] == "20180131"
    assert requests[-1].kwargs["start_date"] == "20180201"
    failed = _persist_fetch_manifest(tmp_path, fetcher, results)
    assert failed.status == "holes" and failed.units_written == 0
    assert (failed.coverage_start_date, failed.coverage_end_date) == ("20180101", "20180131")
    assert failed.holes[0].unit == "file"

    retry = _fetcher(
        tmp_path, endpoint, successful_response, end="20180202",
        force_retry_units=frozenset({(endpoint, "file")}),
    )
    retry_results = retry.fetch()
    assert retry_results[0].files_written == 1
    assert retry.holes == ()
    writer.assert_called_once()
    healed = _persist_fetch_manifest(tmp_path, retry, retry_results)
    assert healed.status == "complete" and healed.holes == ()
    assert (healed.coverage_start_date, healed.coverage_end_date) == ("20180101", "20180202")
    pd.testing.assert_frame_equal(pd.read_parquet(path), candidate)


def test_namechange_end_date_correction_preserves_other_names_with_same_effective_date(tmp_path):
    old = [_namechange(name="First name"), _namechange(name="Second name")]
    path = _seed(tmp_path, "namechange", old)
    candidate = pd.DataFrame([{**old[0], "end_date": "20180201"}, old[1]])
    fetcher = _fetcher(tmp_path, "namechange", lambda params: candidate)
    assert fetcher.fetch()[0].files_written == 1
    assert fetcher.holes == ()
    pd.testing.assert_frame_equal(pd.read_parquet(path), candidate)


@pytest.mark.parametrize("field,value", [
    ("ts_code", "600001.SH"),
    ("name", "Different name"),
    ("start_date", "20180106"),
    ("ann_date", "20180106"),
    ("change_reason", "Different reason"),
])
def test_namechange_changes_outside_end_date_cannot_replace_retained_key(tmp_path, field, value):
    row = _namechange()
    path = _seed(tmp_path, "namechange", [row])
    before = path.read_bytes()
    candidate = pd.DataFrame([{**row, field: value}])
    _assert_unusable_preserves_file(
        _fetcher(tmp_path, "namechange", lambda params: candidate), path, before,
    )


def test_conflicting_end_dates_for_one_namechange_key_do_not_use_last_row(tmp_path):
    row = _namechange()
    path = _seed(tmp_path, "namechange", [row])
    before = path.read_bytes()
    candidate = pd.DataFrame([
        {**row, "end_date": "20180115"}, {**row, "end_date": "20180120"},
    ])
    _assert_unusable_preserves_file(
        _fetcher(tmp_path, "namechange", lambda params: candidate), path, before,
    )


@pytest.mark.parametrize("endpoint", ["namechange", "suspend_d"])
def test_exact_duplicate_aggregate_rows_are_deduplicated_without_losing_null_keys(tmp_path, endpoint):
    row = (_namechange(ann_date=None, change_reason=None) if endpoint == "namechange"
           else _suspension(suspend_timing=None))
    path = _seed(tmp_path, endpoint, [row])
    candidate = pd.DataFrame([row, row])
    fetcher = _fetcher(tmp_path, endpoint, lambda params: candidate)
    result, = fetcher.fetch()
    assert result.files_written == 1 and result.rows_total == 1
    assert fetcher.holes == ()
    pd.testing.assert_frame_equal(pd.read_parquet(path), pd.DataFrame([row]))


def test_namechange_filters_by_announcement_not_effective_date_and_retains_null_announcement(tmp_path):
    rows = [
        _namechange(start_date="20100101", ann_date="20180105"),
        _namechange(name="Unknown announcement", start_date="20120101", ann_date=None),
    ]
    path = _seed(tmp_path, "namechange", rows)
    candidate = pd.DataFrame(rows)
    fetcher = _fetcher(tmp_path, "namechange", lambda params: candidate)
    assert fetcher.fetch()[0].files_written == 1
    assert fetcher.holes == ()
    actual = pd.read_parquet(path)
    pd.testing.assert_frame_equal(actual, candidate)
    assert pd.isna(actual.loc[1, "ann_date"])


def test_out_of_window_announcement_is_not_accepted_using_in_window_effective_date(tmp_path):
    row = _namechange()
    path = _seed(tmp_path, "namechange", [row])
    before = path.read_bytes()
    candidate = pd.DataFrame([row, _namechange(name="Outside", ann_date="20171231")])
    _assert_unusable_preserves_file(
        _fetcher(tmp_path, "namechange", lambda params: candidate), path, before,
    )


@pytest.mark.parametrize("endpoint", ["namechange", "suspend_d"])
@pytest.mark.parametrize("problem", ["missing_column", "extra_column", "duplicate_column", "no_schema"])
def test_malformed_response_schema_never_replaces_valid_old_file(tmp_path, endpoint, problem):
    row = _namechange() if endpoint == "namechange" else _suspension()
    path = _seed(tmp_path, endpoint, [row])
    before = path.read_bytes()
    candidate = pd.DataFrame([row])
    if problem == "missing_column":
        candidate = candidate.drop(columns=[list(row)[-1]])
    elif problem == "extra_column":
        candidate["unexpected"] = "value"
    elif problem == "duplicate_column":
        candidate.columns = ["ts_code", *list(candidate.columns)[1:-1], "ts_code"]
    else:
        candidate = pd.DataFrame()
    _assert_unusable_preserves_file(
        _fetcher(tmp_path, endpoint, lambda params: candidate), path, before,
    )


@pytest.mark.parametrize("endpoint", ["namechange", "suspend_d"])
def test_malformed_retained_schema_cannot_be_silently_repaired_by_refresh(tmp_path, endpoint):
    path = _seed(tmp_path, endpoint, [{"old_history": "unknown provenance shape"}])
    before = path.read_bytes()
    row = _namechange() if endpoint == "namechange" else _suspension()
    candidate = pd.DataFrame([row])
    _assert_unusable_preserves_file(
        _fetcher(tmp_path, endpoint, lambda params: candidate), path, before,
    )


@pytest.mark.parametrize("endpoint,field,value", [
    ("suspend_d", "trade_date", None),
    ("suspend_d", "trade_date", "20180230"),
    ("suspend_d", "trade_date", "20180201"),
    ("suspend_d", "trade_date", "２０１８０１０５"),
    ("namechange", "ann_date", "20180230"),
    ("namechange", "start_date", "20180230"),
    ("namechange", "end_date", "20180230"),
])
def test_invalid_nonnull_dates_and_missing_suspension_dates_are_not_repaired(
    tmp_path, endpoint, field, value,
):
    row = _namechange() if endpoint == "namechange" else _suspension()
    path = _seed(tmp_path, endpoint, [row])
    before = path.read_bytes()
    candidate = pd.DataFrame([row, {**row, field: value}])
    _assert_unusable_preserves_file(
        _fetcher(tmp_path, endpoint, lambda params: candidate), path, before,
    )


@pytest.mark.parametrize("endpoint", ["namechange", "suspend_d"])
def test_schema_bearing_empty_first_acquisition_is_valid(tmp_path, endpoint):
    row = _namechange() if endpoint == "namechange" else _suspension()
    empty = pd.DataFrame(columns=list(row))
    fetcher = _fetcher(tmp_path, endpoint, lambda params: empty)
    result, = fetcher.fetch()
    assert result.files_written == 1 and result.rows_total == 0
    assert fetcher.holes == ()
    pd.testing.assert_frame_equal(pd.read_parquet(tmp_path / TARGETS[endpoint]), empty)


def test_suspension_month_windows_are_clipped_across_year_and_leap_day(tmp_path):
    empty = pd.DataFrame(columns=list(_suspension()))
    fetcher = _fetcher(
        tmp_path, "suspend_d", lambda params: empty, start="20191230", end="20200301",
    )
    assert fetcher.fetch()[0].files_written == 1
    assert fetcher.holes == ()
    windows = [(c.kwargs["start_date"], c.kwargs["end_date"])
               for c in fetcher._client.call.call_args_list]
    assert windows == [
        ("20191230", "20191231"), ("20200101", "20200131"),
        ("20200201", "20200229"), ("20200301", "20200301"),
    ]


@pytest.fixture
def aggregate_limits(monkeypatch):
    # Imported only for the extended tests, not the four pre-fix reproductions.
    from src.data.tushare import aggregate_response

    def configure(**values):
        for key, value in values.items():
            if key == "suspend_guard":
                monkeypatch.setitem(aggregate_response.RESPONSE_ROW_GUARDS, "suspend_d", value)
            else:
                monkeypatch.setattr(aggregate_response, key, value)

    return configure


def test_saturated_suspension_window_bisects_without_losing_parent_keys(
    tmp_path, aggregate_limits, monkeypatch,
):
    aggregate_limits(suspend_guard=2)
    candidate = pd.DataFrame([_suspension("20180101"), _suspension("20180104")])
    fetcher = _fetcher(tmp_path, "suspend_d", _date_response(candidate, "trade_date"), end="20180104")
    writer = MagicMock(wraps=fetcher_module.atomic_write_parquet)
    monkeypatch.setattr(fetcher_module, "atomic_write_parquet", writer)
    result, = fetcher.fetch()
    assert result.files_written == 1 and result.rows_total == 2
    assert fetcher.holes == ()
    writer.assert_called_once()
    windows = [(c.kwargs["start_date"], c.kwargs["end_date"])
               for c in fetcher._client.call.call_args_list]
    assert windows == [("20180101", "20180104"), ("20180101", "20180102"), ("20180103", "20180104")]
    pd.testing.assert_frame_equal(pd.read_parquet(tmp_path / TARGETS["suspend_d"]), candidate)


def test_saturated_parent_key_disappearing_from_children_refuses_publication(tmp_path, aggregate_limits):
    aggregate_limits(suspend_guard=2)
    first, lost = _suspension("20180101"), _suspension("20180104")
    path = _seed(tmp_path, "suspend_d", [first], end="20180104")
    before = path.read_bytes()
    parent = pd.DataFrame([first, lost])
    retained_only = _date_response(pd.DataFrame([first]), "trade_date")

    def response(params):
        if (params["start_date"], params["end_date"]) == ("20180101", "20180104"):
            return parent
        return retained_only(params)

    fetcher = _fetcher(tmp_path, "suspend_d", response, end="20180104")
    _assert_unusable_preserves_file(fetcher, path, before)
    assert fetcher._client.call.call_count == 3


def test_saturated_single_day_is_rejected_before_exact_duplicate_deduplication(tmp_path, aggregate_limits):
    aggregate_limits(suspend_guard=2)
    row = _suspension("20180101")
    path = _seed(tmp_path, "suspend_d", [row], end="20180101")
    before = path.read_bytes()
    fetcher = _fetcher(tmp_path, "suspend_d", lambda params: pd.DataFrame([row, row]), end="20180101")
    _assert_unusable_preserves_file(fetcher, path, before)
    fetcher._client.call.assert_called_once()


def test_5000_row_older_slice_cannot_replace_5000_retained_suspension_records(tmp_path):
    old = [_suspension("20180105", ts_code=f"{i:06d}.SZ") for i in range(5000)]
    path = _seed(tmp_path, "suspend_d", old)
    before = path.read_bytes()
    older_slice = pd.DataFrame([
        _suspension("20151005", ts_code=f"{i:06d}.SZ") for i in range(5000)
    ])
    fetcher = _fetcher(
        tmp_path, "suspend_d", _date_response(older_slice, "trade_date"),
        suspend_d_start_date="20151001",
    )
    _assert_unusable_preserves_file(fetcher, path, before)
    assert fetcher._client.call.call_count < 20  # Stops at unresolved one-day saturation.


@pytest.mark.parametrize("budget", ["MAX_PARTITION_CALLS", "MAX_CANDIDATE_ROWS", "MAX_CANDIDATE_BYTES"])
def test_small_endpoint_budget_refuses_without_partial_publication(tmp_path, aggregate_limits, budget):
    aggregate_limits(**{budget: 1})
    old = _suspension("20180131")
    path = _seed(tmp_path, "suspend_d", [old])
    before = path.read_bytes()
    candidate = pd.DataFrame([old, _suspension("20180201")])
    fetcher = _fetcher(tmp_path, "suspend_d", _date_response(candidate, "trade_date"), end="20180201")
    _assert_unusable_preserves_file(fetcher, path, before)
    assert fetcher._client.call.call_count == (1 if budget != "MAX_CANDIDATE_ROWS" else 2)


def test_saturated_parent_response_also_obeys_memory_budget(tmp_path, aggregate_limits):
    aggregate_limits(suspend_guard=2, MAX_CANDIDATE_BYTES=1)
    row = _suspension("20180101")
    path = _seed(tmp_path, "suspend_d", [row], end="20180104")
    before = path.read_bytes()
    candidate = pd.DataFrame([row, _suspension("20180104")])
    fetcher = _fetcher(tmp_path, "suspend_d", _date_response(candidate, "trade_date"), end="20180104")
    _assert_unusable_preserves_file(fetcher, path, before)
    fetcher._client.call.assert_called_once()


@pytest.mark.parametrize("endpoint", ["namechange", "suspend_d"])
def test_cli_records_failed_empty_refresh_without_advancing_coverage_then_heals(tmp_path, monkeypatch, endpoint):
    script = Path(__file__).resolve().parents[2] / "scripts/data_pipeline/01_fetch_tushare.py"
    spec = importlib.util.spec_from_file_location("aggregate_response_cli", script)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    row = _namechange() if endpoint == "namechange" else _suspension()
    path = _seed(tmp_path, endpoint, [row])
    before = path.read_bytes()
    client = MagicMock()
    client.call.return_value = pd.DataFrame(columns=list(row))
    monkeypatch.setattr(cli, "setup_logging", lambda: None)
    monkeypatch.setattr(cli.TushareClient, "from_environment", lambda: client)
    args = ["--output-dir", str(tmp_path), "--endpoints", endpoint,
            "--start-date", "20180101", "--end-date", "20180202",
            "--refresh-current", "--rate-limit-sleep-ms", "0"]
    for _ in range(2):
        assert cli.main(args) == 3
        assert path.read_bytes() == before
        coverage = read_manifest(tmp_path / MANIFEST_FILENAME).endpoints[endpoint]
        assert coverage.status == "holes" and coverage.units_written == 0
        assert (coverage.coverage_start_date, coverage.coverage_end_date) == ("20180101", "20180131")
        assert [(h.unit, h.reason_class) for h in coverage.holes] == [("file", "unusable_response")]
    frame = pd.DataFrame([row])
    date_column = "ann_date" if endpoint == "namechange" else "trade_date"
    client.call.side_effect = lambda api, **params: _date_response(frame, date_column)(params)
    # The saved file hole, not refresh-current, forces this recovery request.
    args.remove("--refresh-current")
    assert cli.main(args) == 0
    coverage = read_manifest(tmp_path / MANIFEST_FILENAME).endpoints[endpoint]
    assert coverage.status == "complete" and coverage.holes == ()
    assert coverage.coverage_end_date == "20180202"


def test_unreadable_retained_parquet_raises_without_overwriting_file(tmp_path):
    path = _seed(tmp_path, "suspend_d", [_suspension()])
    path.write_bytes(b"not a parquet file")
    before = path.read_bytes()
    fetcher = _fetcher(tmp_path, "suspend_d", lambda params: pd.DataFrame([_suspension()]))
    with pytest.raises(TushareFetcherError, match="unreadable aggregate"):
        fetcher.fetch()
    assert path.read_bytes() == before
    assert fetcher.holes == ()


@pytest.mark.parametrize("response", [None, {}, ["not", "a", "frame"]])
def test_non_dataframe_api_result_cannot_replace_retained_file(tmp_path, response):
    path = _seed(tmp_path, "suspend_d", [_suspension()])
    before = path.read_bytes()
    fetcher = _fetcher(tmp_path, "suspend_d", lambda params: pd.DataFrame([_suspension()]))
    fetcher._client.call.side_effect = None
    fetcher._client.call.return_value = response
    _assert_unusable_preserves_file(fetcher, path, before)
