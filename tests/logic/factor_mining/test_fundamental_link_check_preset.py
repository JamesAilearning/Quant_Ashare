"""fundamental_link_check preset 的守卫：holdout 边界、签收名单、字段镜像。

链路验证批次没有 prereg 门，这三条就是它仅有的防线：
* **短窗铁律** —— end_date 止于 2022-12-31，2023+/2025 holdout 绝不进配置；
* **签收名单冻结** —— 金融排除集是操作人 2026-08-17 签收的 120 只，
  签收即冻结，改名单必须重新走签收（改这里的镜像常量 = 显式重签动作）；
* **字段镜像冻结公式** —— fundamental_fields 必须恰好是起步三因子
  （C1_GPA / 资产增长 / C3_cash_based_OP）输入的并集，出处
  docs/prereg/quality_profitability.yaml。多一个字段 = 面板悄悄变宽，
  少一个 = 起步因子写不出来。
"""
from __future__ import annotations

import re

import pandas as pd
import pytest

from src.factor_mining.miner import load_config

_PRESET = "config/factor_mining/fundamental_link_check.yaml"

# 起步三因子输入并集（镜像 docs/prereg/quality_profitability.yaml：
# C1 = revenue/oper_cost/total_assets；资产增长 = total_assets(+prior)；
# C3 += sell_exp/admin_exp/accounts_receiv/inventories/prepayment/
#       accounts_pay/adv_receipts/contract_liab）。
_STARTER_FIELDS = frozenset({
    "revenue", "oper_cost", "sell_exp", "admin_exp", "total_assets",
    "accounts_receiv", "inventories", "prepayment", "accounts_pay",
    "adv_receipts", "contract_liab",
})

_SIGNED_EXCLUSION_COUNT = 120   # 操作人 2026-08-17 签收


@pytest.fixture(scope="module")
def config():
    return load_config(_PRESET)


def test_the_window_never_touches_the_holdout(config):
    """2023+ 段与 2025 holdout 绝不进链路验证配置。"""
    assert config.data.mode == "pit"
    assert pd.Timestamp(config.data.start_date) >= pd.Timestamp("2021-01-01")
    assert pd.Timestamp(config.data.end_date) <= pd.Timestamp("2022-12-31")


def test_the_signed_exclusion_list_is_frozen(config):
    exclusions = list(config.data.financial_exclusions)
    assert len(exclusions) == _SIGNED_EXCLUSION_COUNT
    assert len(set(exclusions)) == _SIGNED_EXCLUSION_COUNT   # 无重复
    for ticker in exclusions:
        # qlib 形式 —— 宇宙掩码按列名裁，ts_code 形式在这里静默失效。
        assert re.fullmatch(r"[A-Z]{2}\d{6}", ticker), ticker
    # 熟脸抽查：五大金融不在名单 = 名单被换过。
    for known in ("SZ000001", "SH600036", "SH601318", "SH600030", "SH601398"):
        assert known in exclusions, known


def test_fields_mirror_the_frozen_starter_formulas(config):
    assert set(config.data.fundamental_fields) == _STARTER_FIELDS
    assert len(config.data.fundamental_fields) == len(_STARTER_FIELDS)


def test_the_quartet_is_complete_and_pv_side_is_legacy(config):
    """四元组齐整（部分配置在 miner 侧即拒，这里钉 preset 不出发即残）。"""
    assert config.data.fundamental_store_root
    assert config.data.fundamental_calendar_path
    assert config.data.fundamental_fields
    assert config.data.universe_name == "csi800"
    assert list(config.data.fields) == []      # pv 侧 = 既有 V1，无冻结义务
    assert config.data.forward_return_price == "close"


def test_link_check_scale_is_deliberately_small(config):
    """链路验证不是搜索：GP 规模超过这个量级就该走战役与 prereg 门。"""
    assert config.gp.population_size <= 60
    assert config.gp.n_generations <= 5
    assert config.pool_top_k is not None and config.pool_top_k <= 30
