"""Export the financial-sector exclusion list for operator sign-off.

The fundamental campaign's ``DataConfig.financial_exclusions`` is a
PERSISTED list (the universe mask and the view must apply the same cut
at mining and at promotion), and reference data enters a preset only
through the repository's curation discipline: this script derives the
list, the operator eyeballs and signs it, and only then does it get
written into a config. The script never writes into ``config/``.

Derivation is the signed rule from ``src.research.financial_pit_view``:
``financial_issuers_from_industry`` over the stock_basic snapshots
(active + delisted), i.e. the stable ``FINANCIAL_INDUSTRIES`` industry
list. Coverage caveat reported, not papered over: the delisted snapshot
carries ``industry`` for only a handful of names, so delisted financial
issuers largely CANNOT be identified by this rule — the count is printed
so the sign-off sees it.

Run::

    python -m scripts.research.export_financial_exclusions \
        --snapshot-dir D:/qlib_data/tushare_raw --out <tmp>/exclusions.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from src.data.pit._common import to_qlib_ticker
from src.research.financial_pit_view import (
    FINANCIAL_INDUSTRIES,
    financial_issuers_from_industry,
)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--snapshot-dir", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args(argv)

    frames = []
    for name in ("active_stocks.parquet", "delisted_stocks.parquet"):
        path = args.snapshot_dir / name
        if not path.is_file():
            print(f"missing snapshot: {path}", file=sys.stderr)
            return 1
        frames.append(pd.read_parquet(path))
    basic = pd.concat(frames, ignore_index=True)
    basic = basic.drop_duplicates(subset="ts_code", keep="first")

    excluded = sorted(financial_issuers_from_industry(basic))
    with_industry = basic["industry"].notna()
    delisted = frames[1]
    payload = {
        "purpose": "financial-exclusions-for-sign-off",
        "rule": "financial_issuers_from_industry (stable industry list)",
        "financial_industries": sorted(FINANCIAL_INDUSTRIES),
        "snapshot_dir": str(args.snapshot_dir),
        "snapshot_dates": sorted(
            basic["snapshot_date"].astype(str).unique().tolist()),
        "n_snapshot_rows": int(len(basic)),
        "n_rows_with_industry": int(with_industry.sum()),
        "n_delisted_rows": int(len(delisted)),
        "n_delisted_with_industry": int(delisted["industry"].notna().sum()),
        "n_excluded": len(excluded),
        "industry_breakdown": {
            ind: int(
                (basic.loc[basic["ts_code"].isin(excluded), "industry"]
                 == ind).sum())
            for ind in sorted(FINANCIAL_INDUSTRIES)
        },
        "ts_codes": excluded,
        "qlib_tickers": [to_qlib_ticker(t) for t in excluded],
    }
    if args.out.exists():
        print(f"{args.out} already exists — refusing to overwrite a "
              "list that may already be under review.", file=sys.stderr)
        return 1
    args.out.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"exported {len(excluded)} exclusions -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
