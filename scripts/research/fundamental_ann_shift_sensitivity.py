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
    _parse_yyyymmdd,
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
    """One (endpoint, instrument, sampled date) at which the winner moves.

    ENDPOINT is part of the identity, not a detail: the view serves each
    endpoint INDEPENDENTLY, so the same ticker legitimately has a different
    served period per endpoint on the same day. Collapsing them would both
    lose movements (one endpoint overwriting another) and manufacture false
    conflicts out of a perfectly legal cross-endpoint difference.
    """

    endpoint: str
    instrument: str
    trade_date: date
    base_period: str | None      # W_base — winner under ORIGINAL availability
    shifted_period: str | None   # W_shift — winner under SHIFTED availability

    @property
    def key(self) -> tuple[str, str, date]:
        return (self.endpoint, self.instrument, self.trade_date)


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
    store_by_key: Mapping[tuple[str, str], pd.DataFrame],
    sampled_dates: Sequence[date],
    shift_days: int,
    *,
    calendar: Sequence[date] | None = None,
) -> tuple[WinnerMove, ...]:
    """Every (endpoint, instrument, sampled date) whose SOURCE-SIDE winner moves.

    ``store_by_key`` is keyed by ``(endpoint, instrument)`` — one frame per
    endpoint per ticker, because endpoints are served independently and a
    single per-ticker key would let one endpoint's frame overwrite another's.

    Computed purely from the store, the shift and the calendar — the rebuilt
    panel is never consulted, so an announcement-blind builder cannot make
    itself look irrelevant.
    """
    if shift_days <= 0:
        raise ShiftDiagnosticError(
            f"shift_days must be positive (got {shift_days}) — the diagnostic "
            "delays announcements; a zero or negative shift tests nothing.")
    moves: list[WinnerMove] = []
    for (endpoint, instrument), raw in store_by_key.items():
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
                moves.append(
                    WinnerMove(endpoint, instrument, td, base, shifted))
    return tuple(moves)


def adjudicate(
    moves: Sequence[WinnerMove],
    served_base: Mapping[tuple[str, str, date], str | None],
    served_shifted: Mapping[tuple[str, str, date], str | None],
) -> ShiftVerdict:
    """Compare what the builder ACTUALLY served against the source-side winners.

    ``served_*`` map ``(endpoint, instrument, trade_date)`` -> the report period
    the panel served there, taken from the panel's own ``periods`` frames.

    A move whose key is ABSENT from both served maps is a VIOLATION, not a
    skip. The source scan is already restricted to the requested, non-financial
    universe (see ``run_diagnostic``), so every expectation it produces SHOULD
    have a panel entry; absence therefore means the builder did not supply the
    required period provenance. Skipping such keys would hand an
    announcement-blind builder a trivial escape — emit no ``periods`` at all,
    have every expectation discarded, and collect an INCONCLUSIVE.
    """
    if not moves:
        return ShiftVerdict(INCONCLUSIVE, (), ())
    violations: list[str] = []
    adjudicated: list[WinnerMove] = []
    for mv in moves:
        adjudicated.append(mv)
        # Membership is checked in EACH map independently, not with `and`.
        # A one-sided omission is the dangerous case: when the expected
        # shifted period is legitimately None (delaying the first disclosure
        # makes nothing available yet), `.get()` on a MISSING key also yields
        # None — so an absent key would masquerade as a correct explicit NA
        # and the pair would adjudicate OK.
        absent = [name for name, served in
                  (("baseline", served_base), ("shifted", served_shifted))
                  if mv.key not in served]
        if absent:
            violations.append(
                f"{mv.endpoint}/{mv.instrument} @ {mv.trade_date}: "
                f"{' and '.join(absent)} reported NO served report period — "
                "required provenance is missing for a key the source scan "
                "proved relevant. Refusing rather than letting an absent key "
                "read as an explicit NA.")
            continue
        got_base = served_base.get(mv.key)
        got_shift = served_shifted.get(mv.key)
        where = f"{mv.endpoint}/{mv.instrument} @ {mv.trade_date}"
        if got_base != mv.base_period:
            violations.append(
                f"{where}: baseline served {got_base!r}, source-side winner "
                f"was {mv.base_period!r}")
        if got_shift != mv.shifted_period:
            violations.append(
                f"{where}: shifted rebuild served {got_shift!r}, expected "
                f"{mv.shifted_period!r}"
                + ("  <-- 被服务的记录没换人：公告日未被消费"
                   if got_shift == got_base else ""))
    return ShiftVerdict(REFUSE if violations else OK, tuple(adjudicated),
                        tuple(violations))


