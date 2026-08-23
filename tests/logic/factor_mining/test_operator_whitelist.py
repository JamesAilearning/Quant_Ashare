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
# 这些用例只依赖**面板推导**的池（run 记录的那一个），不涉及冻结白名单。
# 冻结白名单的用例在本文件末尾单独成段：两套机制回答不同的问题 ——
# 推导池管"同一实验不许换池"，白名单管"这次该用哪个池"。

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


def test_a_v1_run_still_guards_a_prefilled_population():
    """V1 面板下 `_allowed_terminals` 是 None 哨兵，种群守卫**不得**因此
    被跳过：手工填入 opt-in 终端的种群会一路走到求值，n_generations=0
    时甚至正常返回（codex #448 r3 P2 —— 与 score_expression 已修的是
    同一个哨兵误读）。"""
    import pandas as pd
    import pytest

    from src.factor_mining.expression import parse_expression
    from src.factor_mining.grammar import FeatureRegistry, GrammarError

    panel = _tiny_panel(list(FeatureRegistry.V1) + ["$revenue"])
    fwd = pd.DataFrame(0.0, index=list(panel.values())[0].index,
                       columns=list(panel.values())[0].columns)
    v1_only = _tiny_panel(list(FeatureRegistry.V1))
    engine = _engine()
    engine.population = [parse_expression("cs_rank($revenue)")]
    with pytest.raises(GrammarError, match="outside this run's pool"):
        engine.run(v1_only, fwd, n_generations=0)
    # 非空性：V1 内的种群照常放行。
    ok = _engine()
    ok.population = [parse_expression("cs_rank($volume)")]
    ok.run(v1_only, fwd, n_generations=0)
    assert ok._has_run


# --- 冻结终端白名单（与算子白名单对称，openspec 任务 (iv)）-----------------
#
# 面板键推导表达不了"面板带量价、协议只许财报"这件事：本役面板**需要**
# 量价腿（覆盖率分母、forward-return 几何），搜索空间却冻结在财报终端。
# 白名单优先于推导；推导池的 resume 守卫仍然在其后把关。

def test_an_explicit_terminal_whitelist_narrows_below_the_panel():
    """面板带量价 + 财报，白名单只要财报 —— 搜索空间必须是白名单。

    面板需要量价腿（覆盖率分母、forward-return 几何），协议冻结的搜索
    空间却只含财报终端；从面板键推导白名单表达不了这件事。
    """
    import pandas as pd

    panel = _tiny_panel(["$close", "$volume", "$revenue", "$revenue__prior"])
    fwd = pd.DataFrame(0.0, index=list(panel.values())[0].index,
                       columns=list(panel.values())[0].columns)
    engine = _engine(allowed_terminals=("$revenue", "$revenue__prior"))
    engine.run(panel, fwd, n_generations=0)
    assert engine._allowed_terminals == frozenset(
        {"$revenue", "$revenue__prior"})


def test_no_whitelist_keeps_the_panel_derived_default():
    import pandas as pd

    panel = _tiny_panel(["$close", "$volume", "$revenue"])
    fwd = pd.DataFrame(0.0, index=list(panel.values())[0].index,
                       columns=list(panel.values())[0].columns)
    engine = _engine()
    engine.run(panel, fwd, n_generations=0)
    assert engine._allowed_terminals == frozenset(
        {"$close", "$volume", "$revenue"})


def test_an_unregistered_terminal_is_refused():
    """注册表校验在 **__init__** 就做，与 allowed_operators 对称。

    早先它在惰性解析里做，而 mutate_subtree / mutate_point 的采样写在
    `except (GrammarError, ValueError): return expr` 这个可恢复失败块
    内 —— 一个 typo 出来的终端名于是不是 fail-loud，而是让变异静默退化
    成 no-op（codex #446 r14 P2）。前移之后放哪里都吞不掉。
    """
    import pytest

    from src.factor_mining.grammar import GrammarError

    with pytest.raises(GrammarError, match="unregistered"):
        _engine(allowed_terminals=("$revenue", "$not_a_terminal"))


