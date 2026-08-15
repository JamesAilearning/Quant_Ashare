"""跨端点同期强制：遮蔽必须落在**终端层**。

view 的各 endpoint 是**独立服务**的，所以同一天 income 与 balancesheet 的
report_period 可以不同。一个跨端点的比率若不加约束，会静默算出**混季**值。

遮蔽点为什么必须在终端层 —— 两个方向的拓扑，缺一个就漏一类：

* **父级滚动之下**：`ts_mean(div_safe($revenue, $total_assets), 5)` 在 T 日两端点
  可能同期，但滚动窗内**更早日期**的混季比率仍被平均进来；
* **滚动子节点之上**：`add(ts_mean($revenue, 5), ts_mean($total_assets, 5))` 的第一个
  跨端点节点是 `add`，到那时两个子树**各自已经**把错期历史聚合完了。

终端层是唯一不留拓扑口子的位置：没有任何算子（时序的或横截面的）能观察到
一个错期日期。
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.factor_mining.evaluator import (
    align_periods_at_terminals,
    evaluate_expression,
)
from src.factor_mining.expression import parse_expression

_DAYS = pd.DatetimeIndex(pd.date_range("2022-05-02", periods=10, freq="D"))
_INST = ["SZ000001", "SZ000002"]


def _frame(values):
    return pd.DataFrame(values, index=_DAYS, columns=_INST, dtype="float64")


def _periods(values):
    return pd.DataFrame(values, index=_DAYS, columns=_INST, dtype="object")


def _panel_and_periods(*, misaligned_on=()):
    """两个端点的终端；`misaligned_on` 指定哪些行上 balancesheet 落后一期。"""
    revenue = _frame([[10.0, 20.0]] * 10)
    assets = _frame([[100.0, 200.0]] * 10)
    rev_p = _periods([["20220331", "20220331"]] * 10)
    ast_rows = []
    for i in range(10):
        period = "20211231" if i in misaligned_on else "20220331"
        ast_rows.append([period, period])
    ast_p = _periods(ast_rows)
    panel = {"$revenue": revenue, "$total_assets": assets}
    periods = {"$revenue": rev_p, "$total_assets": ast_p}
    return panel, periods


# --- 基本语义 ---------------------------------------------------------------

def test_same_period_cells_are_untouched():
    panel, periods = _panel_and_periods()
    expr = parse_expression("div_safe($revenue, $total_assets)")
    masked = align_periods_at_terminals(panel, periods, expr)
    pd.testing.assert_frame_equal(masked["$revenue"], panel["$revenue"])
    pd.testing.assert_frame_equal(masked["$total_assets"], panel["$total_assets"])


def test_misaligned_cells_are_masked_on_every_referenced_terminal():
    panel, periods = _panel_and_periods(misaligned_on=(2,))
    expr = parse_expression("div_safe($revenue, $total_assets)")
    masked = align_periods_at_terminals(panel, periods, expr)
    for terminal in ("$revenue", "$total_assets"):
        col = masked[terminal].iloc[2]
        assert col.isna().all(), terminal          # 错期日全列 NA
        assert masked[terminal].iloc[1].notna().all()   # 其余日不受影响


def test_a_single_endpoint_expression_is_never_masked():
    """单端点表达式的端点集只有一个元素 —— 天然不遮蔽。"""
    panel, periods = _panel_and_periods(misaligned_on=(2, 5))
    expr = parse_expression("ts_mean($revenue, 5)")
    masked = align_periods_at_terminals(panel, periods, expr)
    assert masked is panel                         # 原样返回，零改动


def test_missing_period_frame_fails_loud():
    """终端没有 period 帧 = 无法证明同期，拒绝而不是不遮蔽。"""
    panel, periods = _panel_and_periods()
    del periods["$total_assets"]
    expr = parse_expression("div_safe($revenue, $total_assets)")
    with pytest.raises(KeyError, match="cross-endpoint alignment needs"):
        align_periods_at_terminals(panel, periods, expr)


def test_na_period_counts_as_disagreement():
    """一侧期为 NA、另一侧有值 —— 无法确认同期，按不一致处理。"""
    panel, periods = _panel_and_periods()
    periods["$total_assets"].iloc[4] = [pd.NA, pd.NA]
    expr = parse_expression("div_safe($revenue, $total_assets)")
    masked = align_periods_at_terminals(panel, periods, expr)
    assert masked["$revenue"].iloc[4].isna().all()


# --- 两个方向的拓扑（核心）-------------------------------------------------

# 错期日固定在索引 5；5 日窗未满的前 4 行本就是 NA，所以断言落在窗已满之后：
# 索引 4 的窗是 [0..4]（不含错期日），索引 5..9 的窗都含错期日。
_MISALIGNED_AT = 5


def _assert_masking_bites(expr_text, panel, periods):
    """遮蔽必须**恰好**吃掉窗内含错期日的那些行，且不多吃。"""
    expr = parse_expression(expr_text)
    masked = evaluate_expression(expr, panel, periods=periods)
    unmasked = evaluate_expression(expr, panel)

    # 窗未覆盖错期日的最后一行：两者都有值且相等（不多吃）
    assert masked.iloc[4].notna().all()
    pd.testing.assert_series_equal(masked.iloc[4], unmasked.iloc[4])

    # 窗覆盖错期日的每一行：遮蔽后为 NA，而不遮蔽时是有值的（非空性）
    for i in range(_MISALIGNED_AT, 10):
        assert masked.iloc[i].isna().all(), i
        assert unmasked.iloc[i].notna().all(), i


def test_rolling_parent_cannot_average_mixed_quarter_history():
    """父级滚动之下：T 日两端点同期，但窗内更早日期错期。

    只遮蔽最终 cell 会在 T 产出一个被污染的非 NA 值；终端层遮蔽让那个更早的
    输入在被 ts_mean 消费**之前**就已是 NA，于是 NA 正确地传播到整个窗。
    """
    panel, periods = _panel_and_periods(misaligned_on=(_MISALIGNED_AT,))
    _assert_masking_bites(
        "ts_mean(div_safe($revenue, $total_assets), 5)", panel, periods)


def test_rolling_children_combined_at_the_top_are_also_masked():
    """滚动子节点之上：第一个跨端点节点是 `add`，那时子树已各自聚合完历史。

    若遮蔽落在"第一个跨端点子树"，这个拓扑会整个漏掉；终端层遮蔽让错期日在
    **两个**子树的输入里就已是 NA。
    """
    panel, periods = _panel_and_periods(misaligned_on=(_MISALIGNED_AT,))
    _assert_masking_bites(
        "add(ts_mean($revenue, 5), ts_mean($total_assets, 5))", panel, periods)


def test_masking_is_opt_in_for_price_volume_callers():
    """不传 periods 时求值路径逐字不变 —— 存量量价调用方零影响。"""
    panel, periods = _panel_and_periods(misaligned_on=tuple(range(10)))
    expr = parse_expression("div_safe($revenue, $total_assets)")
    got = evaluate_expression(expr, panel)
    assert got.notna().all().all()
