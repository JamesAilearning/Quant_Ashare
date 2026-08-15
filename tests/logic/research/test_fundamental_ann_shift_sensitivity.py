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
        {("income", "SZ000001"): _TWO_PERIODS}, _CAL, shift_days=2, calendar=_CAL)
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
        {("income", "SZ000001"): _TWO_PERIODS}, sampled, shift_days=1, calendar=_CAL)
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
        {("income", "SZ000001"): with_restatement}, _CAL, shift_days=1, calendar=_CAL)
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
        {("income", "SZ000001"): uf1_only}, _CAL, shift_days=2, calendar=_CAL)
    assert moves != ()


# --- 判据：被服务的记录必须换人 --------------------------------------------

def _moves():
    return find_winner_moves(
        {("income", "SZ000001"): _TWO_PERIODS}, _CAL, shift_days=2, calendar=_CAL)


def _served(moves, *, base_from, shift_from):
    base = {m.key: base_from(m) for m in moves}
    shifted = {m.key: shift_from(m) for m in moves}
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
        find_winner_moves({("income", "SZ000001"): _TWO_PERIODS}, _CAL, 0, calendar=_CAL)


def test_verdict_renders_its_reason():
    assert "INCONCLUSIVE" in adjudicate((), {}, {}).render()
    moves = _moves()
    base, shifted = _served(moves, base_from=lambda m: m.base_period,
                            shift_from=lambda m: m.base_period)
    assert "REFUSE" in adjudicate(moves, base, shifted).render()


# --- 端到端：诊断必须真的建两侧面板并裁决 ----------------------------------
#
# codex #433 P1：此前 `main` 无条件 raise、无任何调用方，测试自己造 served map ——
# 于是"盲目构建器"从未被真正跑过、也从未被真正拒绝过，防线 (iii) 不成立。
# 下面这组用**真的** build_fundamental_panel 与**真的**平移 store 跑完整条路径。

import pandas as _pd  # noqa: E402
import pytest as _pytest  # noqa: E402

from scripts.research.fundamental_ann_shift_sensitivity import (  # noqa: E402
    run_diagnostic,
    served_periods,
    write_shifted_store,
)
from src.research.fundamental_panel import (  # noqa: E402
    FundamentalPanel,
)

_E2E_DAYS = [date(2022, 3, 31), date(2022, 4, 1), date(2022, 4, 29),
             date(2022, 5, 5), date(2022, 5, 6), date(2022, 5, 9)]
_INCOME = ("revenue", "total_revenue", "oper_cost", "sell_exp",
           "admin_exp", "rd_exp", "int_exp", "fin_exp")


def _store_row(ts, end_date, ann, revenue):
    row = {"ts_code": ts, "end_date": end_date, "ann_date": ann,
           "f_ann_date": ann, "update_flag": "0",
           "_content_hash": f"h_{ts}_{end_date}", "_fetch_batch": "b1",
           "_source_endpoint": "income"}
    for f in _INCOME:
        row[f] = revenue if f == "revenue" else _pd.NA
    return row


@_pytest.fixture
def e2e_store(tmp_path):
    inc = tmp_path / "store" / "income"
    inc.mkdir(parents=True)
    _pd.DataFrame([
        _store_row("000001.SZ", "20211231", "20220331", 100.0),
        _store_row("000001.SZ", "20220331", "20220429", 30.0),
    ]).to_parquet(inc / "000001.SZ.parquet", index=False)
    return tmp_path / "store"


def _run(store, tmp_path, *, build_panel=None, shift_days=2):
    return run_diagnostic(
        store_dir=store, calendar=_E2E_DAYS, trade_dates=_E2E_DAYS,
        fields=["revenue"], instruments=["000001.SZ"],
        financial_issuers=frozenset(), shift_days=shift_days,
        workdir=tmp_path / "work", build_panel=build_panel)


