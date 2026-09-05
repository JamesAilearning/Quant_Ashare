"""Both ends of scanned year-file reuse; all network responses are synthetic."""

from __future__ import annotations

import importlib
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.data.tushare.client import TushareClientError
from src.data.tushare.fetcher import TushareFetcher, TushareFetcherConfig, TushareFetcherError

ENDPOINTS = ("daily", "adj_factor", "daily_basic")
TICKER = "600000.SH"


def _seed(root, endpoint, dates, *, listed="20000101", delisted=None, year="2025"):
    pd.DataFrame({"ts_code": [TICKER], "list_date": [listed], "delist_date": [delisted]}).to_parquet(
        root / "active_stocks.parquet", index=False,
    )
    pd.DataFrame(columns=["ts_code", "list_date", "delist_date"]).to_parquet(
        root / "delisted_stocks.parquet", index=False,
    )
    path = root / endpoint / year / f"{TICKER}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"trade_date": dates, "ts_code": [TICKER] * len(dates)}).to_parquet(path, index=False)
    return path


def _fetcher(root, endpoint, *, start="20250101", end="20251231", calendar="weekdays", response=None, **kwargs):
    def call(api, **params):
        if api == "trade_cal":
            if calendar is None:
                raise TushareClientError("calendar unavailable", kind="param")
            dates = (pd.bdate_range(params["start_date"], params["end_date"]).strftime("%Y%m%d")
                     if calendar == "weekdays" else calendar)
            return pd.DataFrame({"cal_date": dates})
        dates = [params["start_date"], params["end_date"]] if response is None else response
        return pd.DataFrame({"ts_code": [TICKER] * len(dates), "trade_date": dates})

    client = MagicMock()
    client.call.side_effect = call
    return TushareFetcher(client, TushareFetcherConfig(
        output_dir=root, endpoints=(endpoint,), start_date=start, end_date=end,
        rate_limit_sleep_ms=0, **kwargs,
    ))


def _data_calls(fetcher):
    return [c for c in fetcher._client.call.call_args_list if c.args[0] != "trade_cal"]


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_fresh_tail_cannot_verify_missing_leading_history(tmp_path, endpoint):
    path = _seed(tmp_path, endpoint, ["20250701", "20251231"])
    fetcher = _fetcher(tmp_path, endpoint)
    result, = fetcher.fetch()
    assert result.units_verified == result.skipped == 0
    assert result.files_written == 1
    assert len(_data_calls(fetcher)) == 1
    assert _data_calls(fetcher)[0].kwargs["start_date"] == "20250101"
    assert pd.read_parquet(path)["trade_date"].min() == "20250101"
    assert fetcher.holes == ()


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_head_short_refetch_cannot_erase_a_wider_stored_tail(tmp_path, endpoint):
    path = _seed(tmp_path, endpoint, ["20250701", "20251231"])
    before = path.read_bytes()
    fetcher = _fetcher(tmp_path, endpoint, end="20250930")
    result, = fetcher.fetch()
    assert result.units_verified == result.files_written == result.skipped == 0
    assert _data_calls(fetcher) == []
    assert path.read_bytes() == before
    hole, = fetcher.holes
    assert (hole.reason_class, hole.attempts, hole.unit) == (
        "unsafe_overwrite", 0, f"ts_code={TICKER} year=2025",
    )


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_wider_valid_same_year_history_remains_reusable(tmp_path, endpoint):
    path = _seed(tmp_path, endpoint, ["20250101", "20251231"])
    before = path.read_bytes()
    fetcher = _fetcher(tmp_path, endpoint, start="20250701", end="20250930")
    result, = fetcher.fetch()
    assert result.units_verified == result.skipped == 1
    assert result.files_written == 0
    assert _data_calls(fetcher) == []
    assert path.read_bytes() == before
    assert fetcher.holes == ()


@pytest.mark.parametrize("endpoint", ENDPOINTS)
@pytest.mark.parametrize("bad", [None, "20250230", "２０２５０１０１", "2025011", "2025-01-01", "20250101 ",
                                  "20241231", "20260101"])
def test_any_invalid_or_wrong_year_date_prevents_positive_reuse(tmp_path, endpoint, bad):
    path = _seed(tmp_path, endpoint, ["20250101", "20251231", bad])
    before = path.read_bytes()
    fetcher = _fetcher(tmp_path, endpoint, end="20250930")
    result, = fetcher.fetch()
    assert result.units_verified == result.files_written == 0
    assert path.read_bytes() == before
    assert _data_calls(fetcher) == []
    assert fetcher.holes[0].reason_class == "unsafe_overwrite"


