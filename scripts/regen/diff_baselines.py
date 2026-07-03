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
  R4. Aggregate metrics are gated too (codex P1 on #321 round 2): a non-IC
      aggregate key changing at all, or an IC-derived aggregate key changing
      without at least one ATTRIBUTED per-fold IC change ON THE SAME HORIZON
      to derive from (codex P1 round 6: mean_ic_5d needs an attributed
      per-fold ic_5d change — ic_1d evidence does not transfer across
      horizons), aborts — the aggregate block is part of the published
      baseline and must never be re-signed without a per-fold explanation.
      Added/removed aggregate keys are a schema change and abort likewise; an
      "ic"-named key that maps to no known per-fold horizon cannot be
      attributed and aborts.

A fold "has a hit" when a registry instrument BOTH (a) appears in that
fold's FROZEN PREDICTIONS (actual (datetime, instrument) rows — the registry
is full-market, so date overlap alone would let an unrelated market delisting
launder arbitrary IC drift past R3; codex P1 on #321) AND (b) has its
delist_date within [test_start - LOOKBACK_DAYS, test_end + LOOKBACK_DAYS]: the IC forward-return
window of the last prediction rows reaches PAST test_end (T+1 -> T+1+period),
so a delisting a few trading days after the fold ends can legitimately move
that fold's IC (codex P2 on #321 round 2); symmetric slack on both sides. LOOKBACK_DAYS covers max forward period (5) + entry offset +
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


def _load(path: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
    return data


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
        # forward-return reach extends PAST test_end (codex P2 #321 r2)
        hi = pd.Timestamp(end_s) + pd.Timedelta(days=LOOKBACK_DAYS)
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

    old_doc_json = _load(args.old)
    new_doc_json = _load(args.new)
    old = {int(f["fold_index"]): f for f in old_doc_json["per_fold"]}
    new = {int(f["fold_index"]): f for f in new_doc_json["per_fold"]}
    if set(old) != set(new):
        print(f"FAIL: fold sets differ (old={sorted(old)}, new={sorted(new)})")
        return 1
    # Baseline METADATA is guarded too (codex P2 on #321 round 3): identical
    # fold indexes with a shifted/truncated test_period would otherwise slip
    # through — and hits computed from the OLD periods would attribute IC
    # drift against the wrong delisting window. Any window change is a replay
    # semantics change, not a re-signable drift.
    for i in sorted(old):
        if old[i]["test_period"] != new[i]["test_period"]:
            print(
                f"FAIL: fold {i} test_period changed "
                f"({old[i]['test_period']!r} -> {new[i]['test_period']!r}) — "
                "the replay window moved; this gate only re-signs metric "
                "values on IDENTICAL fold windows."
            )
            return 1
    hits = _hits_by_fold(list(old.values()), args.registry, args.frozen)

    lines = [
        "# REGEN-2 baseline re-sign diff",
        "",
        "Acceptance rules R1-R4 enforced by scripts/regen/diff_baselines.py "
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
    # Per-HORIZON attribution (codex P1 #321 r6): each IC aggregate key is
    # gated against the SPECIFIC per-fold horizon it derives from — a single
    # cross-horizon boolean would let an attributed ic_1d change license
    # mean_ic_5d/std_ic_5d drift when no per-fold ic_5d value moved.
    attributed_by_horizon = {
        m: any(
            repr(old[i][m]) != repr(new[i][m]) and hits.get(i)
            for i in sorted(old)
        )
        for m in _IC_METRICS
    }
    # R4 (codex P1 #321 r2): the aggregate block is part of the published
    # baseline — gate it too. IC-derived keys may move ONLY when the per-fold
    # horizon they derive from has an attributed change; everything else
    # (backtest-derived keys, schema changes, unmappable IC keys) aborts.
    agg_old = dict(old_doc_json.get("aggregate_metrics") or {})
    agg_new = dict(new_doc_json.get("aggregate_metrics") or {})
    for key in sorted(set(agg_old) | set(agg_new)):
        if key in agg_old and key not in agg_new or key in agg_new and key not in agg_old:
            n_changed += 1
            lines.append(f"| aggregate | - | {key} | {agg_old.get(key)!r} | {agg_new.get(key)!r} | - | SCHEMA |")
            failures.append(
                f"R4 VIOLATION: aggregate key {key!r} added/removed — schema "
                "change, not a re-signable drift."
            )
            continue
        vo, vn = agg_old[key], agg_new[key]
        if repr(vo) == repr(vn):
            continue
        n_changed += 1
        ic_derived = "ic" in key.lower()
        horizons = [m for m in _IC_METRICS if m in key.lower()]
        attributed_ok = bool(horizons) and all(
            attributed_by_horizon[h] for h in horizons
        )
        attributed = (
            "derived from attributed per-fold " + "+".join(horizons) + " changes"
        ) if (ic_derived and attributed_ok) else "NONE"
        lines.append(f"| aggregate | - | {key} | {vo!r} | {vn!r} | - | {attributed} |")
        if not ic_derived:
            failures.append(
                f"R4 VIOLATION: non-IC aggregate key {key!r} moved "
                f"({vo!r} -> {vn!r}) — this channel only re-signs IC inputs."
            )
        elif not horizons:
            failures.append(
                f"R4 VIOLATION: aggregate key {key!r} looks IC-derived but maps "
                f"to no known per-fold IC horizon {list(_IC_METRICS)} — cannot "
                "attribute, refusing to re-sign."
            )
        elif not attributed_ok:
            unbacked = ", ".join(h for h in horizons if not attributed_by_horizon[h])
            failures.append(
                f"R4 VIOLATION: IC-derived aggregate key {key!r} moved "
                f"({vo!r} -> {vn!r}) but its per-fold horizon(s) [{unbacked}] "
                "have NO attributed change to derive from — cross-horizon "
                "evidence does not transfer."
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
    print("\nOK: acceptance rules R1-R4 hold.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
