"""Synthetic regression coverage for non-destructive ticker-year replacement."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.data.pit.bundle_integrity import read_bundle_integrity, write_bundle_integrity
from src.data.tushare.fetch_manifest import read_manifest
from src.data.tushare.fetcher import TushareFetcher, TushareFetcherConfig

TICKER = "600000.SH"
ENDPOINTS = ("daily", "adj_factor", "daily_basic")
UNIT = f"ts_code={TICKER} year=2025"


def _seed_universe(root, *, tickers=(TICKER,), list_date="20000101"):
    pd.DataFrame({"ts_code": tickers, "list_date": [list_date] * len(tickers)}).to_parquet(
        root / "active_stocks.parquet", index=False,
    )
    pd.DataFrame(columns=["ts_code", "list_date"]).to_parquet(
        root / "delisted_stocks.parquet", index=False,
    )


def _old_file(root, endpoint, dates):
    path = root / endpoint / "2025" / f"{TICKER}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"ts_code": [TICKER] * len(dates), "trade_date": dates}).to_parquet(
        path, index=False,
    )
    return path


def _client(dates):
    def call(api, **params):
        if api == "trade_cal":
            return pd.DataFrame({"cal_date": pd.bdate_range(
                params["start_date"], params["end_date"],
            ).strftime("%Y%m%d")})
        return pd.DataFrame({
            "ts_code": [params["ts_code"]] * len(dates), "trade_date": dates,
        })

    client = MagicMock()
    client.call.side_effect = call
    return client


def _data_calls(client):
    return [c for c in client.call.call_args_list if c.args[0] != "trade_cal"]


def _fetch(root, client, *, endpoint="daily", start="20250701", end="20250702", **kw):
    fetcher = TushareFetcher(client, TushareFetcherConfig(
        output_dir=root, endpoints=(endpoint,), start_date=start, end_date=end,
        rate_limit_sleep_ms=0, **kw,
    ))
    return fetcher, fetcher.fetch()[0]


def _assert_refused(fetcher, result, path, before, client, endpoint="daily"):
    assert path.read_bytes() == before
    assert _data_calls(client) == []
    assert result.files_written == result.rows_total == result.units_verified == 0
    assert len(fetcher.holes) == 1
    hole = fetcher.holes[0]
    assert (hole.endpoint, hole.unit, hole.reason_class, hole.attempts) == (
        endpoint, UNIT, "unsafe_overwrite", 0,
    )


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_narrow_stale_request_preserves_earlier_rows(tmp_path, endpoint):
    _seed_universe(tmp_path)
    path = _old_file(tmp_path, endpoint, ["20250102", "20250630"])
    before = path.read_bytes()
    client = _client(["20250701", "20250702"])
    fetcher, result = _fetch(tmp_path, client, endpoint=endpoint)
    _assert_refused(fetcher, result, path, before, client, endpoint)
    assert "20250102..20250702" in fetcher.holes[0].last_error


@pytest.mark.parametrize("endpoint", ENDPOINTS)
@pytest.mark.parametrize("response", [[], ["20250702"]])
def test_forced_narrow_retry_preserves_later_rows_even_for_empty_response(
    tmp_path, endpoint, response,
):
    _seed_universe(tmp_path)
    path = _old_file(tmp_path, endpoint, ["20250701", "20251231"])
    before = path.read_bytes()
    client = _client(response)
    fetcher, result = _fetch(
        tmp_path, client, endpoint=endpoint, start="20250601",
        force_retry_units=frozenset({(endpoint, UNIT)}),
    )
    _assert_refused(fetcher, result, path, before, client, endpoint)
    assert "20250601..20251231" in fetcher.holes[0].last_error


def test_pre_listing_slice_cannot_clear_later_listing_history(tmp_path):
    _seed_universe(tmp_path, list_date="20250901")
    path = _old_file(tmp_path, "daily", ["20250901", "20251001"])
    before = path.read_bytes()
    client = _client([])
    fetcher, result = _fetch(tmp_path, client)
    _assert_refused(fetcher, result, path, before, client)


@pytest.mark.parametrize("bad_date", [None, "not-a-date", "20250230", "2025-07-01", "2025071"])
def test_partial_retry_preserves_file_with_any_invalid_date(tmp_path, bad_date):
    _seed_universe(tmp_path)
    path = _old_file(tmp_path, "daily", ["20250701", bad_date])
    before = path.read_bytes()
    client = _client(["20250702"])
    fetcher, result = _fetch(
        tmp_path, client, force_retry_units=frozenset({("daily", UNIT)}),
    )
    _assert_refused(fetcher, result, path, before, client)
    assert "full-year" in fetcher.holes[0].last_error


@pytest.mark.parametrize("kind", ["unreadable", "missing_date"])
def test_partial_retry_preserves_uninspectable_old_file(tmp_path, kind):
    _seed_universe(tmp_path)
    path = _old_file(tmp_path, "daily", [])
    if kind == "unreadable":
        path.write_bytes(b"corrupt parquet")
    else:
        pd.DataFrame({"ts_code": [TICKER]}).to_parquet(path, index=False)
    before = path.read_bytes()
    client = _client(["20250702"])
    fetcher, result = _fetch(tmp_path, client)
    _assert_refused(fetcher, result, path, before, client)


@pytest.mark.parametrize("kind", ["missing", "empty", "schema_less_empty", "year_to_date"])
@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_safe_partial_replacement_retains_exact_requested_bounds(tmp_path, kind, endpoint):
    _seed_universe(tmp_path)
    if kind != "missing":
        path = _old_file(tmp_path, endpoint, ["20250102"] if kind == "year_to_date" else [])
        if kind == "schema_less_empty":
            pd.DataFrame().to_parquet(path, index=False)
    start = "20250101" if kind == "year_to_date" else "20250701"
    dates = ["20250102", "20250702"] if kind == "year_to_date" else ["20250702"]
    client = _client(dates)
    fetcher, result = _fetch(tmp_path, client, endpoint=endpoint, start=start)
    assert fetcher.holes == ()
    assert result.files_written == 1
    call, = _data_calls(client)
    assert (call.kwargs["start_date"], call.kwargs["end_date"]) == (start, "20250702")
    assert pd.read_parquet(tmp_path / endpoint / "2025" / f"{TICKER}.parquet")[
        "trade_date"
    ].tolist() == dates


@pytest.mark.parametrize("kind", ["unreadable", "missing_date", "invalid_date"])
def test_explicit_full_year_still_repairs_corrupt_file(tmp_path, kind):
    _seed_universe(tmp_path)
    path = _old_file(tmp_path, "daily", ["20250230"])
    if kind == "unreadable":
        path.write_bytes(b"corrupt parquet")
    elif kind == "missing_date":
        pd.DataFrame({"ts_code": [TICKER]}).to_parquet(path, index=False)
    client = _client(["20250102", "20251231"])
    fetcher, result = _fetch(
        tmp_path, client, start="20250101", end="20251231",
        force_retry_units=frozenset({("daily", UNIT)}),
    )
    assert fetcher.holes == ()
    assert result.files_written == 1
    assert pd.read_parquet(path)["trade_date"].tolist() == ["20250102", "20251231"]


@pytest.mark.parametrize("dates", [["20241231"], ["20241231", "invalid"]])
def test_full_year_repair_cannot_discard_known_out_of_year_rows(tmp_path, dates):
    _seed_universe(tmp_path)
    path = _old_file(tmp_path, "daily", dates)
    before = path.read_bytes()
    client = _client(["20251231"])
    fetcher, result = _fetch(
        tmp_path, client, start="20250101", end="20251231",
        force_retry_units=frozenset({("daily", UNIT)}),
    )
    _assert_refused(fetcher, result, path, before, client)
    assert "partition" in fetcher.holes[0].last_error


def test_cached_narrow_skip_does_not_refuse_or_rewrite_history(tmp_path):
    _seed_universe(tmp_path)
    path = _old_file(tmp_path, "daily", ["20250102", "20250702"])
    before = path.read_bytes()
    client = _client([])
    fetcher, result = _fetch(tmp_path, client)
    assert fetcher.holes == ()
    assert result.skipped == 1
    assert result.files_written == 0
    assert _data_calls(client) == []
    assert path.read_bytes() == before


def test_unsafe_unit_does_not_block_other_tickers(tmp_path):
    _seed_universe(tmp_path, tickers=(TICKER, "600001.SH"))
    path = _old_file(tmp_path, "daily", ["20250102"])
    before = path.read_bytes()
    client = _client(["20250702"])
    fetcher, result = _fetch(tmp_path, client)
    assert path.read_bytes() == before
    assert len(fetcher.holes) == result.files_written == 1
    assert _data_calls(client)[0].kwargs["ts_code"] == "600001.SH"


def test_disabling_empty_placeholders_cannot_bypass_history_guard(tmp_path):
    _seed_universe(tmp_path)
    path = _old_file(tmp_path, "daily", ["20250102"])
    before = path.read_bytes()
    client = _client(["20250702"])
    fetcher, result = _fetch(tmp_path, client, write_empty_placeholders=False)
    _assert_refused(fetcher, result, path, before, client)


def test_dry_run_does_not_write_or_call_api_for_unsafe_unit(tmp_path):
    _seed_universe(tmp_path)
    path = _old_file(tmp_path, "daily", ["20250102"])
    before = path.read_bytes()
    client = _client(["20250702"])
    fetcher, result = _fetch(tmp_path, client, dry_run=True)
    assert path.read_bytes() == before
    assert client.call.call_count == 0
    assert result.files_written == 0
    assert fetcher.holes == ()


def test_cli_records_refusal_then_covering_retry_heals_same_unit(tmp_path):
    cli_path = Path(__file__).resolve().parents[2] / "scripts/data_pipeline/01_fetch_tushare.py"
    spec = importlib.util.spec_from_file_location("_year_guard_cli_test", cli_path)
    assert spec and spec.loader
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    _seed_universe(tmp_path)
    path = _old_file(tmp_path, "daily", ["20250102", "20250630"])
    before = path.read_bytes()
    client = _client(["20250102", "20250630", "20250702"])
    args = ["--output-dir", str(tmp_path), "--endpoints", "daily", "--rate-limit-sleep-ms", "0"]
    with patch.object(cli.TushareClient, "from_environment", return_value=client):
        assert cli.main(args + ["--start-date", "20250701", "--end-date", "20250702"]) == 3
        assert path.read_bytes() == before
        manifest = read_manifest(tmp_path / "fetch_manifest.json")
        assert manifest is not None
        coverage = manifest.endpoints["daily"]
        assert coverage.status == "holes"
        assert coverage.units_written == coverage.units_verified == 0
        assert (coverage.holes[0].unit, coverage.holes[0].reason_class, coverage.holes[0].attempts) == (
            UNIT, "unsafe_overwrite", 0,
        )
        # If a research build explicitly overrides holes, its provenance must
        # still carry this zero-attempt refusal to the recommendation gate.
        bundle = tmp_path / "research_bundle"
        write_bundle_integrity(bundle, built_from_holey_fetch=True, holes=coverage.holes)
        integrity = read_bundle_integrity(bundle)
        assert integrity is not None and integrity.built_from_holey_fetch
        assert integrity.holes == coverage.holes
        assert cli.main(args + ["--start-date", "20250101", "--end-date", "20250702"]) == 0
    manifest = read_manifest(tmp_path / "fetch_manifest.json")
    assert manifest is not None
    assert manifest.endpoints["daily"].status == "complete"
    assert manifest.endpoints["daily"].holes == ()
    assert pd.read_parquet(path)["trade_date"].tolist() == ["20250102", "20250630", "20250702"]
