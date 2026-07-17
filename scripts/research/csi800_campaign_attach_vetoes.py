"""Attach veto checks (2) (3) (4) (5) to a CSI800 paired campaign report.

Why this tool exists
--------------------
``csi800_campaign_pair_report.py`` (guard-1) proves the base/conservative
pairing and computes veto (1) (conservative net excess) from the official
metrics it already verifies. The remaining four checks of the
v2-csi800-expansion-guards veto table need evidence that lives OUTSIDE the
paired official metrics — sleeve attribution blocks, positions series, the
csi300 reference run, and per-fold risk-constraint provenance — so they are
attached by this second, equally deterministic step ("ignition tooling" in
the guard-1 wording). Each attached entry is a dict with ``veto_triggered``
plus the evidence values, matching ``REQUIRED_VETO_CHECKS`` /
``evaluate_promotion_eligibility`` semantics: promotion eligibility flips
only when ALL FIVE canonical checks are present and none triggered.

Operationalization (numbers are spec-pinned, see
openspec/changes/2026-07-16-csi800-antiinflation-guards/specs/):

- (2) csi500 dependence: share = sum over folds of csi500_sleeve
  ``total_effect`` / sum over folds of ``sector_effects_sum`` from the
  CONSERVATIVE arm's fold attribution (gross, diagnostic layer). Trigger:
  share >= 0.80 AND conservative net excess <= 0 (the latter read from the
  already-attached check 1).
- (3) turnover vs csi300 reference: one-way turnover recomputed from the
  persisted per-fold positions of BOTH the csi800 arm and the csi300
  reference with the SAME pure function (``sleeve_turnover`` with an empty
  sleeve map -> single bucket), so the two sides share one formula.
  daily mean = total_oneway / n_transitions pooled across folds;
  annualized = daily mean * 238 (A-share trading days; the veto is a
  RATIO so the constant cancels). Trigger: conservative arm daily mean
  > 1.5x reference daily mean. Reference folds that failed (no positions
  artifact) are skipped and disclosed via ``ref_valid_folds``.
- (4) risk constraints recorded: every fold report of BOTH arms must
  carry ``backtest.provenance.config.risk_constraints`` exactly equal to
  campaign_risk_constraints_v1 (max_per_name 0.05, max_per_board 1.0,
  cash_buffer_min 0.0, max_leverage 1.0, mode raise) and both aggregate
  reports must pin ``risk_constraints_enabled: true`` +
  ``risk_constraints_calibration: campaign_v1``. Trigger: any absence or
  mismatch (unrecorded/retuned constraints invalidate the run).
- (5) midcap concentration: time-average (across folds) of the csi500
  sleeve ``portfolio_weight`` > 0.75, or of the ``unknown`` bucket > 0.10,
  in the conservative arm's sleeve attribution.

Usage::

    python scripts/research/csi800_campaign_attach_vetoes.py \
      --pair-report docs/research/csi800_campaign_pair_report.json \
      --base-run output/walk_forward/csi800_campaign_base \
      --conservative-run output/walk_forward/csi800_campaign_conservative \
      --reference-run output/walk_forward/csi300_campaign_reference
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.research.csi800_campaign_pair_report import (  # noqa: E402
    evaluate_promotion_eligibility,
)
from src.core.attribution_sleeve_loader import sleeve_turnover  # noqa: E402

# Spec-pinned campaign_v1 effective values (veto 4, option-A revision).
CAMPAIGN_V1_EXPECTED: dict[str, Any] = {
    "max_per_name": 0.05,
    "max_per_board": 1.0,
    "cash_buffer_min": 0.0,
    "max_leverage": 1.0,
    "mode": "raise",
}
CSI500_DEPENDENCE_THRESHOLD = 0.80
TURNOVER_RATIO_THRESHOLD = 1.5
CSI500_WEIGHT_THRESHOLD = 0.75
UNKNOWN_WEIGHT_THRESHOLD = 0.10
# A-share trading-day annualization; cancels in the veto-3 ratio.
ANNUALIZATION_DAYS = 238

_FOLD_REPORT_RE = re.compile(r"^fold_(\d+)_report\.json$")


def _fold_reports(run_dir: Path) -> list[tuple[int, dict[str, Any]]]:
    out: list[tuple[int, dict[str, Any]]] = []
    for f in sorted(run_dir.iterdir()):
        m = _FOLD_REPORT_RE.match(f.name)
        if m:
            out.append((int(m.group(1)),
                        json.loads(f.read_text(encoding="utf-8"))))
    if not out:
        raise SystemExit(f"no fold reports under {run_dir}")
    return out


def _sleeve_rows(report: dict[str, Any]) -> dict[str, dict[str, float]]:
    att = report.get("attribution") or {}
    rows = att.get("sector_attribution") or []
    return {r["sector"]: r for r in rows}


def compute_csi500_dependence(cons_dir: Path,
                              cons_net_excess: float) -> dict[str, Any]:
    effect_csi500 = 0.0
    effect_total = 0.0
    folds_used = 0
    for _idx, rep in _fold_reports(cons_dir):
        att = rep.get("attribution") or {}
        if att.get("status") != "ok":
            continue
        rows = _sleeve_rows(rep)
        effect_csi500 += float(rows.get("csi500_sleeve", {})
                               .get("total_effect", 0.0))
        effect_total += float(att.get("sector_effects_sum", 0.0))
        folds_used += 1
    share = (effect_csi500 / effect_total) if effect_total > 0 else None
    dependent = share is not None and share >= CSI500_DEPENDENCE_THRESHOLD
    return {
        "csi500_effect_share_of_gross": share,
        "csi500_effect_sum": effect_csi500,
        "gross_effect_sum": effect_total,
        "folds_used": folds_used,
        "conservative_net_excess": cons_net_excess,
        "threshold_share": CSI500_DEPENDENCE_THRESHOLD,
        "note": ("share undefined (gross effect sum <= 0); dependence "
                 "leg cannot trigger" if share is None else None),
        "veto_triggered": bool(dependent and cons_net_excess <= 0.0),
    }


def _run_turnover(run_dir: Path) -> dict[str, float]:
    total = 0.0
    transitions = 0.0
    folds = 0
    for f in sorted(run_dir.glob("fold_*_positions.json")):
        positions = json.loads(f.read_text(encoding="utf-8"))
        if not positions:
            continue
        block = sleeve_turnover(positions, {})  # single honest bucket
        if not block:
            continue
        total += sum(row["total_oneway"] for row in block.values())
        # n_transitions is identical across buckets of one fold
        transitions += next(iter(block.values()))["n_transitions"]
        folds += 1
    daily = (total / transitions) if transitions else 0.0
    return {"total_oneway": total, "n_transitions": transitions,
            "daily_mean_oneway": daily,
            "annualized_oneway": daily * ANNUALIZATION_DAYS,
            "valid_folds": float(folds)}


def compute_turnover_check(cons_dir: Path, base_dir: Path,
                           ref_dir: Path) -> dict[str, Any]:
    cons = _run_turnover(cons_dir)
    base = _run_turnover(base_dir)
    ref = _run_turnover(ref_dir)
    ratio = (cons["daily_mean_oneway"] / ref["daily_mean_oneway"]
             if ref["daily_mean_oneway"] > 0 else None)
    base_ratio = (base["daily_mean_oneway"] / ref["daily_mean_oneway"]
                  if ref["daily_mean_oneway"] > 0 else None)
    return {
        "conservative": cons,
        "base": base,
        "csi300_reference": ref,
        "conservative_over_reference_ratio": ratio,
        "base_over_reference_ratio": base_ratio,
        "threshold_ratio": TURNOVER_RATIO_THRESHOLD,
        "annualization_days": ANNUALIZATION_DAYS,
        "ref_valid_folds": ref["valid_folds"],
        # fail-closed: an unusable reference cannot certify the check
        "veto_triggered": (True if ratio is None
                           else bool(ratio > TURNOVER_RATIO_THRESHOLD)),
    }


def compute_constraints_check(base_dir: Path, cons_dir: Path) -> dict[str, Any]:
    problems: list[str] = []
    folds_checked = 0
    for label, run_dir in (("base", base_dir), ("conservative", cons_dir)):
        agg = json.loads((run_dir / "walk_forward_report.json")
                         .read_text(encoding="utf-8"))
        cfg = agg.get("config") or {}
        if cfg.get("risk_constraints_enabled") is not True:
            problems.append(f"{label}: risk_constraints_enabled != true")
        if cfg.get("risk_constraints_calibration") != "campaign_v1":
            problems.append(f"{label}: calibration != campaign_v1")
        for idx, rep in _fold_reports(run_dir):
            folds_checked += 1
            rc = ((rep.get("backtest") or {}).get("provenance") or {}) \
                .get("config", {}).get("risk_constraints")
            if rc != CAMPAIGN_V1_EXPECTED:
                problems.append(
                    f"{label} fold {idx}: risk_constraints={rc!r}")
    return {
        "expected": CAMPAIGN_V1_EXPECTED,
        "folds_checked": folds_checked,
        "problems": problems,
        "veto_triggered": bool(problems),
    }


def compute_midcap_concentration(cons_dir: Path) -> dict[str, Any]:
    csi500_w: list[float] = []
    unknown_w: list[float] = []
    for _idx, rep in _fold_reports(cons_dir):
        att = rep.get("attribution") or {}
        if att.get("status") != "ok":
            continue
        rows = _sleeve_rows(rep)
        csi500_w.append(float(rows.get("csi500_sleeve", {})
                              .get("portfolio_weight", 0.0)))
        unknown_w.append(float(rows.get("unknown", {})
                               .get("portfolio_weight", 0.0)))
    if not csi500_w:
        return {"veto_triggered": True,
                "note": "no attribution folds — fail closed"}
    avg500 = sum(csi500_w) / len(csi500_w)
    avg_unknown = sum(unknown_w) / len(unknown_w)
    return {
        "csi500_time_avg_weight": avg500,
        "unknown_time_avg_weight": avg_unknown,
        "folds_used": len(csi500_w),
        "thresholds": {"csi500": CSI500_WEIGHT_THRESHOLD,
                       "unknown": UNKNOWN_WEIGHT_THRESHOLD},
        "veto_triggered": bool(avg500 > CSI500_WEIGHT_THRESHOLD
                               or avg_unknown > UNKNOWN_WEIGHT_THRESHOLD),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--pair-report", required=True, type=Path)
    ap.add_argument("--base-run", required=True, type=Path)
    ap.add_argument("--conservative-run", required=True, type=Path)
    ap.add_argument("--reference-run", required=True, type=Path)
    args = ap.parse_args()

    report = json.loads(args.pair_report.read_text(encoding="utf-8"))
    checklist = report["veto_checklist"]
    check1 = checklist.get("1_conservative_net_excess")
    if not isinstance(check1, dict) or "veto_triggered" not in check1:
        raise SystemExit("pair report lacks computed check 1 — regenerate "
                         "with csi800_campaign_pair_report.py first")
    cons_net = float(check1["value_annualized"])

    checklist["2_csi500_dependence"] = compute_csi500_dependence(
        args.conservative_run, cons_net)
    checklist["3_turnover_vs_csi300_ref"] = compute_turnover_check(
        args.conservative_run, args.base_run, args.reference_run)
    checklist["4_risk_constraints_recorded"] = compute_constraints_check(
        args.base_run, args.conservative_run)
    checklist["5_midcap_concentration"] = compute_midcap_concentration(
        args.conservative_run)

    eligible, incomplete = evaluate_promotion_eligibility(checklist)
    report["promotion_eligible"] = eligible
    report["incomplete_checks"] = incomplete
    report["veto_checklist_status"] = (
        "COMPLETE" if not incomplete else
        "INCOMPLETE — NOT promotion-eligible; checks "
        + ", ".join(incomplete) + " must be attached and pass")
    args.pair_report.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")

    triggered = [name for name, entry in checklist.items()
                 if isinstance(entry, dict) and entry.get("veto_triggered")]
    print(f"attached checks 2-5 -> {args.pair_report}")
    print(f"promotion_eligible={eligible} | triggered={triggered or 'none'}"
          f" | incomplete={incomplete or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
