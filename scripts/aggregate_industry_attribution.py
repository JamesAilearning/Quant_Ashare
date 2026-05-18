"""Aggregate per-fold Brinson sector attribution into a consistency report.

For a given walk-forward run directory, scans all fold_NN_report.json files,
extracts sector_attribution per fold, and computes per-sector aggregates:

  - n_folds_appeared       — how many folds contained this sector
  - mean_portfolio_weight  — average portfolio weight on appearing folds
  - mean_benchmark_weight  — average benchmark weight on appearing folds
  - mean_alloc_effect      — mean allocation_effect across appearing folds
  - mean_select_effect     — mean selection_effect across appearing folds
  - mean_total_effect      — mean total_effect across appearing folds
  - sign_consistency_alloc / _select / _total
                           — count of folds where the per-fold sign matches
                             the per-sector mean sign, divided by n_folds_appeared

Output: a markdown table sorted by abs(mean_total_effect) desc.

Usage:
    python scripts/aggregate_industry_attribution.py <run_dir> [--limit N]
    python scripts/aggregate_industry_attribution.py <run_dir> [--limit N] --compare <other_dir>
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


def _load_fold_reports(run_dir: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Return list of attribution blocks and list of skipped fold IDs."""
    results = []
    skipped = []
    for i in range(32):  # generous upper bound
        path = run_dir / f"fold_{i:02d}_report.json"
        if not path.is_file():
            break  # no more folds
        try:
            with open(path, encoding="utf-8") as f:
                report = json.load(f)
        except (json.JSONDecodeError, OSError):
            skipped.append(f"fold_{i:02d}: file read error")
            continue
        att = report.get("attribution", {})
        if att.get("status") != "ok":
            reason = att.get("skipped_reason", "unknown")
            skipped.append(f"fold_{i:02d}: {reason}")
            continue
        results.append(att)
    return results, skipped