def _stamp(value: object) -> str:
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    return str(value)


# --------------------------------------------------------------- 端到端执行

def write_shifted_store(
    store_dir: Path, out_dir: Path, shift_days: int,
    calendar: Sequence[date],
) -> Path:
    """Write a copy of ``store_dir`` with every announcement delayed.

    Only ``ann_date`` / ``f_ann_date`` are moved: ``available_from_trade_date``
    is DERIVED by the contract layer from those, so the view recomputes it and
    the shifted world stays internally consistent. Writing a shifted
    availability directly would let the two disagree.

    The shift is the only difference — same instruments, same periods, same
    values — so any behavioural difference downstream is attributable to the
    announcement date alone.
    """
    if shift_days <= 0:
        raise ShiftDiagnosticError(
            f"shift_days must be positive (got {shift_days}).")
    src = Path(store_dir).resolve()
    dst = Path(out_dir).resolve()
    # Refuse BEFORE any write: with dst == src (or nested inside it) this
    # function would overwrite the REAL store's parquet files in place —
    # permanently delaying its announcements — and `_record_frames` would then
    # derive its baseline expectations from the already-mutated data, so the
    # verdict compares a corrupted store against itself.
    if src == dst or src in dst.parents or dst in src.parents:
        raise ShiftDiagnosticError(
            f"shifted-store output {dst} overlaps the source store {src} — "
            "writing would mutate the real store in place and invalidate the "
            "baseline. Choose a disjoint workdir.")
    out_dir.mkdir(parents=True, exist_ok=True)
    wrote = 0
    for endpoint_dir in sorted(p for p in store_dir.iterdir() if p.is_dir()):
        target = out_dir / endpoint_dir.name
        target.mkdir(parents=True, exist_ok=True)
        for parquet in sorted(endpoint_dir.glob("*.parquet")):
            frame = pd.read_parquet(parquet)
            for col in ("ann_date", "f_ann_date"):
                if col in frame.columns:
                    # Written back in the store's own YYYYMMDD string form: the
                    # contract layer parses these strictly and rejects any
                    # other rendering as corruption, so emitting date objects
                    # (which parquet would store as ISO) would fail the store
                    # rather than shift it.
                    frame[col] = frame[col].map(
                        lambda d: _shift_stamp(d, shift_days, calendar))
            frame.to_parquet(target / parquet.name, index=False)
            wrote += 1
    if not wrote:
        raise ShiftDiagnosticError(
            f"{store_dir} held no endpoint parquet files — nothing to shift, "
            "so the diagnostic would compare a store against itself.")
    return out_dir


def _shift_stamp(
    day: object, n: int, calendar: Sequence[date],
) -> object:
    """``_shift_forward`` rendered back into the store's YYYYMMDD string form.

    A blank/NA announcement stays blank — a record with no announcement date is
    UNAVAILABLE, and inventing one for it would manufacture availability the
    original store never had.
    """
    if day is None or (not isinstance(day, str) and pd.isna(day)):
        return day
    if isinstance(day, str) and not day.strip():
        return day
    # Parse with the CONTRACT's own strict YYYYMMDD semantics, never with
    # `pd.Timestamp(...)`: the store legitimately holds these tokens as int or
    # exact-.0 float, and `pd.Timestamp(20220331)` reads that as nanoseconds
    # since the epoch — 1970-01-01, not 2022-03-31. The shifted store would then
    # get an announcement near the start of the calendar while the source side
    # parses the original correctly, and the diagnostic would REFUSE the real
    # bridge for a defect of its own making.
    parsed = day if isinstance(day, date) else _parse_yyyymmdd(day)
    if parsed is None:  # pragma: no cover - blank handled above
        return day
    moved = _shift_forward(parsed, n, calendar)
    return "" if moved is None else moved.strftime("%Y%m%d")


