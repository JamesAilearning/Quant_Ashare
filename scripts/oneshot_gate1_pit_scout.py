"""ONE-SHOT recon probe — Gate-1 财报 PIT 可行性勘察(阶段八 H8-Q1).

⚠ NON-PRODUCTION. 一次性只读探针,对标 quality_profitability_gate1_pit_scout_brief.md。
   不进生产路径、不落库、不建特征。只查 tushare 财报端点的 schema / PIT 语义 / 覆盖率,
   把结果打印出来供人工写 preflight memo。可安全删除。

跑法(tushare 恢复后):
    D:/Python/Python11/python.exe scripts/oneshot_gate1_pit_scout.py --sample 60
    # --sample N: 覆盖率抽样的 CSI300 成分数(默认 60);--all 跑全 CSI300-ever。

覆盖 brief 必查项:
  2.1 字段→端点映射(income/balancesheet/cashflow/fina_indicator 列名核对)
  2.2 公告日语义(end_date / ann_date / f_ann_date 是否齐)
  2.3 修订/版本(update_flag;同一 end_date 多行=原始 vs 修订)
  2.4 覆盖率(CSI300 PIT 成分抽样,按年×字段 非空率)
  2.5 人工案例(几个 firm-quarter 展示 ann 晚于报告期末、f_ann_date=可见日)
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd  # noqa: E402

from src.data.tushare.client import TushareClient, TushareClientError  # noqa: E402

# charter C1/C2/C3 所需字段(逐一核对存在性)
FIELDS = {
    "income": [
        "revenue", "total_revenue", "oper_cost", "sell_exp", "admin_exp",
        "rd_exp", "int_exp", "fin_exp",
    ],
    "balancesheet": [
        "total_assets", "total_hldr_eqy_inc_min_int",
        "total_hldr_eqy_exc_min_int", "accounts_receiv", "inventories",
        "prepayment", "accounts_pay", "adv_receipts",
    ],
    "cashflow": ["n_cashflow_act"],
    "fina_indicator": ["roe", "grossprofit_margin", "q_op"],  # cross-check only
}
PIT_COLS = ["end_date", "ann_date", "f_ann_date", "update_flag", "report_type", "end_type"]
_SLEEP_S = 0.25  # rate-limit courtesy


def _call(client: TushareClient, endpoint: str, **kw):
    try:
        df = client.call(endpoint, **kw)
        time.sleep(_SLEEP_S)
        return df
    except TushareClientError as exc:
        print(f"  [{endpoint}] CALL FAILED: {str(exc)[:120]}")
        return None


def _csi300_members(sample: int | None) -> list[str]:
    """CSI300 PIT 成分 ts_code(从已在盘的 instruments/csi300.txt 反推 tushare 代码)."""
    path = Path("D:/qlib_data/my_cn_data_pit/instruments/csi300.txt")
    syms: list[str] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8") as fh:
        for ln in fh:
            p = ln.split("\t")
            if not p:
                continue
            m = re.search(r"(\d{6})", p[0])
            if not m or m.group(1) in seen:
                continue
            seen.add(m.group(1))
            code = m.group(1)
            # qlib SHxxxxxx/SZxxxxxx -> tushare xxxxxx.SH/.SZ
            ex = "SH" if p[0].upper().startswith("SH") else "SZ"
            syms.append(f"{code}.{ex}")
    return syms if sample is None else syms[:sample]


def probe_schema(client: TushareClient) -> None:
    print("\n" + "=" * 70)
    print("2.1/2.2/2.3 — SCHEMA + PIT + REVISION (representative firms)")
    print("=" * 70)
    for ts in ["600519.SH", "000001.SZ"]:
        print(f"\n--- {ts} ---")
        for endpoint, want in FIELDS.items():
            df = _call(client, endpoint, ts_code=ts, start_date="20220101",
                       end_date="20231231")
            if df is None or not len(df):
                print(f"  [{endpoint}] EMPTY/UNAVAILABLE")
                continue
            cols = set(df.columns)
            have = [f for f in want if f in cols]
            miss = [f for f in want if f not in cols]
            pit = [c for c in PIT_COLS if c in cols]
            print(f"  [{endpoint}] rows={len(df)}")
            print(f"     charter fields PRESENT : {have}")
            print(f"     charter fields MISSING : {miss}  <-- 缺=候选 not-feasible")
            print(f"     PIT cols               : {pit}")
            if {"end_date", "ann_date"} <= cols:
                # 2.3 revision: 同一 end_date 多行?
                dup = df.groupby("end_date").size()
                multi = dup[dup > 1]
                uf = df["update_flag"].value_counts().to_dict() if "update_flag" in cols else "n/a"
                print(f"     end_dates w/ >1 row (修订) : {len(multi)}  update_flag={uf}")


def probe_coverage(client: TushareClient, members: list[str]) -> None:
    print("\n" + "=" * 70)
    print(f"2.4 — COVERAGE on {len(members)} CSI300 members (per-year non-null %)")
    print("=" * 70)
    # accumulate per (endpoint, field, year) -> [non_null, total]
    acc: dict[tuple[str, str, str], list[int]] = {}
    for i, ts in enumerate(members):
        for endpoint in ("income", "balancesheet"):
            df = _call(client, endpoint, ts_code=ts, start_date="20180101",
                       end_date="20251231")
            if df is None or not len(df) or "end_date" not in df.columns:
                continue
            df = df.copy()
            df["year"] = df["end_date"].astype(str).str[:4]
            for f in FIELDS[endpoint]:
                if f not in df.columns:
                    continue
                for yr, g in df.groupby("year"):
                    k = (endpoint, f, yr)
                    a = acc.setdefault(k, [0, 0])
                    a[0] += int(g[f].notna().sum())
                    a[1] += int(len(g))
        if (i + 1) % 20 == 0:
            print(f"  ...{i + 1}/{len(members)} members pulled")
    # print a compact year×field coverage table
    fields = sorted({(e, f) for (e, f, _) in acc})
    years = sorted({y for (_, _, y) in acc})
    print(f"\n  {'endpoint.field':38} " + " ".join(f"{y:>6}" for y in years))
    for (e, f) in fields:
        row = []
        for y in years:
            nn, tot = acc.get((e, f, y), [0, 0])
            row.append(f"{(100*nn/tot):5.0f}%" if tot else "   - ")
        print(f"  {e + '.' + f:38} " + " ".join(f"{c:>6}" for c in row))


def probe_cases(client: TushareClient) -> None:
    print("\n" + "=" * 70)
    print("2.5 — MANUAL PIT CASES (ann/f_ann vs report period)")
    print("=" * 70)
    for ts in ["600519.SH", "000651.SZ", "600036.SH"]:
        df = _call(client, "income", ts_code=ts, start_date="20220101",
                   end_date="20231231")
        if df is None or not len(df):
            print(f"  {ts}: unavailable")
            continue
        keep = [c for c in ["end_date", "ann_date", "f_ann_date", "update_flag", "revenue"]
                if c in df.columns]
        print(f"\n  {ts}:")
        print(df[keep].sort_values("end_date").to_string(index=False))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=60,
                    help="覆盖率抽样成分数(默认 60)")
    ap.add_argument("--all", action="store_true", help="覆盖率跑全 CSI300-ever")
    args = ap.parse_args()
    try:
        client = TushareClient.from_environment()
    except TushareClientError as exc:
        print(f"tushare client unavailable: {exc}")
        return 1
    probe_schema(client)
    probe_cases(client)
    members = _csi300_members(None if args.all else args.sample)
    probe_coverage(client, members)
    print("\n[done] 据此填 quality_profitability_gate1_pit_preflight.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
