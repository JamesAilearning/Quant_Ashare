"""防线 (iii)：公告日平移敏感性诊断 —— 从**行为**上验证面板真的消费了公告日。

把店内有效公告日整体后移 N 个交易日重建面板，然后问一个问题：
**被服务的是哪条记录，换人了吗？**

判据为什么不是哈希
------------------
「值+证据一起的哈希必须改变」挡不住它要抓的那个缺陷：一个按 report_period 取值的
构建器可以**继续服务同一条（仍属未来的）披露与其值**，只把该记录**平移后**的
`available_from` 抄进证据 —— 证据字节变了、哈希变了，而值的选择依旧对公告日盲目。
**证据可以抄，「服务了哪一期」抄不了。**

值在平移前后**允许相等**（延迟申报可能重复上期同值、或该字段两期皆 NA），
所以不要求值不等；哈希照录作参考，但不作判据。

相关性为什么必须从源数据算
--------------------------
若把相关性定义为「基线服务它、平移后不服务它」，对**公告日不敏感**的构建器
（正是本诊断的目标）永远建立不起来 —— 它平移后照样服务同一条披露，于是诊断
永远 INCONCLUSIVE，那条 REFUSE **永不触发**。用被检查对象的输出去决定要不要
检查它，是循环论证。

因此相关性只从**源数据**算：store 的披露日、平移量、被请求字段、采样日历。
且判据是**逐采样日比较胜者**，不是逐条披露判区间 —— view 每日只服务一个胜者，
当两条 disclosure-of-record 期的平移区间重叠（年报与一季报同日可用）时，逐条判
会把两条都标为相关，而基线**从未服务过**其中一条。

源行先按 canonical disclosure-of-record 归约（优先 uf0；该期无 uf0 则取 uf1；
选定版本内取最早公告）。丢弃的**只是未被选中的行**；uf1 **不是**一律排除 ——
只有 uf1 行的期，那条 uf1 就是它的 disclosure of record、会被服务，丢掉它会让
近年申报整体从相关性中消失，反而给盲目构建器一个 INCONCLUSIVE 的出口。

三态结论
--------
* ``REFUSE``       —— 有胜者移动，但被服务的记录没换人。公告日未被消费。
* ``OK``           —— 每个胜者移动日上，被服务的记录都按预期换了人。
* ``INCONCLUSIVE`` —— 没有任何采样日的胜者发生移动。不是判构建器有罪，而是
                      提示扩大采样或改用确定性 fixture。
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:  # pragma: no cover - import bootstrap
    sys.path.insert(0, str(_REPO))

import pandas as pd  # noqa: E402

from src.data.pit.financial_pit_contract import (  # noqa: E402
    AVAILABLE_FROM,
    REPORT_PERIOD,
    select_disclosure_of_record,
)

OK = "OK"
REFUSE = "REFUSE"
INCONCLUSIVE = "INCONCLUSIVE"


class ShiftDiagnosticError(RuntimeError):
    """The diagnostic could not be RUN honestly (bad inputs, unreadable store).

    Distinct from ``REFUSE``, which is a verdict ABOUT the builder.
    """


@dataclass(frozen=True)
class WinnerMove:
    """One sampled date at which the source-side winner moves."""

    instrument: str
    trade_date: date
    base_period: str | None      # W_base — winner under ORIGINAL availability
    shifted_period: str | None   # W_shift — winner under SHIFTED availability


@dataclass(frozen=True)
class ShiftVerdict:
    verdict: str
    moves: tuple[WinnerMove, ...]
    violations: tuple[str, ...]

    def render(self) -> str:
        head = f"verdict={self.verdict}  winner-moves={len(self.moves)}"
        if self.verdict == INCONCLUSIVE:
            return (f"{head}\n  没有任何采样日的胜者发生移动 —— 平移未跨过任何"
                    f"采样日。这不是对构建器的裁决：扩大采样日或改用确定性 "
                    f"fixture 再跑。")
        if self.verdict == REFUSE:
            body = "\n".join(f"  - {v}" for v in self.violations)
            return (f"{head}  violations={len(self.violations)}\n"
                    f"REFUSE —— 胜者移动了，但被服务的记录没换人：公告日未被"
                    f"消费（极可能按 report_period 键入）。\n{body}")
        return f"{head}\nOK —— 每个胜者移动日上，被服务的记录都按预期换了人。"


def winner_at(
    disclosures: pd.DataFrame, trade_day: date, *, shift_days: int = 0,
    calendar: Sequence[date] | None = None,
) -> str | None:
    """The canonical as-of winner at ``trade_day``: the LATEST report_period
    whose availability is on or before that date, or None.

    ``disclosures`` must ALREADY be reduced to disclosure-of-record rows.
    ``shift_days`` moves each record's availability later by that many TRADING
    days (using ``calendar``), which is how the shifted world is modelled
    without touching the store.
    """
    if disclosures.empty:
        return None
    avail = disclosures[AVAILABLE_FROM]
    if shift_days:
        avail = avail.map(lambda d: _shift_forward(d, shift_days, calendar))
    eligible = disclosures[avail.map(
        lambda d: d is not None and not pd.isna(d) and d <= trade_day)]
    if eligible.empty:
        return None
    winner = eligible.sort_values(REPORT_PERIOD).iloc[-1]
    period = winner[REPORT_PERIOD]
    return None if pd.isna(period) else _stamp(period)


def _shift_forward(
    day: object, n: int, calendar: Sequence[date] | None,
) -> date | None:
    """Move ``day`` later by ``n`` trading days.

    With no calendar, falls back to calendar days — acceptable ONLY for the
    synthetic fixtures, and stated rather than hidden: on a real store the
    caller passes the trading calendar.
    """
    if day is None or pd.isna(day):
        return None
    base = day if isinstance(day, date) else pd.Timestamp(day).date()
    if calendar is None:
        return base + timedelta(days=n)
    future = [d for d in calendar if d > base]
    if len(future) < n:
        # Past the end of the calendar the record is simply never available.
        return None
    return future[n - 1]


def find_winner_moves(
    store_by_instrument: Mapping[str, pd.DataFrame],
    sampled_dates: Sequence[date],
    shift_days: int,
    *,
    calendar: Sequence[date] | None = None,
) -> tuple[WinnerMove, ...]:
    """Every (instrument, sampled date) whose SOURCE-SIDE winner moves.

    Computed purely from the store, the shift and the calendar — the rebuilt
    panel is never consulted, so an announcement-blind builder cannot make
    itself look irrelevant.
    """
    if shift_days <= 0:
        raise ShiftDiagnosticError(
            f"shift_days must be positive (got {shift_days}) — the diagnostic "
            "delays announcements; a zero or negative shift tests nothing.")
    moves: list[WinnerMove] = []
    for instrument, raw in store_by_instrument.items():
        # Canonical selection FIRST: rows the view would never serve must not
        # be able to move a winner, or a correct panel gets refused for obeying
        # the serve-rule. Note this KEEPS a uf1-only period — that row IS its
        # period's disclosure of record.
        record = select_disclosure_of_record(raw)
        for td in sampled_dates:
            base = winner_at(record, td)
            shifted = winner_at(record, td, shift_days=shift_days,
                                calendar=calendar)
            if base != shifted:
                moves.append(WinnerMove(instrument, td, base, shifted))
    return tuple(moves)


def adjudicate(
    moves: Sequence[WinnerMove],
    served_base: Mapping[tuple[str, date], str | None],
    served_shifted: Mapping[tuple[str, date], str | None],
) -> ShiftVerdict:
    """Compare what the builder ACTUALLY served against the source-side winners.

    ``served_*`` map (instrument, trade_date) -> the report period the panel
    served there, taken from the panel's own ``periods`` frames.
    """
    if not moves:
        return ShiftVerdict(INCONCLUSIVE, (), ())
    violations: list[str] = []
    for mv in moves:
        key = (mv.instrument, mv.trade_date)
        got_base = served_base.get(key)
        got_shift = served_shifted.get(key)
        if got_base != mv.base_period:
            violations.append(
                f"{mv.instrument} @ {mv.trade_date}: baseline served "
                f"{got_base!r}, source-side winner was {mv.base_period!r}")
        if got_shift != mv.shifted_period:
            violations.append(
                f"{mv.instrument} @ {mv.trade_date}: shifted rebuild served "
                f"{got_shift!r}, expected {mv.shifted_period!r}"
                + ("  <-- 被服务的记录没换人：公告日未被消费"
                   if got_shift == got_base else ""))
    return ShiftVerdict(REFUSE if violations else OK, tuple(moves),
                        tuple(violations))


def _stamp(value: object) -> str:
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    return str(value)


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--store-dir", required=True)
    p.add_argument("--shift-days", type=int, default=5,
                   help="delay every effective announcement by N trading days")
    args = p.parse_args(argv)
    raise ShiftDiagnosticError(
        "the CLI entry point is wired by the campaign runner (it needs the "
        f"panel factory, calendar and sampled dates); {args.store_dir} was not "
        "read. Use find_winner_moves()/adjudicate() from the campaign script."
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
