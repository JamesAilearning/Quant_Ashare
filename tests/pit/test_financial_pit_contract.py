"""Financial-PIT contract dating (阶段8 Gate-2 PR-1).

Governance: availability = the first trading day STRICTLY AFTER the
announcement (never the report-period end); f_ann_date preferred with the
ann_date fallback recorded; missing BOTH announcement dates → unavailable,
never a period-end fallback; revision linkage; latest-batch resolution.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from src.data.pit.financial_pit_contract import (
    ANNOUNCEMENT_DATE,
    ANNOUNCEMENT_SOURCE,
    AVAILABLE_FROM,
    REPORT_PERIOD,
    REVISION_OF,
    FinancialPITContractError,
    build_contract_frame,
    resolve_current_versions,
)
from src.data.trading_calendar import (
    StaticTradingCalendar,
    load_static_calendar_from_file,
)
from src.data.tushare.financial_statements import COL_CONTENT_HASH, COL_FETCH_BATCH

# 2022-04-01 (Fri) then a gap (weekend + Qingming holiday) to 2022-04-06 (Wed).
_CAL = StaticTradingCalendar([
    date(2022, 3, 30), date(2022, 3, 31), date(2022, 4, 1), date(2022, 4, 6),
])


def _store() -> pd.DataFrame:
    return pd.DataFrame([
        # A: original + revised (revised announced a day later)
        {"ts_code": "A", "end_date": "20211231", "ann_date": "20220331",
         "f_ann_date": "20220331", "update_flag": "0",
         COL_CONTENT_HASH: "hA0", COL_FETCH_BATCH: "b1"},
        {"ts_code": "A", "end_date": "20211231", "ann_date": "20220401",
         "f_ann_date": "20220401", "update_flag": "1",
         COL_CONTENT_HASH: "hA1", COL_FETCH_BATCH: "b1"},
        # B: f_ann_date missing -> ann_date fallback
        {"ts_code": "B", "end_date": "20211231", "ann_date": "20220331",
         "f_ann_date": pd.NA, "update_flag": "0",
         COL_CONTENT_HASH: "hB0", COL_FETCH_BATCH: "b1"},
        # C: BOTH announcement dates missing -> unavailable
        {"ts_code": "C", "end_date": "20211231", "ann_date": pd.NA,
         "f_ann_date": pd.NA, "update_flag": "0",
         COL_CONTENT_HASH: "hC0", COL_FETCH_BATCH: "b1"},
    ])


def test_available_is_next_trading_day_strictly_after_announcement() -> None:
    out = build_contract_frame(_store(), _CAL)
    a0 = out[(out["ts_code"] == "A") & (out["update_flag"] == "0")].iloc[0]
    assert a0[REPORT_PERIOD] == date(2021, 12, 31)
    assert a0[ANNOUNCEMENT_DATE] == date(2022, 3, 31)
    assert a0[ANNOUNCEMENT_SOURCE] == "f_ann_date"
    # strictly AFTER the announcement, and NOT the report-period end
    assert a0[AVAILABLE_FROM] == date(2022, 4, 1)
    assert a0[AVAILABLE_FROM] > a0[ANNOUNCEMENT_DATE]
    assert a0[AVAILABLE_FROM] != a0[REPORT_PERIOD]


def test_ann_date_fallback_recorded() -> None:
    out = build_contract_frame(_store(), _CAL)
    b = out[out["ts_code"] == "B"].iloc[0]
    assert b[ANNOUNCEMENT_DATE] == date(2022, 3, 31)
    assert b[ANNOUNCEMENT_SOURCE] == "ann_date"      # fallback used AND recorded
    assert b[AVAILABLE_FROM] == date(2022, 4, 1)


def test_missing_both_announcement_dates_is_unavailable_never_period_end() -> None:
    out = build_contract_frame(_store(), _CAL)
    c = out[out["ts_code"] == "C"].iloc[0]
    assert c[ANNOUNCEMENT_DATE] is None
    assert c[ANNOUNCEMENT_SOURCE] == ""
    assert c[AVAILABLE_FROM] is None                 # unavailable
    assert c[AVAILABLE_FROM] != c[REPORT_PERIOD]     # NEVER the period end


def test_revision_links_to_original() -> None:
    out = build_contract_frame(_store(), _CAL)
    a1 = out[(out["ts_code"] == "A") & (out["update_flag"] == "1")].iloc[0]
    a0 = out[(out["ts_code"] == "A") & (out["update_flag"] == "0")].iloc[0]
    assert a1[REVISION_OF] == a0[COL_CONTENT_HASH]   # revised -> original's hash
    assert pd.isna(a0[REVISION_OF])                  # original links to nothing


def test_resolve_current_keeps_latest_batch() -> None:
    frame = pd.DataFrame([
        {"ts_code": "A", "end_date": "20211231", "update_flag": "0",
         "revenue": 100.0, COL_CONTENT_HASH: "h1", COL_FETCH_BATCH: "b1"},
        {"ts_code": "A", "end_date": "20211231", "update_flag": "0",
         "revenue": 200.0, COL_CONTENT_HASH: "h2", COL_FETCH_BATCH: "b2"},
    ])
    cur = resolve_current_versions(frame)
    assert len(cur) == 1
    assert cur.iloc[0]["revenue"] == 200.0           # latest batch wins
    assert cur.iloc[0][COL_FETCH_BATCH] == "b2"


def test_malformed_date_raises_not_hidden() -> None:
    # a non-blank but impossible date (month 13) is corruption, not missingness —
    # must fail loud, else report_period/availability silently go wrong (codex
    # #340 P2). True blanks/NA still degrade to unavailable (other tests).
    frame = pd.DataFrame([
        {"ts_code": "A", "end_date": "20221301", "ann_date": "20220331",
         "f_ann_date": "20220331", "update_flag": "0",
         COL_CONTENT_HASH: "h", COL_FETCH_BATCH: "b1"},
    ])
    with pytest.raises(FinancialPITContractError, match="malformed"):
        build_contract_frame(frame, _CAL)


def test_build_contract_requires_provenance() -> None:
    # a provenance-stripped frame (no _content_hash / _fetch_batch) must fail
    # loud rather than silently drop restatement lineage (codex #340 r5 P2).
    frame = pd.DataFrame([
        {"ts_code": "A", "end_date": "20211231", "ann_date": "20220331",
         "f_ann_date": "20220331", "update_flag": "0"},  # no provenance cols
    ])
    with pytest.raises(FinancialPITContractError, match="missing"):
        build_contract_frame(frame, _CAL)


def test_resolve_current_fails_loud_without_provenance() -> None:
    # a frame lacking _fetch_batch / logical-key columns cannot be resolved to a
    # single current version; returning it unresolved would expose superseded
    # versions as current — must fail loud (codex #340 P2).
    frame = pd.DataFrame([
        {"ts_code": "A", "end_date": "20211231", "update_flag": "0", "revenue": 100.0},
    ])  # no _fetch_batch
    with pytest.raises(FinancialPITContractError, match="missing"):
        resolve_current_versions(frame)


def test_nonzero_fractional_date_raises_but_dot_zero_tolerated() -> None:
    # "20220331.5" must NOT be truncated to a valid date (codex #340 r4 P2);
    # an exact ".0" float coercion is still tolerated.
    bad = pd.DataFrame([
        {"ts_code": "A", "end_date": "20220331.5", "ann_date": "20220331",
         "f_ann_date": "20220331", "update_flag": "0",
         COL_CONTENT_HASH: "h", COL_FETCH_BATCH: "b1"},
    ])
    with pytest.raises(FinancialPITContractError, match="malformed"):
        build_contract_frame(bad, _CAL)
    ok = pd.DataFrame([
        {"ts_code": "A", "end_date": "20211231.0", "ann_date": "20220331.0",
         "f_ann_date": "20220331.0", "update_flag": "0",
         COL_CONTENT_HASH: "h", COL_FETCH_BATCH: "b1"},
    ])
    out = build_contract_frame(ok, _CAL)
    assert out.iloc[0][REPORT_PERIOD] == date(2021, 12, 31)
    assert out.iloc[0][AVAILABLE_FROM] == date(2022, 4, 1)


def test_calendar_next_trading_day_and_end_of_calendar() -> None:
    assert _CAL.next_trading_day_after(date(2022, 4, 1)) == date(2022, 4, 6)  # skips gap
    assert _CAL.next_trading_day_after(date(2022, 3, 30)) == date(2022, 3, 31)
    assert _CAL.next_trading_day_after(date(2022, 4, 6)) is None              # past last day


def test_load_static_calendar_from_file(tmp_path) -> None:
    p = tmp_path / "day.txt"
    p.write_text("2018-01-02\n2018-01-03\n2018-01-04\n", encoding="utf-8")
    cal = load_static_calendar_from_file(p)
    assert cal.next_trading_day_after(date(2018, 1, 2)) == date(2018, 1, 3)
    assert cal.count_trading_days(date(2018, 1, 2), date(2018, 1, 4)) == 3
