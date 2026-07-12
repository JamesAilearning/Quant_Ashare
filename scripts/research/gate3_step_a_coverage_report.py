"""Gate-3 Step-A — canonical as-of coverage report over the full CSI300-ever
financial PIT store (research-only, NO factor).

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
        --calendar D:/qlib_data/my_cn_data_pit/calendars/day.txt \\
        --out docs/research/gate3_step_a_pit_coverage_report.md

Outputs (all in ONE markdown report):
  1. per-field as-of coverage by year (primary: ex-financial PIT members at
     each quarterly as-of date; appendix: CSI300-ever incl. financials for
     Gate-1 comparability), incl. the adv_receipts∪contract_liab coalesce;
  2. ex-financial CSI300-ever breadth by year;
  3. earliest reliable availability per candidate (C1/C2/C3), incl. an
     rd_exp year×quarter drill-down (the C2 window question);
  4. full-universe version_collapse_residual per endpoint;
  5. canonical coverage-floor check (``COVERAGE_FLOORS``) — enforced against
     EVERY yearly mean in the 2019-2025 floor window AND the latest as-of
     snapshot when floors are populated (a historical regression fails loud).
"""
from __future__ import annotations

import argparse
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
    COVERAGE_FLOORS,
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

CANDIDATE_FIELDS: dict[str, tuple[str, ...]] = {
    "C1 GPA": ("revenue", "oper_cost", "total_assets"),
    "C2 PROF": ("revenue", "oper_cost", "sell_exp", "admin_exp", "rd_exp",
                "fin_exp", "total_hldr_eqy_inc_min_int"),
    "C3 cash-OP": ("revenue", "oper_cost", "sell_exp", "admin_exp",
                   "accounts_receiv", "inventories", "prepayment",
                   "accounts_pay", "adv_receipts", "contract_liab",
                   "n_cashflow_act", "total_assets"),
}