def _canonical_period(raw: object) -> str | None:
    """A served report-period token in canonical YYYYMMDD spelling, or None."""
    if raw is None or pd.isna(raw):
        return None
    parsed = _parse_yyyymmdd(raw)
    return None if parsed is None else parsed.strftime("%Y%m%d")


def served_periods(
    panel_periods: Mapping[str, pd.DataFrame],
) -> dict[tuple[str, str, date], str | None]:
    """A panel's ``periods`` frames as (endpoint, instrument, date) -> period.

    Grouped BY ENDPOINT, because the view serves endpoints independently: on
    one trade date a ticker's income period and balance-sheet period may
    legitimately differ, and flattening them into a single per-ticker key would
    turn that legal difference into a spurious conflict (and silently drop one
    endpoint's answer).

    WITHIN one endpoint the requested fields do share a served period, so a
    disagreement there IS a panel bug — asserted rather than averaged over.
    """
    out: dict[tuple[str, str, date], str | None] = {}
    for terminal, frame in panel_periods.items():
        endpoint = _endpoint_of_terminal(terminal)
        for when in frame.index:
            for inst in frame.columns:
                raw = frame.loc[when, inst]
                # Canonicalise through the contract's strict YYYYMMDD parser,
                # exactly as the announcement tokens are: a contract-valid
                # store may spell `end_date` as the exact-`.0` float
                # `20220331.0`, and the view preserves that raw spelling in
                # its period frame — while `winner_at()` (parsing through the
                # contract) emits "20220331". Comparing the two spellings
                # verbatim would REFUSE a correct bridge over a formatting
                # difference, not a behavioural one.
                value = _canonical_period(raw)
                key = (endpoint, str(inst), pd.Timestamp(when).date())
                if key in out and out[key] != value:
                    raise ShiftDiagnosticError(
                        f"fields of endpoint {endpoint!r} disagree on the "
                        f"served period at {key}: {out[key]!r} vs {value!r} — "
                        "a panel bug, not a shift-sensitivity finding.")
                out[key] = value
    return out


def _endpoint_of_terminal(terminal: str) -> str:
    """``$revenue`` -> ``income``, through the view's own field table."""
    from src.research.financial_pit_view import _FIELD_ENDPOINT  # noqa: PLC0415
    from src.research.fundamental_panel import to_field  # noqa: PLC0415

    return _FIELD_ENDPOINT[to_field(terminal)]


