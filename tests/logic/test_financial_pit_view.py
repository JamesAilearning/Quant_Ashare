"""FinancialPITDataView governance (阶段8 Gate-2 PR-2).

Pins the view's PIT contract on synthetic store data: unreadable before
announcement / next-trading-day effect / original-disclosure-first / as-of
carry-forward / missing->NA (never 0) / delisted names served / financial-sector
exclusion + cross-check / coverage floor.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from src.data.trading_calendar import StaticTradingCalendar
from src.research.financial_pit_view import (
    FinancialPITDataView,
    FinancialPITViewError,
    financial_issuers_from_industry,
)

# next_trading_day_after(20220331)=20220401 ; next_trading_day_after(20220429)=20220505
_CAL = StaticTradingCalendar([
    date(2022, 3, 31), date(2022, 4, 1), date(2022, 4, 29), date(2022, 5, 5),
])

_INCOME_DATA = ("revenue", "total_revenue", "oper_cost", "sell_exp", "admin_exp",
                "rd_exp", "int_exp", "fin_exp")


def _row(ts, end_date, uf, ann, **data):
    row = {
        "ts_code": ts, "end_date": end_date, "ann_date": ann, "f_ann_date": ann,
        "update_flag": uf, "_content_hash": f"h_{ts}_{end_date}_{uf}",
        "_fetch_batch": "b1", "_source_endpoint": "income",
    }
    for f in _INCOME_DATA:
        row[f] = data.get(f, pd.NA)
    return row


def _make_store(tmp_path):
    inc = tmp_path / "income"
    inc.mkdir(parents=True)
    # 000001.SZ: Q4-2021 (orig+revised) + Q1-2022 ; rd_exp NA throughout
    pd.DataFrame([
        _row("000001.SZ", "20211231", "0", "20220331", revenue=100.0, oper_cost=60.0),
        _row("000001.SZ", "20211231", "1", "20220331", revenue=999.0, oper_cost=60.0),
        _row("000001.SZ", "20220331", "0", "20220429", revenue=30.0, oper_cost=20.0),
    ]).to_parquet(inc / "000001.SZ.parquet", index=False)
    # 600000.SH: a DELISTED CSI300-ever name — must still be served (no gap)
    pd.DataFrame([
        _row("600000.SH", "20211231", "0", "20220331", revenue=50.0, oper_cost=40.0),
    ]).to_parquet(inc / "600000.SH.parquet", index=False)
    return tmp_path


def _view(tmp_path, financial=()):
    return FinancialPITDataView(_make_store(tmp_path), _CAL, financial_issuers=financial)


# (a) unreadable before announcement + (b) next-trading-day effect ------------

def test_invisible_before_availability_and_usable_next_trading_day(tmp_path):
    v = _view(tmp_path)
    # announced 20220331 -> available 20220401 (next trading day). Not the ann day.
    assert pd.isna(v.as_of("2022-03-30", ["revenue"], ["000001.SZ"]).loc["000001.SZ", "revenue"])
    assert pd.isna(v.as_of("2022-03-31", ["revenue"], ["000001.SZ"]).loc["000001.SZ", "revenue"])
    assert v.as_of("2022-04-01", ["revenue"], ["000001.SZ"]).loc["000001.SZ", "revenue"] == 100.0


# (c) original-disclosure-first (revised not backfilled) ----------------------

def test_serves_original_not_revised(tmp_path):
    v = _view(tmp_path)
    # a revised (update_flag=1, revenue 999) exists for the same period; the view
    # serves the update_flag=0 original (100), never the revised value.
    assert v.as_of("2022-04-01", ["revenue"], ["000001.SZ"]).loc["000001.SZ", "revenue"] == 100.0


# as-of carry-forward: latest already-announced period held, not a fill --------

def test_as_of_carry_forward_holds_latest_announced_period(tmp_path):
    v = _view(tmp_path)
    # 2022-05-01: only Q4-2021 announced (Q1-2022 available 2022-05-05) -> holds 100
    assert v.as_of("2022-05-01", ["revenue"], ["000001.SZ"]).loc["000001.SZ", "revenue"] == 100.0
    # 2022-05-05: Q1-2022 now available -> carry forward to the newer period (30)
    assert v.as_of("2022-05-05", ["revenue"], ["000001.SZ"]).loc["000001.SZ", "revenue"] == 30.0


# (d) missing field -> NA, never 0/median/latest/future -----------------------

def test_missing_field_is_na_never_zero(tmp_path):
    v = _view(tmp_path)
    got = v.as_of("2022-04-01", ["rd_exp"], ["000001.SZ"]).loc["000001.SZ", "rd_exp"]
    assert pd.isna(got)  # rd_exp undisclosed -> NA, NOT 0


# (f) delisted CSI300-ever names are served (no survivorship gap) --------------

def test_delisted_name_is_served(tmp_path):
    v = _view(tmp_path)
    panel = v.as_of("2022-04-01", ["revenue"], ["000001.SZ", "600000.SH"])
    assert panel.loc["600000.SH", "revenue"] == 50.0  # delisted issuer present


# financial-sector exclusion + cross-check ------------------------------------

def test_financial_issuer_excluded_from_universe(tmp_path):
    v = _view(tmp_path, financial=["000001.SZ"])  # bank excluded
    panel = v.as_of("2022-04-01", ["revenue"], ["000001.SZ", "600000.SH"])
    assert "000001.SZ" not in panel.index      # excluded
    assert "600000.SH" in panel.index          # kept


def test_cross_check_reports_financial_with_oper_cost(tmp_path):
    v = _view(tmp_path, financial=["000001.SZ"])
    # 000001.SZ is on the financial list but DOES report oper_cost -> disagreement
    # reported (not silently resolved).
    dis = v.cross_check_exclusion(["000001.SZ"])
    assert any(d.ts_code == "000001.SZ" and d.kind == "financial_has_oper_cost" for d in dis)


# (g) coverage floor fails loud -----------------------------------------------

def test_coverage_below_floor_fails_loud(tmp_path):
    v = _view(tmp_path)
    # ghost instrument has no store file -> revenue coverage 1/2 = 0.5 < floor 0.9
    with pytest.raises(FinancialPITViewError, match="coverage below"):
        v.assert_coverage_floor({"revenue": 0.9}, ["000001.SZ", "999999.SZ"], "2022-04-01")
    # a satisfiable floor passes
    v.assert_coverage_floor({"revenue": 0.5}, ["000001.SZ", "600000.SH"], "2022-04-01")


def test_all_financial_universe_returns_empty_columned_frame(tmp_path):
    # every requested instrument excluded -> empty but correctly-columned frame
    # (no KeyError in downstream panel[field] / coverage).
    v = _view(tmp_path, financial=["000001.SZ", "600000.SH"])
    panel = v.as_of("2022-04-01", ["revenue"], ["000001.SZ", "600000.SH"])
    assert panel.empty
    assert list(panel.columns) == ["revenue"]
    assert v.coverage("revenue", ["000001.SZ", "600000.SH"], "2022-04-01") == 0.0


def test_absent_store_column_fails_loud(tmp_path):
    # a store frame that EXISTS but lacks a charter column (old/bad ingest) is a
    # schema corruption — fail loud, not serve NA (codex #342 P2). A per-row NA
    # (column present, value absent) stays legitimate missingness (other tests).
    inc = tmp_path / "income"
    inc.mkdir(parents=True)
    pd.DataFrame([{
        "ts_code": "000001.SZ", "end_date": "20211231", "ann_date": "20220331",
        "f_ann_date": "20220331", "update_flag": "0", "revenue": 100.0,
        "_content_hash": "h", "_fetch_batch": "b1",  # NOTE: no rd_exp column
    }]).to_parquet(inc / "000001.SZ.parquet", index=False)
    v = FinancialPITDataView(tmp_path, _CAL, financial_issuers=frozenset())
    with pytest.raises(FinancialPITViewError, match="missing charter column"):
        v.as_of("2022-04-01", ["rd_exp"], ["000001.SZ"])
    # a present column on the same partial store still serves fine
    assert v.as_of("2022-04-01", ["revenue"], ["000001.SZ"]).loc["000001.SZ", "revenue"] == 100.0


def test_unknown_field_fails_loud(tmp_path):
    v = _view(tmp_path)
    with pytest.raises(FinancialPITViewError, match="unknown charter field"):
        v.as_of("2022-04-01", ["not_a_field"], ["000001.SZ"])


def test_constructor_requires_financial_issuers(tmp_path):
    # no default: a forgotten exclusion set must not silently include banks —
    # the caller has to make the exclusion source explicit (codex #342 r3).
    with pytest.raises(TypeError):
        FinancialPITDataView(_make_store(tmp_path), _CAL)  # type: ignore[call-arg]


def test_financial_issuers_from_industry():
    sb = pd.DataFrame({
        "ts_code": ["000001.SZ", "600519.SH", "601318.SH"],
        "industry": ["银行", "白酒", "保险"],
    })
    got = financial_issuers_from_industry(sb)
    assert got == frozenset({"000001.SZ", "601318.SH"})  # bank + insurer, not 白酒
