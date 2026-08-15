"""prior 期终端：Δ 类因子必须**写得出来**。

`prior_panels` 只挂在面板旁边时，evaluator 只消费已注册且在值 mapping 里的
key —— AST 根本引用不到它，于是起步三因子里的**资产增长与 C3 应计写不出来**，
而"跑通链路"又要求它们跑。

建模为**终端**而非算子：evaluator 按名字对面板解析终端，算子则需要把第二个
mapping 穿过每一个调用点，换不来任何表达力。
"""
from __future__ import annotations

from random import Random

import pandas as pd
import pytest

from src.factor_mining.evaluator import (
    align_periods_at_terminals,
    evaluate_expression,
)
from src.factor_mining.expression import Terminal, feature_terminals, parse_expression
from src.factor_mining.grammar import (
    ExprType,
    FeatureRegistry,
    random_expression,
    sampling_pool,
)

_DAYS = pd.DatetimeIndex(pd.date_range("2022-05-02", periods=6, freq="D"))
_INST = ["SZ000001", "SZ000002"]


def _f(v):
    return pd.DataFrame([[v, v * 2]] * 6, index=_DAYS, columns=_INST,
                        dtype="float64")


def _p(period):
    return pd.DataFrame([[period, period]] * 6, index=_DAYS, columns=_INST,
                        dtype="object")


# --- 注册 -------------------------------------------------------------------

def test_every_statement_terminal_has_a_prior_counterpart():
    assert len(FeatureRegistry.FINANCIAL_STATEMENT_PRIOR) == len(
        FeatureRegistry.FINANCIAL_STATEMENT)
    for name in FeatureRegistry.FINANCIAL_STATEMENT:
        prior = name + FeatureRegistry.PRIOR_SUFFIX
        assert prior in FeatureRegistry.FINANCIAL_STATEMENT_PRIOR
        assert FeatureRegistry.is_prior(prior)
        assert FeatureRegistry.current_of_prior(prior) == name


def test_prior_terminals_are_constructible_and_pure():
    for prior in FeatureRegistry.FINANCIAL_STATEMENT_PRIOR:
        assert FeatureRegistry.is_feature(prior)
        assert FeatureRegistry.terminal_type(prior) == ExprType(
            "FEATURE", "PURE")
        assert Terminal(prior).name == prior


def test_prior_terminals_stay_out_of_the_default_set():
    assert set(FeatureRegistry.FINANCIAL_STATEMENT_PRIOR).isdisjoint(
        FeatureRegistry.V1)
    assert not set(sampling_pool("PURE", None)) & set(
        FeatureRegistry.FINANCIAL_STATEMENT_PRIOR)


def test_current_of_prior_rejects_a_non_prior_name():
    from src.factor_mining.grammar import GrammarError

    with pytest.raises(GrammarError, match="not a prior-period terminal"):
        FeatureRegistry.current_of_prior("$revenue")


# --- GP 能生成 --------------------------------------------------------------

def test_gp_can_generate_an_adjacent_period_difference():
    """白名单含当期与 prior 时，GP 必须能长出同时引用两者的表达式。

    目标类型用 **CSF** —— 那才是战役里 GP 产出的根类型；``FEATURE`` 在生成器
    表里没有任何算子，只会长出单个叶子，用它测「能否同时引用两个终端」等于
    什么都没测。
    """
    allowed = frozenset({"$total_assets", "$total_assets__prior"})
    rng = Random(5)
    saw_both = False
    for _ in range(400):
        expr = random_expression(
            ExprType("CSF", "PURE"), 4, 2, rng, allowed_terminals=allowed)
        if feature_terminals(expr) == allowed:
            saw_both = True
            break
    assert saw_both, "GP 从未生成同时引用当期与 prior 的表达式"


# --- 求值确为相邻期差分 ------------------------------------------------------