def test_a_terminal_absent_from_the_panel_is_refused():
    """白名单声明了面板没有的终端 → 求值器会 KeyError；这是 setup 级
    契约失败，必须在育第一代之前拒。"""
    import pandas as pd
    import pytest

    from src.factor_mining.grammar import GrammarError

    panel = _tiny_panel(["$revenue"])
    fwd = pd.DataFrame(0.0, index=list(panel.values())[0].index,
                       columns=list(panel.values())[0].columns)
    engine = _engine(allowed_terminals=("$revenue", "$total_assets"))
    with pytest.raises(GrammarError, match="absent from the panel"):
        engine.run(panel, fwd, n_generations=0)


def test_initialize_population_honours_the_frozen_whitelist():
    """公开生命周期方法先于 run() 被调用时也必须用冻结白名单 ——
    否则生成走 V1 池，run() 又保留该种群，等于在协议禁止的量价终端上
    育种（codex #446 r3 P2）。"""
    from src.factor_mining.expression import feature_terminals

    engine = _engine(allowed_terminals=("$revenue", "$total_assets"),
                     allowed_operators=("cs_rank", "div_safe", "sub"))
    engine.initialize_population()
    assert engine.population
    used = set()
    for expr in engine.population:
        used |= set(feature_terminals(expr))
    assert used <= {"$revenue", "$total_assets"}, used


def test_direct_initialization_rejects_an_unregistered_terminal():
    """直接初始化时也要做注册表校验：生成器会与注册表求交，typo 项被
    静默丢掉 → 种群只含合法终端，而 checkpoint 下来的 config 仍宣称
    typo 在搜索空间里（codex #446 r4 P2）。与 allowed_operators 在
    __init__ 就校验对称。"""
    import pytest

    from src.factor_mining.grammar import GrammarError

    with pytest.raises(GrammarError, match="unregistered"):
        _engine(allowed_terminals=("$revenue", "$typo"))


def test_score_expression_enforces_the_frozen_whitelist():
    """直接打分路径与育种路径同一把尺：面板带得起、白名单不准的终端，
    注入打分必须被拒（codex #446 r1 P2 —— 算子那半有守卫、终端没有）。

    这条在把 #446 的白名单接到 #448 的 score_expression 守卫上时一度
    丢失：守卫接回来了、证人没搬回来。整条白名单分支被删掉，全套测试
    仍然全绿，而 starter-check 会给协议排除的量价终端出分。
    """
    import pandas as pd
    import pytest

    from src.factor_mining.expression import parse_expression
    from src.factor_mining.grammar import GrammarError

    panel = _tiny_panel(["$close", "$volume", "$revenue", "$total_assets"])
    fwd = pd.DataFrame(0.0, index=list(panel.values())[0].index,
                       columns=list(panel.values())[0].columns)
    # 全新引擎（_has_run=False）—— starter-check 每因子一个，与生产同构。
    engine = _engine(allowed_terminals=("$revenue",),
                     allowed_operators=("cs_rank", "div_safe"))
    # 白名单内：放行
    engine.score_expression(parse_expression("cs_rank($revenue)"), panel, fwd)
    # 面板有、白名单没有：拒（若白名单分支缺席，这里会退回宽面板而放行）
    with pytest.raises(GrammarError, match="outside this engine's sampling pool"):
        engine.score_expression(
            parse_expression("cs_rank(div_safe($revenue, $total_assets))"),
            panel, fwd)


def test_a_whitelisted_run_keeps_its_pool_when_scoring_later():
    """跑过一轮之后再打分，池仍是冻结白名单而非当次面板键。

    守的是两半的**接缝**：白名单分支排在 `elif self._has_run` 之前，
    所以已跑过的带白名单引擎绝不能因为打分时传了更宽的面板而放宽。
    """
    import pandas as pd
    import pytest

    from src.factor_mining.expression import parse_expression
    from src.factor_mining.grammar import GrammarError

    narrow = _tiny_panel(["$revenue", "$total_assets"])
    fwd = pd.DataFrame(0.0, index=list(narrow.values())[0].index,
                       columns=list(narrow.values())[0].columns)
    engine = _engine(allowed_terminals=("$revenue", "$total_assets"),
                     allowed_operators=("cs_rank", "div_safe"))
    engine.run(narrow, fwd, n_generations=0)
    wide = _tiny_panel(["$close", "$volume", "$revenue", "$total_assets"])
    with pytest.raises(GrammarError, match="outside this engine's sampling pool"):
        engine.score_expression(parse_expression("cs_rank($volume)"), wide, fwd)


