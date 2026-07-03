"""REGEN-2 baseline re-sign diff + acceptance gate (audit P2, operator decision 2).

Compares an old vs a newly generated baseline JSON and ENFORCES the
operator-approved acceptance rules BEFORE a re-sign can proceed — committed
up front, not negotiated after seeing the numbers:

  R1. Folds WITHOUT a delisted-instrument hit must be IDENTICAL (bit-level via
      the committed float repr) on every pinned metric — not merely "close".
  R2. Backtest metrics (annualized_return / max_drawdown / information_ratio)
      must be identical on EVERY fold — this channel only re-signs IC-input
      changes; any backtest drift aborts.
  R3. An IC change on a fold with NO attributable delisted instrument aborts —
      investigate, never explain past it.

A fold "has a hit" when a registry instrument's delist_date falls within
[test_start - LOOKBACK_DAYS, test_end]: the IC forward-return window of the
last pre-delist prediction rows reaches past the delist date, so those folds
may legitimately move. LOOKBACK_DAYS covers max forward period (5) + entry
offset + calendar slack.

Exit 0 = all rules hold (diff table written); exit 1 = a rule failed (the
workflow job goes red and no re-sign bundle should be trusted).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

_METRICS = ("ic_1d", "ic_5d", "annualized_return", "max_drawdown", "information_ratio")
_BACKTEST_METRICS = ("annualized_return", "max_drawdown", "information_ratio")
_IC_METRICS = ("ic_1d", "ic_5d")
LOOKBACK_DAYS = 14  # calendar days: max_period(5)+entry(1) trading days + holiday slack


def _load(path: str) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return list(data["per_fold"])


def _hits_by_fold(
    folds: list[dict[str, Any]], registry_path: str,
) -> dict[int, list[str]]:
    import pandas as pd

    reg = pd.read_parquet(registry_path)
    reg["dd"] = pd.to_datetime(reg["delist_date"], format="mixed", errors="coerce")
    reg = reg[reg["dd"].notna()]
    out: dict[int, list[str]] = {}
    for f in folds:
        start_s, end_s = f["test_period"].split("..")
        lo = pd.Timestamp(start_s) - pd.Timedelta(days=LOOKBACK_DAYS)
        hi = pd.Timestamp(end_s)
        hit = reg[(reg["dd"] >= lo) & (reg["dd"] <= hi)]
        out[int(f["fold_index"])] = sorted(hit["ticker"].tolist())
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--old", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--registry", default=str(
        Path(__file__).resolve().parents[2]
        / "tests/regression/fixtures/regen2/delisted_registry_frozen_20260618.parquet"
    ))
    ap.add_argument("--out-md", required=True)
    args = ap.parse_args(argv)

    old = {int(f["fold_index"]): f for f in _load(args.old)}
    new = {int(f["fold_index"]): f for f in _load(args.new)}
    if set(old) != set(new):
        print(f"FAIL: fold sets differ (old={sorted(old)}, new={sorted(new)})")
        return 1
    hits = _hits_by_fold(list(old.values()), args.registry)

    lines = [
        "# REGEN-2 baseline re-sign diff",
        "",
        "Acceptance rules R1-R3 enforced by scripts/regen/diff_baselines.py "
        "(committed BEFORE the numbers were seen).",
        "",
        "| fold | test_period | metric | old | new | delta | attributed to |",
        "|---|---|---|---|---|---|---|",
    ]
    failures: list[str] = []
    n_changed = 0
    for i in sorted(old):
        fo, fn = old[i], new[i]
        fold_hits = hits.get(i, [])
        for m in _METRICS:
            vo, vn = fo[m], fn[m]
            identical = repr(vo) == repr(vn)
            if identical:
                continue
            n_changed += 1
            attributed = ", ".join(fold_hits) if fold_hits else "NONE"
            lines.append(
                f"| {i} | {fo['test_period']} | {m} | {vo!r} | {vn!r} "
                f"| {vn - vo:+.3e} | {attributed} |"
            )
            if m in _BACKTEST_METRICS:
                failures.append(
                    f"R2 VIOLATION: fold {i} backtest metric {m} moved "
                    f"({vo!r} -> {vn!r}) — this channel only re-signs IC inputs."
                )
            elif m in _IC_METRICS and not fold_hits:
                failures.append(
                    f"R3 VIOLATION: fold {i} {m} moved ({vo!r} -> {vn!r}) with NO "
                    "attributable delisted instrument in its window."
                )
    if n_changed == 0:
        lines.append("| - | - | (no changes: baselines identical) | | | | |")
    lines.append("")
    lines.append(f"Changed cells: {n_changed}; rule violations: {len(failures)}")
    for f in failures:
        lines.append(f"- **{f}**")
    Path(args.out_md).write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    if failures:
        print(f"\nFAIL: {len(failures)} acceptance-rule violation(s).")
        return 1
    print("\nOK: acceptance rules R1-R3 hold.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
