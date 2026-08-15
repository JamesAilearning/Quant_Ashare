"""基本面池不得进入生产物化路径 —— 机器可执行的拒绝，不是文档注记。

生产物化路径（`src/data/mined_factor_handler.py`）在**没有** report-period
provenance 的 qlib 面板上求值。基本面池若走到那里，会在**缺少终端层对齐掩码**
的情况下被物化 —— 用一把尺裁决、用另一把尺出厂，与「验证/晋升要用同一把尺」
是同一类缺陷。

接线那个消费者属后续 change。在它落地之前，这条边界必须是**可执行的拒绝**：

* **主拒绝点在写盘方**，且早于 `target_dir.mkdir()` —— 目录先于 pool 组装被
  创建，拒绝若放在 `save` 之前仍会留下一个**空的生产版本目录**，下次尝试撞上
  "目录已存在"直接失败：一次**被拒**的晋升改动了生产、并永久吃掉那个版本号。
* **handler 侧作纵深防御** —— 「写盘方拒绝了」是关于一条代码路径的断言，
  而 handler 才是真正会把数字端上桌的那条路径。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.factor_mining.expression import parse_expression
from src.factor_mining.factor_pool import FactorPool, PoolEntry
from src.factor_mining.promote import (
    PromotionError,
    _refuse_fundamental_pool_in_production,
)

_PV = "cs_rank(div_safe($money, $volume))"
_FUNDAMENTAL = "cs_rank(div_safe($revenue, $total_assets))"


def _pool(*expr_texts):
    pool = FactorPool()
    for i, text in enumerate(expr_texts):
        expr = parse_expression(text)
        pool.add(PoolEntry(
            expr=expr, fitness=1.0, ic_mean=0.01, ic_std=0.1, ir=0.5,
            rank_ic_mean=0.01, rank_ic_std=0.1, rank_ir=0.5,
            turnover_daily=0.1, coverage=0.9, n_obs_per_day_min=100,
            expr_size=5, expr_hash=i, method="rank",
        ))
    return pool


# --- 写盘方（主拒绝点）------------------------------------------------------

def test_a_fundamental_pool_is_refused(tmp_path):
    target = tmp_path / "v1"
    with pytest.raises(PromotionError, match="FUNDAMENTAL pool"):
        _refuse_fundamental_pool_in_production(_pool(_FUNDAMENTAL), target)


def test_the_refusal_names_the_offending_expression_and_terminals(tmp_path):
    target = tmp_path / "v1"
    with pytest.raises(PromotionError) as exc:
        _refuse_fundamental_pool_in_production(_pool(_FUNDAMENTAL), target)
    message = str(exc.value)
    assert "$revenue" in message and "$total_assets" in message
    assert "mined_factor_handler" in message      # 指出后续 change 在哪


def test_a_price_volume_pool_passes(tmp_path):
    """非空性：拒绝不得退化成拒绝一切。"""
    _refuse_fundamental_pool_in_production(_pool(_PV), tmp_path / "v1")


def test_a_mixed_pool_is_refused(tmp_path):
    """只要有一个基本面幸存者就拒绝 —— 不是"多数决"。"""
    with pytest.raises(PromotionError, match="1 survivor"):
        _refuse_fundamental_pool_in_production(
            _pool(_PV, _FUNDAMENTAL), tmp_path / "v1")


def test_an_empty_pool_passes(tmp_path):
    _refuse_fundamental_pool_in_production(_pool(), tmp_path / "v1")


def test_the_refusal_leaves_the_target_path_absent(tmp_path):
    """被拒的晋升**完全没碰**生产：目录不存在，版本号未被吃掉。

    这是"拒绝早于 mkdir"的直接断言 —— 只断言"没写出 pool 文件"抓不到空目录。
    """
    target = tmp_path / "v1"
    with pytest.raises(PromotionError):
        _refuse_fundamental_pool_in_production(_pool(_FUNDAMENTAL), target)
    assert not target.exists()


# --- handler 侧（纵深防御）--------------------------------------------------

def test_handler_also_refuses_fundamental_entries():
    from src.data.mined_factor_handler import (
        MinedFactorHandlerError,
        _refuse_fundamental_entries,
    )

    entries = list(_pool(_FUNDAMENTAL).all_entries())
    with pytest.raises(MinedFactorHandlerError, match="FUNDAMENTAL factor"):
        _refuse_fundamental_entries(entries)


def test_handler_passes_price_volume_entries():
    from src.data.mined_factor_handler import _refuse_fundamental_entries

    _refuse_fundamental_entries(list(_pool(_PV).all_entries()))


def test_both_layers_agree_on_what_counts_as_fundamental():
    """两层用的是同一个判据（注册表），不是各自一份名单。"""
    from src.data.mined_factor_handler import (
        MinedFactorHandlerError,
        _refuse_fundamental_entries,
    )
    from src.factor_mining.grammar import FeatureRegistry

    for terminal in FeatureRegistry.FINANCIAL_STATEMENT:
        text = f"cs_rank({terminal})"
        pool = _pool(text)
        with pytest.raises(PromotionError):
            _refuse_fundamental_pool_in_production(pool, Path("unused"))
        with pytest.raises(MinedFactorHandlerError):
            _refuse_fundamental_entries(list(pool.all_entries()))
