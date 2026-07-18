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
import math
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
# v2: walk-forward sides pin per-fold report content hashes
# (``fold_report_sha256``) so post-pairing fold evidence is verifiable
# (codex #373 r5).
# v3 (2026-07-17-csi800-cadence-campaign DP-5): the csi300 REFERENCE run
# is certified as a third party (same four-piece identity as the paired
# sides), and both csi800 sides record per-fold GROSS annualized excess
# (the 50% gross-collapse criterion input) extracted from the
# hash-pinned fold reports at pairing time.
SCHEMA_VERSION = "csi800_pair_report_v3"

# #371-pinned reference-vs-base projected config diff (exact key set)
# and reference identity — binds the veto-3 baseline to "同配置 csi300
# 参照". (Moved here from the attach tool at v3: pairing itself now
# certifies the reference.)
REFERENCE_DIFF_FIELDS: frozenset[str] = frozenset(
    {"instruments", "benchmark_code", "attribution_sleeve_grouping"})
REFERENCE_UNIVERSE = "csi300"
REFERENCE_BENCHMARK = "SH000300TR"


class PairReportError(RuntimeError):
    """Fail-loud: refuse to certify what cannot be proven paired."""


def _config_sha256(cfg: dict[str, Any]) -> str:
    """Hash a CANONICAL serialization of the config itself — never the
    surrounding artifact (codex #369 r4 P2: hashing the full
    walk_forward_report.json mixes timestamps/outcomes into a field
    labeled ``config_sha256``, so identical configs hash differently and
    the field cannot be verified against the embedded config)."""
    canonical = json.dumps(cfg, sort_keys=True, ensure_ascii=False,
                           default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _require_finite_net(side: dict[str, Any], label: str) -> float:
    """Both sides' net excess is REQUIRED finite (codex #369 r4 P1):
    a pair whose base (or conservative) net is missing/None/bool/NaN/Inf
    is not a completed sensitivity band — refuse, never certify."""
    om = side.get("official_metrics") or {}
    value = (om.get("excess_return_with_cost") or {}).get(
        "annualized_return")
    if (value is None or isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)):
        raise PairReportError(
            f"{label} net excess is {value!r} — the sensitivity-band "
            "contract requires BOTH sides' net excess as FINITE numbers; "
            "refusing to certify."
        )
    return float(value)


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
        "config_sha256": _config_sha256(cfg),
        "metric_status": metrics.get("metric_status"),
        "official_metrics": metrics.get("official_metrics"),
        "benchmark": metrics.get("benchmark"),
    }


