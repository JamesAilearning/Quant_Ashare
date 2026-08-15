"""财报终端组的注册纪律（阶段8 基本面方向 · GP 接线）。

三条互相独立、缺一不可的性质：

1. **默认集不变** —— 财报组在 ``V1`` 之外。``pit_adapter._default_fields()``
   就是 ``tuple(FeatureRegistry.V1)``，若把新组 append 进 V1，所有
   ``fields=()`` 的存量 PIT run 会开始索取非 qlib 列；而 ``GPEngine`` 的
   ``_allowed_terminals`` 默认 ``None``（不限制），存量战役还会长出研究专用终端。
2. **白名单下点变异仍然变异** —— 采样池与替换池都必须按**已注册**终端取。
   若沿用手写的 legacy 组并集，只含财报终端的白名单交出来是**空集**，
   ``mutate_point`` 吞掉 GrammarError 返回原式 —— 整个战役的点变异**静默失效**。
3. **终端名与 charter 字段一一对应** —— 一个注册了却无处取数的终端，或一个
   有数却取不到的字段，都会在接线时变成沉默的坑。
"""
from __future__ import annotations

from random import Random

import pytest

from src.factor_mining.expression import feature_terminals
from src.factor_mining.grammar import (
    ExprType,
    FeatureRegistry,
    GrammarError,
    random_expression,
    sampling_pool,
)

# --- 1. 默认集不得被污染 ----------------------------------------------------

def test_financial_terminals_are_outside_the_default_set():
    assert set(FeatureRegistry.FINANCIAL_STATEMENT).isdisjoint(
        FeatureRegistry.V1)


def test_the_default_set_is_still_exactly_the_legacy_twelve():
    """存量默认面板必须逐字不变 —— 这是 opt-in 的全部意义。"""
    assert FeatureRegistry.V1 == (
        "$open", "$high", "$low", "$close",
        "$volume", "$money",
        "$pe", "$pb", "$ps", "$turnover_rate", "$circ_mv", "$total_mv",
    )
    assert len(FeatureRegistry.V1) == 12
    assert FeatureRegistry.current() == FeatureRegistry.V1


def test_pit_adapter_default_fields_are_unchanged():
    """默认字段直接取自 V1；财报列绝不能混进来。"""
    from src.factor_mining.pit_adapter import _default_fields

    assert _default_fields() == FeatureRegistry.V1
    assert not set(_default_fields()) & set(
        FeatureRegistry.FINANCIAL_STATEMENT)


def test_unrestricted_generation_never_reaches_financial_terminals():
    """没有白名单时，生成器只能采样默认集 —— 存量战役不会长出财报表达式。

    它们的面板里根本没有那些 key，长出来就是 KeyError。
    """
    rng = Random(20260814)
    financial = set(FeatureRegistry.FINANCIAL_STATEMENT)
    for _ in range(300):
        expr = random_expression(ExprType("FEATURE", "PURE"), 3, 2, rng)
        assert not (set(feature_terminals(expr)) & financial), expr.to_qlib_string()


def test_unrestricted_pool_is_the_default_set_only():
    for taint in ("PURE", "ADJ_TAINTED"):
        pool = set(sampling_pool(taint, None))
        assert pool <= set(FeatureRegistry.V1), taint
        assert not pool & set(FeatureRegistry.FINANCIAL_STATEMENT), taint


# --- 2. 白名单下必须仍能生成与变异 -------------------------------------------

def test_registered_pool_includes_financial_terminals_under_a_whitelist():
    allowed = frozenset({"$revenue", "$total_assets"})
    pool = set(sampling_pool("PURE", allowed))
    assert {"$revenue", "$total_assets"} <= pool


def test_generation_under_a_financial_whitelist_yields_those_terminals():
    allowed = frozenset({"$revenue", "$total_assets"})
    rng = Random(7)
    seen: set[str] = set()
    for _ in range(200):
        expr = random_expression(
            ExprType("FEATURE", "PURE"), 3, 2, rng, allowed_terminals=allowed)
        seen |= set(feature_terminals(expr))
    # 只长出白名单内的终端，且两个都能被采到（不是只碰得到一个）
    assert seen <= allowed          # feature_terminals 只回 $ 终端
    assert {"$revenue", "$total_assets"} <= seen


def test_point_mutation_still_mutates_under_a_financial_whitelist():
    """核心回归：只含财报终端的白名单下，点变异**不得**退化为 no-op。

    沿用手写 legacy 并集时这个交集是空的 —— GrammarError 被 mutate_point
    吞掉，表达式原样返回，整场战役的点变异静默失效。
    """
    from src.factor_mining.expression import Terminal
    from src.factor_mining.gp_engine import GPEngine

    allowed = frozenset({"$revenue", "$total_assets"})
    engine = GPEngine.__new__(GPEngine)          # 不跑完整 __init__
    engine.rng = Random(11)                       # type: ignore[attr-defined]
    engine._allowed_terminals = allowed           # noqa: SLF001

    got = {engine._random_terminal_same_type(       # noqa: SLF001
        ExprType("FEATURE", "PURE"), exclude="$revenue").name
        for _ in range(50)}
    assert got == {"$total_assets"}               # 换了人，且只在白名单内
    assert isinstance(Terminal("$revenue"), Terminal)


def test_single_terminal_whitelist_raises_rather_than_silently_holding():
    """白名单合法地只含一个同类型终端 —— 无从替换。

    这与"池搭错了"是两回事，但都不该静默：`_random_terminal_same_type`
    抛 GrammarError（其上层 `mutate_point` 才决定如何处置），不返回原式。
    """
    from src.factor_mining.gp_engine import GPEngine

    engine = GPEngine.__new__(GPEngine)
    engine.rng = Random(3)                        # type: ignore[attr-defined]
    engine._allowed_terminals = frozenset({"$revenue"})   # noqa: SLF001
    with pytest.raises(GrammarError, match="no alternative terminal"):
        engine._random_terminal_same_type(        # noqa: SLF001
            ExprType("FEATURE", "PURE"), exclude="$revenue")


# --- 3. 终端 ↔ charter 字段一一对应 -----------------------------------------

def test_every_financial_terminal_maps_to_a_charter_field():
    """注册了却无处取数的终端 = 接线时的沉默坑。"""
    from src.data.tushare.financial_statements import DATA_FIELDS

    charter = {f for fields in DATA_FIELDS.values() for f in fields}
    for terminal in FeatureRegistry.FINANCIAL_STATEMENT:
        assert terminal.startswith("$")
        assert terminal[1:] in charter, terminal


def test_every_charter_field_has_a_registered_terminal():
    """反方向：有数却取不到的字段，同样是坑。"""
    from src.data.tushare.financial_statements import DATA_FIELDS

    charter = {f for fields in DATA_FIELDS.values() for f in fields}
    registered = {t[1:] for t in FeatureRegistry.FINANCIAL_STATEMENT}
    assert registered == charter


def test_financial_terminals_are_pure_and_constructible():
    from src.factor_mining.expression import Terminal

    for terminal in FeatureRegistry.FINANCIAL_STATEMENT:
        assert FeatureRegistry.is_feature(terminal)
        assert FeatureRegistry.terminal_type(terminal) == ExprType(
            "FEATURE", "PURE")
        assert Terminal(terminal).name == terminal


def test_registered_of_taint_covers_both_groups():
    pure = set(FeatureRegistry.registered_of_taint("PURE"))
    assert set(FeatureRegistry.FINANCIAL_STATEMENT) <= pure
    assert set(FeatureRegistry.V1_SCALE_FREE) <= pure
    assert not set(FeatureRegistry.V1_RAW_PRICE) & pure