def test_shifted_store_only_moves_the_announcement(e2e_store, tmp_path):
    """平移 store 只动 ann_date/f_ann_date —— 值与期一律不动。"""
    out = write_shifted_store(e2e_store, tmp_path / "s", 2, _E2E_DAYS)
    before = _pd.read_parquet(e2e_store / "income" / "000001.SZ.parquet")
    after = _pd.read_parquet(out / "income" / "000001.SZ.parquet")
    assert list(after["revenue"]) == list(before["revenue"])
    assert list(after["end_date"]) == list(before["end_date"])
    assert list(after["ann_date"]) != list(before["ann_date"])


def test_the_real_bridge_passes_end_to_end(e2e_store, tmp_path):
    """真桥走完整条路径 → OK（非空性：诊断不是恒 REFUSE）。"""
    verdict = _run(e2e_store, tmp_path)
    assert verdict.verdict == OK, verdict.render()
    assert verdict.moves != ()          # 确有胜者移动，不是靠 INCONCLUSIVE 蒙混


def _announcement_blind_builder(view, fields, trade_dates, instruments, **kw):
    """一个**真的**按 report_period 取值的错误实现。

    它无视 available_from，直接把「期末日 <= 交易日」的最新一期端上来 ——
    也就是财报一结账就当天可见，公告日完全不参与。这正是防线 (iii) 存在的理由。
    """
    from src.data.pit._common import to_qlib_ticker

    insts = [to_qlib_ticker(i) if "." in i else i for i in instruments]
    idx = _pd.DatetimeIndex(sorted(trade_dates))
    values = _pd.DataFrame(index=idx, columns=insts, dtype="object")
    periods = _pd.DataFrame(index=idx, columns=insts, dtype="object")
    evidence = _pd.DataFrame(index=idx, columns=insts, dtype="object")
    store = view._store_dir / "income"          # noqa: SLF001 - 故意绕过 view
    for parquet in store.glob("*.parquet"):
        raw = _pd.read_parquet(parquet)
        col = to_qlib_ticker(parquet.stem)
        if col not in insts:
            continue
        for when in idx:
            ok = raw[raw["end_date"].map(
                lambda e, _w=when: _pd.Timestamp(e).date() <= _w.date())]
            if ok.empty:
                continue
            hit = ok.sort_values("end_date").iloc[-1]
            values.loc[when, col] = hit["revenue"]
            periods.loc[when, col] = str(hit["end_date"])
            evidence.loc[when, col] = when.strftime("%Y%m%d")
    key = "$" + fields[0]
    return FundamentalPanel({key: values}, {key: evidence}, {key: periods},
                            {}, {}, {})


def test_an_announcement_blind_builder_is_refused_end_to_end(e2e_store,
                                                             tmp_path):
    """核心：真跑一个盲目构建器，诊断必须 REFUSE。

    它平移前后服务的都是同一期（因为它根本不看公告日），所以"被服务的记录
    没换人" —— 这正是判据要抓的。
    """
    verdict = _run(e2e_store, tmp_path, build_panel=_announcement_blind_builder)
    assert verdict.verdict == REFUSE, verdict.render()
    assert verdict.violations != ()


def test_blind_builder_does_not_escape_via_inconclusive_end_to_end(e2e_store,
                                                                   tmp_path):
    """相关性来自源数据，所以盲目构建器**建立得起**相关性，逃不掉裁决。"""
    verdict = _run(e2e_store, tmp_path, build_panel=_announcement_blind_builder)
    assert verdict.verdict != INCONCLUSIVE
    assert verdict.moves != ()


def test_served_periods_rejects_disagreement_within_one_endpoint():
    """同一 endpoint 的两个字段给出不同的期 = 面板 bug，必须 fail-loud。"""
    idx = _pd.DatetimeIndex([_pd.Timestamp(2022, 5, 5)])
    a = _pd.DataFrame([["20220331"]], index=idx, columns=["SZ000001"])
    b = _pd.DataFrame([["20211231"]], index=idx, columns=["SZ000001"])
    with _pytest.raises(ShiftDiagnosticError, match="disagree on the served"):
        served_periods({"$revenue": a, "$oper_cost": b})   # 都是 income


