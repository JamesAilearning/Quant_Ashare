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

A fold "has a hit" when a registry instrument BOTH (a) appears in that
fold's FROZEN PREDICTIONS (actual (datetime, instrument) rows — the registry
is full-market, so date overlap alone would let an unrelated market delisting
launder arbitrary IC drift past R3; codex P1 on #321) AND (b) has its
delist_date within [test_start - LOOKBACK_DAYS, test_end]: the IC
forward-return window of the last pre-delist prediction rows reaches past the
delist date. LOOKBACK_DAYS covers max forward period (5) + entry offset +
calendar slack. The frozen-scores fixture is REQUIRED — no membership data,
no attribution, no re-sign.

Exit 0 = all rules hold (diff table written); exit 1 = a rule failed (the
workflow job goes red and no re-sign bundle should be trusted).
"""
from __future__ import annotations

import argparse
import gzip
import json
import pickle
from pathlib import Path
from typing import Any

_METRICS = ("ic_1d", "ic_5d", "annualized_return", "max_drawdown", "information_ratio")
_BACKTEST_METRICS = ("annualized_return", "max_drawdown", "information_ratio")
_IC_METRICS = ("ic_1d", "ic_5d")
LOOKBACK_DAYS = 14  # calendar days: max_period(5)+entry(1) trading days + holiday slack


def _load(path: str) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return list(data["per_fold"])


def _fold_instruments(frozen_path: str) -> dict[int, set[str]]:
    """Per-fold prediction membership from the frozen-scores fixture."""
    with gzip.open(frozen_path, "rb") as fh:
        frozen = pickle.load(fh)
    out: dict[int, set[str]] = {}
    for i, entry in frozen.items():
        insts = entry["scores"].index.get_level_values("instrument")
        out[int(i)] = {str(x).upper() for x in insts}
    return out


def _hits_by_fold(
    folds: list[dict[str, Any]], registry_path: str, frozen_path: str,
) -> dict[int, list[str]]:
    import pandas as pd

    membership = _fold_instruments(frozen_path)
    reg = pd.read_parquet(registry_path)
    reg["dd"] = pd.to_datetime(reg["delist_date"], format="mixed", errors="coerce")
    reg = reg[reg["dd"].notna()]
    out: dict[int, list[str]] = {}
    for f in folds:
        idx = int(f["fold_index"])
        if idx not in membership:
            raise SystemExit(
                f"FAIL: fold {idx} has no frozen predictions — cannot derive "
                "attribution membership; refusing to gate on date overlap alone."
            )
        start_s, end_s = f["test_period"].split("..")
        lo = pd.Timestamp(start_s) - pd.Timedelta(days=LOOKBACK_DAYS)
        hi = pd.Timestamp(end_s)
        in_window = reg[(reg["dd"] >= lo) & (reg["dd"] <= hi)]
        # codex P1 on #321: attribution requires PREDICTION MEMBERSHIP, not
        # just date overlap — the registry is full-market, and an unrelated
        # delisting in the same quarter must not launder IC drift past R3.
        out[idx] = sorted(
            t for t in in_window["ticker"].astype(str).str.upper()
            if t in membership[idx]
        )
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--old", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--registry", default=str(
        Path(__file__).resolve().parents[2]
        / "tests/regression/fixtures/regen2/delisted_registry_frozen_20260618.parquet"
    ))
    ap.add_argument("--frozen", default=str(
        Path(__file__).resolve().parents[2]
        / "tests/regression/fixtures/regen2/frozen_fold_scores.pkl.gz"
    ))
    ap.add_argument("--out-md", required=True)
    args = ap.parse_args(argv)

    old = {int(f["fold_index"]): f for f in _load(args.old)}
    new = {int(f["fold_index"]): f for f in _load(args.new)}
    if set(old) != set(new):
        print(f"FAIL: fold sets differ (old={sorted(old)}, new={sorted(new)})")
        return 1
    hits = _hits_by_fold(list(old.values()), args.registry, args.frozen)

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