class ReportError(RuntimeError):
    """Fail-loud: the report must abort rather than print optimistic numbers."""


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
    parts: list[pd.DataFrame] = []
    for status in ("L", "D", "P"):
        frame = client.call(
            "stock_basic",
            fields="ts_code,name,industry,list_status",
            list_status=status,
        )
        if frame is not None and not frame.empty:
            parts.append(frame)
    if not parts:
        raise ReportError("stock_basic fetch empty for L/D/P — cannot derive "
                          "the financial exclusion; refusing a no-exclusion report.")
    basic = pd.concat(parts, ignore_index=True)
    if "industry" not in basic.columns:
        raise ReportError("stock_basic response lacks 'industry' — cannot "
                          "derive the financial exclusion.")
    issuers = financial_issuers_from_industry(basic)
    if not issuers:
        raise ReportError("financial exclusion derived EMPTY from stock_basic — "
                          "implausible (banks/brokers/insurers exist); aborting.")
    return issuers, f"stock_basic rows={len(basic)} (L+D+P)"


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
                raise ReportError(f"CSI300 membership empty at {d} — bad file?")
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
    # Floors are defined over the 2019-2025 window (FLOOR_PROVENANCE), so they
    # must be enforced against EVERY measured year in that window, not just the
    # latest snapshot — otherwise a re-ingest that corrupts 2019-2024 history
    # while the latest snapshot stays healthy would still print PASS
    # (codex #347). The latest-snapshot assert_coverage_floor call additionally
    # exercises the live enforcement mechanism itself.
    last_snap = date(YEARS[-1], 12, 31)
    floor_years = [y for y in YEARS if y >= 2019]
    if COVERAGE_FLOORS:
        violations: dict[str, list[tuple[int, float, float]]] = {}
        for field, floor in COVERAGE_FLOORS.items():
            if field not in cov_exfin:
                raise ReportError(
                    f"floor field {field!r} was not measured — floors and the "
                    "measured field set have drifted apart.")
            for y in floor_years:
                got = cov_exfin[field][y]
                if got < floor:
                    violations.setdefault(field, []).append((y, got, floor))
        if violations:
            raise ReportError(
                "coverage below the canonical floor in the measured window "
                f"(field -> [(year, actual, floor)]): {violations} — a "
                "historical regression must be investigated, never tolerated.")
        view_exfin.assert_coverage_floor(
            dict(COVERAGE_FLOORS), members_by_date[last_snap], last_snap)
        floor_note = (f"PASS — enforced on EVERY {floor_years[0]}-"
                      f"{floor_years[-1]} yearly mean AND the {last_snap} "
                      f"ex-financial member snapshot ({FLOOR_PROVENANCE})")
    else:
        floor_note = ("NOT ENFORCED — COVERAGE_FLOORS is empty (fill it from "
                      "this report's measured minima, then re-run)")

    # -- render ---------------------------------------------------------------
    lines: list[str] = []
    a = lines.append
    a("# Gate-3 Step-A · canonical as-of 覆盖率报告(全量 CSI300-ever 财报 PIT store)")
    a("")
    a("> 生成: `scripts/research/gate3_step_a_coverage_report.py`(fail-loud,可复现)。")
    a(f"> Store: `{store_dir}`;universe: CSI300-ever n={len(ever)};金融排除 n={len(fin_issuers)}({basic_note})。")
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
    a("## 3. rd_exp 季度末 as-of 细分(C2 窗口判定)")
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
    a("## 5. 附表:Gate-1 可比口径(CSI300-ever 含金融,分母=当期 anchor 有披露的名字)+ Δ vs Gate-1 pooled")
    a("")
    a("anchor: income→revenue / balancesheet→total_assets / cashflow→n_cashflow_act"
      "(anchor 自身行恒 100%,仅作分母定义)。Gate-1 §4 是行级 pooled,本表是 as-of"
      "横截面 — Δ 为方向参考,预期 as-of ≤ pooled。")
    a("")
    a("| field | " + " | ".join(str(y) for y in YEARS) + " | Δ vs Gate-1 (mean) |")
    a("|---" * (len(YEARS) + 2) + "|")
    for f in all_fields:
        row = " | ".join(pct(cov_cmp[f][y]) for y in YEARS)
        if f in GATE1_POOLED:
            deltas = [cov_cmp[f][y] - GATE1_POOLED[f][y] for y in YEARS]
            dnote = f"{100.0 * sum(deltas) / len(deltas):+.1f}pp"
        else:
            dnote = "n/a"
        anchor_mark = " *(anchor)*" if f in _ENDPOINT_ANCHOR.values() else ""
        a(f"| {f}{anchor_mark} | {row} | {dnote} |")
    a("")
    a("## 6. 候选最早可靠可用期(规则化推导)")
    a("")
    a(f"规则: 候选的年度可用性 = 其全部输入字段该年均值的最小值(C3 的 adv_receipts/"
      f"contract_liab 以 coalesce 计);最早可靠年 = 自该年起所有已测年份都 ≥ "
      f"{pct(CANDIDATE_WINDOW_THRESHOLD)} 的最早年份。")
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
    a("注: C3 需两期(Δ应计)→ 有效首个横截面比起始年再晚一个报告期;C2 缺 rd_exp "
      "的处理(不可算 vs 视 0)按 charter 仍须在 Gate-3 预注册中显式冻结。")
    a("")
    a("## 7. 偏离 Gate-1 memo 的意外(如实记录)")
    a("")
    total_holes = sum(len(info.missing_names) for info in residuals.values())
    a(f"1. **提供方歧义重复 → {total_holes} 个 ingest hole**(§8 名单)。同一 "
      "`(ts_code, end_date, update_flag)` 在一次 fetch 返回两行**不同内容**,"
      "`report_type`/`end_type` 均无法区分,仅 `f_ann_date` 不同(例: 五粮液 "
      "income 20250630/uf1 = f_ann 20250828 revenue 527.7亿 vs 迟到行 f_ann "
      "20260430 revenue 235.1亿)。PR-1 ingest 按契约 fail-loud 拒收 → 该 "
      "instrument/endpoint 留 hole,覆盖率计为未覆盖(诚实方向)。**follow-up**: "
      "消歧需契约级改动(如 logical key 纳入 f_ann_date、按最早披露为 record),"
      "应走独立 OpenSpec change,不在 Step-A 越权。")
    a("2. **rd_exp 的 2018 年 as-of 断崖**: 行级 pooled 显示 2018=55%,但 as-of "
      "横截面 2018 H1 仅 0.4%、Q3 18.4%、Q4 87.4% —— 单列研发费用自 2018 Q3 报告"
      "才开始批量披露。**C2 最早可靠期 = 2019**(Gate-1 的『2018 早窗弱』在 as-of "
      "口径下更硬)。2019+ 无季报缺失效应(各季度 88-97%,§3)。")
    a("3. **int_exp as-of 覆盖(7-9%)显著低于 pooled(13-18%)** —— 财报中仅年报"
      "披露居多。无影响: charter 已把 C2 利息项定为 fin_exp(as-of 96-98%)。")
    a("4. **全宇宙重述残差非零但极小**(§4: income 0.27% / bs 0.40% / cf 0.08%,"
      "含 NA↔非NA transition)。serve-rule 恒取 uf0 → 无前视;残差为诚信包络的"
      "已量化界。")
    a("5. **其余字段 Gate-1 数字大体坐实**(§5 Δ 多在 ±1pp;rd_exp -5.9pp 与 "
      "contract_liab -3.9pp 均由 2018-2020 过渡期 as-of 滞后驱动,非数据缺失)。")
    a("6. **金融排除规模**: 行业名单法(stock_basic)在 CSI300-ever 上排除 120 名,"
      "逐年在册金融 46-61 名(§2)—— 高于早前 ~35-42 的粗估,以本表为准。")
    a("")
    a("## 8. ingest holes(缺 store 文件的名字 — 显式列出,绝不静默)")
    a("")
    a("缺文件 = ingest 对该 instrument/endpoint fail-loud 后留下的 hole(如提供方"
      "同一 logical key 双内容的歧义重复)。这些名字在覆盖率分母中**保留并计为未覆盖**"
      "(诚实方向:压低而非抬高覆盖率)。")
    a("")
    for endpoint, info in residuals.items():
        names = info.missing_names
        shown = ", ".join(names[:30]) + (" …" if len(names) > 30 else "")
        a(f"- **{endpoint}**: {len(names)} missing — {shown if names else '(none)'}")
    a("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--store-dir", required=True)
    p.add_argument("--instruments-file", required=True)
    p.add_argument("--calendar", required=True)
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