def test_served_periods_keeps_endpoints_apart():
    """不同 endpoint 同日给出不同的期是**合法**的 —— view 各端点独立服务。

    压平成 per-ticker 一个键，会把这个合法差异变成假冲突，并悄悄丢掉一个
    端点的答案（codex #433 r2 P1）。
    """
    idx = _pd.DatetimeIndex([_pd.Timestamp(2022, 5, 5)])
    income = _pd.DataFrame([["20220331"]], index=idx, columns=["SZ000001"])
    balance = _pd.DataFrame([["20211231"]], index=idx, columns=["SZ000001"])
    got = served_periods({"$revenue": income, "$total_assets": balance})
    assert got[("income", "SZ000001", date(2022, 5, 5))] == "20220331"
    assert got[("balancesheet", "SZ000001", date(2022, 5, 5))] == "20211231"


def test_empty_store_is_refused(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with _pytest.raises(ShiftDiagnosticError, match="nothing to shift"):
        write_shifted_store(empty, tmp_path / "o", 1, _E2E_DAYS)


# --- 源侧扫描必须限定在被请求的宇宙内（codex #433 r2 P1）--------------------

@_pytest.fixture
def store_with_extras(tmp_path):
    """一个"全宇宙"小店：请求的名字 + 一个未请求的 + 一个金融的。"""
    inc = tmp_path / "store" / "income"
    inc.mkdir(parents=True)
    for ts in ("000001.SZ", "000009.SZ", "600000.SH"):
        _pd.DataFrame([
            _store_row(ts, "20211231", "20220331", 100.0),
            _store_row(ts, "20220331", "20220429", 30.0),
        ]).to_parquet(inc / f"{ts}.parquet", index=False)
    return tmp_path / "store"


def test_unrequested_instruments_do_not_refuse_a_correct_bridge(
        store_with_extras, tmp_path):
    """只请求 000001.SZ：另外两个名字的胜者移动不得拿来裁决。

    否则源侧扫描会为面板从未被要求构建的名字生成移动，然后拿"面板里没有的
    条目"去比对 —— 误 REFUSE 一个完全正确的桥。
    """
    verdict = run_diagnostic(
        store_dir=store_with_extras, calendar=_E2E_DAYS, trade_dates=_E2E_DAYS,
        fields=["revenue"], instruments=["000001.SZ"],
        financial_issuers=frozenset(), shift_days=2, workdir=tmp_path / "w1")
    assert verdict.verdict == OK, verdict.render()
    assert {m.instrument for m in verdict.moves} == {"SZ000001"}


def test_financially_excluded_instruments_are_not_adjudicated(
        store_with_extras, tmp_path):
    """金融 issuer 在 view 内部被剔除，面板里没有它 —— 不得据此拒绝。"""
    verdict = run_diagnostic(
        store_dir=store_with_extras, calendar=_E2E_DAYS, trade_dates=_E2E_DAYS,
        fields=["revenue"], instruments=["000001.SZ", "600000.SH"],
        financial_issuers=frozenset({"600000.SH"}), shift_days=2,
        workdir=tmp_path / "w2")
    assert verdict.verdict == OK, verdict.render()
    assert {m.instrument for m in verdict.moves} == {"SZ000001"}


def test_qlib_form_instruments_are_matched_against_the_store(
        store_with_extras, tmp_path):
    """入参给 qlib 标签时，源侧扫描仍要能对上 ts_code 命名的 store 文件。"""
    verdict = run_diagnostic(
        store_dir=store_with_extras, calendar=_E2E_DAYS, trade_dates=_E2E_DAYS,
        fields=["revenue"], instruments=["SZ000001"],
        financial_issuers=frozenset(), shift_days=2, workdir=tmp_path / "w3")
    assert verdict.moves != ()          # 匹配上了，不是静默空扫
    assert verdict.verdict == OK, verdict.render()


# --- 缺 provenance 必须判负，不得靠 INCONCLUSIVE 脱身（codex #433 r3 P1）----

def _panel_without_periods(view, fields, trade_dates, instruments, **kw):
    """一个不输出 report period 的构建器。

    源侧扫描已限定在被请求的非金融宇宙，所以它产出的每个 key **都应该**在面板
    里有条目。若"缺 key 就跳过"，这个构建器会让全部期望被丢弃、拿到
    INCONCLUSIVE —— 不输出 provenance 反而成了逃生路。
    """
    from src.data.pit._common import to_qlib_ticker

    insts = [to_qlib_ticker(i) if "." in i else i for i in instruments]
    idx = _pd.DatetimeIndex(sorted(trade_dates))
    empty = _pd.DataFrame(index=idx, columns=insts, dtype="object")
    key = "$" + fields[0]
    return FundamentalPanel({key: empty.copy()}, {key: empty.copy()},
                            {key: empty.copy()}, {}, {}, {})


def _panel_with_no_periods_at_all(view, fields, trade_dates, instruments, **kw):
    """更彻底的一种：periods 映射整个为空 —— 连 key 都不存在。"""
    from src.data.pit._common import to_qlib_ticker

    insts = [to_qlib_ticker(i) if "." in i else i for i in instruments]
    idx = _pd.DatetimeIndex(sorted(trade_dates))
    empty = _pd.DataFrame(index=idx, columns=insts, dtype="object")
    key = "$" + fields[0]
    return FundamentalPanel({key: empty.copy()}, {key: empty.copy()},
                            {}, {}, {}, {})


def test_all_na_periods_are_refused(e2e_store, tmp_path):
    """periods 帧在、但全 NA：key 存在而值为 None —— 走常规比对，照样 REFUSE。"""
    verdict = _run(e2e_store, tmp_path, build_panel=_panel_without_periods)
    assert verdict.verdict == REFUSE, verdict.render()
    assert verdict.violations != ()


def test_a_builder_that_omits_periods_is_refused_not_inconclusive(
        e2e_store, tmp_path):
    """periods 整个不给 —— key 不存在。**不得**因此把期望丢掉判 INCONCLUSIVE。

    源侧已限定在被请求的非金融宇宙，所以每个期望都该有面板条目；缺失是
    「必需的 provenance 没给」，不是「合法的未请求」。跳过它等于给盲目
    构建器一条逃生路：不输出 periods 就能免于裁决（codex #433 r3 P1）。
    """
    verdict = _run(e2e_store, tmp_path,
                   build_panel=_panel_with_no_periods_at_all)
    assert verdict.verdict == REFUSE, verdict.render()
    assert verdict.verdict != INCONCLUSIVE
    assert any("NO served report period" in v for v in verdict.violations)


def test_missing_provenance_is_reported_per_key():
    """缺失是逐 key 报告的，不是一句笼统的失败。"""
    moves = _moves()
    verdict = adjudicate(moves, {}, {})
    assert verdict.verdict == REFUSE
    assert len(verdict.violations) == len(moves)
    assert len(verdict.moves) == len(moves)     # 期望没有被丢弃


# --- 单侧遗漏必须各自独立检查（codex #433 r4 P1）----------------------------

def test_one_sided_missing_key_cannot_masquerade_as_an_explicit_na():
    """base 有、shifted 缺，且期望的 shifted_period 恰好是 None。

    用 `and` 连接两侧的成员检查时，这一侧的缺失走不进缺 provenance 分支，
    随后 `.get()` 把「缺 key」变成 None —— 正好等于期望，判 OK。
    缺失必须**逐侧**判定，绝不能让"没给"读成"明确的 NA"。
    """
    moves = find_winner_moves(
        {("income", "SZ000001"): _TWO_PERIODS}, _CAL, shift_days=2,
        calendar=_CAL)
    # 挑一个期望 shifted_period 为 None 的移动（首期被推迟到采样日之后）
    target = next(m for m in moves if m.shifted_period is None)
    base = {target.key: target.base_period}
    shifted: dict = {}                      # 这一侧整个没给
    verdict = adjudicate([target], base, shifted)
    assert verdict.verdict == REFUSE, verdict.render()
    assert any("shifted reported NO served" in v for v in verdict.violations)


def test_missing_baseline_side_alone_is_also_refused():
    moves = _moves()
    target = moves[0]
    verdict = adjudicate([target], {}, {target.key: target.shifted_period})
    assert verdict.verdict == REFUSE
    assert any("baseline reported NO served" in v for v in verdict.violations)


def test_explicit_na_on_both_sides_still_adjudicates_normally():
    """非空性：两侧都**明确给出** None 时，不得被误判为缺失。"""
    moves = find_winner_moves(
        {("income", "SZ000001"): _TWO_PERIODS}, _CAL, shift_days=2,
        calendar=_CAL)
    target = next(m for m in moves if m.shifted_period is None)
    verdict = adjudicate(
        [target], {target.key: target.base_period},
        {target.key: target.shifted_period})     # 明确的 None
    assert verdict.verdict == OK, verdict.render()


# --- 金融排除集的命名空间归一（codex #433 r4 P1）----------------------------

def test_qlib_form_financial_exclusion_is_normalised(store_with_extras,
                                                     tmp_path):
    """排除集给 qlib 形（SH600000）时，源侧也必须减掉对应的 600000.SH。

    view 会归一化后剔除该 issuer，面板里没有它；裸减法减不掉，源侧就会为它
    生成移动，然后拿"面板里没有的条目"去比对 —— 误 REFUSE 正确的桥。
    """
    verdict = run_diagnostic(
        store_dir=store_with_extras, calendar=_E2E_DAYS, trade_dates=_E2E_DAYS,
        fields=["revenue"], instruments=["000001.SZ", "600000.SH"],
        financial_issuers=frozenset({"SH600000"}),   # qlib 形
        shift_days=2, workdir=tmp_path / "wq")
    assert verdict.verdict == OK, verdict.render()
    assert {m.instrument for m in verdict.moves} == {"SZ000001"}


# --- 公告日 token 的数值形态（codex #433 r5 P1）-----------------------------

@_pytest.mark.parametrize("token_kind", ["str", "int", "float"])
def test_numeric_announcement_tokens_shift_correctly(tmp_path, token_kind):
    """store 合法地把 YYYYMMDD 存成 int / 精确 .0 float / str，三者必须等价。

    `pd.Timestamp(20220331)` 会把整数当作**epoch 纳秒**读成 1970-01-01 ——
    平移后的 store 于是拿到一个落在日历开头的公告日，而源侧仍按 2022-03-31
    解析，诊断就会为自己制造的缺陷去 REFUSE 一个正确的桥。
    """
    cast = {"str": str, "int": int, "float": float}[token_kind]
    inc = tmp_path / "store" / "income"
    inc.mkdir(parents=True)
    rows = []
    for end, ann, rev in (("20211231", "20220331", 100.0),
                          ("20220331", "20220429", 30.0)):
        row = _store_row("000001.SZ", end, ann, rev)
        row["ann_date"] = cast(ann)
        row["f_ann_date"] = cast(ann)
        rows.append(row)
    _pd.DataFrame(rows).to_parquet(inc / "000001.SZ.parquet", index=False)

    out = write_shifted_store(tmp_path / "store", tmp_path / f"s_{token_kind}",
                              2, _E2E_DAYS)
    shifted = _pd.read_parquet(out / "income" / "000001.SZ.parquet")
    # 平移 2 个交易日：20220331 -> 20220429（日历中 0331 之后的第 2 个交易日）
    got = sorted(str(v) for v in shifted["ann_date"])
    assert all(v.startswith("2022") for v in got), got
    assert not any(v.startswith("1970") for v in got), got


def test_the_diagnostic_passes_on_a_numeric_token_store(tmp_path):
    """端到端：int 形 token 的 store 上，真桥仍判 OK（不误拒）。"""
    inc = tmp_path / "store" / "income"
    inc.mkdir(parents=True)
    rows = []
    for end, ann, rev in (("20211231", "20220331", 100.0),
                          ("20220331", "20220429", 30.0)):
        row = _store_row("000001.SZ", end, ann, rev)
        row["ann_date"] = int(ann)
        row["f_ann_date"] = int(ann)
        rows.append(row)
    _pd.DataFrame(rows).to_parquet(inc / "000001.SZ.parquet", index=False)

    verdict = _run(tmp_path / "store", tmp_path)
    assert verdict.verdict == OK, verdict.render()
    assert verdict.moves != ()


# --- served period token 规范化 + 输出路径重叠（codex #433 r6）--------------

def test_float_spelled_end_dates_do_not_refuse_the_bridge(tmp_path):
    """end_date 为精确 .0 float 时，view 的 period 帧保留原拼写
    （"20220331.0"），而 winner_at 走契约给出 "20220331" —— 若逐字比较，
    正确的桥会因**拼写**差异而非行为差异被 REFUSE。
    """
    inc = tmp_path / "store" / "income"
    inc.mkdir(parents=True)
    rows = []
    for end, ann, rev in (("20211231", "20220331", 100.0),
                          ("20220331", "20220429", 30.0)):
        row = _store_row("000001.SZ", end, ann, rev)
        row["end_date"] = float(end)          # 20220331.0
        rows.append(row)
    _pd.DataFrame(rows).to_parquet(inc / "000001.SZ.parquet", index=False)

    verdict = _run(tmp_path / "store", tmp_path)
    assert verdict.verdict == OK, verdict.render()
    assert verdict.moves != ()


def test_overlapping_output_path_is_refused_before_any_write(e2e_store,
                                                             tmp_path):
    """输出路径 == 源 store（或互相嵌套）时必须在写之前拒绝。

    否则会**就地改写真实 store** 的公告日，且 `_record_frames` 随后从已被
    污染的数据推基线 —— 裁决变成拿一个损坏的 store 与它自己比。
    """
    before = _pd.read_parquet(e2e_store / "income" / "000001.SZ.parquet")
    with _pytest.raises(ShiftDiagnosticError, match="overlaps the source"):
        write_shifted_store(e2e_store, e2e_store, 2, _E2E_DAYS)
    with _pytest.raises(ShiftDiagnosticError, match="overlaps the source"):
        write_shifted_store(e2e_store, e2e_store / "income", 2, _E2E_DAYS)
    with _pytest.raises(ShiftDiagnosticError, match="overlaps the source"):
        write_shifted_store(e2e_store.parent, e2e_store, 2, _E2E_DAYS)
    # 源 store 一个字节没动
    after = _pd.read_parquet(e2e_store / "income" / "000001.SZ.parquet")
    _pd.testing.assert_frame_equal(before, after)


def test_disjoint_output_path_still_works(e2e_store, tmp_path):
    out = write_shifted_store(e2e_store, tmp_path / "elsewhere", 2, _E2E_DAYS)
    assert (out / "income" / "000001.SZ.parquet").exists()


# --- 复用的输出树必须整树拒绝（codex #433 r7 P2）----------------------------

def test_a_preexisting_output_tree_is_refused(e2e_store, tmp_path):
    """根级 resolve 检查挡不住**子级 symlink**：`shifted_2/income -> store/income`
    让两个根看似不相交，而写入会跟着链接改写真实 store。要求全新输出树把
    整类问题关掉 —— 没有任何既存物可以被跟随。
    """
    out = tmp_path / "reused"
    out.mkdir()
    (out / "leftover.txt").write_text("x", encoding="utf-8")
    before = _pd.read_parquet(e2e_store / "income" / "000001.SZ.parquet")
    with _pytest.raises(ShiftDiagnosticError, match="already exists"):
        write_shifted_store(e2e_store, out, 2, _E2E_DAYS)
    after = _pd.read_parquet(e2e_store / "income" / "000001.SZ.parquet")
    _pd.testing.assert_frame_equal(before, after)     # 源 store 未被触碰


def test_symlinked_child_cannot_reach_the_real_store(e2e_store, tmp_path):
    """有权限时直接验 symlink 情形本身（无权限则跳过 —— 前一条已覆盖该类）。"""
    out = tmp_path / "sneaky"
    out.mkdir()
    try:
        (out / "income").symlink_to(e2e_store / "income",
                                    target_is_directory=True)
    except OSError:
        _pytest.skip("此环境无 symlink 权限；已由 already-exists 拒绝覆盖")
    before = _pd.read_parquet(e2e_store / "income" / "000001.SZ.parquet")
    with _pytest.raises(ShiftDiagnosticError, match="already exists"):
        write_shifted_store(e2e_store, out, 2, _E2E_DAYS)
    after = _pd.read_parquet(e2e_store / "income" / "000001.SZ.parquet")
    _pd.testing.assert_frame_equal(before, after)