def _growth_panel():
    panel = {"$total_assets": _f(110.0), "$total_assets__prior": _f(100.0)}
    periods = {"$total_assets": _p("20220331"),
               "$total_assets__prior": _p("20211231")}
    return panel, periods


def test_asset_growth_evaluates_to_the_adjacent_difference():
    panel, periods = _growth_panel()
    expr = parse_expression(
        "div_safe(sub($total_assets, $total_assets__prior), "
        "$total_assets__prior)")
    got = evaluate_expression(expr, panel, periods=periods)
    # (110-100)/100 = 0.10 ；第二列 (220-200)/200 = 0.10
    assert (got.round(6) == 0.10).all().all()


def test_a_prior_terminal_differing_from_its_current_is_NOT_masked():
    """核心：prior 与当期的 report_period **本来就该不同**。

    若遮蔽把所有被引用终端一视同仁地要求同期，资产增长会被整片遮成 NA ——
    一条把它本要保护的因子删掉的防线。
    """
    panel, periods = _growth_panel()
    expr = parse_expression("sub($total_assets, $total_assets__prior)")
    masked = align_periods_at_terminals(panel, periods, expr)
    assert masked is panel                      # 零遮蔽
    got = evaluate_expression(expr, panel, periods=periods)
    assert got.notna().all().all()


def test_cross_endpoint_within_the_prior_generation_is_still_masked():
    """但 prior 组**内部**跨端点错期仍要遮 —— 分组不是放行。"""
    panel = {"$revenue__prior": _f(10.0), "$total_assets__prior": _f(100.0)}
    periods = {"$revenue__prior": _p("20211231"),
               "$total_assets__prior": _p("20210930")}   # 组内不一致
    expr = parse_expression("div_safe($revenue__prior, $total_assets__prior)")
    got = evaluate_expression(expr, panel, periods=periods)
    assert got.isna().all().all()


def test_cross_endpoint_within_the_current_generation_is_still_masked():
    panel = {"$revenue": _f(10.0), "$total_assets": _f(100.0)}
    periods = {"$revenue": _p("20220331"), "$total_assets": _p("20211231")}
    expr = parse_expression("div_safe($revenue, $total_assets)")
    got = evaluate_expression(expr, panel, periods=periods)
    assert got.isna().all().all()


def test_a_masked_cell_hits_both_generations():
    """当期腿被判错期时，prior 腿也一并遮 —— 半条腿没证据的 Δ 同样不可用。"""
    panel = {"$revenue": _f(10.0), "$total_assets": _f(100.0),
             "$total_assets__prior": _f(90.0)}
    periods = {"$revenue": _p("20220331"), "$total_assets": _p("20211231"),
               "$total_assets__prior": _p("20210930")}
    expr = parse_expression(
        "div_safe($revenue, sub($total_assets, $total_assets__prior))")
    masked = align_periods_at_terminals(panel, periods, expr)
    for terminal in ("$revenue", "$total_assets", "$total_assets__prior"):
        assert masked[terminal].isna().all().all(), terminal


# --- 跨期代必须邻接（codex #437 r8 P1）--------------------------------------

def test_cross_generation_non_adjacent_is_masked():
    """`div_safe($revenue, $total_assets__prior)`：每个期代各一个终端，
    组内比较双双空转 —— 端点推进不一致时，当期值会与**非相邻**的 prior
    静默相除。跨期代的要求是**邻接**：prior 期 == 当期期的上一季度末。
    """
    panel = {"$revenue": _f(10.0), "$total_assets__prior": _f(100.0)}
    periods = {"$revenue": _p("20220331"),
               "$total_assets__prior": _p("20210930")}   # 非相邻（差两季）
    expr = parse_expression("div_safe($revenue, $total_assets__prior)")
    got = evaluate_expression(expr, panel, periods=periods)
    assert got.isna().all().all()


