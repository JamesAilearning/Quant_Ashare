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


def test_an_unknown_whitelist_name_is_refused_everywhere():
    """typo 的白名单静默收窄搜索、config dump 却仍宣称原池 —— 两个入口
    （生成器与引擎）都必须在生成前拒绝（codex #441 r7 P2）。"""
    import pytest

    from src.factor_mining.fitness import FitnessConfig
    from src.factor_mining.grammar import GrammarError

    with pytest.raises(GrammarError, match="unregistered|refusing"):
        random_expression(
            ExprType("CSF", "PURE"), 4, 2, Random(1),
            allowed_operators=frozenset({"cs_rank", "typo_op"}))
    with pytest.raises(GrammarError, match="unregistered"):
        GPEngine(GPConfig(allowed_operators=("cs_rank", "typo_op")),
                 FitnessConfig())


# --- 已建立终端池的守卫（共享打分/恢复语义）--------------------------------
#
# 这些用例只依赖**面板推导**的池（run 记录的那一个），不涉及冻结白名单
# —— 白名单机制属预注册协议 PR，与本 PR 分开。

def _tiny_panel(keys):
    import pandas as pd
    idx = pd.DatetimeIndex(pd.date_range("2024-01-01", periods=6, freq="D"))
    cols = ["SZ000001", "SZ000002", "SZ000003"]
    return {k: pd.DataFrame(1.0, index=idx, columns=cols) for k in keys}


def _engine(**gp_kw):
    from src.factor_mining.fitness import FitnessConfig

    return GPEngine(GPConfig(population_size=4, n_generations=1, max_depth=3,
                             seed=5, **gp_kw), FitnessConfig())


def test_score_expression_without_a_whitelist_uses_panel_keys():
    """全新引擎：池 = 面板键。"""
    import pandas as pd
    import pytest

    from src.factor_mining.expression import parse_expression
    from src.factor_mining.grammar import GrammarError

    panel = _tiny_panel(["$close", "$volume"])
    fwd = pd.DataFrame(0.0, index=list(panel.values())[0].index,
                       columns=list(panel.values())[0].columns)
    engine = _engine()
    engine.score_expression(parse_expression("cs_rank($volume)"), panel, fwd)
    with pytest.raises(GrammarError, match="outside this engine's sampling pool"):
        engine.score_expression(parse_expression("cs_rank($revenue)"),
                                panel, fwd)


def test_scoring_after_a_run_keeps_that_run_s_terminal_pool():
    """跑过的引擎带着那次 run 的池；之后用更宽的面板打分不得放宽它。"""
    import pandas as pd
    import pytest

    from src.factor_mining.expression import parse_expression
    from src.factor_mining.grammar import GrammarError

    narrow = _tiny_panel(["$revenue"])
    fwd = pd.DataFrame(0.0, index=list(narrow.values())[0].index,
                       columns=list(narrow.values())[0].columns)
    engine = _engine()
    engine.run(narrow, fwd, n_generations=0)
    assert engine._allowed_terminals == frozenset({"$revenue"})
    wider = _tiny_panel(["$revenue", "$volume"])
    with pytest.raises(GrammarError, match="outside this engine's sampling pool"):
        engine.score_expression(parse_expression("cs_rank($volume)"),
                                wider, fwd)


def test_a_v1_run_keeps_the_v1_pool_when_scoring_later():
    """跑过之后 `_allowed_terminals is None` 是"V1 池"的哨兵，不是"未知"。"""
    import pandas as pd
    import pytest

    from src.factor_mining.expression import parse_expression
    from src.factor_mining.grammar import FeatureRegistry, GrammarError

    v1_panel = _tiny_panel(list(FeatureRegistry.V1))
    fwd = pd.DataFrame(0.0, index=list(v1_panel.values())[0].index,
                       columns=list(v1_panel.values())[0].columns)
    engine = _engine()
    engine.run(v1_panel, fwd, n_generations=0)
    assert engine._allowed_terminals is None and engine._has_run
    wider = _tiny_panel(list(FeatureRegistry.V1) + ["$revenue"])
    engine.score_expression(parse_expression("cs_rank($volume)"), wider, fwd)
    with pytest.raises(GrammarError, match="outside this engine's sampling pool"):
        engine.score_expression(parse_expression("cs_rank($revenue)"),
                                wider, fwd)