@pytest.mark.parametrize("listed,delisted,start,end,dates,calendar", [
    ("20250701", None, "20250101", "20251231", ["20250701", "20251231"], "weekdays"),
    ("20250705", None, "20250101", "20251231", ["20250707", "20251231"], "weekdays"),
    ("20250101", None, "20250101", "20251231", ["20250102", "20251231"], ["20250102", "20251231"]),
    ("20000101", "20250705", "20250101", "20251231", ["20250101", "20250704"], "weekdays"),
    ("20000101", None, "20250705", "20250706", ["20250101", "20251231"], []),
    ("20000101", None, "20250705", "20250707", ["20250707"], None),
    ("20260101", None, "20250101", "20251231", [], "weekdays"),
    ("20000101", "20241231", "20250101", "20251231", [], "weekdays"),
])
def test_listing_and_calendar_bounds_preserve_legitimate_reuse(
    tmp_path, listed, delisted, start, end, dates, calendar,
):
    path = _seed(tmp_path, "daily", dates, listed=listed, delisted=delisted)
    before = path.read_bytes()
    fetcher = _fetcher(tmp_path, "daily", start=start, end=end, calendar=calendar)
    result, = fetcher.fetch()
    assert result.units_verified == result.skipped == 1
    assert _data_calls(fetcher) == []
    assert path.read_bytes() == before


@pytest.mark.parametrize("listed,delisted", [
    ("20250431", None), ("２０２５０７０１", None), ("00000000", None),
    (None, None), ("", None), ("20251201", "20250101"),
])
@pytest.mark.parametrize("calendar", [None, "weekdays"])
def test_malformed_listing_bounds_do_not_hide_short_heads_or_crash(tmp_path, listed, delisted, calendar):
    _seed(tmp_path, "daily", ["20250701", "20251231"], listed=listed, delisted=delisted)
    fetcher = _fetcher(tmp_path, "daily", calendar=calendar)
    result, = fetcher.fetch()
    assert result.units_verified == 0
    assert result.files_written == 1
    assert len(_data_calls(fetcher)) == 1


def test_head_short_failure_preserves_history_and_remains_retryable(tmp_path):
    path = _seed(tmp_path, "daily", ["20250701", "20251231"])
    before = path.read_bytes()
    fetcher = _fetcher(tmp_path, "daily")
    original = fetcher._client.call.side_effect

    def call(api, **params):
        if api != "trade_cal":
            raise TushareClientError("rate limit exceeded")
        return original(api, **params)

    fetcher._client.call.side_effect = call
    with patch("src.data.tushare.fetcher.time.sleep"):
        for _ in range(2):
            result, = fetcher.fetch()
            assert result.files_written == result.units_verified == 0
            assert path.read_bytes() == before
            assert len(fetcher.holes) == 1
    assert len(_data_calls(fetcher)) > 1


def test_persistent_vendor_head_shortfall_does_not_invent_systemic_policy(tmp_path):
    _seed(tmp_path, "daily", ["20250701", "20251231"])
    fetcher = _fetcher(tmp_path, "daily", response=["20250701", "20251231"])
    for _ in range(2):
        result, = fetcher.fetch()
        assert result.files_written == 1
        assert result.units_verified == 0
        assert fetcher.holes == ()
    assert len(_data_calls(fetcher)) == 2


@pytest.mark.parametrize("endpoint", ENDPOINTS)
@pytest.mark.parametrize("start,end", [
    ("20250431", "20250530"), ("20250101", "20250230"),
    ("20250229", "20250301"), ("00000101", "20251231"),
    ("20250101", "２０２５１２３１"), ("２０２５０１０１", "２０２５１２３１"),
])
def test_invalid_requested_date_fails_before_calendar_or_year_file_changes(tmp_path, endpoint, start, end):
    path = _seed(tmp_path, endpoint, ["20250101", "20251231"])
    before = path.read_bytes()
    fetcher = _fetcher(tmp_path, endpoint, start=start, end=end, calendar=None)
    with pytest.raises(TushareFetcherError, match="real.*calendar dates"):
        fetcher.fetch()
    fetcher._client.call.assert_not_called()
    assert path.read_bytes() == before


def test_cli_reports_invalid_start_cleanly_without_attempting_unavailable_calendar(tmp_path):
    path = _seed(tmp_path, "daily", ["20250501", "20250530"])
    before = path.read_bytes()
    client = MagicMock()
    client.call.side_effect = TushareClientError("calendar unavailable", kind="param")
    cli = importlib.import_module("scripts.data_pipeline.01_fetch_tushare")
    with patch.object(cli.TushareClient, "from_environment", return_value=client):
        status = cli.main([
            "--output-dir", str(tmp_path), "--endpoints", "daily",
            "--start-date", "20250431", "--end-date", "20250530",
            "--rate-limit-sleep-ms", "0",
        ])
    assert status == 1
    client.call.assert_not_called()
    assert path.read_bytes() == before
    assert not (tmp_path / "fetch_manifest.json").exists()
