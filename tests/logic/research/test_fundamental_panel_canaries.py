"""防线 (ii)：合成金丝雀 —— 注入的泄漏必须在进入因子评估之前就被拒。

**金丝雀的设计约束**（提案 codex #427 P1/r2 P1 两轮重设计后的定稿）：
金丝雀只能腐蚀 builder **自己能计算出来**的不变量。初版那种"未来值金丝雀"
（一个由 forward return 派生、却带着满足 `available_from <= T` 的看似合法证据的
值）是**装饰性的** —— builder 无从把它与真实财报值区分；而让 fixture 省略证据，
测到的只是"拒绝无证据输入"。

因此定为两条纯结构不变量：

① **提前公告**：证据 > 交易日 —— 直接违反 `available_from <= T`，可计算。
② **可用日单调性**：同一 instrument 的证据序列必须随交易日**非递减**
   （carry-forward 只会前进到更新的已公告期）—— 未来信息回填会打破单调性，
   无需知道值的语义即可判定。

**显式排除**"证据-值不同源"金丝雀：builder 没有独立的记录身份可用来识破
"P₂ 的值贴着 P₁ 的 provenance"这个谎；若唯一路径被完全绕过，builder 更是根本
不运行。值与 provenance 的对应性是 **view 的不变量**，由 canonical PIT battery
守，不在面板层重述 —— 否则那条测试只是在验证自己的 mock。

两条金丝雀都断言**拒绝发生在进入因子评估之前**：`build_fundamental_panel` 抛错，
调用方拿不到面板。
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.research.fundamental_panel import (
    FundamentalPanelError,
    _assert_evidence_gates_every_cell,
    _assert_evidence_is_monotonic,
)

_DAYS = [pd.Timestamp(2022, 4, 1), pd.Timestamp(2022, 4, 29),
         pd.Timestamp(2022, 5, 5)]
_INST = ["SZ000001", "SZ000002"]


def _frame(rows):
    f = pd.DataFrame(rows, index=pd.DatetimeIndex(_DAYS), columns=_INST)
    f.index.name = "datetime"
    f.columns.name = "instrument"
    return f


def _clean():
    """一份合法面板：证据恒 <= 交易日，且随交易日非递减。"""
    values = _frame([[100.0, pd.NA], [100.0, 210.0], [30.0, 210.0]])
    evidence = _frame([["20220401", pd.NA],
                       ["20220401", "20220429"],
                       ["20220505", "20220429"]])
    return {"$revenue": values}, {"$revenue": evidence}


# --- 基线：干净面板必须放行（非空性）--------------------------------------

def test_a_clean_panel_passes(monkeypatch):
    values, evidence = _clean()
    _assert_evidence_gates_every_cell(values, evidence, "")  # 不抛


# --- 金丝雀 ①：提前公告 ----------------------------------------------------

def test_canary_early_announcement_is_refused():
    """证据晚于其交易日 = 面板看见了尚未可用的申报。"""
    values, evidence = _clean()
    # 0401 这天贴上 0505 才可用的证据
    evidence["$revenue"].loc[_DAYS[0], "SZ000001"] = "20220505"
    with pytest.raises(FundamentalPanelError, match="dated AFTER its trade date"):
        _assert_evidence_gates_every_cell(values, evidence, "")


def test_canary_early_announcement_names_where():
    """拒绝必须指出具体 (日期, instrument) —— 不说在哪等于让人查一下午。"""
    values, evidence = _clean()
    evidence["$revenue"].loc[_DAYS[0], "SZ000002"] = "20220505"
    values["$revenue"].loc[_DAYS[0], "SZ000002"] = 1.0
    with pytest.raises(FundamentalPanelError, match=r"2022-04-01.*SZ000002"):
        _assert_evidence_gates_every_cell(values, evidence, "")


# --- 金丝雀 ②：值无证据 ----------------------------------------------------

def test_canary_value_without_evidence_is_refused():
    """有值却无证据 = builder 产出了一个无法归因到任何已公告披露的数字。

    这正是"无证据面板"，与泄漏面板不可区分，必须拒绝而不是返回。
    """
    values, evidence = _clean()
    values["$revenue"].loc[_DAYS[0], "SZ000002"] = 42.0   # 证据仍为 NA
    with pytest.raises(FundamentalPanelError, match="NO availability evidence"):
        _assert_evidence_gates_every_cell(values, evidence, "")


# --- 形状错位 --------------------------------------------------------------

def test_misaligned_evidence_is_refused():
    """证据帧形状对不上 = 无法逐 cell 归因，等同没有证据。"""
    values, evidence = _clean()
    evidence["$revenue"] = evidence["$revenue"].iloc[:2]
    with pytest.raises(FundamentalPanelError, match="does not align"):
        _assert_evidence_gates_every_cell(values, evidence, "")


# --- 金丝雀 ③：可用日单调性（回填检测）------------------------------------

def test_clean_panel_has_monotonic_evidence():
    _, evidence = _clean()
    _assert_evidence_is_monotonic(evidence, "")   # 不抛


def test_canary_backfilled_evidence_is_refused():
    """回填：更晚的交易日却服务了更早期的记录。

    构造成**每一格都满足 `available_from <= 交易日`**，只有序列倒退 ——
    这样它证明单调性是独立于金丝雀①的**第二把刀**：提前公告那条查不出它。
    """
    values, evidence = _clean()
    ev = evidence["$revenue"]
    ev.loc[_DAYS[1], "SZ000001"] = "20220429"   # 0429 服务 0429 的期（合法）
    ev.loc[_DAYS[2], "SZ000001"] = "20220401"   # 0505 却退回 0401 的期（倒退）
    with pytest.raises(FundamentalPanelError, match="goes BACKWARDS"):
        _assert_evidence_is_monotonic(evidence, "")
    # 同一份数据在金丝雀①下是干净的 —— 两条刀互不替代，缺一个就漏一类
    _assert_evidence_gates_every_cell(values, evidence, "")


def test_monotonicity_tolerates_carry_forward():
    """连续持有同一期（证据不变）是合法的，不得误判为回填。"""
    _, evidence = _clean()
    ev = evidence["$revenue"]
    ev.loc[_DAYS[2], "SZ000001"] = "20220401"
    ev.loc[_DAYS[1], "SZ000001"] = "20220401"
    ev.loc[_DAYS[0], "SZ000001"] = "20220401"
    _assert_evidence_is_monotonic(evidence, "")   # 不抛


def test_monotonicity_names_where_it_broke():
    values, evidence = _clean()
    ev = evidence["$revenue"]
    ev.loc[_DAYS[1], "SZ000002"] = "20220429"
    ev.loc[_DAYS[2], "SZ000002"] = "20220401"
    with pytest.raises(FundamentalPanelError, match=r"SZ000002.*2022-05-05"):
        _assert_evidence_is_monotonic(evidence, "")


# --- 边界：证据全 NA 的列不构成违规 ----------------------------------------

def test_all_na_evidence_column_is_not_a_violation():
    """尚无任何记录被服务的 instrument：值与证据同为 NA，合法。"""
    values = _frame([[pd.NA, pd.NA], [pd.NA, pd.NA], [pd.NA, pd.NA]])
    evidence = _frame([[pd.NA, pd.NA], [pd.NA, pd.NA], [pd.NA, pd.NA]])
    _assert_evidence_gates_every_cell({"$x": values}, {"$x": evidence}, "")
