"""平移诊断的胜者规则必须与 view 的 as-of 规则逐日一致。

`fundamental_ann_shift_sensitivity.winner_at` 是 canonical as-of 规则的
**第二份实现** —— 诊断必须在**源侧独立**算胜者（不能查重建出来的面板，否则对
公告日不敏感的构建器永远建立不起相关性，那条 REFUSE 永不触发），所以这份复刻
是必要的。

但复刻就有漂移风险，而且漂移的方向很难看：一个**与被审计规则不一致的审计器**
会按另一套规则去判对错。#425 里"报告的候选字段与冻结公式漂移"是同一类问题。

因此在 `shift_days=0`（不平移）时钉住：`winner_at` 必须逐 (instrument, 交易日)
等于 view 实际服务的 `report_period`。有了这条，白名单里放行该脚本直接读 store
才是安全的。
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from scripts.research.fundamental_ann_shift_sensitivity import winner_at
from src.data.pit.financial_pit_contract import (
    build_contract_frame,
    resolve_current_versions,
    select_disclosure_of_record,
)
from src.data.trading_calendar import StaticTradingCalendar
from src.research.financial_pit_view import FinancialPITDataView

_DAYS = [date(2022, 3, 30), date(2022, 3, 31), date(2022, 4, 1),
         date(2022, 4, 29), date(2022, 5, 5)]
_CAL = StaticTradingCalendar(_DAYS)

_INCOME_FIELDS = ("revenue", "total_revenue", "oper_cost", "sell_exp",
                  "admin_exp", "rd_exp", "int_exp", "fin_exp")


def _row(ts, end_date, uf, ann, **data):
    row = {
        "ts_code": ts, "end_date": end_date, "ann_date": ann, "f_ann_date": ann,
        "update_flag": uf, "_content_hash": f"h_{ts}_{end_date}_{uf}",
        "_fetch_batch": "b1", "_source_endpoint": "income",
    }
    for f in _INCOME_FIELDS:
        row[f] = data.get(f, pd.NA)
    return row


# 覆盖三种形态：两版本并存（须解析为 uf0）、uf1-only 期（须照常参与）、
# 以及一个只有单期的名字。
_SPECS = {
    "000001.SZ": [
        _row("000001.SZ", "20211231", "0", "20220331", revenue=100.0),
        _row("000001.SZ", "20211231", "1", "20220331", revenue=999.0),
        _row("000001.SZ", "20220331", "0", "20220429", revenue=30.0),
    ],
    "000002.SZ": [
        _row("000002.SZ", "20211231", "0", "20220331", revenue=200.0),
        _row("000002.SZ", "20220331", "1", "20220429", revenue=210.0),
    ],
    "000003.SZ": [
        _row("000003.SZ", "20220331", "1", "20220429", revenue=7.0),
    ],
}


@pytest.fixture
def store(tmp_path):
    inc = tmp_path / "income"
    inc.mkdir(parents=True)
    for ts, rows in _SPECS.items():
        pd.DataFrame(rows).to_parquet(inc / f"{ts}.parquet", index=False)
    return tmp_path


def test_winner_at_matches_the_view_on_every_sampled_day(store):
    view = FinancialPITDataView(store, _CAL, financial_issuers=frozenset())
    instruments = list(_SPECS)
    for td in _DAYS:
        served = view.as_of(td, ["revenue"], instruments,
                            include_report_periods=True)
        for ts in instruments:
            from_view = served.loc[ts, "_report_period__income"]
            expected = None if pd.isna(from_view) else str(from_view)

            # 逐步复刻 view 的三段链路（view 内部就是这三步），
            # 保证比对的是同一条规则而不是我另起的一条。
            raw = pd.read_parquet(store / "income" / f"{ts}.parquet")
            record = select_disclosure_of_record(
                resolve_current_versions(build_contract_frame(raw, _CAL)))
            got = winner_at(record, td)

            assert got == expected, (
                f"{ts} @ {td}: 诊断算出 {got!r}，view 实际服务 {expected!r} "
                "—— 审计器与被审计规则漂移了")