def _aggregate(folds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compute per-sector aggregates across folds."""
    sector_data: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: {
            "pw": [], "bw": [],
            "ae": [], "se": [], "te": [],
        }
    )
    for fold in folds:
        for entry in fold.get("sector_attribution", []):
            name = entry["sector"]
            d = sector_data[name]
            d["pw"].append(float(entry.get("portfolio_weight", 0)))
            d["bw"].append(float(entry.get("benchmark_weight", 0)))
            d["ae"].append(float(entry.get("allocation_effect", 0)))
            d["se"].append(float(entry.get("selection_effect", 0)))
            d["te"].append(float(entry.get("total_effect", 0)))

    rows = []
    for name, d in sector_data.items():
        n = len(d["te"])

        def _mean(vals):
            return sum(vals) / len(vals) if vals else 0.0

        def _sign_consistency(vals, mean_val):
            if not vals or mean_val == 0:
                return 1.0 if not vals else float("nan")
            same = sum(1 for v in vals if (v > 0) == (mean_val > 0) or v == 0)
            return same / len(vals)

        mae = _mean(d["ae"])
        mse = _mean(d["se"])
        mte = _mean(d["te"])
        rows.append({
            "sector": name,
            "n_folds": n,
            "mean_portfolio_weight": _mean(d["pw"]),
            "mean_benchmark_weight": _mean(d["bw"]),
            "mean_alloc_effect": mae,
            "sign_consistency_alloc": _sign_consistency(d["ae"], mae),
            "mean_select_effect": mse,
            "sign_consistency_select": _sign_consistency(d["se"], mse),
            "mean_total_effect": mte,
            "sign_consistency_total": _sign_consistency(d["te"], mte),
        })

    rows.sort(key=lambda r: abs(r["mean_total_effect"]), reverse=True)
    return rows


def _fmt(v: float, width: int = 8) -> str:
    if math.isnan(v):
        return " " * width
    return f"{v:{width}.4f}"


def _table(rows: list[dict[str, Any]], limit: int) -> str:
    header = (
        "| Sector                           | N | PW     | BW     | Alloc  | sc_A | Select | sc_S | Total  | sc_T |"
    )
    sep = (
        "|----------------------------------|---|--------|--------|--------|------|--------|------|--------|------|"
    )
    lines = [header, sep]
    for r in rows[:limit]:
        sc_a = _fmt(r["sign_consistency_alloc"], 5).strip()
        sc_s = _fmt(r["sign_consistency_select"], 5).strip()
        sc_t = _fmt(r["sign_consistency_total"], 5).strip()
        lines.append(
            f"| {r['sector']:32s} "
            f"| {r['n_folds']:1d} "
            f"| {_fmt(r['mean_portfolio_weight'])} "
            f"| {_fmt(r['mean_benchmark_weight'])} "
            f"| {_fmt(r['mean_alloc_effect'])} "
            f"| {sc_a:>4s} "
            f"| {_fmt(r['mean_select_effect'])} "
            f"| {sc_s:>4s} "
            f"| {_fmt(r['mean_total_effect'])} "
            f"| {sc_t:>4s} |"
        )
    return "\n".join(lines)


def _compare_table(rows_a: list[dict[str, Any]], rows_b: list[dict[str, Any]],
                   label_a: str, label_b: str, limit: int) -> str:
    """Generate a sign-consistency delta table between two runs."""
    map_a = {r["sector"]: r for r in rows_a}
    map_b = {r["sector"]: r for r in rows_b}
    all_sectors = sorted(set(map_a) | set(map_b))

    deltas = []
    for name in all_sectors:
        ra = map_a.get(name)
        rb = map_b.get(name)
        if not ra or not rb:
            continue
        delta_sel = rb["sign_consistency_select"] - ra["sign_consistency_select"]
        delta_tot = rb["sign_consistency_total"] - ra["sign_consistency_total"]
        deltas.append({
            "sector": name,
            f"sc_sel_{label_a}": ra["sign_consistency_select"],
            f"sc_sel_{label_b}": rb["sign_consistency_select"],
            "delta_sel": delta_sel,
            f"sc_tot_{label_a}": ra["sign_consistency_total"],
            f"sc_tot_{label_b}": rb["sign_consistency_total"],
            "delta_tot": delta_tot,
        })

    deltas.sort(key=lambda d: d["delta_tot"], reverse=True)

    header = (
        f"| Sector                           | scT_{label_a} | scT_{label_b} | delta_T |"
    )
    sep = (
        "|----------------------------------|---------|---------|---------|"
    )
    lines = [header, sep]
    for d in deltas[:limit]:
        sca = _fmt(d[f"sc_tot_{label_a}"], 6).strip()
        scb = _fmt(d[f"sc_tot_{label_b}"], 6).strip()
        lines.append(
            f"| {d['sector']:32s} "
            f"| {sca:>6s} "
            f"| {scb:>6s} "
            f"| {_fmt(d['delta_tot'], 7)} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Aggregate per-fold Brinson sector attribution.")
    p.add_argument("run_dir", type=Path, help="Walk-forward run directory.")
    p.add_argument("--limit", "-n", type=int, default=30, help="Max rows in output (default: 30).")
    p.add_argument("--compare", "-c", type=Path, default=None,
                   help="Second run directory for sign-consistency delta table.")
    args = p.parse_args(argv)

    if not args.run_dir.is_dir():
        print(f"ERROR: {args.run_dir} is not a directory.", file=sys.stderr)
        sys.exit(1)

    folds, skipped = _load_fold_reports(args.run_dir)
    if not folds:
        print("ERROR: no fold reports with status=ok found.", file=sys.stderr)
        sys.exit(1)

    rows = _aggregate(folds)

    if skipped:
        print(f"## Skipped folds ({len(skipped)})")
        for s in skipped:
            print(f"- {s}")
        print()

    print(f"## Sector α Consistency — {args.run_dir.name}")
    print(f"({len(folds)} folds, {len(rows)} sectors)")
    print()
    print(_table(rows, args.limit))

    if args.compare and args.compare.is_dir():
        folds_b, skipped_b = _load_fold_reports(args.compare)
        if folds_b:
            rows_b = _aggregate(folds_b)
            print()
            print(f"## Ensemble effect: sign-consistency delta ({args.compare.name} → {args.run_dir.name})")
            print(f"(top {args.limit} by |delta_total|)")
            print()
            print(_compare_table(rows_b, rows, args.compare.name, args.run_dir.name, args.limit))


if __name__ == "__main__":
    main()
