"""防线 (i)：面板的 PIT 属性 —— 在合成小店上钉住公告日门控。

这一条不是"再测一遍 view"：它测的是**桥**在把 view 的逐日响应堆成面板的过程中
有没有把 PIT 语义弄丢。所以每条断言都直接比对 `panel[T] == view.as_of(T)`，
并覆盖边界日（公告日当天、前一日、之后首个交易日）。
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from src.data.trading_calendar import StaticTradingCalendar
from src.research.financial_pit_view import FinancialPITDataView
from src.research.fundamental_panel import (
    FundamentalPanelError,
    build_fundamental_panel,
    to_field,
    to_terminal,
)

# 20220331 公告 -> 20220401 可用；20220429 公告 -> 20220505 可用
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


@pytest.fixture
def store(tmp_path):
    inc = tmp_path / "income"
    inc.mkdir(parents=True)
    # 000001.SZ: Q4-2021 原始 + 重述，再加 Q1-2022；rd_exp 全程未披露
    pd.DataFrame([
        _row("000001.SZ", "20211231", "0", "20220331", revenue=100.0),
        _row("000001.SZ", "20211231", "1", "20220331", revenue=999.0),
        _row("000001.SZ", "20220331", "0", "20220429", revenue=30.0),
    ]).to_parquet(inc / "000001.SZ.parquet", index=False)
    # 000002.SZ: 只有一期，且只有 uf1 行（provider 未留原始）
    pd.DataFrame([
        _row("000002.SZ", "20220331", "1", "20220429", revenue=210.0),
    ]).to_parquet(inc / "000002.SZ.parquet", index=False)
    return tmp_path


@pytest.fixture
def view(store):
    return FinancialPITDataView(store, _CAL, financial_issuers=frozenset())


def _build(view, **kw):
    return build_fundamental_panel(
        view, ["revenue"], _DAYS, ["000001.SZ", "000002.SZ"], **kw)


# --- 逐日与 view 同源 -------------------------------------------------------

def test_every_cell_equals_the_view_at_that_date(view):
    """面板不是"另一套 as-of 实现"，它必须逐 cell 等于 view 的当日响应。"""
    got = _build(view)
    values = got.panels["$revenue"]
    for td in _DAYS:
        served = view.as_of(td, ["revenue"], ["000001.SZ", "000002.SZ"])
        for ts, qlib in (("000001.SZ", "SZ000001"), ("000002.SZ", "SZ000002")):
            expected = served.loc[ts, "revenue"]
            actual = values.loc[pd.Timestamp(td), qlib]
            if pd.isna(expected):
                assert pd.isna(actual), (td, ts)
            else:
                assert actual == expected, (td, ts)


# --- 公告日边界 -------------------------------------------------------------

def test_invisible_before_availability_and_on_the_announcement_day(view):
    values = _build(view).panels["$revenue"]
    col = values["SZ000001"]
    assert pd.isna(col[pd.Timestamp(2022, 3, 30)])   # 公告前一日
    assert pd.isna(col[pd.Timestamp(2022, 3, 31)])   # 公告日当天仍不可见
    assert col[pd.Timestamp(2022, 4, 1)] == 100.0    # 严格次日起可见


def test_pending_filing_does_not_blank_an_already_available_cell(view):
    """新一期未可用时，继续服务旧期 —— 不是把 cell 置 NA。

    写成置 NA 会在每次申报前后制造人为缺口，同时移动覆盖率与因子值。
    """
    col = _build(view).panels["$revenue"]["SZ000001"]
    # Q1-2022 公告于 0429、0505 才可用；0429 当天仍应服务 Q4-2021 的 100
    assert col[pd.Timestamp(2022, 4, 29)] == 100.0
    assert col[pd.Timestamp(2022, 5, 5)] == 30.0


def test_cell_is_na_only_when_nothing_is_available_yet(view):
    """只有尚无任何已可用期时才 NA —— 000002.SZ 首期可用前即此情形。"""
    col = _build(view).panels["$revenue"]["SZ000002"]
    assert pd.isna(col[pd.Timestamp(2022, 4, 1)])
    assert col[pd.Timestamp(2022, 5, 5)] == 210.0


def test_restated_period_resolves_to_its_original(view):
    """版本选择决定一个期取哪个值：uf0 原始值，绝不被 uf1 重述顶掉。"""
    col = _build(view).panels["$revenue"]["SZ000001"]
    assert col[pd.Timestamp(2022, 4, 1)] == 100.0   # 不是 999.0


def test_update_flag_1_only_period_is_served(view):
    """该期无 uf0 行时，uf1 就是它的 disclosure of record —— 照常服务。"""
    col = _build(view).panels["$revenue"]["SZ000002"]
    assert col[pd.Timestamp(2022, 5, 5)] == 210.0


def test_missing_field_stays_na_never_imputed(view):
    """rd_exp 全程未披露 → NA，绝不填 0 / 中位数 / 最近值。"""
    got = build_fundamental_panel(
        view, ["rd_exp"], _DAYS, ["000001.SZ"])
    assert got.panels["$rd_exp"]["SZ000001"].isna().all()


# --- 证据 -------------------------------------------------------------------

def test_evidence_has_the_same_shape_and_gates_every_value(view):
    got = _build(view)
    values, ev = got.panels["$revenue"], got.evidence["$revenue"]
    assert values.shape == ev.shape
    assert values.index.equals(ev.index)
    assert values.columns.equals(ev.columns)
    # 每个非 NA 值都有证据，且证据不晚于其交易日
    for td in values.index:
        for inst in values.columns:
            if pd.notna(values.loc[td, inst]):
                stamp = ev.loc[td, inst]
                assert pd.notna(stamp)
                assert stamp <= td.strftime("%Y%m%d")


def test_served_record_with_na_value_still_carries_evidence(view):
    """证据记的是"服务了哪条披露"，不是"值是否有效"。

    rd_exp 未披露，但 000001.SZ 在 0401 起确有一条被服务的记录。
    """
    got = build_fundamental_panel(view, ["rd_exp"], _DAYS, ["000001.SZ"])
    ev = got.evidence["$rd_exp"]["SZ000001"]
    assert pd.isna(got.panels["$rd_exp"]["SZ000001"][pd.Timestamp(2022, 4, 1)])
    assert ev[pd.Timestamp(2022, 4, 1)] == "20220401"
    # 尚无记录被服务的日期，证据才是 NA
    assert pd.isna(ev[pd.Timestamp(2022, 3, 31)])


def test_evidence_moves_with_the_served_period(view):
    ev = _build(view).evidence["$revenue"]["SZ000001"]
    assert ev[pd.Timestamp(2022, 4, 1)] == "20220401"
    assert ev[pd.Timestamp(2022, 4, 29)] == "20220401"   # 仍服务旧期
    assert ev[pd.Timestamp(2022, 5, 5)] == "20220505"    # 新期可用后移动


# --- 报告期 provenance -------------------------------------------------------

def test_panel_carries_the_served_report_period(view):
    periods = _build(view).periods["$revenue"]["SZ000001"]
    assert periods[pd.Timestamp(2022, 4, 1)] == "20211231"
    assert periods[pd.Timestamp(2022, 5, 5)] == "20220331"


def test_prior_period_leg_carries_its_own_provenance(view):
    got = build_fundamental_panel(
        view, ["revenue"], _DAYS, ["000001.SZ"], include_prior_period=True)
    at = pd.Timestamp(2022, 5, 5)
    assert got.panels["$revenue"]["SZ000001"][at] == 30.0
    assert got.prior_panels["$revenue"]["SZ000001"][at] == 100.0
    assert got.prior_periods["$revenue"]["SZ000001"][at] == "20211231"
    # prior 的证据是它自己的可用日，不是当期的
    assert got.prior_evidence["$revenue"]["SZ000001"][at] == "20220401"
    assert got.evidence["$revenue"]["SZ000001"][at] == "20220505"


def test_prior_frames_absent_unless_requested(view):
    got = _build(view)
    assert got.prior_panels == {}
    assert got.prior_evidence == {}
    assert got.prior_periods == {}


# --- 命名空间 ---------------------------------------------------------------

def test_instruments_are_emitted_in_qlib_namespace(view):
    """view 说 ts_code，GP 面板说 qlib 标签，两者零交集。"""
    cols = list(_build(view).panels["$revenue"].columns)
    assert cols == ["SZ000001", "SZ000002"]
    assert not any("." in c for c in cols)


def test_qlib_input_labels_are_accepted_too(view):
    got = build_fundamental_panel(
        view, ["revenue"], _DAYS, ["SZ000001"])
    assert list(got.panels["$revenue"].columns) == ["SZ000001"]


# --- 终端名映射 -------------------------------------------------------------

def test_panel_keys_are_terminal_form(view):
    assert set(_build(view).panels) == {"$revenue"}


def test_terminal_and_field_names_round_trip():
    assert to_terminal("revenue") == "$revenue"
    assert to_field("$revenue") == "revenue"


def test_unprefixed_key_is_refused():
    with pytest.raises(FundamentalPanelError, match="not a terminal name"):
        to_field("revenue")


@pytest.mark.parametrize("bad", ["$not_a_field", "$", "$REVENUE", "$revenue "])
def test_terminal_naming_no_charter_field_is_refused(bad):
    """前缀对但名字不指向任何 charter 字段 —— 也必须在桥边界拒绝。

    只查 `$` 前缀会把拼错的终端一路放行，最后变成一次令人困惑的查表落空，
    而不是 fail-loud（codex #433 P2）。直接测 `to_field`：此前唯一相关的用例
    走的是 build_fundamental_panel，依赖 view 先拒绝，所以这条回归会漏
    （codex #433 r2 P1）。
    """
    with pytest.raises(FundamentalPanelError, match="maps to no charter field"):
        to_field(bad)


def test_every_charter_field_round_trips_through_the_mapping():
    """非空性：合法字段必须全部通得过，拒绝不能退化成拒绝一切。"""
    from src.research.financial_pit_view import _FIELD_ENDPOINT

    for field in _FIELD_ENDPOINT:
        assert to_field(to_terminal(field)) == field


def test_unknown_charter_field_fails_loud(view):
    with pytest.raises(Exception):  # noqa: B017 - view refuses first
        build_fundamental_panel(view, ["not_a_field"], _DAYS, ["000001.SZ"])


# --- 拒绝而不是兜底 ---------------------------------------------------------

def test_group_resolver_is_refused_not_silently_ignored(view):
    """行业中性化属后续 change；绝不以当前快照兜底。"""
    with pytest.raises(FundamentalPanelError, match="group_resolver"):
        build_fundamental_panel(
            view, ["revenue"], _DAYS, ["000001.SZ"],
            group_resolver=lambda td, insts: {})


def test_empty_inputs_are_refused(view):
    with pytest.raises(FundamentalPanelError, match="fields is empty"):
        build_fundamental_panel(view, [], _DAYS, ["000001.SZ"])
    with pytest.raises(FundamentalPanelError, match="trade_dates is empty"):
        build_fundamental_panel(view, ["revenue"], [], ["000001.SZ"])


def test_single_string_is_refused(view):
    with pytest.raises(FundamentalPanelError, match="COLLECTIONS"):
        build_fundamental_panel(view, "revenue", _DAYS, ["000001.SZ"])


# --- prior 期必须进求值 mapping（codex #433 P1）------------------------------

def test_as_evaluation_mapping_folds_prior_in_as_terminal_keys(view):
    """prior 只挂在旁边 = AST 引用不到 = Δ 类因子根本写不出来。

    evaluator 按**名字**对值 mapping 解析终端，所以 prior 必须以
    `$field__prior` 这个 key 出现在同一个 mapping 里。
    """
    got = build_fundamental_panel(
        view, ["revenue"], _DAYS, ["000001.SZ"], include_prior_period=True)
    values, periods = got.as_evaluation_mapping()
    assert set(values) == {"$revenue", "$revenue__prior"}
    assert set(periods) == {"$revenue", "$revenue__prior"}


def test_the_folded_prior_frames_carry_the_prior_values_and_periods(view):
    got = build_fundamental_panel(
        view, ["revenue"], _DAYS, ["000001.SZ"], include_prior_period=True)
    values, periods = got.as_evaluation_mapping()
    at = pd.Timestamp(2022, 5, 5)
    assert values["$revenue"]["SZ000001"][at] == 30.0
    assert values["$revenue__prior"]["SZ000001"][at] == 100.0
    assert periods["$revenue"]["SZ000001"][at] == "20220331"
    assert periods["$revenue__prior"]["SZ000001"][at] == "20211231"


def test_without_prior_the_mapping_is_just_the_current_panel(view):
    got = _build(view)
    values, periods = got.as_evaluation_mapping()
    assert set(values) == {"$revenue"}
    assert not any(k.endswith("__prior") for k in values)
    assert not any(k.endswith("__prior") for k in periods)


# 注：`PRIOR_SUFFIX` 与 grammar 的 `FeatureRegistry.PRIOR_SUFFIX` 必须一致，
# 但那个常量在 #437（GP 接线）里。**跨 PR 的守卫现在加就是悬空守卫** ——
# 待两个 PR 都进 main 后于 PR-4 补上逐字相等的断言。


# --- period token 在桥内规范化（codex #433 r7 P1）---------------------------

def test_float_spelled_end_dates_are_canonicalised_in_the_period_frames(
        tmp_path):
    """两个 store 可以把**同一个季度**拼成 20220331 与 20220331.0。

    view 保留各自原拼写；若桥原样导出，跨端点对齐掩码会把**真同期**的 cell
    按拼写差异判成错期 —— 混端点因子全部被错误遮蔽。规范化放在**帧的出生地**
    （桥），所有下游消费者一次受保护。
    """
    inc = tmp_path / "income"
    inc.mkdir(parents=True)
    row = _row("000001.SZ", "20220331", "0", "20220331", revenue=10.0)
    row["end_date"] = 20220331.0                    # 精确 .0 float 拼写
    pd.DataFrame([row]).to_parquet(inc / "000001.SZ.parquet", index=False)

    v = FinancialPITDataView(tmp_path, _CAL, financial_issuers=frozenset())
    got = build_fundamental_panel(
        v, ["revenue"], [date(2022, 4, 1)], ["000001.SZ"],
        include_prior_period=True)
    served = got.periods["$revenue"].loc[pd.Timestamp(2022, 4, 1), "SZ000001"]
    assert served == "20220331"                     # 不是 "20220331.0"


# --- 输入守卫补全（codex #433 r8 P2 ×2）-------------------------------------

def test_empty_instruments_are_refused(view):
    """空宇宙会"成功"：零列面板看起来有效，配置错误被静默吞掉。"""
    with pytest.raises(FundamentalPanelError, match="instruments is empty"):
        build_fundamental_panel(view, ["revenue"], _DAYS, [])


def test_duplicate_fields_are_refused(view):
    """重复字段会塌进一个累加器 + view 返回重名列 → 偶然的 pandas 形状错误。

    必须以本契约文档化的 FundamentalPanelError 拒绝，而不是让实现细节炸出来。
    """
    with pytest.raises(FundamentalPanelError, match="duplicates"):
        build_fundamental_panel(
            view, ["revenue", "revenue"], _DAYS, ["000001.SZ"])


# --- prior 腿不做单调性 + 金融排除后空宇宙拒绝（codex #433 r9）---------------

def test_prior_evidence_may_legitimately_go_backwards(tmp_path):
    """prior 腿会**换角色**，它的证据合法倒退 —— 单调性金丝雀不适用于它。

    时间线（codex 给的构造）：Q4 当期于 0401 可用；其迟交的 Q3 prior 于
    0429 才到（prior 证据 = 0429）；0505 Q1 成为当期，prior 腿**切换**到
    Q4 —— 其证据是 0401，一次**倒退**，而全程零泄漏。
    """
    days = [date(2022, 3, 31), date(2022, 4, 1), date(2022, 4, 29),
            date(2022, 5, 5)]
    cal = StaticTradingCalendar(days)
    inc = tmp_path / "income"
    inc.mkdir(parents=True)
    pd.DataFrame([
        # 公告日 -> 可用日（严格次交易日）：0331->0401, 0401->0429, 0429->0505
        _row("000001.SZ", "20210930", "0", "20220401", revenue=5.0),   # Q3 迟交,0429 可用
        _row("000001.SZ", "20211231", "0", "20220331", revenue=10.0),  # Q4,0401 可用
        _row("000001.SZ", "20220331", "0", "20220429", revenue=20.0),  # Q1,0505 可用
    ]).to_parquet(inc / "000001.SZ.parquet", index=False)
    v = FinancialPITDataView(tmp_path, cal, financial_issuers=frozenset())

    got = build_fundamental_panel(
        v, ["revenue"], days, ["000001.SZ"], include_prior_period=True)
    ev = got.prior_evidence["$revenue"]["SZ000001"].dropna()
    # 0429：当期 Q4、prior=Q3（证据 20220429）；0505：当期切到 Q1、prior 切到
    # Q4（证据 20220401）—— 序列 [20220429, 20220401]，确实倒退
    assert list(ev) == ["20220429", "20220401"], list(ev)
    assert not ev.is_monotonic_increasing
    # 但 gate 不变式仍然全程成立
    for when, stamp in ev.items():
        assert stamp <= when.strftime("%Y%m%d")


def test_all_financial_universe_is_refused(tmp_path):
    """请求非空、但全被金融排除 —— 与空请求同一类配置错误，晚一层暴露。"""
    inc = tmp_path / "income"
    inc.mkdir(parents=True)
    pd.DataFrame([
        _row("600000.SH", "20211231", "0", "20220331", revenue=1.0),
    ]).to_parquet(inc / "600000.SH.parquet", index=False)
    v = FinancialPITDataView(
        tmp_path, _CAL, financial_issuers=frozenset({"600000.SH"}))
    with pytest.raises(FundamentalPanelError, match="no effective instruments"):
        build_fundamental_panel(v, ["revenue"], _DAYS, ["600000.SH"])


# --- 三元解包契约 + 公开 builder 的日期归一（codex #433 r16 P2 ×2）----------

def test_three_value_unpacking_contract_holds(view):
    """规格写的就是 `panels, evidence, periods = build_...` —— 六字段
    NamedTuple 让这句必炸（too many values to unpack），位置字段的增长悄悄
    改写了公开契约。现在迭代恰好产出三元，prior 只走命名属性。
    """
    panels, evidence, periods = build_fundamental_panel(
        view, ["revenue"], _DAYS, ["000001.SZ"], include_prior_period=True)
    assert set(panels) == {"$revenue"}
    assert set(evidence) == {"$revenue"}
    assert set(periods) == {"$revenue"}


def test_mixed_date_types_are_accepted_by_the_builder(view):
    """date/datetime/Timestamp 混合满足注解且 view 接受 —— 公开 builder
    不得先在 sorted(set(...)) 上炸掉。"""
    from datetime import datetime as _dt

    mixed = [pd.Timestamp(_DAYS[0]), _dt(2022, 4, 1), _DAYS[4]]
    got = build_fundamental_panel(view, ["revenue"], mixed, ["000001.SZ"])
    assert got.panels["$revenue"].shape[0] == 3
    assert got.panels["$revenue"]["SZ000001"][pd.Timestamp(2022, 4, 1)] == 100.0


def test_served_cell_with_blank_period_is_refused(tmp_path):
    """空 end_date：契约容许 None、view 照常服务 —— 值与证据都在、期为 NA。

    对齐逻辑靠期回答同期问题，一个说不出自己期的观测恰是整条链要挡的
    不可证明 cell（codex #433 r18 P2）。拒绝而不是返回。
    """
    inc = tmp_path / "income"
    inc.mkdir(parents=True)
    row = _row("000001.SZ", "", "0", "20220331", revenue=10.0)   # 空 end_date
    pd.DataFrame([row]).to_parquet(inc / "000001.SZ.parquet", index=False)
    v = FinancialPITDataView(tmp_path, _CAL, financial_issuers=frozenset())
    with pytest.raises(FundamentalPanelError, match="report .*period is blank"):
        build_fundamental_panel(v, ["revenue"], _DAYS, ["000001.SZ"])
