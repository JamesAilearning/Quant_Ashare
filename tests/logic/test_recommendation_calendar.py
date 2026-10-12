"""Offline source-schedule tests: no credentials, SDK calls or qlib provider."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest

import scripts.fetch_recommendation_calendar as cli
import src.data.recommendation_calendar as calendar
from src.contracts.recommendation_timing import CN_TZ


def _clock(value="2026-10-12T10:30:00+08:00"):
    return datetime.fromisoformat(value)


def _document():
    # The explicit fixture is a schedule, not a weekday-derived approximation.
    opens = {"20261008", "20261009", "20261012", "20261013", "20261014", "20261015", "20261016"}
    start = date(2026, 10, 8)
    return {
        "schema_version": 1, "source_api": "trade_cal", "exchange": "SSE",
        "query": {"start_date": "20261008", "end_date": "20261016", "fields": "exchange,cal_date,is_open"},
        "fetched_at": "2026-10-12T10:00:00+08:00",
        "rows": [{"exchange": "SSE", "cal_date": (start + timedelta(days=n)).strftime("%Y%m%d"),
                  "is_open": int((start + timedelta(days=n)).strftime("%Y%m%d") in opens)} for n in range(9)],
    }


def _write_literal(tmp_path, document=None, *, name="schedule.json"):
    path = tmp_path / name
    path.write_text(json.dumps(_document() if document is None else document), encoding="utf-8")
    return path


def _snapshot(tmp_path, *, document=None, now=None):
    return calendar.read_snapshot(_write_literal(tmp_path, document), now=now or _clock())


def test_same_bytes_determine_sha_and_complete_provenance(tmp_path):
    path = _write_literal(tmp_path)
    snapshot = calendar.read_snapshot(path, now=_clock())
    assert snapshot.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert snapshot.context == {
        "path": str(path.absolute()), "sha256": snapshot.sha256, "source_api": "trade_cal", "exchange": "SSE",
        "query": {"start_date": "20261008", "end_date": "20261016", "fields": "exchange,cal_date,is_open"},
        "fetched_at": "2026-10-12T10:00:00+08:00",
    }
    changed = snapshot.context
    changed["query"]["end_date"] = "20991231"
    assert snapshot.context["query"]["end_date"] == "20261016"
    with pytest.raises(FrozenInstanceError):
        snapshot.sha256 = "0" * 64


def test_fetch_is_one_exact_canonical_call_and_does_not_mutate_response():
    document = _document()
    frame = pd.DataFrame(document["rows"])
    original = frame.copy(deep=True)
    client = Mock()
    client.call.return_value = frame
    result = calendar.fetch_snapshot(client, start_date="20261008", end_date="20261016", now=_clock())
    client.call.assert_called_once_with(
        "trade_cal", exchange="SSE", start_date="20261008", end_date="20261016", fields="exchange,cal_date,is_open",
    )
    assert result["rows"] == document["rows"]
    assert result["fetched_at"] == "2026-10-12T10:30:00+08:00"
    pd.testing.assert_frame_equal(frame, original)


def test_vendor_exception_identity_is_preserved_without_retry():
    failure = RuntimeError("opaque vendor failure")
    client = Mock()
    client.call.side_effect = failure
    with pytest.raises(RuntimeError) as caught:
        calendar.fetch_snapshot(client, start_date="20261008", end_date="20261016", now=_clock())
    assert caught.value is failure
    assert client.call.call_count == 1


@pytest.mark.parametrize(("start", "end"), [
    ("2026109", "20261016"), ("２０２６1008", "20261016"), ("20260230", "20261016"),
    ("20261016", "20261008"), ("20200101", "20240101"), (20261008, "20261016"),
])
def test_invalid_query_refuses_before_vendor_call(start, end):
    client = Mock()
    with pytest.raises(calendar.CalendarSnapshotError):
        calendar.fetch_snapshot(client, start_date=start, end_date=end, now=_clock())
    client.call.assert_not_called()


@pytest.mark.parametrize("bad", [True, False, 1.0, 0.0, "1", None, 2, -1, float("nan"), float("inf")])
def test_open_marker_is_strict_integer_not_a_coercion(tmp_path, bad):
    document = _document()
    document["rows"][0]["is_open"] = bad
    with pytest.raises(calendar.CalendarSnapshotError):
        _snapshot(tmp_path, document=document)


@pytest.mark.parametrize("mutation", [
    "missing_day", "duplicate_day", "leading_truncation", "trailing_truncation", "outside_day",
    "bad_date", "unicode_date", "wrong_exchange", "row_extra", "row_missing", "all_closed",
])
def test_invalid_calendar_rows_never_become_arrangement(tmp_path, mutation):
    document = _document()
    rows = document["rows"]
    if mutation == "missing_day":
        rows.pop(4)
    elif mutation == "duplicate_day":
        rows[4] = dict(rows[3])
    elif mutation == "leading_truncation":
        rows.pop(0)
    elif mutation == "trailing_truncation":
        rows.pop()
    elif mutation == "outside_day":
        rows[0]["cal_date"] = "20261007"
    elif mutation == "bad_date":
        rows[0]["cal_date"] = "20261340"
    elif mutation == "unicode_date":
        rows[0]["cal_date"] = "٢٠٢٦1008"
    elif mutation == "wrong_exchange":
        rows[0]["exchange"] = "SZSE"
    elif mutation == "row_extra":
        rows[0]["future_price"] = 1
    elif mutation == "row_missing":
        del rows[0]["exchange"]
    else:
        for row in rows:
            row["is_open"] = 0
    with pytest.raises(calendar.CalendarSnapshotError):
        _snapshot(tmp_path, document=document)


@pytest.mark.parametrize(("key", "value"), [
    ("schema_version", True), ("schema_version", 2), ("source_api", "daily"), ("exchange", "SZSE"),
    ("fetched_at", "2026-10-12T11:00:00+08:00"), ("fetched_at", "2026-10-05T10:29:59+08:00"),
    ("fetched_at", "2026-10-12T10:00:00"), ("fetched_at", None),
])
def test_wrong_source_schema_or_acquisition_time_refuses(tmp_path, key, value):
    document = _document()
    document[key] = value
    with pytest.raises(calendar.CalendarSnapshotError):
        _snapshot(tmp_path, document=document)


@pytest.mark.parametrize("where", ["root", "query", "rows"])
def test_unknown_keys_are_not_silently_ignored(tmp_path, where):
    document = _document()
    target = document if where == "root" else document["query"] if where == "query" else document["rows"][0]
    target["unknown"] = "value"
    with pytest.raises(calendar.CalendarSnapshotError):
        _snapshot(tmp_path, document=document)


def test_exact_seven_day_snapshot_is_still_valid(tmp_path):
    document = _document()
    document["fetched_at"] = "2026-10-05T10:30:00+08:00"
    assert _snapshot(tmp_path, document=document).fetched_at == document["fetched_at"]


def test_read_and_resolution_require_aware_clock(tmp_path):
    path = _write_literal(tmp_path)
    with pytest.raises(ValueError, match="timezone-aware"):
        calendar.read_snapshot(path, now=datetime(2026, 10, 12, 10, 30))
    snapshot = calendar.read_snapshot(path, now=_clock())
    with pytest.raises(ValueError, match="timezone-aware"):
        calendar.resolve_session_dates(["2026-10-08", "2026-10-09"], snapshot, now=date(2026, 10, 12))


@pytest.mark.parametrize("payload", [
    b"", b"\xff", b"{", b'{"schema_version":1,"schema_version":1}',
    b'{"query":{"start_date":"20261008","start_date":"20261008"}}',
    b'{"rows":NaN}', b'{"rows":Infinity}', b'{"rows":-Infinity}',
])
def test_bad_json_or_duplicate_fields_are_rejected(tmp_path, payload):
    path = tmp_path / "bad.json"
    path.write_bytes(payload)
    with pytest.raises(calendar.CalendarSnapshotError):
        calendar.read_snapshot(path, now=_clock())


def test_byte_budget_refuses_before_decode(tmp_path, monkeypatch):
    path = tmp_path / "large.json"
    path.write_bytes(b" " * (1024 * 1024 + 1))
    decode = Mock(side_effect=AssertionError("must not decode"))
    monkeypatch.setattr(calendar, "_decode", decode)
    with pytest.raises(calendar.CalendarSnapshotError, match="byte budget"):
        calendar.read_snapshot(path, now=_clock())
    decode.assert_not_called()


def test_payload_mutated_during_read_is_not_accepted(tmp_path, monkeypatch):
    path = _write_literal(tmp_path)
    original = calendar.os.fstat
    calls = 0

    def changed_stat(fd):
        nonlocal calls
        calls += 1
        result = original(fd)
        if calls == 2:
            path.write_bytes(path.read_bytes() + b" ")
            return original(fd)
        return result

    monkeypatch.setattr(calendar.os, "fstat", changed_stat)
    with pytest.raises(calendar.CalendarSnapshotError, match="changed"):
        calendar.read_snapshot(path, now=_clock())


def test_decoding_cannot_reopen_a_different_snapshot_for_its_sha(tmp_path, monkeypatch):
    path = _write_literal(tmp_path)
    original_bytes = path.read_bytes()
    decode = calendar._decode

    def change_after_read(payload):
        alternate = _document()
        alternate["fetched_at"] = "2026-10-12T10:10:00+08:00"
        path.write_text(json.dumps(alternate), encoding="utf-8")
        return decode(payload)

    monkeypatch.setattr(calendar, "_decode", change_after_read)
    snapshot = calendar.read_snapshot(path, now=_clock())
    assert snapshot.fetched_at == "2026-10-12T10:00:00+08:00"
    assert snapshot.sha256 == hashlib.sha256(original_bytes).hexdigest()
    assert snapshot.sha256 != hashlib.sha256(path.read_bytes()).hexdigest()


def test_hardlinked_snapshot_is_not_an_independent_file(tmp_path):
    path = _write_literal(tmp_path)
    alias = tmp_path / "alias.json"
    os.link(path, alias)
    with pytest.raises(calendar.CalendarSnapshotError, match="single-link"):
        calendar.read_snapshot(path, now=_clock())


def test_new_snapshot_is_published_fsynced_and_never_overwritten(tmp_path, monkeypatch):
    document = _document()
    original = copy.deepcopy(document)
    path = tmp_path / "new" / "schedule.json"
    fsync = Mock(wraps=calendar.os.fsync)
    monkeypatch.setattr(calendar.os, "fsync", fsync)
    pin = calendar.write_snapshot(path, document)
    assert fsync.call_count >= 1
    assert pin == {"size": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    assert calendar.read_snapshot(path, now=_clock()).context["sha256"] == pin["sha256"]
    assert document == original
    saved = path.read_bytes()
    with pytest.raises(FileExistsError):
        calendar.write_snapshot(path, document)
    assert path.read_bytes() == saved
    assert list(path.parent.iterdir()) == [path]


def test_invalid_response_cannot_create_output_directory(tmp_path):
    document = _document()
    document["rows"].pop()
    path = tmp_path / "absent" / "bad.json"
    with pytest.raises(calendar.CalendarSnapshotError):
        calendar.write_snapshot(path, document)
    assert not path.parent.exists()


def test_failed_private_fsync_never_publishes_partial_file(tmp_path, monkeypatch):
    path = tmp_path / "schedule.json"
    monkeypatch.setattr(calendar.os, "fsync", Mock(side_effect=OSError("synthetic fsync failure")))
    with pytest.raises(OSError):
        calendar.write_snapshot(path, _document())
    assert not path.exists()
    assert list(tmp_path.iterdir()) == []


def test_racing_existing_output_is_not_overwritten(tmp_path, monkeypatch):
    path = tmp_path / "schedule.json"
    publication = "rename" if os.name == "nt" else "link"
    original = getattr(calendar.os, publication)

    def competitor(source, destination):
        Path(destination).write_bytes(b"another writer")
        return original(source, destination)

    monkeypatch.setattr(calendar.os, publication, competitor)
    with pytest.raises(FileExistsError):
        calendar.write_snapshot(path, _document())
    assert path.read_bytes() == b"another writer"
    assert list(tmp_path.iterdir()) == [path]


def test_latest_closed_friday_plans_monday_without_future_price_bars(tmp_path):
    snapshot = _snapshot(tmp_path)
    prices = ["2026-10-08", "2026-10-09"]
    assert calendar.resolve_session_dates(prices, snapshot, now=_clock()) == calendar.SessionDates(
        "2026-10-09", "2026-10-12", "2026-10-09",
    )
    assert prices == ["2026-10-08", "2026-10-09"]


@pytest.mark.parametrize("now", ["2026-10-12T15:30:00+08:00", "2026-10-12T16:00:00+08:00"])
def test_conservative_cutoff_uses_today_only_with_today_recorded_prices(tmp_path, now):
    snapshot = _snapshot(tmp_path)
    assert calendar.resolve_session_dates(
        ["2026-10-08", "2026-10-09", "2026-10-12"], snapshot, now=_clock(now),
    ) == calendar.SessionDates("2026-10-12", "2026-10-13", "2026-10-12")
    with pytest.raises(calendar.CalendarSnapshotError, match="latest closed"):
        calendar.resolve_session_dates(["2026-10-08", "2026-10-09"], snapshot, now=_clock(now))


def test_just_before_cutoff_rejects_today_partial_prices_even_for_historical_asof(tmp_path):
    snapshot = _snapshot(tmp_path)
    for explicit in (None, "2026-10-08"):
        with pytest.raises(calendar.CalendarSnapshotError, match="intraday or future"):
            calendar.resolve_session_dates(
                ["2026-10-08", "2026-10-09", "2026-10-12"], snapshot,
                now=_clock("2026-10-12T15:29:59+08:00"), as_of_date=explicit,
            )


def test_weekend_and_holiday_come_from_source_not_weekday_guess(tmp_path):
    document = _document()
    document["fetched_at"] = "2026-10-10T09:00:00+08:00"
    snapshot = _snapshot(tmp_path, document=document, now=_clock("2026-10-10T10:00:00+08:00"))
    assert calendar.resolve_session_dates(
        ["2026-10-08", "2026-10-09"], snapshot, now=_clock("2026-10-10T10:00:00+08:00"),
    ).entry_date == "2026-10-12"
    document["rows"][1]["is_open"] = 0  # Fixture announces Friday closed.
    snapshot = _snapshot(tmp_path, document=document)
    assert calendar.resolve_session_dates(["2026-10-08"], snapshot, now=_clock()).entry_date == "2026-10-12"


def test_aware_utc_clock_is_normalized_to_shanghai(tmp_path):
    snapshot = _snapshot(tmp_path)
    now = datetime(2026, 10, 12, 2, 30, tzinfo=timezone.utc)
    assert calendar.resolve_session_dates(["2026-10-08", "2026-10-09"], snapshot, now=now).entry_date == "2026-10-12"


def test_lagging_default_refuses_but_explicit_recorded_history_is_candidate_recalculation(tmp_path):
    snapshot = _snapshot(tmp_path)
    with pytest.raises(calendar.CalendarSnapshotError, match="latest closed"):
        calendar.resolve_session_dates(["2026-10-08"], snapshot, now=_clock())
    assert calendar.resolve_session_dates(
        ["2026-10-08"], snapshot, now=_clock(), as_of_date="2026-10-08",
    ) == calendar.SessionDates("2026-10-08", "2026-10-09", "2026-10-09")


@pytest.mark.parametrize("prices", [
    [], ["2026-10-09", "2026-10-08"], ["2026-10-08", "2026-10-08"],
    ["2026-10-08", "2026-10-10"], ["2026-10-08", "2026-10-12"],
    ["2026-10-8"], [20261009], [pd.NaT], [pd.Timestamp("2026-10-09T12:00:00")],
    [pd.Timestamp("2026-10-09T00:00:00.000000001")], ["2026-10-07"],
])
def test_price_dates_are_real_ordered_sessions_not_quietly_coerced(tmp_path, prices):
    snapshot = _snapshot(tmp_path)
    with pytest.raises(calendar.CalendarSnapshotError):
        calendar.resolve_session_dates(prices, snapshot, now=_clock())


def test_missing_open_session_in_overlap_is_refused(tmp_path):
    snapshot = _snapshot(tmp_path)
    with pytest.raises(calendar.CalendarSnapshotError, match="overlapping span"):
        calendar.resolve_session_dates(
            ["2026-10-08", "2026-10-12"], snapshot, now=_clock("2026-10-12T16:00:00+08:00"),
        )


def test_date_and_midnight_timestamp_price_calendars_are_normalized(tmp_path):
    snapshot = _snapshot(tmp_path)
    for prices in ([date(2026, 10, 8), date(2026, 10, 9)], pd.to_datetime(["2026-10-08", "2026-10-09"])):
        assert calendar.resolve_session_dates(prices, snapshot, now=_clock()).as_of_date == "2026-10-09"


@pytest.mark.parametrize("asof", ["2026-10-10", "2026-10-12", "2026-10-07", "20261009"])
def test_explicit_asof_must_be_a_real_closed_recorded_price_session(tmp_path, asof):
    snapshot = _snapshot(tmp_path)
    with pytest.raises(calendar.CalendarSnapshotError):
        calendar.resolve_session_dates(["2026-10-08", "2026-10-09"], snapshot, now=_clock(), as_of_date=asof)


def test_missing_clock_date_or_next_open_session_refuses(tmp_path):
    snapshot = _snapshot(tmp_path)
    with pytest.raises(calendar.CalendarSnapshotError, match="clock date"):
        calendar.resolve_session_dates(
            ["2026-10-08", "2026-10-09"], snapshot, now=_clock("2026-10-17T10:00:00+08:00"),
        )
    document = _document()
    for row in document["rows"]:
        if row["cal_date"] >= "20261012":
            row["is_open"] = 0
    snapshot = _snapshot(tmp_path, document=document)
    with pytest.raises(calendar.CalendarSnapshotError, match="next open"):
        calendar.resolve_session_dates(["2026-10-08", "2026-10-09"], snapshot, now=_clock())


def test_cached_snapshot_freshness_is_rechecked_at_resolution(tmp_path):
    snapshot = _snapshot(tmp_path)
    with pytest.raises(calendar.CalendarSnapshotError, match="seven days"):
        calendar.resolve_session_dates(
            ["2026-10-08", "2026-10-09"], snapshot, now=_clock("2026-10-19T10:00:01+08:00"),
        )


def test_cli_explicit_output_one_call_and_schedule_only_receipt(tmp_path, monkeypatch, capsys):
    client = Mock()
    client.call.return_value = pd.DataFrame(_document()["rows"])
    factory = Mock(return_value=client)
    monkeypatch.setattr(cli.TushareClient, "from_environment", factory)
    clock = Mock()
    clock.now.return_value = _clock()
    monkeypatch.setattr(cli, "datetime", clock)
    path = tmp_path / "schedule.json"
    assert cli.main(["--start-date", "20261008", "--end-date", "20261016", "--output", str(path)]) == 0
    assert client.call.call_count == 1
    clock.now.assert_called_once_with(CN_TZ)
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["scope"] == "session_arrangement_only"
    assert receipt["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert "production_accepted" not in receipt


def test_cli_bad_query_does_not_construct_secret_client_or_write(tmp_path, monkeypatch):
    factory = Mock(side_effect=AssertionError("must not construct client"))
    monkeypatch.setattr(cli.TushareClient, "from_environment", factory)
    path = tmp_path / "absent.json"
    assert cli.main(["--start-date", "20260230", "--end-date", "20261016", "--output", str(path)]) == 1
    factory.assert_not_called()
    assert not path.exists()


def test_cli_vendor_failure_is_not_retried_or_echoed(tmp_path, monkeypatch, capsys):
    client = Mock()
    client.call.side_effect = cli.TushareClientError("SECRET_SENTINEL_TOKEN_AND_HTTP_DETAIL")
    monkeypatch.setattr(cli.TushareClient, "from_environment", Mock(return_value=client))
    path = tmp_path / "absent.json"
    assert cli.main(["--start-date", "20261008", "--end-date", "20261016", "--output", str(path)]) == 1
    output = capsys.readouterr()
    assert "SECRET_SENTINEL" not in output.err + output.out
    assert "TushareClientError" in output.err
    assert client.call.call_count == 1
    assert not path.exists()


@pytest.mark.parametrize("args", [[], ["--start-date", "20261008", "--end-date", "20261016"], ["--now", "20261012"]])
def test_cli_has_no_implicit_path_or_clock_injection(args, monkeypatch):
    factory = Mock(side_effect=AssertionError("must not construct client"))
    monkeypatch.setattr(cli.TushareClient, "from_environment", factory)
    with pytest.raises(SystemExit):
        cli.main(args)
    factory.assert_not_called()