def test_a_resumed_run_may_not_change_the_terminal_pool():
    """同一个实验不得横跨两个搜索空间：已跑过的引擎换面板 resume 即拒。"""
    import pandas as pd
    import pytest

    from src.factor_mining.grammar import GrammarError

    narrow = _tiny_panel(["$revenue"])
    fwd = pd.DataFrame(0.0, index=list(narrow.values())[0].index,
                       columns=list(narrow.values())[0].columns)
    engine = _engine()
    engine.run(narrow, fwd, n_generations=0)
    engine.run(narrow, fwd, n_generations=0)          # 同面板 resume：放行
    wider = _tiny_panel(["$revenue", "$volume"])
    with pytest.raises(GrammarError, match="one experiment cannot span"):
        engine.run(wider, fwd, n_generations=0)


def test_a_population_bred_under_a_wider_pool_is_refused():
    """预填种群（checkpoint 恢复 / 手工填）引用池外终端即拒。"""
    import pandas as pd
    import pytest

    from src.factor_mining.expression import parse_expression
    from src.factor_mining.grammar import GrammarError

    panel = _tiny_panel(["$volume", "$revenue"])
    fwd = pd.DataFrame(0.0, index=list(panel.values())[0].index,
                       columns=list(panel.values())[0].columns)
    engine = _engine()
    engine.population = [parse_expression("cs_rank($volume)")]
    narrow = _tiny_panel(["$revenue"])
    with pytest.raises(GrammarError, match="outside this run's pool"):
        engine.run(narrow, fwd, n_generations=0)


def test_checkpoint_round_trip_preserves_the_established_pool():
    """checkpoint 恢复的引擎必须带回那次 run 的池。"""
    import tempfile
    from pathlib import Path as _P

    import pandas as pd
    import pytest

    from src.factor_mining.expression import parse_expression
    from src.factor_mining.fitness import FitnessConfig
    from src.factor_mining.grammar import GrammarError

    panel = _tiny_panel(["$revenue"])
    fwd = pd.DataFrame(0.0, index=list(panel.values())[0].index,
                       columns=list(panel.values())[0].columns)
    engine = _engine()
    engine.run(panel, fwd, n_generations=0)
    with tempfile.TemporaryDirectory() as tmp:
        ckpt = _P(tmp) / "ck.json"
        engine.save_checkpoint(ckpt)
        loaded = GPEngine.load_checkpoint(ckpt, fitness_config=FitnessConfig())
    assert loaded._has_run
    assert loaded._allowed_terminals == frozenset({"$revenue"})
    wider = _tiny_panel(["$revenue", "$volume"])
    with pytest.raises(GrammarError, match="outside this engine's sampling pool"):
        loaded.score_expression(parse_expression("cs_rank($volume)"),
                                wider, fwd)


def test_a_checkpoint_without_the_recorded_pool_is_refused(tmp_path):
    """老 checkpoint 缺池记录 → 拒绝，而不是当作全新引擎。

    "当作全新"是**宽松**方向：恢复的种群与缓存明摆着来自一次 run，
    池不匹配守卫却被跳过，宽面板 resume 会保留窄种群而后续世代从宽池
    育种 —— 一个实验横跨两个搜索空间（codex #448 r1 P1）。窄池今天就
    可达（合并后的基本面面板），不是假想。
    """
    import json

    import pandas as pd
    import pytest

    from src.factor_mining.fitness import FitnessConfig

    panel = _tiny_panel(["$revenue"])
    fwd = pd.DataFrame(0.0, index=list(panel.values())[0].index,
                       columns=list(panel.values())[0].columns)
    engine = _engine()
    engine.run(panel, fwd, n_generations=0)
    ckpt = tmp_path / "legacy.json"
    engine.save_checkpoint(ckpt)
    state = json.loads(ckpt.read_text(encoding="utf-8"))
    assert state["allowed_terminals"] == ["$revenue"]     # 新格式确有记录
    state.pop("has_run")
    state.pop("allowed_terminals")                        # 退化成老格式
    ckpt.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(RuntimeError, match="predates terminal-pool recording"):
        GPEngine.load_checkpoint(ckpt, fitness_config=FitnessConfig())
