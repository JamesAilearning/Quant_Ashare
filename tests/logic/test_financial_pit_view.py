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
    # 000002.SZ: OLDER period has an update_flag=0 original; the RECENT period has
    # ONLY update_flag=1 (provider kept no original) — the view must serve the
    # recent uf1 period (its disclosure of record), NOT stale-fall-back to the
    # older uf0 (阶段8 Gate-2 correction). ann 20220429 -> available 20220505.
    pd.DataFrame([
        _row("000002.SZ", "20211231", "0", "20220331", revenue=200.0, oper_cost=120.0),
        _row("000002.SZ", "20220331", "1", "20220429", revenue=210.0, oper_cost=130.0),
    ]).to_parquet(inc / "000002.SZ.parquet", index=False)
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


def test_scalar_string_instruments_and_fields_rejected(tmp_path):
    # a scalar str for instruments/fields would iterate into characters — reject
    # it on every sequence-param entry point (codex #342 r9).
    v = _view(tmp_path)
    with pytest.raises(FinancialPITViewError, match="instruments must be"):
        v.as_of("2022-04-01", ["revenue"], "000001.SZ")
    with pytest.raises(FinancialPITViewError, match="fields must be"):
        v.as_of("2022-04-01", "revenue", ["000001.SZ"])
    with pytest.raises(FinancialPITViewError, match="instruments must be"):
        v.cross_check_exclusion("000001.SZ")


def test_qlib_style_instruments_normalized_to_ts_code(tmp_path):
    # the canonical universe is qlib-style (SH600000); the store is ts_code-keyed
    # (600000.SH.parquet). A qlib-style query must resolve, not silently NA, and
    # the panel is indexed by the canonical ts_code (codex #342).
    v = _view(tmp_path)
    panel = v.as_of("2022-04-01", ["revenue"], ["SZ000001"])  # -> 000001.SZ
    assert list(panel.index) == ["000001.SZ"]
    assert panel.loc["000001.SZ", "revenue"] == 100.0
    # mixed formats in one call both resolve to their ts_code rows
    mixed = v.as_of("2022-04-01", ["revenue"], ["SZ000001", "600000.SH"])
    assert set(mixed.index) == {"000001.SZ", "600000.SH"}


def test_qlib_style_exclusion_matches_ts_code_issuer(tmp_path):
    # financial_issuers given as ts_code; instrument queried qlib-style -> still
    # excluded (both sides normalize to ts_code).
    v = _view(tmp_path, financial=("000001.SZ",))
    panel = v.as_of("2022-04-01", ["revenue"], ["SZ000001", "600000.SH"])
    assert list(panel.index) == ["600000.SH"]  # SZ000001 excluded
    # and the reverse: qlib-style exclusion list matches a ts_code instrument
    v2 = FinancialPITDataView(_make_store(tmp_path / "b"), _CAL,
                              financial_issuers=("SZ000001",))
    panel2 = v2.as_of("2022-04-01", ["revenue"], ["000001.SZ", "600000.SH"])
    assert list(panel2.index) == ["600000.SH"]


def test_malformed_instrument_fails_loud(tmp_path):
    # neither ts_code nor qlib-style -> fail loud, never a silent all-NA row.
    v = _view(tmp_path)
    for bad in ("FOOBAR", "12345", "600000", "600000.sh"):
        with pytest.raises(FinancialPITViewError, match="neither a Tushare ts_code"):
            v.as_of("2022-04-01", ["revenue"], [bad])
    with pytest.raises(FinancialPITViewError, match="neither a Tushare ts_code"):
        v.cross_check_exclusion(["FOOBAR"])


def test_datetime_trade_date_normalized(tmp_path):
    # a datetime / pandas Timestamp (common from qlib-style calendars) must be
    # normalized to a date, not crash the date<=date comparison (codex #342 r8).
    from datetime import datetime as _dt
    v = _view(tmp_path)
    assert v.as_of(_dt(2022, 4, 1, 15, 30), ["revenue"], ["000001.SZ"]).loc[
        "000001.SZ", "revenue"] == 100.0
    assert v.as_of(pd.Timestamp("2022-04-01 15:30"), ["revenue"], ["000001.SZ"]).loc[
        "000001.SZ", "revenue"] == 100.0


