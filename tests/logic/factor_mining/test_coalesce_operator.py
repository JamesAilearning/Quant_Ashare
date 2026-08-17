"""coalesce 算子：first-non-NA 选择，为冻结 C3 的"每期先合并再差分"而生。

语义红线：coalesce 绝不发明值（双 NA 保持 NA），且只在同 taint 输入间
选择 —— 混 taint 会让 cell 的量纲随"哪边是 NA"悄悄切换。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.factor_mining.evaluator import evaluate_expression
from src.factor_mining.expression import parse_expression
from src.factor_mining.grammar import REGISTRY, ExprType, GrammarError

_IDX = pd.DatetimeIndex(pd.date_range("2024-01-01", periods=3, freq="D"))
_COLS = ["SZ000000", "SZ000001"]


def _f(rows):
    return pd.DataFrame(rows, index=_IDX, columns=_COLS, dtype="float64")


def test_first_non_na_selection_and_both_na_stays_na():
    panel = {
        "$adv_receipts": _f([[1.0, np.nan], [np.nan, np.nan], [3.0, 4.0]]),
        "$contract_liab": _f([[9.0, 2.0], [np.nan, 8.0], [9.0, 9.0]]),
    }
    got = evaluate_expression(
        parse_expression("coalesce($adv_receipts, $contract_liab)"), panel)
    # a 有值取 a；a NA 取 b；双 NA 保持 NA —— 绝不发明值。
    expected = _f([[1.0, 2.0], [np.nan, 8.0], [3.0, 4.0]])
    pd.testing.assert_frame_equal(got, expected)


def test_first_wins_makes_it_noncommutative():
    op = REGISTRY.get("coalesce")
    assert op.commutative is False


def test_taint_mixing_is_refused():
    rule = REGISTRY.get("coalesce").output_type_fn
    pure = ExprType("FLOAT", "PURE")
    adj = ExprType("FLOAT", "ADJ_TAINTED")
    assert rule(pure, pure) == pure
    assert rule(adj, adj) == adj
    with pytest.raises(GrammarError, match="taint mismatch"):
        rule(pure, adj)


def test_the_frozen_c3_shape_parses_and_evaluates():
    """冻结 C3 的 coalesce-then-difference 形在求值器上端到端可走。"""
    panel = {
        "$adv_receipts": _f([[np.nan, 5.0]] * 3),
        "$contract_liab": _f([[7.0, np.nan]] * 3),
        "$adv_receipts__prior": _f([[np.nan, 3.0]] * 3),
        "$contract_liab__prior": _f([[4.0, np.nan]] * 3),
    }
    expr = parse_expression(
        "sub(coalesce($adv_receipts, $contract_liab), "
        "coalesce($adv_receipts__prior, $contract_liab__prior))")
    got = evaluate_expression(expr, panel)
    # 每期先合并（NA 侧取另一列）再差分：7-4=3 / 5-3=2。
    pd.testing.assert_frame_equal(got, _f([[3.0, 2.0]] * 3))
