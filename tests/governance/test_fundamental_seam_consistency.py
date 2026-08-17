"""跨 PR 一致性守卫：research 桥与 factor_mining 语法的公共常量。

`$field__prior` 的后缀同时活在两侧：桥用它折叠 prior 帧的求值键
（`src/research/fundamental_panel.PRIOR_SUFFIX`），语法用它注册 prior
终端组（`FeatureRegistry.PRIOR_SUFFIX`）。两侧各自演化而不互相 import
（隔离闸），所以一致性只能由测试钉住 —— 任一侧改拼写，prior 值在求值
mapping 里就变成 AST 引用不到的孤儿键，Δ 因子静默死亡。
"""
from __future__ import annotations

from src.factor_mining.grammar import FeatureRegistry
from src.research.fundamental_panel import PRIOR_SUFFIX


def test_prior_suffix_is_one_constant_on_both_sides():
    assert PRIOR_SUFFIX == FeatureRegistry.PRIOR_SUFFIX


def test_every_prior_terminal_is_current_plus_the_shared_suffix():
    assert FeatureRegistry.FINANCIAL_STATEMENT_PRIOR == tuple(
        name + PRIOR_SUFFIX
        for name in FeatureRegistry.FINANCIAL_STATEMENT
    )


def test_the_bridge_evaluation_keys_are_registered_terminals():
    """桥折出来的 `$X__prior` 键必须逐一是已注册终端 —— 键集与注册表
    脱钩的话，GP 育得出、面板里却没有（或反之）。"""
    registered = set(FeatureRegistry.FINANCIAL_STATEMENT_PRIOR)
    folded = {
        f"{terminal}{PRIOR_SUFFIX}"
        for terminal in FeatureRegistry.FINANCIAL_STATEMENT
    }
    assert folded == registered