def test_unknown_field_fails_loud(tmp_path):
    v = _view(tmp_path)
    with pytest.raises(FinancialPITViewError, match="unknown charter field"):
        v.as_of("2022-04-01", ["not_a_field"], ["000001.SZ"])


def test_empty_originals_missing_column_still_fails_loud(tmp_path):
    # a store whose only row is a revision (no update_flag=0 original) AND missing
    # a charter column must still fail loud — the column check runs before any
    # serve/return (codex #342 r6). Post Gate-2 fix the uf1 row IS the disclosure
    # of record (served, not dropped), but a missing column still fails loud.
    inc = tmp_path / "income"
    inc.mkdir(parents=True)
    pd.DataFrame([{
        "ts_code": "000001.SZ", "end_date": "20211231", "ann_date": "20220331",
        "f_ann_date": "20220331", "update_flag": "1", "revenue": 100.0,  # revised ONLY
        "_content_hash": "h", "_fetch_batch": "b1",  # NOTE: no rd_exp column
    }]).to_parquet(inc / "000001.SZ.parquet", index=False)
    v = FinancialPITDataView(tmp_path, _CAL, financial_issuers=frozenset())
    with pytest.raises(FinancialPITViewError, match="missing charter column"):
        v.as_of("2022-04-01", ["rd_exp"], ["000001.SZ"])


def test_cross_check_absent_oper_cost_column_fails_loud(tmp_path):
    # a store missing the oper_cost column is a schema corruption, not "never
    # reports" — the exclusion cross-check must fail loud (codex #342 r5).
    inc = tmp_path / "income"
    inc.mkdir(parents=True)
    pd.DataFrame([{
        "ts_code": "000001.SZ", "end_date": "20211231", "ann_date": "20220331",
        "f_ann_date": "20220331", "update_flag": "0", "revenue": 100.0,
        "_content_hash": "h", "_fetch_batch": "b1",  # NOTE: no oper_cost column
    }]).to_parquet(inc / "000001.SZ.parquet", index=False)
    v = FinancialPITDataView(tmp_path, _CAL, financial_issuers=frozenset())
    with pytest.raises(FinancialPITViewError, match="missing charter column"):
        v.cross_check_exclusion(["000001.SZ"])


def test_scalar_string_financial_issuers_rejected(tmp_path):
    # a single str satisfies Iterable[str] but iterates into characters — reject
    # it loudly so the exclusion set can't silently become chars (codex #342 r7).
    with pytest.raises(FinancialPITViewError, match="not a single string"):
        FinancialPITDataView(_make_store(tmp_path), _CAL, financial_issuers="000001.SZ")


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


# 阶段8 Gate-2 correction: uf1-only recent period served (no stale fall-back) --

def test_double_disclosure_serves_earliest_record_end_to_end(tmp_path):
    # provider double disclosure (same period+uf, two f_ann dates, different
    # values — the 五粮液 pattern): the view serves the EARLIEST-announced
    # record; the late re-announcement never serves, at any as-of date
    # (fix-financial-ingest-ambiguous-duplicates, end-to-end through the view).
    inc = tmp_path / "income"
    inc.mkdir(parents=True)
    r_early = _row("000003.SZ", "20211231", "1", "20220331", revenue=500.0)
    r_late = _row("000003.SZ", "20211231", "1", "20220429", revenue=235.0)
    r_late["_content_hash"] = "h_late"  # distinct content, own announcement
    pd.DataFrame([r_early, r_late]).to_parquet(inc / "000003.SZ.parquet", index=False)
    v = FinancialPITDataView(tmp_path, _CAL, financial_issuers=frozenset())
    # after the early availability (2022-04-01): early record serves
    assert v.as_of("2022-04-01", ["revenue"], ["000003.SZ"]).loc[
        "000003.SZ", "revenue"] == 500.0
    # even after the late row's availability (2022-05-05): STILL the record
    assert v.as_of("2022-05-05", ["revenue"], ["000003.SZ"]).loc[
        "000003.SZ", "revenue"] == 500.0


