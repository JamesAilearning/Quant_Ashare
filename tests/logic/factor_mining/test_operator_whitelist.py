"""算子采样池：默认 = 冻结 V1 基线，新算子 opt-in（codex #441 r6 P1）。

注册表可以增长，**默认采样池不许**：否则注册 coalesce 之后，重跑冻结的
pv preset（无显式白名单）会采样到冻结名单外的算子 —— 同一份 preset 与
seed 描述的是另一个实验。与财报终端的 opt-in 模式完全对称。
"""
from __future__ import annotations

from random import Random

from src.factor_mining.expression import Expression, OperatorCall
from src.factor_mining.gp_engine import GPConfig, GPEngine
from src.factor_mining.grammar import (
    REGISTRY,
    V1_OPERATORS,
    ExprType,
    random_expression,
)


def _operators_of(expr: Expression) -> set[str]:
    out: set[str] = set()
    stack = [expr]
    while stack:
        node = stack.pop()
        if isinstance(node, OperatorCall):
            out.add(node.op_name)
            stack.extend(node.children)
    return out


def test_default_pool_is_the_frozen_baseline_not_the_registry():
    assert "coalesce" in {op.name for op in REGISTRY.all_operators()}
    assert "coalesce" not in V1_OPERATORS
    assert len(V1_OPERATORS) == 28


def test_default_generation_never_samples_a_post_baseline_operator():
    rng = Random(20260818)
    seen: set[str] = set()
    for _ in range(300):
        expr = random_expression(ExprType("CSF", "PURE"), 4, 2, rng)
        seen |= _operators_of(expr)
    assert "coalesce" not in seen
    assert seen <= V1_OPERATORS


def test_an_explicit_whitelist_reaches_the_new_operator():
    """非空性：opt-in 真的能采到 —— 池收窄到必然出现的组合。"""
    rng = Random(7)
    allowed = frozenset({"coalesce", "cs_rank"})
    seen: set[str] = set()
    for _ in range(200):
        expr = random_expression(
            ExprType("CSF", "PURE"), 4, 2, rng, allowed_operators=allowed)
        ops = _operators_of(expr)
        assert ops <= allowed
        seen |= ops
    assert "coalesce" in seen


def test_whitelist_reaches_nested_subtrees():
    """白名单贯穿子树递归 —— 只在根层过滤的话，深层又掉回默认池。"""
    rng = Random(11)
    allowed = frozenset({"coalesce", "cs_rank", "add"})
    deep_ops: set[str] = set()
    for _ in range(200):
        expr = random_expression(
            ExprType("CSF", "PURE"), 5, 3, rng, allowed_operators=allowed)
        for node in _walk_children(expr):
            deep_ops |= _operators_of(node)
    assert deep_ops <= allowed


def _walk_children(expr):
    if isinstance(expr, OperatorCall):
        for child in expr.children:
            yield child
            yield from _walk_children(child)


def test_gp_engine_forwards_the_config_whitelist():
    engine = GPEngine(
        GPConfig(population_size=6, n_generations=1, max_depth=4,
                 seed=3, allowed_operators=("coalesce", "cs_rank")),
        __import__("src.factor_mining.fitness",
                   fromlist=["FitnessConfig"]).FitnessConfig())
    assert engine._allowed_operators == frozenset({"coalesce", "cs_rank"})
    engine_default = GPEngine(
        GPConfig(population_size=6, n_generations=1, seed=3),
        __import__("src.factor_mining.fitness",
                   fromlist=["FitnessConfig"]).FitnessConfig())
    assert engine_default._allowed_operators is None