def test_cross_generation_adjacent_is_not_masked():
    """相邻（20220331 的上一季 = 20211231）→ 合法，不遮。"""
    panel = {"$revenue": _f(10.0), "$total_assets__prior": _f(100.0)}
    periods = {"$revenue": _p("20220331"),
               "$total_assets__prior": _p("20211231")}
    expr = parse_expression("div_safe($revenue, $total_assets__prior)")
    got = evaluate_expression(expr, panel, periods=periods)
    assert got.notna().all().all()


def test_malformed_current_period_cannot_prove_adjacency():
    """畸形期 token 映射到哨兵值，永远证不出邻接 —— 保守遮蔽。"""
    panel = {"$revenue": _f(10.0), "$total_assets__prior": _f(100.0)}
    periods = {"$revenue": _p("2022Q1"),                  # 畸形
               "$total_assets__prior": _p("20211231")}
    expr = parse_expression("div_safe($revenue, $total_assets__prior)")
    got = evaluate_expression(expr, panel, periods=periods)
    assert got.isna().all().all()


def test_adjacency_rule_matches_the_research_side():
    """factor_mining 的邻接函数与 research 侧 previous_quarter_end 不得漂移。

    隔离闸禁止 src/factor_mining import src.research（所以是本地纯函数），
    这条测试就是两份实现的汇合点 —— 与 #425 的"窗口字段 == 冻结公式"同款。
    """
    from datetime import date as _date

    from src.factor_mining.evaluator import _previous_quarter_token
    from src.research.financial_pit_view import previous_quarter_end

    for token, parsed in (("20220331", _date(2022, 3, 31)),
                          ("20220630", _date(2022, 6, 30)),
                          ("20220930", _date(2022, 9, 30)),
                          ("20221231", _date(2022, 12, 31))):
        assert _previous_quarter_token(token) == \
            previous_quarter_end(parsed).strftime("%Y%m%d"), token


def test_trailing_garbage_in_current_period_cannot_prove_adjacency():
    """"20220331junk" 只取前 8 位会算出 20211231，恰好等于干净的 prior ——
    腐坏的 provenance 就此"证明"了邻接（codex #437 r9 P2）。整串必须是
    8 位纯数字，否则哨兵值、保守遮蔽。
    """
    panel = {"$revenue": _f(10.0), "$total_assets__prior": _f(100.0)}
    periods = {"$revenue": _p("20220331junk"),
               "$total_assets__prior": _p("20211231")}
    expr = parse_expression("div_safe($revenue, $total_assets__prior)")
    got = evaluate_expression(expr, panel, periods=periods)
    assert got.isna().all().all()


def test_matching_corruption_cannot_agree():
    """两个端点都带同一个畸形 token（"junk"=="junk"）—— 相等不是证明。

    腐坏的 provenance 无论怎么互相吻合都不得确立同期（codex #437 r10 P2）。
    """
    panel = {"$revenue": _f(10.0), "$total_assets": _f(100.0)}
    periods = {"$revenue": _p("junk"), "$total_assets": _p("junk")}
    expr = parse_expression("div_safe($revenue, $total_assets)")
    got = evaluate_expression(expr, panel, periods=periods)
    assert got.isna().all().all()


def test_sentinel_literal_in_prior_cannot_prove_adjacency():
    """prior 帧字面含 "<malformed>" 时不得与哨兵撞出"邻接"。"""
    panel = {"$revenue": _f(10.0), "$total_assets__prior": _f(100.0)}
    periods = {"$revenue": _p("2022bad1"),
               "$total_assets__prior": _p("<malformed>")}
    expr = parse_expression("div_safe($revenue, $total_assets__prior)")
    got = evaluate_expression(expr, panel, periods=periods)
    assert got.isna().all().all()


def test_non_quarter_end_dates_are_invalid_provenance():
    """8 位纯数字但非季度末（20220315）同样不是合法报告期。"""
    panel = {"$revenue": _f(10.0), "$total_assets": _f(100.0)}
    periods = {"$revenue": _p("20220315"), "$total_assets": _p("20220315")}
    expr = parse_expression("div_safe($revenue, $total_assets)")
    got = evaluate_expression(expr, panel, periods=periods)
    assert got.isna().all().all()


