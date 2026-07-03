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

FOLD-0 EXCEPTION (codex P2 on #321 round 7): fold-0's frozen scores are
degenerate and its topk selection is PER-RUNNER bimodal even on the canonical
pin — the replay regression test accepts the committed state (A) or the ONE
documented alternate (B) for fold-0's topk-dependent backtest metrics and the
seven aggregate keys derived from them. This gate mirrors that exception
EXACTLY (same constants, same 1e-6 tolerance, same group-wise all-A-or-all-B
check, same state-consistency between fold-0 and its derived aggregates), so
the sanctioned re-sign workflow cannot fail nondeterministically on a runner
that lands on B. A THIRD state, a per-metric A/B mix, or a fold-0/aggregate
state mismatch still aborts. Everything else stays strict.

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
import math
import pickle
from pathlib import Path
from typing import Any

_METRICS = ("ic_1d", "ic_5d", "annualized_return", "max_drawdown", "information_ratio")
_BACKTEST_METRICS = ("annualized_return", "max_drawdown", "information_ratio")
_IC_METRICS = ("ic_1d", "ic_5d")
LOOKBACK_DAYS = 14  # calendar days: max_period(5)+entry(1) trading days + holiday slack

# Fold-0 A/B bimodality — MIRRORED from tests/regression/
# test_walk_forward_replay_baseline_regen2.py (codex P2 #321 r7; keep the two
# files in sync — the regression test is the source of truth). fold-0's
# degenerate scores make its topk selection per-runner bimodal on the
# canonical pin; the re-sign runner may land on the documented alternate (B)
# with NO semantic change, so this gate must accept exactly {A, B} — and
# nothing else — or the only sanctioned re-sign channel fails
# nondeterministically before producing artifacts.
_FOLD0_DEGENERATE_INDEX = 0
_FOLD0_TOPK_DEPENDENT = ("annualized_return", "max_drawdown", "information_ratio")
_KNOWN_FOLD0_BACKTEST_ALT = {
    "annualized_return": -0.004711347265649301,
    "max_drawdown": -0.02726356962697682,
    "information_ratio": -0.0712889987158074,
}
_KNOWN_AGGREGATE_ALT = {
    "mean_annualized_return": 0.028012145880259152,
    "mean_annualized_return_ci_low": -0.04948816928667496,
    "mean_annualized_return_ci_high": 0.09865727070463469,
    "mean_information_ratio": 0.19787663958380639,
    "std_information_ratio": 1.9755829786899575,
    "mean_information_ratio_ci_low": -0.6482102836796528,
    "mean_information_ratio_ci_high": 0.9631212903466849,
}
_FOLD0_ABS_TOL = 1e-6  # same tolerance the replay regression test uses


def _tol_close(a: Any, b: Any) -> bool:
    return math.isclose(float(a), float(b), rel_tol=0.0, abs_tol=_FOLD0_ABS_TOL)


def _fold0_state(
    old: dict[int, dict[str, Any]], new: dict[int, dict[str, Any]],
) -> str | None:
    """Which known selection fold-0 landed on: "A" (committed), "B" (documented
    alternate), or None (a THIRD state / per-metric mix — a real regression).
    Group-wise on purpose: all three topk metrics come from ONE held portfolio,
    so a mix of A and B values cannot arise in a genuine run."""
    fo = old[_FOLD0_DEGENERATE_INDEX]
    fn = new[_FOLD0_DEGENERATE_INDEX]
    if all(_tol_close(fn[m], fo[m]) for m in _FOLD0_TOPK_DEPENDENT):
        return "A"
    if all(
        _tol_close(fn[m], _KNOWN_FOLD0_BACKTEST_ALT[m])
        for m in _FOLD0_TOPK_DEPENDENT
    ):
        return "B"
    return None


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
    # Fold-0 A/B exception (codex P2 #321 r7): its topk-dependent backtest
    # metrics are checked as a GROUP against the two documented states instead
    # of the strict per-metric R2 — everything else on fold-0 (its ICs) and
    # every other fold stays strict.
    fold0_state = (
        _fold0_state(old, new) if _FOLD0_DEGENERATE_INDEX in old else None
    )
    if _FOLD0_DEGENERATE_INDEX in old and fold0_state is None:
        failures.append(
            "R2 VIOLATION: fold 0 topk-dependent backtest metrics are neither "
            "the committed selection (A) nor the documented tie-break alternate "
            "(B) — a THIRD state (or a per-metric A/B mix) is a real "
            "regression, not the known fold-0 bimodality."
        )
    for i in sorted(old):
        fo, fn = old[i], new[i]
        fold_hits = hits.get(i, [])
        for m in _METRICS:
            vo, vn = fo[m], fn[m]
            identical = repr(vo) == repr(vn)
            if identical:
                continue
            if i == _FOLD0_DEGENERATE_INDEX and m in _FOLD0_TOPK_DEPENDENT:
                # group-checked above; the table still records the movement
                n_changed += 1
                lines.append(
                    f"| {i} | {fo['test_period']} | {m} | {vo!r} | {vn!r} "
                    f"| {vn - vo:+.3e} | fold-0 selection "
                    f"{fold0_state or 'UNKNOWN'} (documented bimodality) |"
                )
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
        changed = repr(vo) != repr(vn)
        if key in _KNOWN_AGGREGATE_ALT and _FOLD0_DEGENERATE_INDEX in old:
            # fold-0-derived aggregate keys are bimodal WITH fold-0 (codex P2
            # #321 r7): each must match the SAME selection fold-0 landed on —
            # A -> committed value, B -> documented alternate — checked even
            # when the value did NOT move (an unchanged A value under a
            # fold-0 B flip is an internally inconsistent baseline). A third
            # value or a fold-0/aggregate state mismatch aborts.
            target = (
                vo if fold0_state == "A"
                else _KNOWN_AGGREGATE_ALT[key] if fold0_state == "B"
                else None
            )
            ok = target is not None and _tol_close(vn, target)
            if changed:
                n_changed += 1
            if changed or not ok:
                attributed = (
                    f"fold-0 selection {fold0_state} (documented bimodality)"
                    if ok else "NONE"
                )
                lines.append(
                    f"| aggregate | - | {key} | {vo!r} | {vn!r} | - | "
                    f"{attributed} |"
                )
            if not ok:
                failures.append(
                    f"R4 VIOLATION: fold-0-derived aggregate key {key!r} "
                    f"(old={vo!r}, new={vn!r}) does not match fold-0's "
                    f"selection state ({fold0_state or 'UNDETERMINED'}) — "
                    "bimodality must be state-consistent between fold-0 and "
                    "its derived aggregates."
                )
            continue
        if not changed:
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
