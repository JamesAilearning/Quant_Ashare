"""Gate-3 Step-A — canonical as-of coverage report over a full *-ever financial
PIT store (research-only, NO factor). The universe comes from
``--instruments-file`` and its floor bundle from ``--floors-universe``.

Generates ``docs/research/gate3_step_a_pit_coverage_report.md`` from the
ingested store, measuring what :class:`FinancialPITDataView` ACTUALLY SERVES
(the corrected disclosure-of-record as-of rule) — not ingest row-level
non-null rates. Everything is fail-loud: a missing store / calendar /
stock_basic fetch aborts the report rather than emitting optimistic numbers
(the Gate-1 one-shot probe's drop-from-denominator flaw is exactly what this
script must never repeat).

Usage:
    python scripts/research/gate3_step_a_coverage_report.py \\
        --store-dir D:/qlib_data/financial_pit_raw \\
        --instruments-file D:/qlib_data/my_cn_data_pit/instruments/csi300.txt \\
        --floors-universe csi300 \\
        --calendar D:/qlib_data/my_cn_data_pit/calendars/day.txt \\
        --out docs/research/gate3_step_a_pit_coverage_report.md

``--floors-universe`` is REQUIRED and selects which per-universe coverage-floor
bundle to enforce (``csi300`` / ``csi800``); it must match the universe of
``--instruments-file`` (an obvious mismatch is refused).

Outputs (all in ONE markdown report):
  1. per-field as-of coverage by year (primary: ex-financial PIT members at
     each quarterly as-of date; appendix: the run's universe incl. financials —
     the Δ vs Gate-1 pooled column is emitted ONLY for csi300, since Gate-1's
     pooled table is a CSI300-ever measurement), incl. the
     adv_receipts∪contract_liab coalesce;
  2. ex-financial breadth by year for the run's universe;
  3. earliest reliable availability per candidate (C1/C2/C3), incl. an
     rd_exp year×quarter drill-down (the C2 window question);
  4. full-universe version_collapse_residual per endpoint;
  5. canonical coverage-floor check — the floor bundle SELECTED by
     ``--floors-universe`` (floors are per-universe), enforced against EVERY
     yearly mean in the 2019-2025 floor window AND the latest as-of snapshot
     when populated (a historical regression fails loud).
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd  # noqa: E402

from src.data.pit._common import qlib_to_ts_code  # noqa: E402
from src.data.pit.financial_pit_contract import (  # noqa: E402
    VersionCollapseResidual,
    build_contract_frame,
    resolve_current_versions,
    version_collapse_residual,
)
from src.data.trading_calendar import (  # noqa: E402
    StaticTradingCalendar,
    load_static_calendar_from_file,
)
from src.data.tushare.client import TushareClient  # noqa: E402
from src.data.tushare.financial_statements import DATA_FIELDS  # noqa: E402
from src.research.financial_pit_coverage_floors import (  # noqa: E402
    ADV_CONTRACT_COALESCE_FLOOR,
    COVERAGE_FLOORS,
    CSI800_ADV_CONTRACT_COALESCE_FLOOR,
    CSI800_COVERAGE_FLOORS,
    CSI800_FLOOR_PROVENANCE,
    FLOOR_PROVENANCE,
)
from src.research.financial_pit_view import (  # noqa: E402
    FinancialPITDataView,
    financial_issuers_from_industry,
)

YEARS = tuple(range(2018, 2026))
QUARTER_ENDS = ((3, 31), (6, 30), (9, 30), (12, 31))

# Gate-1 memo §4 pooled row-level table (full CSI300-ever incl. financials) —
# embedded so the report auto-computes the deviation column. "~" values in the
# memo are recorded at their stated midpoint.
#
# The baseline is BOUND TO THE MEMBERSHIP IT WAS MEASURED ON. Gate-1 measured a
# CSI300-ever set of 627 names; the instruments file has since been rebuilt and
# the same label now resolves to 949. Differencing against a baseline from a
# different member set mixes membership composition into a column that claims
# to isolate "as-of vs pooled" — and it shows: on the 949-name set several
# deltas come out POSITIVE, contradicting the as-of <= pooled expectation the
# column is read under (codex #425 r10).
#
# Enabling the comparison requires a VERIFIABLE membership identity, and size is
# not one: any csi300.txt that happens to hold 627 distinct issuers would re-open
# the comparison over a different member set, which is the very defect the check
# was added for (codex #425 r13). The original 627-name list was NOT preserved,
# so no fingerprint can be recorded today and the comparison stays OFF.
#
# This is deliberately left as a recordable slot rather than deleted: whoever
# recovers the Gate-1 membership list records its fingerprint here and the
# comparison re-enables itself, with the equality check doing real work. Until
# then `None` means "no identity" — never "skip the check".
GATE1_POOLED_MEMBERSHIP_SHA256: str | None = None
GATE1_POOLED_EVER_COUNT = 627  # recorded for the record; NOT an identity
GATE1_POOLED: dict[str, dict[int, float]] = {
    "revenue":       {2018: 1.00, 2019: 1.00, 2020: 1.00, 2021: 1.00, 2022: 1.00, 2023: 1.00, 2024: 1.00, 2025: 1.00},
    "admin_exp":     {2018: 1.00, 2019: 1.00, 2020: 1.00, 2021: 1.00, 2022: 1.00, 2023: 1.00, 2024: 1.00, 2025: 1.00},
    "oper_cost":     {2018: 0.88, 2019: 0.89, 2020: 0.89, 2021: 0.86, 2022: 0.88, 2023: 0.87, 2024: 0.87, 2025: 0.88},
    "fin_exp":       {2018: 0.88, 2019: 0.89, 2020: 0.89, 2021: 0.86, 2022: 0.88, 2023: 0.87, 2024: 0.87, 2025: 0.88},
    "sell_exp":      {2018: 0.86, 2019: 0.87, 2020: 0.87, 2021: 0.84, 2022: 0.86, 2023: 0.85, 2024: 0.86, 2025: 0.86},
    "rd_exp":        {2018: 0.55, 2019: 0.83, 2020: 0.83, 2021: 0.83, 2022: 0.84, 2023: 0.83, 2024: 0.83, 2025: 0.84},
    "int_exp":       {2018: 0.13, 2019: 0.16, 2020: 0.16, 2021: 0.18, 2022: 0.17, 2023: 0.19, 2024: 0.18, 2025: 0.18},
    "total_assets":  {2018: 1.00, 2019: 1.00, 2020: 1.00, 2021: 1.00, 2022: 1.00, 2023: 1.00, 2024: 1.00, 2025: 1.00},
    "adv_receipts":  {2018: 0.82, 2019: 0.79, 2020: 0.34, 2021: 0.33, 2022: 0.37, 2023: 0.39, 2024: 0.41, 2025: 0.45},
    "contract_liab": {2018: 0.10, 2019: 0.16, 2020: 0.85, 2021: 0.91, 2022: 0.90, 2023: 0.93, 2024: 0.92, 2025: 0.91},
}

# a candidate-year is "reliable" when EVERY input field's yearly mean as-of
# coverage is at or above this (C3's adv/contract judged by their coalesce).
CANDIDATE_WINDOW_THRESHOLD = 0.85

# These MUST equal the frozen formulas' inputs (gate4a_ic_evaluator's
# C1/C2/C3_FIELDS), and a governance test pins that equality — a window derived
# from fields the adjudicated formula does not read is a window for a different
# factor (codex #425 r8).
#
# This REVERSES codex #347 r3, which asked for rd_exp on the authority of the
# signed charter's "…− 销售管理费用 + 研发…". The charter is superseded on this
# point by the frozen erratum (docs/prereg/quality_profitability.yaml, decisions
# ⑤ and ①, 2026-07-13): the OMIT formulation never deducts single-line R&D, so
# "rd_exp 不再是任何候选的输入" — MOOT, explicitly. The implemented formulas
# agree. rd_exp's 2018 cliff stays MEASURED and REPORTED (§3), it just cannot
# delay a window for a factor that never reads it. Same for n_cashflow_act: the
# adjudicated C3 is the pure-balance-sheet accrual. Do not re-add either without
# first changing the frozen plan.
CANDIDATE_FIELDS: dict[str, tuple[str, ...]] = {
    "C1 GPA": ("revenue", "oper_cost", "total_assets"),
    "C2 PROF": ("revenue", "oper_cost", "sell_exp", "admin_exp",
                "fin_exp", "total_hldr_eqy_inc_min_int"),
    "C3 cash-OP": ("revenue", "oper_cost", "sell_exp", "admin_exp",
                   "accounts_receiv", "inventories", "prepayment",
                   "accounts_pay", "adv_receipts", "contract_liab",
                   "total_assets"),
}


# Both the VERDICT and the CAUSE are derived from the measured deltas, never
# asserted: which fields deviate most, and whether the deviation actually
# concentrates in the 2018-2020 transition, change with the inputs. A sentence
# reading "大体坐实, 由过渡期滞后驱动" regardless of the numbers printed beside
# it is a conclusion the report has not earned (codex #425 r11). Extracted as a
# pure function so both branches are unit-testable — on the current membership
# the Gate-1 comparison is suppressed entirely, so an inline version would ship
# untested.
GATE1_TRANSITION_LAST_YEAR = 2020
GATE1_CAUSE_CONCENTRATION = 0.60


def gate1_delta_note(per_year: dict[str, dict[int, float]]) -> str:
    """Render §7's Gate-1 deviation finding from the deltas actually computed.

    ``per_year`` maps field -> {year -> delta in percentage points}.
    """
    mean_deltas = {f: sum(d.values()) / len(d) for f, d in per_year.items()}
    within = sum(1 for d in mean_deltas.values() if abs(d) <= 1.0)
    worst = sorted(mean_deltas.items(), key=lambda kv: -abs(kv[1]))[:2]
    worst_txt = "、".join(f"{f} {d:+.1f}pp" for f, d in worst)
    # STRICT majority: a 50/50 split is not "多数" (codex #425 r12), and the
    # tie must fall to the weaker claim — the report never rounds an even split
    # up into a confirmation.
    headline = ("**其余字段 Gate-1 数字大体坐实**"
                if within * 2 > len(mean_deltas)
                else "**其余字段与 Gate-1 的偏离已不算小**")
    shares: list[float] = []
    for wf, _ in worst:
        abs_total: float = sum(abs(v) for v in per_year[wf].values())
        abs_early: float = sum(
            abs(v) for y, v in per_year[wf].items()
            if y <= GATE1_TRANSITION_LAST_YEAR)
        shares.append(abs_early / abs_total if abs_total > 0 else 0.0)
    # WHERE the deviation sits is measured; WHY it sits there is NOT. Early
    # concentration is equally consistent with as-of disclosure lag, an
    # incomplete historical store, and provider gaps in those years — and this
    # function sees only deltas-by-year, none of the ingest / missing-file /
    # disclosure-lag evidence that would separate them (codex #425 r13). So the
    # concentration is REPORTED as the measurement it is, and no cause is named.
    if shares and min(shares) >= GATE1_CAUSE_CONCENTRATION:
        where = (f"—— 偏离的 {pct(min(shares))}+ 集中在 "
                 f"2018-{GATE1_TRANSITION_LAST_YEAR};**成因不由本表判定**"
                 "(as-of 滞后 / 早年 store 不全 / 提供方缺口在本表看来一模一样,"
                 "须另引证据)")
    else:
        where = (f"—— 偏离并未集中在 2018-{GATE1_TRANSITION_LAST_YEAR};"
                 "**成因不由本表判定**")
    return (f"{headline}(§5 的 {len(mean_deltas)} 个可比字段中 {within} 个 Δ 在 "
            f"±1pp 内;偏离最大的是 {worst_txt} {where})。")


class ReportError(RuntimeError):
    """Fail-loud: the report must abort rather than print optimistic numbers."""


# universe -> (field floors, coalesce floor, provenance). Floors are PER-UNIVERSE
# (coverage is a property of the universe's issuer mix), so the report must
# enforce the bundle matching the universe it is measuring: the CSI300 bundle
# applied to CSI800 REJECTS valid coverage where CSI800's calibrated floor is
# lower (contract_liab 0.21 vs 0.12) and SILENTLY ACCEPTS a regression where it
# is higher (oper_cost 0.97 vs 0.98).
_FLOOR_BUNDLES: dict[str, tuple[dict[str, float], float, str]] = {
    "csi300": (dict(COVERAGE_FLOORS), ADV_CONTRACT_COALESCE_FLOOR,
               FLOOR_PROVENANCE),
    "csi800": (dict(CSI800_COVERAGE_FLOORS), CSI800_ADV_CONTRACT_COALESCE_FLOOR,
               CSI800_FLOOR_PROVENANCE),
}


def resolve_floor_bundle(
    universe: str, instruments_file: Path | str,
) -> tuple[dict[str, float], float, str]:
    """Pick the floor bundle for ``universe``, bound to its CANONICAL members.

    The universe is an EXPLICIT required choice (never inferred, never
    defaulted) — a silently-defaulted bundle is how the wrong floors get
    enforced.

    The instruments file must be the universe's canonical membership file
    (``<universe>.txt``). Matching on "the filename does not name a DIFFERENT
    universe" is not enough (codex #425 r3): a basename naming neither known
    universe — a custom sleeve, or simply a RENAMED csi300.txt — would pass
    with any bundle, so a CSI300 membership could be scored against CSI800
    floors (silently loosening contract_liab) and the artifact would still be
    labelled with the selected universe.

    Rejecting an unregistered membership is the point, not a limitation:
    floors are calibrated on a specific issuer mix, so a universe with no
    measured floors has nothing valid to be checked against. The way to run a
    new universe is to measure and register ITS floors, not to borrow
    another's.

    The match is on the COMPLETE filename, not ``Path.stem`` (codex #425 r4):
    stem equals ``csi800`` for ``csi800.csv``, ``csi800.bak`` and a bare
    ``csi800`` alike, so a stem check would accept a stale backup or an
    unrelated export as if it were the canonical membership.

    Note the boundary this does NOT cover, at any level of filename strictness:
    a filename states INTENT, it cannot attest CONTENT. An edited or stale
    ``csi800.txt`` still resolves. The report prints the resolved member count
    (``universe: <U>-ever n=…``) so the content side stays inspectable in the
    artifact rather than being implicitly claimed as verified.
    """
    if universe not in _FLOOR_BUNDLES:
        raise ReportError(
            f"unknown floors universe {universe!r}; known: "
            f"{sorted(_FLOOR_BUNDLES)}"
        )
    expected_name = f"{universe}.txt"
    if Path(instruments_file).name.lower() != expected_name:
        raise ReportError(
            f"--floors-universe={universe} requires that universe's canonical "
            f"membership file ('{expected_name}'); got "
            f"'{Path(instruments_file).name}'. Floors are calibrated on a "
            "specific issuer mix — a renamed, backup, or custom membership "
            "cannot be validated against them. Measure and register floors "
            "for that universe first (see financial_pit_coverage_floors)."
        )
    return _FLOOR_BUNDLES[universe]


def parse_membership(path: Path) -> list[tuple[str, str, str]]:
    """qlib instruments intervals -> [(ts_code, start_iso, end_iso), ...]."""
    if not path.is_file():
        raise ReportError(f"instruments file not found: {path}")
    rows: list[tuple[str, str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if not parts:
            continue
        if len(parts) != 3:
            raise ReportError(f"malformed instruments line: {line!r}")
        rows.append((qlib_to_ts_code(parts[0]), parts[1], parts[2]))
    if not rows:
        raise ReportError(f"instruments file is empty: {path}")
    return rows


def members_on(intervals: Sequence[tuple[str, str, str]], d: date) -> list[str]:
    iso = d.isoformat()
    return sorted({ts for ts, s, e in intervals if s <= iso <= e})


def ever_universe(intervals: Sequence[tuple[str, str, str]]) -> list[str]:
    return sorted({ts for ts, _, _ in intervals})


def quarterly_dates(year: int) -> list[date]:
    return [date(year, m, dd) for m, dd in QUARTER_ENDS]


def fetch_financial_issuers(client: TushareClient) -> tuple[frozenset[str], str]:
    """Derive the financial-sector exclusion set from a live stock_basic
    snapshot (the sanctioned rule). Delisted names (list_status=D) are fetched
    too — the CSI300-ever universe contains them. Fail loud on empty/missing."""
    # L (listed) and D (delisted) are NEVER legitimately empty on the A-share
    # market (thousands each) — an empty response is a transient/partial fetch
    # and silently omitting it would shrink the financial exclusion (delisted
    # banks would slip into the research universe unnoticed, codex #347 r4).
    # P (暂停上市) IS legitimately empty today (the status was effectively
    # abolished by the 2020 delisting reform — verified live) — allowed, but
    # RECORDED in the provenance note, never silently dropped.
    required = {"L", "D"}
    parts: list[pd.DataFrame] = []
    empty_optional: list[str] = []
    for status in ("L", "D", "P"):
        frame = client.call(
            "stock_basic",
            fields="ts_code,name,industry,list_status",
            list_status=status,
        )
        if frame is None or frame.empty:
            if status in required:
                raise ReportError(
                    f"stock_basic(list_status={status!r}) returned empty — "
                    "never legitimately empty; a partial fetch would silently "
                    "shrink the financial exclusion; refusing to continue.")
            empty_optional.append(status)
            continue
        parts.append(frame)
    basic = pd.concat(parts, ignore_index=True)
    empty_note = (f"; {'+'.join(empty_optional)} empty (recorded)"
                  if empty_optional else "")
    if "industry" not in basic.columns:
        raise ReportError("stock_basic response lacks 'industry' — cannot "
                          "derive the financial exclusion.")
    issuers = financial_issuers_from_industry(basic)
    if not issuers:
        raise ReportError("financial exclusion derived EMPTY from stock_basic — "
                          "implausible (banks/brokers/insurers exist); aborting.")
    return issuers, f"stock_basic rows={len(basic)} (L+D+P{empty_note})"


def coalesce_coverage(view: FinancialPITDataView, insts: Sequence[str],
                      d: date) -> float:
    panel = view.as_of(d, ["adv_receipts", "contract_liab"], insts)
    if panel.empty:
        return 0.0
    either = panel["adv_receipts"].notna() | panel["contract_liab"].notna()
    return float(either.mean())


def yearly_asof_coverage(
    view: FinancialPITDataView,
    fields: Sequence[str],
    universe_by_date: dict[date, list[str]],
) -> tuple[dict[str, dict[int, float]], dict[int, float]]:
    """Mean as-of coverage per field per year over the quarterly snapshots.
    Also returns the adv∪contract coalesce row."""
    per_field: dict[str, dict[int, float]] = {f: {} for f in fields}
    coalesce: dict[int, float] = {}
    for year in YEARS:
        dates = quarterly_dates(year)
        missing_dates = [d for d in dates if d not in universe_by_date]
        if missing_dates:
            # a PARTIAL year must fail loud, not silently average fewer
            # snapshots (drop-from-denominator is the exact Gate-1 probe flaw).
            raise ReportError(f"universe snapshots missing for {missing_dates}")
        for f in fields:
            vals = [view.coverage(f, universe_by_date[d], d) for d in dates]
            per_field[f][year] = sum(vals) / len(vals)
        cvals = [coalesce_coverage(view, universe_by_date[d], d) for d in dates]
        coalesce[year] = sum(cvals) / len(cvals)
    return per_field, coalesce


# per-endpoint "reporting presence" anchor: a name counts in the comparable
# denominator only if its anchor field is served non-NA at the as-of date —
# isolating FIELD sparsity (Gate-1 §4's measurand) from listing/universe effects.
_ENDPOINT_ANCHOR = {"income": "revenue", "balancesheet": "total_assets",
                    "cashflow": "n_cashflow_act"}


def gate1_comparable_coverage(
    view: FinancialPITDataView,
    universe: Sequence[str],
) -> dict[str, dict[int, float]]:
    """Appendix table: per field per year, coverage among names whose endpoint
    ANCHOR is served (denominator = reporting names, Gate-1-pooled-comparable).
    The anchor's own row trivially reads 100% and is marked in the report."""
    field_endpoint = {f: ep for ep, fs in DATA_FIELDS.items() for f in fs}
    out: dict[str, dict[int, float]] = {f: {} for f in field_endpoint}
    for year in YEARS:
        panels = [
            view.as_of(d, list(field_endpoint), universe)
            for d in quarterly_dates(year)
        ]
        for f, ep in field_endpoint.items():
            anchor = _ENDPOINT_ANCHOR[ep]
            vals: list[float] = []
            for panel in panels:
                reporters = panel[anchor].notna()
                if int(reporters.sum()) == 0:
                    continue  # no reporter at this snapshot -> no comparison
                vals.append(float(panel.loc[reporters, f].notna().mean()))
            if not vals:
                raise ReportError(
                    f"{f}: zero reporting names in ALL {year} snapshots — "
                    "store empty/miswired; refusing a silent 0%."
                )
            out[f][year] = sum(vals) / len(vals)
    return out


def rd_exp_quarter_table(
    view: FinancialPITDataView,
    universe_by_date: dict[date, list[str]],
) -> dict[int, list[float]]:
    out: dict[int, list[float]] = {}
    for year in YEARS:
        out[year] = [
            view.coverage("rd_exp", universe_by_date[d], d)
            for d in quarterly_dates(year)
        ]
    return out


@dataclass(frozen=True)
class EndpointResidual:
    """Per-endpoint audit result + explicit hole accounting."""

    residual: VersionCollapseResidual
    missing_names: list[str]
    instruments: int


def residual_tables(store_dir: Path, cal: StaticTradingCalendar,
                    universe: Sequence[str]) -> dict[str, EndpointResidual]:
    out: dict[str, EndpointResidual] = {}
    for endpoint, fields in DATA_FIELDS.items():
        frames: list[pd.DataFrame] = []
        missing_names: list[str] = []
        for ts in universe:
            path = store_dir / endpoint / f"{ts}.parquet"
            if not path.is_file():
                missing_names.append(ts)
                continue
            cur = resolve_current_versions(
                build_contract_frame(pd.read_parquet(path), cal))
            if not cur.empty:
                frames.append(cur)
        if not frames:
            raise ReportError(f"{endpoint}: no store files under {store_dir} — "
                              "run the full ingest first.")
        allcur = pd.concat(frames, ignore_index=True)
        res = version_collapse_residual(allcur, list(fields))
        out[endpoint] = EndpointResidual(
            residual=res,
            missing_names=missing_names,
            instruments=len(frames),
        )
    return out


def pct(x: float) -> str:
    return f"{100.0 * x:.1f}%"


def build_report(args: argparse.Namespace) -> str:
    store_dir = Path(args.store_dir)
    if not store_dir.is_dir():
        raise ReportError(f"store dir not found: {store_dir} — ingest first.")
    cal = load_static_calendar_from_file(args.calendar)
    intervals = parse_membership(Path(args.instruments_file))
    ever = ever_universe(intervals)

    client = TushareClient.from_environment()
    fin_issuers, basic_note = fetch_financial_issuers(client)

    view_exfin = FinancialPITDataView(store_dir, cal, financial_issuers=fin_issuers)
    view_all = FinancialPITDataView(store_dir, cal, financial_issuers=frozenset())

    all_fields = [f for fields in DATA_FIELDS.values() for f in fields]

    # -- universes per as-of date ------------------------------------------
    members_by_date: dict[date, list[str]] = {}
    breadth_rows: list[tuple[int, int, int, int]] = []  # year, members, fin, exfin
    for year in YEARS:
        m_tot: list[int] = []
        m_fin: list[int] = []
        for d in quarterly_dates(year):
            members = members_on(intervals, d)
            if not members:
                raise ReportError(
                    f"membership empty at {d} for {args.instruments_file} "
                    "— bad file?")
            members_by_date[d] = members
            n_fin = sum(1 for ts in members if ts in fin_issuers)
            m_tot.append(len(members))
            m_fin.append(n_fin)
        breadth_rows.append((
            year, round(sum(m_tot) / len(m_tot)),
            round(sum(m_fin) / len(m_fin)),
            round(sum(m_tot) / len(m_tot)) - round(sum(m_fin) / len(m_fin)),
        ))

    # -- coverage tables ----------------------------------------------------
    cov_exfin, coal_exfin = yearly_asof_coverage(view_exfin, all_fields, members_by_date)
    cov_cmp = gate1_comparable_coverage(view_all, ever)
    rd_quarters = rd_exp_quarter_table(view_exfin, members_by_date)

    # -- residual audit -----------------------------------------------------
    residuals = residual_tables(store_dir, cal, ever)

    # -- canonical floor check ---------------------------------------------
    # Floors are defined over the 2019-2025 window (the bundle's provenance), so
    # they must be enforced against EVERY measured year in that window, not just
    # the latest snapshot — otherwise a re-ingest that corrupts 2019-2024 history
    # while the latest snapshot stays healthy would still print PASS
    # (codex #347). The latest-snapshot assert_coverage_floor call additionally
    # exercises the live enforcement mechanism itself.
    # The bundle is the one matching THIS run's universe (codex #425 P1): the
    # constants exist per universe, so the report must select, not hardcode.
    floors, coalesce_floor, floor_provenance = resolve_floor_bundle(
        args.floors_universe, args.instruments_file,
    )
    last_snap = date(YEARS[-1], 12, 31)
    floor_years = [y for y in YEARS if y >= 2019]
    if floors:
        violations: dict[str, list[tuple[int, float, float]]] = {}
        for field, floor in floors.items():
            if field not in cov_exfin:
                raise ReportError(
                    f"floor field {field!r} was not measured — floors and the "
                    "measured field set have drifted apart.")
            for y in floor_years:
                got = cov_exfin[field][y]
                if got < floor:
                    violations.setdefault(field, []).append((y, got, floor))
        # the C3-consumable coalesce is floored SEPARATELY: its component
        # floors are regime tripwires only, so a collapsed union with healthy
        # components must still fail loud (codex #347).
        for y in floor_years:
            if coal_exfin[y] < coalesce_floor:
                violations.setdefault("adv∪contract (coalesce)", []).append(
                    (y, coal_exfin[y], coalesce_floor))
        if violations:
            raise ReportError(
                "coverage below the canonical floor in the measured window "
                f"(universe={args.floors_universe}; field -> [(year, actual, "
                f"floor)]): {violations} — a historical regression must be "
                "investigated, never tolerated.")
        view_exfin.assert_coverage_floor(
            dict(floors), members_by_date[last_snap], last_snap)
        snap_coalesce = coalesce_coverage(
            view_exfin, members_by_date[last_snap], last_snap)
        if snap_coalesce < coalesce_floor:
            raise ReportError(
                f"adv∪contract coalesce {snap_coalesce:.4f} below its floor "
                f"{coalesce_floor} on the {last_snap} snapshot.")
        floor_note = (f"PASS (universe={args.floors_universe}) — enforced on "
                      f"EVERY {floor_years[0]}-{floor_years[-1]} yearly mean "
                      f"AND the {last_snap} ex-financial member snapshot, incl. "
                      f"the adv∪contract coalesce floor {coalesce_floor} "
                      f"({floor_provenance})")
    else:
        floor_note = (f"NOT ENFORCED — the {args.floors_universe} floors are "
                      "empty (fill them from this report's measured minima, "
                      "then re-run)")

    # -- render ---------------------------------------------------------------
    lines: list[str] = []
    a = lines.append
    # The universe label follows the run's actual universe — a report that
    # hardcodes one universe's name while measuring another's members is a
    # factual error in the artifact itself (codex #425 P1 follow-through).
    universe_label = f"{args.floors_universe.upper()}-ever"
    a(f"# Gate-3 Step-A · canonical as-of 覆盖率报告(全量 {universe_label} 财报 PIT store)")
    a("")
    a("> 生成: `scripts/research/gate3_step_a_coverage_report.py`(fail-loud,可复现)。")
    # fin_issuers is the FULL-MARKET classifier set; the number excluded from
    # THIS universe is its intersection with the universe's members. Reporting
    # the classifier size as the universe's exclusion count overstates it
    # (codex #425 r5).
    universe_fin = sorted(set(ever) & set(fin_issuers))
    a(f"> Store: `{store_dir}`;universe: {universe_label} n={len(ever)};"
      f"金融排除 n={len(universe_fin)}(全市场金融分类器 n={len(fin_issuers)};{basic_note})。")
    a("> 口径: **view 实际服务值**(修正后 disclosure-of-record serve-rule 的 as-of 横截面),每年 4 个季度末快照取均值 —— 不是 ingest 行级非空率。")
    a(f"> Coverage-floor 检查: {floor_note}。")
    a("")
    a("## 1. 逐字段 as-of 覆盖率(主表:各快照日 ex-金融 在册成员)")
    a("")
    hdr = "| field | " + " | ".join(str(y) for y in YEARS) + " |"
    sep = "|---" * (len(YEARS) + 1) + "|"
    a(hdr)
    a(sep)
    for f in all_fields:
        a(f"| {f} | " + " | ".join(pct(cov_exfin[f][y]) for y in YEARS) + " |")
    a("| **adv∪contract (coalesce)** | "
      + " | ".join(pct(coal_exfin[y]) for y in YEARS) + " |")
    a("")
    a("## 2. ex-金融 breadth(年均在册数)")
    a("")
    a("| year | members | financial | ex-financial |")
    a("|---|---|---|---|")
    for year, tot, fin, exfin in breadth_rows:
        a(f"| {year} | {tot} | {fin} | {exfin} |")
    a("")
    a("## 3. rd_exp 季度末 as-of 细分(记录在案的数据事实)")
    a("")
    a("**注意口径**: 冻结勘误(`docs/prereg/quality_profitability.yaml` 决策⑤/①)"
      "把 C2/C3 定为 **OMIT 式**(单列研发不扣除)—— rd_exp **不是任何候选的输入**"
      "(原文:『OMIT 式下 rd_exp 不再是任何候选的输入』),已实现的冻结公式亦不读它。"
      "因此本节是**数据事实的记录**,不再是候选窗口的判据;窗口见 §6。")
    a("")
    a("| year | 03-31 | 06-30 | 09-30 | 12-31 |")
    a("|---|---|---|---|---|")
    for year in YEARS:
        a(f"| {year} | " + " | ".join(pct(v) for v in rd_quarters[year]) + " |")
    a("")
    a("## 4. 全宇宙 version_collapse_residual(逐表)")
    a("")
    a("| endpoint | instruments | missing files | both-version periods | differing fraction | n differ |")
    a("|---|---|---|---|---|---|")
    for endpoint, info in residuals.items():
        res = info.residual
        a(f"| {endpoint} | {info.instruments} | {len(info.missing_names)} | "
          f"{res.n_both_version_periods} | "
          f"{res.overall_differing_fraction():.4%} | "
          f"{len(res.differing)} |")
    a("")
    # Gate-1's pooled table is a CSI300-ever measurement, so every Gate-1
    # comparison below is gated on the run's universe (codex #425 P2) AND on
    # the membership it was measured on (codex #425 r10) — same label, rebuilt
    # file, different member set.
    ever_fingerprint = hashlib.sha256(
        "\n".join(sorted(ever)).encode("utf-8")).hexdigest()
    gate1_comparable = (args.floors_universe == "csi300"
                        and GATE1_POOLED_MEMBERSHIP_SHA256 is not None
                        and ever_fingerprint == GATE1_POOLED_MEMBERSHIP_SHA256)
    a("## 5. 附表:"
      + ("Gate-1 可比口径" if gate1_comparable else "含金融的 anchor 分母口径")
      + f"({universe_label} 含金融,分母=当期 anchor 有披露的名字)"
      + ("+ Δ vs Gate-1 pooled" if gate1_comparable else ""))
    a("")
    a("anchor: income→revenue / balancesheet→total_assets / cashflow→n_cashflow_act"
      "(anchor 自身行恒 100%,仅作分母定义)。")
    if gate1_comparable:
        a("Gate-1 §4 是行级 pooled,本表是 as-of 横截面 — Δ 为方向参考,预期 as-of ≤ pooled。")
    else:
        # GATE1_POOLED is a CSI300-ever measurement; differencing another
        # universe against it compares different issuer sets and would read as
        # a coverage delta when it is a universe delta.
        if args.floors_universe != "csi300":
            why = (f"本报告宇宙为 {universe_label},而 Gate-1 §4 pooled 是 "
                   "CSI300-ever 口径")
        else:
            why = ("宇宙标签同为 csi300,但**基线的成员身份无法核验** —— Gate-1 "
                   f"测于 {GATE1_POOLED_EVER_COUNT} 名的 CSI300-ever(本次成分文件"
                   f"解析出 {len(ever)} 名),而那份原始名单**未留存**,"
                   "无指纹可比")
        a(f"**Δ 列不适用**: {why} —— issuer 集合无法证明相同,相减可能混入"
          "**成分差异**而非覆盖差异,故本表只列本宇宙的 as-of 值,不做 Δ 对比。"
          f"(本次成员集指纹 `{ever_fingerprint[:16]}…`;补录 Gate-1 名单的指纹后"
          "对比即自动恢复,规模相等**不**作为身份。)")
    a("")
    a("| field | " + " | ".join(str(y) for y in YEARS)
      + (" | Δ vs Gate-1 (mean) |" if gate1_comparable else " |"))
    a("|---" * (len(YEARS) + (2 if gate1_comparable else 1)) + "|")
    for f in all_fields:
        row = " | ".join(pct(cov_cmp[f][y]) for y in YEARS)
        anchor_mark = " *(anchor)*" if f in _ENDPOINT_ANCHOR.values() else ""
        if not gate1_comparable:
            # no Δ column off the CSI300 universe — see the note above
            a(f"| {f}{anchor_mark} | {row} |")
            continue
        if f in GATE1_POOLED:
            deltas = [cov_cmp[f][y] - GATE1_POOLED[f][y] for y in YEARS]
            dnote = f"{100.0 * sum(deltas) / len(deltas):+.1f}pp"
        else:
            dnote = "n/a"
        a(f"| {f}{anchor_mark} | {row} | {dnote} |")
    a("")
    a("## 6. 候选最早可靠可用期(规则化推导)")
    a("")
    a(f"规则: 候选的年度可用性 = 其全部输入字段该年均值的最小值(C3 的 adv_receipts/"
      f"contract_liab 以 coalesce 计);最早可靠年 = 自该年起所有已测年份都 ≥ "
      f"{pct(CANDIDATE_WINDOW_THRESHOLD)} 的最早年份。")
    a("")
    # The input list is PRINTED, not just used: the published artifact is what
    # most readers consume, and a window whose inputs are invisible cannot be
    # checked against the frozen formulas by anyone reading it. A governance
    # test parses these lines back and pins them to CANDIDATE_FIELDS, so the
    # checked-in report cannot drift from the generator (codex #425 r9).
    a("输入字段(取自冻结公式,由治理测试钉住):")
    a("")
    for cand, fields in CANDIDATE_FIELDS.items():
        a(f"- `{cand}`: " + ", ".join(f"`{f}`" for f in sorted(fields)))
    a("")
    a("| candidate | " + " | ".join(str(y) for y in YEARS) + " | earliest reliable |")
    a("|---" * (len(YEARS) + 2) + "|")
    for cand, fields in CANDIDATE_FIELDS.items():
        mins: dict[int, float] = {}
        for y in YEARS:
            vals = []
            for f in fields:
                if f in ("adv_receipts", "contract_liab"):
                    vals.append(coal_exfin[y])
                else:
                    vals.append(cov_exfin[f][y])
            mins[y] = min(vals)
        earliest = None
        for y in YEARS:
            if all(mins[yy] >= CANDIDATE_WINDOW_THRESHOLD for yy in YEARS if yy >= y):
                earliest = y
                break
        a(f"| {cand} | " + " | ".join(pct(mins[y]) for y in YEARS)
          + f" | **{earliest if earliest else 'NONE'}** |")
    a("")
    a("注: C3 需两期(Δ应计)→ 有效首个横截面比起始年再晚一个报告期。输入字段取自"
      "**冻结公式**(OMIT 式:rd_exp 与 n_cashflow_act 均非候选输入),由治理测试"
      "钉住与 `gate4a_ic_evaluator` 的 C1/C2/C3_FIELDS 逐字段相等 —— 用公式不读的"
      "字段推出来的窗口,是另一个因子的窗口。")
    a("")
    # The table answers "when is the DATA good enough", which is not the
    # same question as "when may the experiment start". Reading the former
    # as the latter would silently reopen a frozen decision, so both are
    # stated here, where the number is actually read off.
    a("**本表只回答『数据何时够用』,不回答『实验何时可以开始』**: 主检验窗口由"
      "冻结勘误②统一定为 **2019 起**(理由是会计口径 regime 一致性 —— 2018 前"
      "多数名 admin_exp 内含研发,属混合 regime),与本表推出的数据可用年"
      "**各自独立**。本表出现 2018 不构成从 2018 起跑的依据。")
    a("")
    # §7's frame is "deviations from the Gate-1 memo", and Gate-1 is a
    # CSI300-ever measurement. Off that universe, every Gate-1-derived number
    # is another universe's finding: stating it here would contradict §5's own
    # "not comparable" note and pass CSI300 conclusions off as this universe's
    # (codex #425 P2). So off csi300 the section reports only THIS universe's
    # measured facts.
    # The suppression reason must name the ACTUAL mismatch: off csi300 it is the
    # universe, on csi300 it is the membership the baseline was measured on.
    # Printing "Gate-1 is CSI300-ever" as the reason on a CSI300-ever run reads
    # as a contradiction (codex #425 r10).
    if gate1_comparable:
        head7 = "偏离 Gate-1 memo 的意外(如实记录)"
    elif args.floors_universe != "csi300":
        head7 = (f"{universe_label} as-of 口径要点(如实记录;Gate-1 memo 为 "
                 "CSI300-ever 口径,本节不与之对比)")
    else:
        head7 = (f"{universe_label} as-of 口径要点(如实记录;Gate-1 基线的成员名单"
                 "未留存、身份无法核验,故本节不与之对比 —— 见 §5)")
    a("## 7. " + head7)
    a("")
    # §7 items are numbered at emission, not hardcoded: item 5 is emitted only
    # when the Gate-1 comparison applies, so a literal "6." would leave a
    # visible 4 -> 6 gap off csi300.
    findings: list[str] = []
    note = findings.append
    total_missing = sum(len(info.missing_names) for info in residuals.values())
    # A missing store file is NOT evidence of a fail-loud ingest hole: the
    # ingestor treats a legitimately EMPTY provider frame as success and writes
    # nothing, so "no file" covers both refusals and genuine no-data responses.
    # The report can observe only the absence; classifying every absence as a
    # true-ambiguity hole overstates the hole count (codex #425 r6). The
    # authoritative split lives in the ingest run's own hole counter/log.
    note(f"**提供方歧义重复(已消歧)+ 现存 {total_missing} 个缺失 store 文件**"
      "(§8 名单)。原 27 hole 的主因 —— 同一 `(ts_code, end_date, update_flag)` "
      "两行不同内容、仅公告日可区分(例五粮液)—— 已由 OpenSpec "
      "`fix-financial-ingest-ambiguous-duplicates` 消歧:版本身份 = 有效公告日"
      "(f_ann_date 缺则 ann_date),同三元组不同公告日 = 两个独立披露事件都保留,"
      "record = 最早披露。**但缺文件的成因本报告一律不定性** —— 它只观测到"
      "'store 里没有这个 parquet',而多种互斥成因留下的观测量完全相同:ingest 对真"
      "歧义 fail-loud 拒写、提供方返回空数据(ingest 视为成功且不写文件)、以及"
      "ingest 未跑完/中断/被裁剪/该名字根本没抓过。定性须查该次 ingest 的 "
      "manifest / hole 计数 / 日志,本表做不到;详见 §8。")
    note("**rd_exp 的 2018 年 as-of 断崖(已记录,但不再驱动任何窗口)**: "
      + ("行级 pooled 显示 2018=55%,但 " if gate1_comparable else "")
      + f"as-of 横截面 2018 H1 仅 {pct(rd_quarters[2018][1])}、Q3 "
      f"{pct(rd_quarters[2018][2])}、Q4 {pct(rd_quarters[2018][3])} —— 单列研发"
      "费用自 2018 Q3 报告才开始批量披露"
      + ("(Gate-1 的『2018 早窗弱』在 as-of 口径下更硬)" if gate1_comparable else "")
      + "。**但冻结勘误的 OMIT 式下 rd_exp 不是任何候选的输入**,因此这条断崖"
      "既不能推迟也不能否决 C2/C3 的可用窗口;各候选窗口一律由 §6 按冻结公式的"
      "输入字段推出。主检验窗口统一 2019 起的理由在勘误②(会计口径 regime 一致性),"
      "不是 rd_exp 覆盖率。")
    int_rng = [cov_exfin["int_exp"][y] for y in YEARS]
    fin_rng = [cov_exfin["fin_exp"][y] for y in YEARS]
    note(f"**int_exp as-of 覆盖 {pct(min(int_rng))}-{pct(max(int_rng))}**"
      + ("(显著低于 pooled 13-18%)" if gate1_comparable else "(年报为主的稀疏科目)")
      + " —— 财报中仅年报披露居多。无影响: charter 已把 C2 利息项"
      f"定为 fin_exp(as-of {pct(min(fin_rng))}-{pct(max(fin_rng))})。")
    res_note = " / ".join(
        f"{ep} {info.residual.overall_differing_fraction():.2%}"
        for ep, info in residuals.items())
    note(f"**全宇宙重述残差非零但极小**(§4: {res_note},"
      "含 NA↔非NA transition)。serve-rule 恒取 uf0/最早披露 → 无前视;残差为"
      "诚信包络的已量化界。")
    if gate1_comparable:
        # Derived from the deltas actually computed above, never hardcoded: a
        # narrative pinned to yesterday's numbers contradicts the table printed
        # directly above it the moment the inputs move (codex #425 r10).
        note(gate1_delta_note({
            f: {y: 100.0 * (cov_cmp[f][y] - GATE1_POOLED[f][y]) for y in YEARS}
            for f in all_fields if f in GATE1_POOLED
        }))
    fin_counts = [fin for _, _, fin, _ in breadth_rows]
    note(f"**金融排除规模**: 行业名单法(stock_basic)在 {universe_label} 上排除 "
      f"{len(universe_fin)} 名(全市场金融分类器 {len(fin_issuers)} 名,与本宇宙成分"
      f"求交后为前者),逐年在册金融 {min(fin_counts)}-{max(fin_counts)} 名"
      "(§2)—— 以本表为准。")
    for idx, item in enumerate(findings, start=1):
        a(f"{idx}. {item}")
    a("")
    a("## 8. 缺失的 store 文件(显式列出,绝不静默)")
    a("")
    # This section reports an OBSERVATION (no parquet), never a diagnosis: the
    # ingestor writes nothing both when it fail-loud refuses a true ambiguity
    # AND when the provider legitimately returns an empty frame, and a partial /
    # interrupted / pruned / never-attempted ingest leaves the very same trace.
    # Only the ingest run's own manifest can tell them apart (codex #425 r7).
    a("缺文件 = 该 (instrument, endpoint) 在 store 中没有 parquet。**本表只报告这个"
      "观测事实,不对成因定性** —— 多种互斥成因留下的痕迹完全相同:①ingest 对真歧义 "
      "fail-loud 拒写;②提供方返回空数据,ingest 视为成功、不写文件;③ingest 未跑完"
      "/中断/store 被裁剪/该名字根本没抓过。要定性,查该次 ingest 的 manifest / "
      "hole 计数 / 日志,而非本表。")
    a("")
    # The coverage tables' denominator is the EX-FINANCIAL members in force at
    # each snapshot, while this list spans the whole -ever universe. A missing
    # name that is financial never enters that denominator, so claiming every
    # missing name depresses reported coverage is false for those (codex #425
    # r7). The split is therefore computed and stated, not asserted.
    miss_all = sorted({n for info in residuals.values() for n in info.missing_names})
    # Same namespace on both sides (ts_code) — cf. the `set(ever) & set(
    # fin_issuers)` intersection above; no conversion, and no conversion that
    # could silently miss.
    miss_fin = [n for n in miss_all if n in fin_issuers]
    a(f"**与覆盖率分母的关系**:覆盖率主表分母 = 各快照日**在册 ex-金融**成员,"
      f"而本表覆盖整个 {universe_label} 宇宙。因此只有**非金融且当日在册**的缺失名字"
      f"才真正压低所报覆盖率(诚实方向:压低而非抬高);金融名字与当日不在册的名字"
      f"根本不进分母,列出它们是为审计完整性,不影响覆盖率数字。本次 {len(miss_all)} "
      f"个缺失 instrument 中金融 {len(miss_fin)} 个"
      + (f"({', '.join(miss_fin)})" if miss_fin else "") + "。")
    a("")
    for endpoint, info in residuals.items():
        names = info.missing_names
        marked = [f"{n}(金融)" if n in miss_fin else n for n in names]
        shown = ", ".join(marked[:30]) + (" …" if len(marked) > 30 else "")
        a(f"- **{endpoint}**: {len(names)} missing — {shown if names else '(none)'}")
    a("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--store-dir", required=True)
    p.add_argument("--instruments-file", required=True)
    p.add_argument("--calendar", required=True)
    # REQUIRED, never defaulted: floors are per-universe, and a silently
    # defaulted bundle enforces the wrong floors (codex #425 P1).
    p.add_argument("--floors-universe", required=True,
                   choices=sorted(_FLOOR_BUNDLES),
                   help="which coverage-floor bundle to enforce; must match "
                        "the universe of --instruments-file")
    p.add_argument("--out", default=None,
                   help="write the markdown report here (default: stdout)")
    args = p.parse_args(argv)
    report = build_report(args)
    if args.out:
        Path(args.out).write_bytes(report.encode("utf-8"))
        print(f"report written: {args.out}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
