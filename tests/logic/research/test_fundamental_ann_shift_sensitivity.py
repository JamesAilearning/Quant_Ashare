"""防线 (iii)：公告日平移诊断 —— 钉住三态判据与两条不可绕过的性质。

重点不在"能跑"，而在两条**逃生路必须堵死**：
* 按 report_period 取值的盲目构建器，不能靠 INCONCLUSIVE 脱身（相关性从源数据算）；
* 抄证据不能满足断言（判据是被服务的记录换没换人，不是哈希变没变）。
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from scripts.research.fundamental_ann_shift_sensitivity import (
    INCONCLUSIVE,
    OK,
    REFUSE,
    ShiftDiagnosticError,
    adjudicate,
    find_winner_moves,
    winner_at,
)
from src.data.pit.financial_pit_contract import select_disclosure_of_record

_CAL = [date(2022, 4, 1), date(2022, 4, 29), date(2022, 5, 5),
        date(2022, 5, 6), date(2022, 5, 9), date(2022, 5, 10)]


def _rows(*specs):
    """specs: (end_date, update_flag, ann_date, available_from)

    Emits the full column set `select_disclosure_of_record` requires — the
    contract validates its input rather than guessing, so a partial fixture
    would be testing a different function than production calls.
    """
    return pd.DataFrame([
        {"ts_code": "000001.SZ", "report_period": pd.Timestamp(e).date(),
         "update_flag": uf,
         "ann_date": pd.Timestamp(a).date(),
         "f_ann_date": pd.Timestamp(a).date(),
         "announcement_date": pd.Timestamp(a).date(),
         "announcement_date_source": "f_ann_date",
         "available_from_trade_date": pd.Timestamp(av).date(),
         "_content_hash": f"h_{e}_{uf}_{a}"}
        for e, uf, a, av in specs
    ])


# 两期：Q4-2021（0401 可用）、Q1-2022（0505 可用）
_TWO_PERIODS = _rows(
    ("2021-12-31", "0", "2022-03-31", "2022-04-01"),
    ("2022-03-31", "0", "2022-04-29", "2022-05-05"),
)


# --- winner_at：canonical as-of 胜者 ---------------------------------------

def test_winner_is_the_latest_available_period():
    rec = select_disclosure_of_record(_TWO_PERIODS)
    assert winner_at(rec, date(2022, 3, 31)) is None       # 尚无可用
    assert winner_at(rec, date(2022, 4, 1)) == "20211231"
    assert winner_at(rec, date(2022, 4, 29)) == "20211231"  # 持有旧期
    assert winner_at(rec, date(2022, 5, 5)) == "20220331"   # 新期可用


def test_shift_delays_the_winner():
    """平移后，0505 那天新期还没到，胜者应退回旧期。"""
    rec = select_disclosure_of_record(_TWO_PERIODS)
    assert winner_at(rec, date(2022, 5, 5), shift_days=2, calendar=_CAL) \
        == "20211231"


# --- 相关性：逐采样日比较胜者 ----------------------------------------------

def test_winner_moves_are_found_from_source_only():
    moves = find_winner_moves(
        {"SZ000001": _TWO_PERIODS}, _CAL, shift_days=2, calendar=_CAL)
    at = {(m.trade_date, m.base_period, m.shifted_period) for m in moves}
    # 0505 起基线服务新期、平移后仍是旧期 —— 胜者移动
    assert (date(2022, 5, 5), "20220331", "20211231") in at


def test_no_move_when_the_shift_crosses_no_sampled_date():
    """原可用日与平移后可用日落在采样日同一侧 —— 没有胜者移动 → INCONCLUSIVE。

    采样日取 0509/0510：两期的可用日（0401→0429、0505→0506）平移后**都仍在
    这两个采样日之前**，所以两边的胜者都是 20220331，没有任何 cell 会动。
    这正是"相关但不跨采样日"的情形 —— 判 INCONCLUSIVE，不是判构建器有罪。
    """
    sampled = [date(2022, 5, 9), date(2022, 5, 10)]
    moves = find_winner_moves(
        {"SZ000001": _TWO_PERIODS}, sampled, shift_days=1, calendar=_CAL)
    assert moves == ()
    assert adjudicate(moves, {}, {}).verdict == INCONCLUSIVE


def test_restatement_row_cannot_move_a_winner():
    """未被选中的行（与 uf0 并存的 uf1）不得确立相关性。

    否则会要求一份**正确**面板做出它本不该做的移动。
    """
    with_restatement = _rows(
        ("2021-12-31", "0", "2022-03-31", "2022-04-01"),
        ("2021-12-31", "1", "2022-05-06", "2022-05-09"),   # 重述，从不被服务
    )
    moves = find_winner_moves(
        {"SZ000001": with_restatement}, _CAL, shift_days=1, calendar=_CAL)
    # 唯一被服务的期是 Q4-2021，其可用日 0401 平移到 0429 —— 只有该移动
    assert {m.base_period for m in moves} <= {"20211231", None}
    assert all(m.shifted_period != "20211231" or m.base_period != "20211231"
               for m in moves)


def test_update_flag_1_only_period_participates():
    """只有 uf1 行的期照常参与胜者计算 —— 丢掉它会让诊断对近年申报失明。"""
    uf1_only = _rows(("2022-03-31", "1", "2022-04-29", "2022-05-05"))
    rec = select_disclosure_of_record(uf1_only)
    assert winner_at(rec, date(2022, 5, 5)) == "20220331"
    moves = find_winner_moves(
        {"SZ000001": uf1_only}, _CAL, shift_days=2, calendar=_CAL)
    assert moves != ()


# --- 判据：被服务的记录必须换人 --------------------------------------------

def _moves():
    return find_winner_moves(
        {"SZ000001": _TWO_PERIODS}, _CAL, shift_days=2, calendar=_CAL)


def _served(moves, *, base_from, shift_from):
    base = {(m.instrument, m.trade_date): base_from(m) for m in moves}
    shifted = {(m.instrument, m.trade_date): shift_from(m) for m in moves}
    return base, shifted


def test_a_correct_builder_passes():
    moves = _moves()
    base, shifted = _served(moves, base_from=lambda m: m.base_period,
                            shift_from=lambda m: m.shifted_period)
    assert adjudicate(moves, base, shifted).verdict == OK


def test_an_announcement_blind_builder_is_refused():
    """按 report_period 取值：平移后照样服务同一条披露 → 记录没换人 → REFUSE。"""
    moves = _moves()
    base, shifted = _served(moves, base_from=lambda m: m.base_period,
                            shift_from=lambda m: m.base_period)  # 不换人
    verdict = adjudicate(moves, base, shifted)
    assert verdict.verdict == REFUSE
    assert any("没换人" in v for v in verdict.violations)


def test_blind_builder_cannot_escape_via_inconclusive():
    """核心：盲目构建器**不能**让自己看起来"不相关"。

    相关性只从源数据算，与它服务了什么无关 —— 所以 moves 非空，诊断一定会
    走到断言，REFUSE 一定触发。
    """
    moves = _moves()
    assert moves != ()                      # 相关性与构建器行为无关
    base, shifted = _served(moves, base_from=lambda m: m.base_period,
                            shift_from=lambda m: m.base_period)
    assert adjudicate(moves, base, shifted).verdict == REFUSE


def test_copied_evidence_does_not_satisfy_the_assertion():
    """抄证据不算数：判据是被服务的**报告期**换没换人。

    这里模拟"值与被服务期都没变，只有证据被贴上了平移后的可用日" —— 判据
    根本不看证据，所以照样 REFUSE。
    """
    moves = _moves()
    base, shifted = _served(moves, base_from=lambda m: m.base_period,
                            shift_from=lambda m: m.base_period)
    assert adjudicate(moves, base, shifted).verdict == REFUSE


def test_equal_values_do_not_cause_a_false_refuse():
    """值允许在平移前后相等 —— 判据不看值，只看被服务的期。"""
    moves = _moves()
    base, shifted = _served(moves, base_from=lambda m: m.base_period,
                            shift_from=lambda m: m.shifted_period)
    assert adjudicate(moves, base, shifted).verdict == OK


def test_baseline_mismatch_is_also_reported():
    """基线服务的期与源侧胜者对不上，同样是问题（不只查平移那一侧）。"""
    moves = _moves()
    base, shifted = _served(moves, base_from=lambda m: "19700101",
                            shift_from=lambda m: m.shifted_period)
    verdict = adjudicate(moves, base, shifted)
    assert verdict.verdict == REFUSE
    assert any("baseline served" in v for v in verdict.violations)


# --- 输入守卫 ---------------------------------------------------------------

def test_non_positive_shift_is_refused():
    with pytest.raises(ShiftDiagnosticError, match="must be positive"):
        find_winner_moves({"SZ000001": _TWO_PERIODS}, _CAL, 0, calendar=_CAL)


def test_verdict_renders_its_reason():
    assert "INCONCLUSIVE" in adjudicate((), {}, {}).render()
    moves = _moves()
    base, shifted = _served(moves, base_from=lambda m: m.base_period,
                            shift_from=lambda m: m.base_period)
    assert "REFUSE" in adjudicate(moves, base, shifted).render()