def test_accepted_float_spelling_is_equality_not_corruption():
    """契约明确接受 "20211231.0" 拼写 —— #437 单独看时 view 仍会发原拼写，
    严判会把合法数据当腐坏遮掉（codex #437 r11 P2）。规范化先行：
    ".0" 与规范形是**同一个** token，相等而非分歧。
    """
    panel = {"$revenue": _f(10.0), "$total_assets": _f(100.0)}
    periods = {"$revenue": _p("20220331.0"), "$total_assets": _p("20220331")}
    expr = parse_expression("div_safe($revenue, $total_assets)")
    got = evaluate_expression(expr, panel, periods=periods)
    assert got.notna().all().all()          # 同期，不遮


def test_float_spelling_participates_in_adjacency():
    panel = {"$revenue": _f(10.0), "$total_assets__prior": _f(100.0)}
    periods = {"$revenue": _p("20220331.0"),
               "$total_assets__prior": _p("20211231.0")}
    expr = parse_expression("div_safe($revenue, $total_assets__prior)")
    got = evaluate_expression(expr, panel, periods=periods)
    assert got.notna().all().all()          # 规范化后相邻，不遮


def test_normalizer_mirrors_every_contract_accepted_spelling():
    """求值层不得定义比生产方更窄的 provenance 契约（codex #437 r12 P2）。

    逐一断言：契约 `_parse_yyyymmdd` 接受的拼写，求值层规范化后同为合法；
    契约拒绝的拼写，求值层同为无效 —— 两份实现由本测试钉住（隔离方向禁止
    import，见 evaluator 内注释）。
    """
    from src.data.pit.financial_pit_contract import (
        FinancialPITContractError,
        _parse_yyyymmdd,
    )
    from src.factor_mining.evaluator import _canonical_period_token

    accepted = ["20211231", "20211231.0", "20211231.00", " 20211231 ",
                "20220331.000", 20211231, 20211231.0]
    for token in accepted:
        parsed = _parse_yyyymmdd(token)
        assert parsed is not None, token
        assert _canonical_period_token(token) == parsed.strftime("%Y%m%d"), \
            token

    rejected = ["20211231.5", "20220331junk", "2022", "<malformed>"]
    for token in rejected:
        try:
            _parse_yyyymmdd(token)
            contract_ok = True
        except FinancialPITContractError:
            contract_ok = False
        assert not contract_ok, token
        assert _canonical_period_token(token) is None, token

    # 求值层额外收紧的一条：合法日期但非季度末 —— 契约收、报告期语义不收
    assert _parse_yyyymmdd("20220315") is not None
    assert _canonical_period_token("20220315") is None


def test_whitespace_and_multi_zero_fraction_do_not_mask():
    panel = {"$revenue": _f(10.0), "$total_assets": _f(100.0)}
    periods = {"$revenue": _p(" 20220331 "),
               "$total_assets": _p("20220331.00")}
    expr = parse_expression("div_safe($revenue, $total_assets)")
    got = evaluate_expression(expr, panel, periods=periods)
    assert got.notna().all().all()


def test_impossible_calendar_date_cannot_agree():
    """"00000331" 形状与后缀都对，但年零不是日历日 —— 两个端点带同一个
    不可能 token 相等即"同期"照样是腐坏互证（codex #437 r13 P2）。
    形状之外必须证明它是真日期（与契约解析器同款）。
    """
    panel = {"$revenue": _f(10.0), "$total_assets": _f(100.0)}
    periods = {"$revenue": _p("00000331"), "$total_assets": _p("00000331")}
    expr = parse_expression("div_safe($revenue, $total_assets)")
    got = evaluate_expression(expr, panel, periods=periods)
    assert got.isna().all().all()