def _resolve_fold_report(run_dir: Path, report_path: str) -> Path:
    """Resolve a fold report, CONFINED to the claimed run dir (codex
    #369 r5 P1): an aggregate must not be able to point at another run's
    official fold reports to borrow their status while certifying its
    own arbitrary headline metrics. The producer's repo-relative form
    (``report_path = str(output_dir / "fold_XX_report.json")``) is
    supported by resolving it first — but the RESOLVED path must stay
    under ``run_dir``."""
    run_root = run_dir.resolve()
    rp = Path(report_path)
    # CONFINED candidates first (codex #376 r2+r3): the producer
    # declares ``str(output_dir / "fold_XX_report.json")``, so a run dir
    # that was MATERIALIZED elsewhere (certify's anchored-bytes temp
    # copy) resolves by basename within the claimed dir — and that
    # confined fallback must be tried BEFORE any repo-root candidate,
    # otherwise an unrelated leftover at the original working-tree path
    # would trip the outside-run_dir refusal even though the confined
    # copy is valid. Outside candidates remain last purely so a
    # borrowed-evidence attempt still refuses loudly below.
    confined = ((run_dir / rp.name,) if rp.is_absolute()
                else (run_dir / rp, run_dir / rp.name))
    for candidate in confined:
        resolved = candidate.resolve()
        # a confined candidate that escapes run_root (e.g. ../) is just
        # a failed confined attempt — skip, don't refuse yet.
        if resolved.is_file() and resolved.is_relative_to(run_root):
            return resolved
    outside = (rp,) if rp.is_absolute() else (_REPO / rp,)
    for candidate in outside:
        resolved = candidate.resolve()
        if resolved.is_file() and not resolved.is_relative_to(run_root):
            raise PairReportError(
                f"fold report {report_path!r} resolves OUTSIDE the "
                f"claimed run dir {run_dir} ({resolved}) — refusing: "
                "borrowed fold status from another run cannot certify "
                "this aggregate's metrics."
            )
        if resolved.is_file():
            return resolved
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
    # (the aggregate deliberately keeps fold summaries compact). The
    # aggregate records only report_path (no per-fold digest), so the
    # PAIR REPORT is where each fold report's content hash is pinned
    # (codex #373 r5 P1): downstream veto tooling verifies these hashes
    # before consuming any fold payload, otherwise a fold report could
    # be replaced post-pairing together with its positions series in a
    # self-consistent way.
    fold_report_sha256: dict[str, str] = {}
    per_fold_gross: list[float] = []
    seen_paths: set[Path] = set()
    for f in folds:
        # duplicate fold indices / report paths must refuse (codex #373
        # r7 P1): a repeated entry would silently overwrite the pinned
        # digest and let ONE favorable fold stand in as coverage for
        # several while an adverse fold is omitted.
        idx_key = str(f.get("fold_index"))
        if idx_key in fold_report_sha256:
            raise PairReportError(
                f"{wf_p} declares fold_index {idx_key} more than once — "
                "duplicate fold entries cannot certify distinct "
                "coverage; refusing.")
        fold_report = _resolve_fold_report(run_dir, str(f["report_path"]))
        if fold_report in seen_paths:
            raise PairReportError(
                f"{wf_p} declares report path {fold_report} for more "
                "than one fold entry — refusing.")
        seen_paths.add(fold_report)
        raw = fold_report.read_bytes()
        fold_report_sha256[idx_key] = hashlib.sha256(raw).hexdigest()
        payload = json.loads(raw.decode("utf-8"))
        # the selected fold must BELONG to this aggregate entry (codex
        # #369 r5): the producer stamps fold_index into each fold report.
        if payload.get("fold_index") != f.get("fold_index"):
            raise PairReportError(
                f"fold report {fold_report} carries fold_index="
                f"{payload.get('fold_index')!r} but the aggregate entry "
                f"claims {f.get('fold_index')!r} — mismatched fold, "
                "refusing to certify."
            )
        # producer schema (write_fold_report): the status is NESTED under
        # "backtest" (codex #369 r2 — a top-level read is always None and
        # would refuse every real pair).
        backtest = payload.get("backtest")
        if not isinstance(backtest, dict) or "metric_status" not in backtest:
            raise PairReportError(
                f"fold report {fold_report} has no backtest.metric_status "
                "block — producer schema mismatch; refusing to certify."
            )
        status = backtest["metric_status"]
        if status != "official":
            raise PairReportError(
                f"fold {f.get('fold_index')} metric_status={status!r} "
                f"({fold_report}) — every campaign fold must ride the "
                "official path."
            )
        # v3: per-fold GROSS annualized excess, extracted from the
        # hash-pinned fold report at pairing time — the 50%
        # gross-collapse criterion input must live in the certified
        # artifact, never in an editable document (codex #374 r1/r8).
        gross = ((backtest.get("risk_analysis") or {})
                 .get("excess_return_without_cost") or {}).get(
                     "annualized_return")
        if (gross is None or isinstance(gross, bool)
                or not isinstance(gross, (int, float))
                or not math.isfinite(gross)):
            raise PairReportError(
                f"fold {f.get('fold_index')} gross annualized excess is "
                f"{gross!r} ({fold_report}) — the v3 pair artifact "
                "requires a FINITE per-fold gross series; refusing."
            )
        per_fold_gross.append(float(gross))
    agg = report.get("aggregate_metrics") or {}
    net_ann = agg.get("mean_annualized_return")
    # FINITE required (codex #369 r3): json.loads happily yields
    # NaN/Infinity floats, and ``nan <= 0.0`` is False — a malformed or
    # legacy artifact would falsely present an uncomputable conservative
    # net excess as "veto not triggered".
    if (isinstance(net_ann, bool)
            or not isinstance(net_ann, (int, float))
            or not math.isfinite(net_ann)):
        raise PairReportError(
            f"{wf_p} aggregate_metrics.mean_annualized_return is "
            f"{net_ann!r} — a campaign decision needs a FINITE cross-fold "
            "NET excess (per-fold values come from "
            "excess_return_with_cost via extract_cost_metrics)."
        )
    per_fold_net = [f.get("annualized_return") for f in folds]
    return {
        "run_id": run_dir.name,
        "artifact_shape": "walk_forward",
        "config": cfg,
        "config_sha256": _config_sha256(cfg),
        "report_sha256": hashlib.sha256(wf_p.read_bytes()).hexdigest(),
        "fold_report_sha256": fold_report_sha256,
        "per_fold_gross_annualized": per_fold_gross,
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


def _load_reference_side(ref_dir: Path,
                         base_cfg: dict[str, Any]) -> dict[str, Any]:
    """Certify the csi300 REFERENCE run as the pair's third party (v3).

    Structural binding: the reference's embedded config must differ from
    the base config by EXACTLY the #371-pinned reference fields
    (projection excludes run-identity), which also proves slippage
    parity at 5 bps. Documented-run binding: every fold the aggregate
    documents as completed must resolve (confined, fold_index-owned,
    no duplicates) to its own OFFICIAL fold report, whose content hash
    is pinned; folds with ``report_path`` null are the documented
    failures, disclosed via ``ref_failed_folds``.
    """
    wf_p = ref_dir / "walk_forward_report.json"
    if not wf_p.is_file():
        raise PairReportError(f"reference aggregate missing: {wf_p}")
    report = json.loads(wf_p.read_text(encoding="utf-8"))
    cfg = report.get("config")
    if not isinstance(cfg, dict):
        raise PairReportError(f"{wf_p} carries no embedded config mapping.")
    diff = _projected_diff(base_cfg, cfg)
    if set(diff) != REFERENCE_DIFF_FIELDS:
        raise PairReportError(
            f"reference config diff vs base is {sorted(diff)} — expected "
            f"exactly {sorted(REFERENCE_DIFF_FIELDS)}; a reference that "
            "is not 同配置 cannot serve as the veto-3 baseline.")
    if (cfg.get("instruments") != REFERENCE_UNIVERSE
            or cfg.get("benchmark_code") != REFERENCE_BENCHMARK
            or cfg.get("slippage_bps") != BASE_SLIPPAGE_BPS):
        raise PairReportError(
            "reference identity mismatch: expected "
            f"{REFERENCE_UNIVERSE}/{REFERENCE_BENCHMARK}/"
            f"{BASE_SLIPPAGE_BPS}bps, got "
            f"{cfg.get('instruments')!r}/{cfg.get('benchmark_code')!r}/"
            f"{cfg.get('slippage_bps')!r}.")
    folds = report.get("folds") or []
    num_folds = report.get("num_folds")
    if not isinstance(num_folds, int) or len(folds) != num_folds:
        raise PairReportError(
            f"{wf_p}: num_folds={num_folds!r} but {len(folds)} fold "
            "entries — torn aggregate, refusing.")
    fold_report_sha256: dict[str, str] = {}
    failed: list[int] = []
    seen_indices: set[int] = set()
    seen_paths: set[Path] = set()
    for entry in folds:
        idx = int(entry["fold_index"])
        if idx in seen_indices:
            raise PairReportError(
                f"{wf_p} declares fold_index {idx} more than once — "
                "refusing.")
        seen_indices.add(idx)
        rp = entry.get("report_path")
        if rp is None:
            failed.append(idx)
            continue
        resolved = _resolve_fold_report(ref_dir, str(rp))
        if resolved in seen_paths:
            raise PairReportError(
                f"{wf_p} declares report path {resolved} for more than "
                "one fold entry — refusing.")
        seen_paths.add(resolved)
        raw = resolved.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
        if payload.get("fold_index") != idx:
            raise PairReportError(
                f"{resolved} carries fold_index="
                f"{payload.get('fold_index')!r} but the aggregate entry "
                f"claims {idx} — mismatched fold, refusing.")
        status = ((payload.get("backtest") or {}).get("metric_status"))
        if status != "official":
            raise PairReportError(
                f"reference fold {idx} metric_status={status!r} yet the "
                "aggregate documents it as completed — not a documented "
                "reference run, refusing.")
        fold_report_sha256[str(idx)] = hashlib.sha256(raw).hexdigest()
    return {
        "run_id": ref_dir.name,
        "artifact_shape": "walk_forward",
        "config": cfg,
        "config_sha256": _config_sha256(cfg),
        "report_sha256": hashlib.sha256(wf_p.read_bytes()).hexdigest(),
        "fold_report_sha256": fold_report_sha256,
        "num_folds": num_folds,
        "ref_failed_folds": sorted(failed),
    }


def _projected_diff(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    keys = (set(a) | set(b)) - RUN_IDENTITY_FIELDS
    return {k: {"base": a.get(k), "conservative": b.get(k)}
            for k in sorted(keys) if a.get(k) != b.get(k)}


# the five canonical veto checks (DP-4) — eligibility is evaluated
# against THIS set, not against whatever keys a (possibly truncated)
# checklist happens to carry (codex #369 r7 P1: `{}` must not read as
# eligible). Governance-pinned in
# tests/governance/test_csi800_expansion_guards.py.
REQUIRED_VETO_CHECKS: tuple[str, ...] = (
    "1_conservative_net_excess",
    "2_csi500_dependence",
    "3_turnover_vs_csi300_ref",
    "4_risk_constraints_recorded",
    "5_midcap_concentration",
)


def evaluate_promotion_eligibility(
    veto_checklist: dict[str, Any],
) -> tuple[bool, list[str]]:
    """``(promotion_eligible, incomplete_checks)`` for a veto checklist
    (codex #369 r6+r7 P1): a run is promotion-eligible ONLY when every
    one of the five CANONICAL checks (``REQUIRED_VETO_CHECKS``) is
    PRESENT (a dict with ``veto_triggered``) and NONE triggered —
    membership is judged against the canonical set, so a truncated
    checklist (``{}``, or one carrying only a passing check) is
    ineligible with the absent names listed. Extra supplied entries are
    still inspected for triggers (an unknown-but-triggered check must
    not be ignored)."""
    incomplete: list[str] = []
    any_triggered = False
    for name in REQUIRED_VETO_CHECKS:
        entry = veto_checklist.get(name)
        if not isinstance(entry, dict) or "veto_triggered" not in entry:
            incomplete.append(name)
            continue
        if entry["veto_triggered"] is not False:
            any_triggered = True
    for name, entry in veto_checklist.items():
        if (name not in REQUIRED_VETO_CHECKS and isinstance(entry, dict)
                and entry.get("veto_triggered") is not False
                and "veto_triggered" in entry):
            any_triggered = True
    eligible = not incomplete and not any_triggered
    return eligible, incomplete


def build_pair_report(base_dir: Path, cons_dir: Path,
                      ref_dir: Path) -> dict[str, Any]:
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

    # BOTH sides required-finite (codex #369 r3+r4): a NaN/None on either
    # side must refuse — never read as "not vetoed", never certify a
    # half-computed band.
    base_net = _require_finite_net(base, "base")
    cons_net = _require_finite_net(cons, "conservative")
    # Reference certification LAST — pairing validity (shape/diff/band/
    # identity/finite) is established before the third party is bound.
    reference = _load_reference_side(ref_dir, base["config"])
    side_keys = ("run_id", "artifact_shape", "config_sha256",
                 "report_sha256", "fold_report_sha256",
                 "per_fold_gross_annualized", "official_metrics",
                 "benchmark", "num_folds", "per_fold_net_annualized")
    ref_keys = ("run_id", "artifact_shape", "config_sha256",
                "report_sha256", "fold_report_sha256", "num_folds",
                "ref_failed_folds")
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "projection_whitelist": sorted(RUN_IDENTITY_FIELDS),
        "config_diff_projected": diff,
        "base": {k: base[k] for k in side_keys if k in base},
        "reference": {k: reference[k] for k in ref_keys if k in reference},
        "conservative": {k: cons[k] for k in side_keys if k in cons},
        # veto ① is directly computable from the pair; ②③⑤ need the
        # sleeve report / turnover / csi300 reference and are attached at
        # ignition time — explicit nulls, never silently "passed".
        "veto_checklist": {
            "1_conservative_net_excess": {
                "value_annualized": cons_net,
                "base_value_annualized": base_net,
                "veto_triggered": bool(cons_net <= 0.0),
            },
            "2_csi500_dependence": None,
            "3_turnover_vs_csi300_ref": None,
            "4_risk_constraints_recorded": None,
            "5_midcap_concentration": None,
        },
    }
    # self-declared verdict (codex #369 r6 P1): with checks 2-5 pending
    # guard-2/ignition tooling, this artifact must be UNMISTAKABLE as
    # incomplete — promotion consumers key on ``promotion_eligible``,
    # which only a fully-present, fully-untriggered checklist can set.
    eligible, incomplete = evaluate_promotion_eligibility(
        report["veto_checklist"])
    report["promotion_eligible"] = eligible
    report["incomplete_checks"] = incomplete
    report["veto_checklist_status"] = (
        "complete" if not incomplete else
        "INCOMPLETE — NOT promotion-eligible; checks "
        + ", ".join(incomplete)
        + " must be attached (guard-2 / ignition tooling) and pass "
        "before this pair can support promotion."
    )
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base-run", required=True, type=Path)
    p.add_argument("--conservative-run", required=True, type=Path)
    p.add_argument("--reference-run", required=True, type=Path,
                   help="csi300 reference run dir — certified as the "
                        "pair's third party at v3")
    p.add_argument("--out", required=True, type=Path)
    args = p.parse_args(argv)
    try:
        report = build_pair_report(args.base_run, args.conservative_run,
                                   args.reference_run)
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
    print(f"veto checklist: {report['veto_checklist_status']} | "
          f"promotion_eligible={report['promotion_eligible']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