def test_a_whitelisted_run_refuses_a_population_from_outside_the_pool():
    """#448 的既有种群守卫在白名单场景下仍然咬人：预填的种群引用了池外
    终端 → 拒。两半的另一处接缝（守卫读的是 _allowed_terminals，而它
    在白名单场景由 _validated_terminal_pool 赋值）。"""
    import pandas as pd
    import pytest

    from src.factor_mining.expression import parse_expression
    from src.factor_mining.grammar import GrammarError

    panel = _tiny_panel(["$close", "$volume", "$revenue", "$total_assets"])
    fwd = pd.DataFrame(0.0, index=list(panel.values())[0].index,
                       columns=list(panel.values())[0].columns)
    engine = _engine(allowed_terminals=("$revenue", "$total_assets"),
                     allowed_operators=("cs_rank", "div_safe"))
    engine.population = [parse_expression("cs_rank($volume)")]
    with pytest.raises(GrammarError, match="outside this run's pool"):
        engine.run(panel, fwd, n_generations=0)


def test_every_mutation_entry_point_honours_the_frozen_whitelist():
    """公开变异入口先于 run/initialize_population 被调用时也必须用冻结
    池 —— 否则 `_allowed_terminals` 仍是 None，变异静默退回 V1 量价池
    （codex #446 r12 P2：实测点变异漏出 $volume/$pb/$pe/$total_mv 等）。

    守的是**单点解析**这个机制本身：每个采样入口都经 `_sampling_pool()`
    取池，所以再加第五个公开入口时不会重犯。
    """
    from src.factor_mining.expression import feature_terminals, parse_expression

    pool = {"$revenue", "$total_assets"}
    for entry in ("mutate_point", "mutate_subtree"):
        engine = _engine(allowed_terminals=tuple(sorted(pool)),
                         allowed_operators=("cs_rank", "div_safe", "sub"))
        seen = set()
        for _ in range(120):
            try:
                out = getattr(engine, entry)(
                    parse_expression("cs_rank($revenue)"))
            except Exception:  # noqa: BLE001  生成失败不算泄漏
                continue
            seen |= set(feature_terminals(out))
        assert seen <= pool, f"{entry} 漏出池外终端: {sorted(seen - pool)}"


def test_an_invalid_whitelist_is_not_swallowed_by_mutation():
    """可恢复失败块吞不掉配置错误 —— 因为根本走不到那里。

    守的是"注册表校验前移"这个决定本身：只要它退回惰性解析，
    `mutate_subtree` 的 `except (GrammarError, ValueError): return expr`
    就会把一个无效的声明式搜索空间变成"变异 no-op"，而不是拒。
    """
    import pytest

    from src.factor_mining.grammar import GrammarError

    with pytest.raises(GrammarError, match="unregistered"):
        _engine(allowed_terminals=("$revenue", "$typo"),
                allowed_operators=("cs_rank", "div_safe"))
    # 合法白名单不受影响：构造成功，且变异只在池内取样。
    from src.factor_mining.expression import feature_terminals, parse_expression

    engine = _engine(allowed_terminals=("$revenue", "$total_assets"),
                     allowed_operators=("cs_rank", "div_safe", "sub"))
    seen = set()
    for _ in range(80):
        try:
            seen |= set(feature_terminals(
                engine.mutate_subtree(parse_expression("cs_rank($revenue)"))))
        except Exception:  # noqa: BLE001
            continue
    assert seen <= {"$revenue", "$total_assets"}, sorted(seen)