def run_diagnostic(
    *,
    store_dir: Path,
    calendar: Sequence[date],
    trade_dates: Sequence[date],
    fields: Sequence[str],
    instruments: Sequence[str],
    financial_issuers: frozenset[str],
    shift_days: int,
    workdir: Path,
    build_panel: object = None,
) -> ShiftVerdict:
    """Run defense (iii) end to end: build BOTH panels and adjudicate.

    This is the operational entry point — the helpers above only compute the
    expectation. A diagnostic whose two sides are supplied by its caller proves
    nothing about any builder, so the shifted store is constructed here, both
    panels are built through the REAL factory, and the served periods are read
    off the panels' own ``periods`` frames.

    ``build_panel`` defaults to the production bridge; tests inject a
    deliberately announcement-blind builder to prove the REFUSE path fires.
    """
    from src.data.trading_calendar import StaticTradingCalendar  # noqa: PLC0415
    from src.research.financial_pit_view import (  # noqa: PLC0415
        FinancialPITDataView,
    )
    from src.research.fundamental_panel import (  # noqa: PLC0415
        build_fundamental_panel,
    )

    factory = build_fundamental_panel if build_panel is None else build_panel
    cal = StaticTradingCalendar(list(calendar))

    base_view = FinancialPITDataView(store_dir, cal,
                                     financial_issuers=financial_issuers)
    base_panel = factory(base_view, fields, trade_dates, instruments)  # type: ignore[operator]

    shifted_dir = write_shifted_store(
        store_dir, workdir / f"shifted_{shift_days}", shift_days, calendar)
    shifted_view = FinancialPITDataView(shifted_dir, cal,
                                        financial_issuers=financial_issuers)
    shifted_panel = factory(shifted_view, fields, trade_dates, instruments)  # type: ignore[operator]

    # The expectation comes from the SOURCE data, never from either panel —
    # otherwise an announcement-blind builder would define itself irrelevant.
    moves = find_winner_moves(
        _record_frames(store_dir, fields, cal, instruments, financial_issuers),
        trade_dates, shift_days, calendar=calendar)
    return adjudicate(moves, served_periods(base_panel.periods),
                      served_periods(shifted_panel.periods))


def _record_frames(
    store_dir: Path, fields: Sequence[str], calendar: object,
    instruments: Sequence[str], financial_issuers: frozenset[str],
) -> dict[tuple[str, str], pd.DataFrame]:
    """Disclosure-of-record frames per (endpoint, instrument) for the queried
    endpoints, RESTRICTED to the universe the panels were actually built for.

    Both restrictions matter on a real full-universe store:

    * a ticker the panels were not asked to build has no panel entry to compare
      against, and
    * a FINANCIAL issuer is dropped inside the view, so it has no entry either.

    Scanning every parquet would generate winner movements for such tickers and
    then refuse the (correct) bridge for not serving them.

    Reduced by the CANONICAL selection before any winner is computed, so rows
    the view would never serve cannot move a winner — and so a uf1-only period,
    which IS its period's disclosure of record, still can.
    """
    from src.data.pit._common import (
        qlib_to_ts_code,  # noqa: PLC0415
        to_qlib_ticker,  # noqa: PLC0415
    )
    from src.data.pit.financial_pit_contract import (  # noqa: PLC0415
        build_contract_frame,
        resolve_current_versions,
    )
    from src.research.financial_pit_view import (  # noqa: PLC0415
        _FIELD_ENDPOINT,
    )

    # Store files are ts_code-named; callers may pass either namespace, so
    # normalise through the SAME converter the bridge uses rather than
    # hand-splitting the label.
    wanted_ts = {
        label if "." in label else qlib_to_ts_code(label)
        for label in instruments
    }
    # The exclusion set is normalised the SAME way the view normalises it.
    # A raw subtraction would miss a qlib-form exclusion (``SH600000``) whose
    # store file is ``600000.SH``: the view drops that issuer, the panels have
    # no entry for it, yet the source scan would still emit moves for it and
    # REFUSE the correct bridge.
    wanted_ts -= {
        label if "." in label else qlib_to_ts_code(label)
        for label in financial_issuers
    }

    endpoints = {_FIELD_ENDPOINT[f] for f in fields if f in _FIELD_ENDPOINT}
    out: dict[tuple[str, str], pd.DataFrame] = {}
    for endpoint in sorted(endpoints):
        endpoint_dir = Path(store_dir) / endpoint
        if not endpoint_dir.is_dir():
            continue
        for parquet in sorted(endpoint_dir.glob("*.parquet")):
            if parquet.stem not in wanted_ts:
                continue
            raw = pd.read_parquet(parquet)
            record = select_disclosure_of_record(
                resolve_current_versions(
                    build_contract_frame(raw, calendar)))  # type: ignore[arg-type]
            out[(endpoint, to_qlib_ticker(parquet.stem))] = record
    return out


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
