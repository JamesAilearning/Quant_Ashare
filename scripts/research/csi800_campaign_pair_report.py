"""CSI800 campaign PAIRED sensitivity-band report (guard-1, codex #368).

Why this tool exists
--------------------
The v2-csi800-expansion-guards contract requires every campaign decision
run to exist as a base(5bps)/conservative(20bps) PAIR whose pairing is
proven by the artifact itself: two independent run reports cannot show
matching inputs, and an unfavorable conservative artifact could simply
be omitted. This tool consumes both run dirs and either emits ONE paired
report (with run ids + a projected full-config diff proving the ONLY
semantic difference is ``slippage_bps``) or refuses loudly.

Comparison projection (codex #368 r2): the diff excludes an EXPLICIT
run-identity whitelist — walk-forward pairs necessarily use different
output dirs, so an unprojected all-fields diff would reject every real
pair. The whitelist is a governance-pinned constant
(tests/governance/test_csi800_expansion_guards.py); adding a semantic
field to it is a forbidden escape hatch.

Usage::

    python scripts/research/csi800_campaign_pair_report.py \\
        --base-run output/runs/<id> --conservative-run output/runs/<id> \\
        --out docs/research/<campaign>_pair_report.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import yaml

_REPO = Path(__file__).resolve().parents[2]

# Run-identity / output-location fields excluded from the pairing diff.
# GOVERNANCE-PINNED: extending this set is reviewed via the pin test —
# it may never contain an execution-semantic field.
RUN_IDENTITY_FIELDS: frozenset[str] = frozenset({"output_dir"})

BASE_SLIPPAGE_BPS = 5.0
CONSERVATIVE_SLIPPAGE_BPS = 20.0
CAMPAIGN_UNIVERSE = "csi800"
CAMPAIGN_BENCHMARK = "SH000906TR"
SCHEMA_VERSION = "csi800_pair_report_v1"


class PairReportError(RuntimeError):
    """Fail-loud: refuse to certify what cannot be proven paired."""


def _load_side(run_dir: Path) -> dict[str, Any]:
    """Load one side. Two artifact shapes (codex P1 on #369):

    - WALK-FORWARD run dir (the campaign shape): ``walk_forward_report
      .json`` embeds the full config; per-fold ``metric_status`` is read
      from each fold's own report via ``report_path`` and ALL folds must
      be official.
    - pipeline run dir (probe shape): ``config.yaml`` + ``metrics.json``.
    """
    if not run_dir.is_dir():
        raise PairReportError(
            f"run dir missing: {run_dir} — a pair with an absent side "
            "(especially the conservative one) is INVALID, not pending."
        )
    wf_p = run_dir / "walk_forward_report.json"
    if wf_p.is_file():
        return _load_walk_forward_side(run_dir, wf_p)
    return _load_pipeline_side(run_dir)


def _load_pipeline_side(run_dir: Path) -> dict[str, Any]:
    cfg_p, met_p = run_dir / "config.yaml", run_dir / "metrics.json"
    for p in (cfg_p, met_p):
        if not p.is_file():
            raise PairReportError(f"required artifact missing: {p}")
    cfg = yaml.safe_load(cfg_p.read_text(encoding="utf-8"))
    metrics = json.loads(met_p.read_text(encoding="utf-8"))
    if not isinstance(cfg, dict):
        raise PairReportError(f"{cfg_p} is not a mapping.")
    return {
        "run_id": run_dir.name,
        "artifact_shape": "pipeline",
        "config": cfg,
        "config_sha256": hashlib.sha256(cfg_p.read_bytes()).hexdigest(),
        "metric_status": metrics.get("metric_status"),
        "official_metrics": metrics.get("official_metrics"),
        "benchmark": metrics.get("benchmark"),
    }


def _resolve_fold_report(run_dir: Path, report_path: str) -> Path:
    rp = Path(report_path)
    for candidate in ((rp,) if rp.is_absolute()
                      else (run_dir / rp, _REPO / rp)):
        if candidate.is_file():
            return candidate
    raise PairReportError(
        f"fold report unreadable: {report_path!r} (tried under {run_dir} "
        "and the repo root) — per-fold official status cannot be "
        "verified, refusing to certify the pair."
    )


def _load_walk_forward_side(run_dir: Path, wf_p: Path) -> dict[str, Any]:
    report = json.loads(wf_p.read_text(encoding="utf-8"))
    cfg = report.get("config")
    if not isinstance(cfg, dict):
        raise PairReportError(
            f"{wf_p} carries no embedded config mapping — cannot prove "
            "pairing without it."
        )
    folds = report.get("folds") or []
    if not folds:
        raise PairReportError(f"{wf_p} has no folds — nothing to certify.")
    # Per-fold official status lives in each fold's own report
    # (the aggregate deliberately keeps fold summaries compact).
    for f in folds:
        fold_report = _resolve_fold_report(run_dir, str(f["report_path"]))
        status = json.loads(
            fold_report.read_text(encoding="utf-8")).get("metric_status")
        if status != "official":
            raise PairReportError(
                f"fold {f.get('fold_index')} metric_status={status!r} "
                f"({fold_report}) — every campaign fold must ride the "
                "official path."
            )
    agg = report.get("aggregate_metrics") or {}
    net_ann = agg.get("mean_annualized_return")
    if not isinstance(net_ann, (int, float)):
        raise PairReportError(
            f"{wf_p} aggregate_metrics.mean_annualized_return is "
            f"{net_ann!r} — a campaign decision needs a valid cross-fold "
            "NET excess (per-fold values come from "
            "excess_return_with_cost via extract_cost_metrics)."
        )
    per_fold_net = [f.get("annualized_return") for f in folds]
    return {
        "run_id": run_dir.name,
        "artifact_shape": "walk_forward",
        "config": cfg,
        "config_sha256": hashlib.sha256(wf_p.read_bytes()).hexdigest(),
        "metric_status": "official",   # proven per fold above
        # normalized to the pipeline shape so the veto computation is
        # shape-agnostic; fold headline metrics ARE the with-cost excess.
        "official_metrics": {
            "excess_return_with_cost": {
                "annualized_return": net_ann,
                "information_ratio": agg.get("mean_information_ratio"),
                "max_drawdown": agg.get("worst_drawdown"),
            },
        },
        "benchmark": {"code": cfg.get("benchmark_code")},
        "num_folds": report.get("num_folds"),
        "per_fold_net_annualized": per_fold_net,
    }


def _projected_diff(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    keys = (set(a) | set(b)) - RUN_IDENTITY_FIELDS
    return {k: {"base": a.get(k), "conservative": b.get(k)}
            for k in sorted(keys) if a.get(k) != b.get(k)}


def build_pair_report(base_dir: Path, cons_dir: Path) -> dict[str, Any]:
    base, cons = _load_side(base_dir), _load_side(cons_dir)
    if base["artifact_shape"] != cons["artifact_shape"]:
        raise PairReportError(
            "pairing REFUSED: mixed artifact shapes "
            f"({base['artifact_shape']} vs {cons['artifact_shape']}) — "
            "both sides of a sensitivity band must come from the same "
            "engine (the campaign shape is walk_forward)."
        )
    diff = _projected_diff(base["config"], cons["config"])
    if set(diff) != {"slippage_bps"}:
        raise PairReportError(
            "pairing REFUSED: projected config diff must be exactly "
            f"{{'slippage_bps'}}, got {sorted(diff)} — the pair does not "
            "prove a same-inputs sensitivity band. (Run-identity fields "
            f"excluded: {sorted(RUN_IDENTITY_FIELDS)}.)"
        )
    got = (diff["slippage_bps"]["base"], diff["slippage_bps"]["conservative"])
    if got != (BASE_SLIPPAGE_BPS, CONSERVATIVE_SLIPPAGE_BPS):
        raise PairReportError(
            f"pairing REFUSED: slippage band must be exactly "
            f"({BASE_SLIPPAGE_BPS}, {CONSERVATIVE_SLIPPAGE_BPS}) bps "
            f"(DP-2, pre-registered); got {got}. The conservative "
            "magnitude may not be tuned after the fact."
        )
    for side in (base, cons):
        cfg = side["config"]
        if (cfg.get("instruments"), cfg.get("benchmark_code")) != (
                CAMPAIGN_UNIVERSE, CAMPAIGN_BENCHMARK):
            raise PairReportError(
                f"pairing REFUSED: run {side['run_id']} is not a csi800 "
                f"campaign run (instruments={cfg.get('instruments')!r}, "
                f"benchmark={cfg.get('benchmark_code')!r}; expected "
                f"{CAMPAIGN_UNIVERSE!r}/{CAMPAIGN_BENCHMARK!r})."
            )
        if side["metric_status"] != "official":
            raise PairReportError(
                f"pairing REFUSED: run {side['run_id']} metric_status="
                f"{side['metric_status']!r} — only official-path metrics "
                "can enter the veto checklist."
            )

    def _net_ann(side: dict[str, Any]) -> Any:
        om = side.get("official_metrics") or {}
        return (om.get("excess_return_with_cost") or {}).get(
            "annualized_return")

    cons_net = _net_ann(cons)
    side_keys = ("run_id", "artifact_shape", "config_sha256",
                 "official_metrics", "benchmark", "num_folds",
                 "per_fold_net_annualized")
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "projection_whitelist": sorted(RUN_IDENTITY_FIELDS),
        "config_diff_projected": diff,
        "base": {k: base[k] for k in side_keys if k in base},
        "conservative": {k: cons[k] for k in side_keys if k in cons},
        # veto ① is directly computable from the pair; ②③⑤ need the
        # sleeve report / turnover / csi300 reference and are attached at
        # ignition time — explicit nulls, never silently "passed".
        "veto_checklist": {
            "1_conservative_net_excess": {
                "value_annualized": cons_net,
                "veto_triggered": (None if cons_net is None
                                   else bool(cons_net <= 0.0)),
            },
            "2_csi500_dependence": None,
            "3_turnover_vs_csi300_ref": None,
            "4_risk_constraints_recorded": None,
            "5_midcap_concentration": None,
        },
    }
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base-run", required=True, type=Path)
    p.add_argument("--conservative-run", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    args = p.parse_args(argv)
    try:
        report = build_pair_report(args.base_run, args.conservative_run)
    except (PairReportError, OSError, ValueError) as exc:
        print(f"PAIR REPORT REFUSED: {exc}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"paired report written: {args.out} "
          f"(base={report['base']['run_id']}, "
          f"conservative={report['conservative']['run_id']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