def test_uf1_only_recent_period_served_not_stale(tmp_path):
    # 000002.SZ: the older 2021-Q4 period has an update_flag=0 original
    # (revenue 200); the recent 2022-Q1 period exists ONLY as update_flag=1
    # (revenue 210) — the provider kept no original for it. The view must serve
    # the recent uf1 period once available (its disclosure of record), NOT
    # stale-fall-back to the older uf0.
    v = _view(tmp_path)
    # before 2022-Q1 is available (avail 2022-05-05): only the older period shows
    assert v.as_of("2022-04-30", ["revenue"], ["000002.SZ"]).loc[
        "000002.SZ", "revenue"] == 200.0
    # once available, the recent uf1 period is served (the fix) — not stale 200
    assert v.as_of("2022-05-05", ["revenue"], ["000002.SZ"]).loc[
        "000002.SZ", "revenue"] == 210.0
    # and it is picked as the LATEST available period, over the older uf0 one
    assert v.as_of("2022-05-05", ["oper_cost"], ["000002.SZ"]).loc[
        "000002.SZ", "oper_cost"] == 130.0


def test_as_of_report_period_metadata_cross_endpoint(tmp_path):
    """include_report_periods=True surfaces the SERVED end_date per queried
    endpoint so cross-endpoint consumers can enforce report-period alignment
    (codex #354 r6 P1): endpoints are served independently, and a lagging
    balancesheet must be VISIBLE as a different period, never silently mixed
    into an income/total_assets ratio. Default output stays unchanged."""
    inc = tmp_path / "income"
    inc.mkdir(parents=True)
    pd.DataFrame([
        _row("000001.SZ", "20211231", "0", "20220331", revenue=100.0),
        _row("000001.SZ", "20220331", "0", "20220429", revenue=30.0),
    ]).to_parquet(inc / "000001.SZ.parquet", index=False)
    bs = tmp_path / "balancesheet"
    bs.mkdir(parents=True)
    bs_row = {
        "ts_code": "000001.SZ", "end_date": "20211231", "ann_date": "20220331",
        "f_ann_date": "20220331", "update_flag": "0", "_content_hash": "hb",
        "_fetch_batch": "b1", "_source_endpoint": "balancesheet",
        "total_assets": 500.0,
    }
    pd.DataFrame([bs_row]).to_parquet(bs / "000001.SZ.parquet", index=False)
    v = FinancialPITDataView(tmp_path, _CAL, financial_issuers=frozenset())

    # 2022-05-05: income serves Q1-2022, balancesheet still FY-2021 —
    # the metadata must expose the misalignment.
    got = v.as_of("2022-05-05", ["revenue", "total_assets"], ["000001.SZ"],
                  include_report_periods=True)
    assert got.loc["000001.SZ", "_report_period__income"] == "20220331"
    assert got.loc["000001.SZ", "_report_period__balancesheet"] == "20211231"
    assert got.loc["000001.SZ", "revenue"] == 30.0
    assert got.loc["000001.SZ", "total_assets"] == 500.0

    # 2022-04-01: both endpoints serve FY-2021 — aligned.
    got2 = v.as_of("2022-04-01", ["revenue", "total_assets"], ["000001.SZ"],
                   include_report_periods=True)
    assert (got2.loc["000001.SZ", "_report_period__income"]
            == got2.loc["000001.SZ", "_report_period__balancesheet"]
            == "20211231")

    # an endpoint with NOTHING available yet -> NA period, fields NA
    got3 = v.as_of("2022-03-31", ["revenue", "total_assets"], ["000001.SZ"],
                   include_report_periods=True)
    assert pd.isna(got3.loc["000001.SZ", "_report_period__income"])

    # default call: NO metadata columns (byte-compatible output)
    plain = v.as_of("2022-05-05", ["revenue", "total_assets"], ["000001.SZ"])
    assert [c for c in plain.columns if c.startswith("_report_period")] == []
