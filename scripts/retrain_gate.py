#!/usr/bin/env python3
"""Per-retrain gate MEASUREMENT runner (PR-B' of
2026-07-20-csi800-n5-production-promotion, R1-DP-B).

Runs the qlib-bound measurements for the five light gates and turns
them into a machine-readable gate artifact via the PURE
``scripts/retrain_gate_lib.py`` (where every verdict rule is
unit-tested). Two scopes, matching the bootstrap grading (codex #389
r13) and the quarterly rotation alike:

  --scope member    gates (a) trainer integrity + (d) valid-window
                    IC(1d) direction, for ONE freshly trained member.
  --scope ensemble  gates (b) degeneracy + (c) campaign_v1 constraint
                    dry-run + (e) serving veto faces 2/3/5, for a
                    CANDIDATE manifest over a trailing-quarter window.
                    Predictions are produced through the STRICT serving
                    loader + blend (``src.inference.ensemble_serving``)
                    — the dry run exercises the exact production path.

veto3's reference is ANCHORED: the iso_week re-check run's pooled
daily one-way turnover, recomputed from the positions series committed
under ``docs/research/evidence/.../csi800_cadence5_conservative_isoweek``
as read via ``git show`` (never the working tree) at a SINGLE commit
id resolved once from ``origin/main`` (codex #391 r28 — a moving ref
could pool the anchor from mixed revisions), with each series bound to
its fold report's ``positions_sha256``.

NO net-return measurement exists in this tool by design (R1: the
per-retrain gates carry no performance authority).

This is real compute on the LIVE bundle (read-only) — run FOREGROUND,
serially. Exit codes: 0 = all gates PASS (artifact written), 1 = at
least one gate FAIL (artifact written — failures must leave a trace),
2 = producer/tool error (no verdict).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.eval_profiles import resolve_profile  # noqa: E402
from scripts.regen.replay_frozen_baseline import (  # noqa: E402
    COMMISSION,
    EXEC_PRICE,
    INIT_CASH,
    LAG,
    LIMIT_THRESHOLD,
    MIN_COST,
    N_DROP,
    TOPK,
)
from scripts.retrain_gate_lib import (  # noqa: E402
    SCOPE_ENSEMBLE,
    SCOPE_MEMBER,
    assemble_gate_artifact,
    gate_constraint_dry_run,
    gate_degeneracy,
    gate_ic_direction,
    gate_serving_veto,
    gate_trainer_integrity,
    serving_veto_share,
)
from src.core.attribution_sleeve_loader import (  # noqa: E402
    SleeveResolutionError,
    resolve_sleeve_map,
    sleeve_turnover,
)
from src.core.backtest_runner import (  # noqa: E402
    BacktestRunner,
    BacktestRunnerError,
)
from src.core.canonical_backtest_contract import (  # noqa: E402
    ADJUST_MODE_PRE,
    CN_STAMP_TAX_SCHEDULE_DEFAULT,
    CanonicalAccountConfig,
    CanonicalBacktestInput,
    CanonicalExchangeConfig,
    CanonicalExchangeCostModel,
)
from src.core.performance_attribution import (  # noqa: E402
    AttributionConfig,
    PerformanceAttribution,
)
from src.core.qlib_runtime import (  # noqa: E402
    QlibRuntimeConfig,
    init_qlib_canonical,
)
from src.core.risk_constraints import (  # noqa: E402
    RiskConstraintError,
    campaign_risk_constraints_v1,
)
from src.core.signal_analyzer import (  # noqa: E402
    SignalAnalysisConfig,
    SignalAnalyzer,
)
from src.inference.ensemble_serving import (  # noqa: E402
    EnsembleServingError,
    ensemble_predict,
    load_ensemble_manifest,
    load_member_models,
)

# The anchored iso_week re-check evidence (PR-B of this change, run
# gen2 on main 4df3109) — veto3's turnover reference is recomputed
# from THESE positions series as committed on the mainline.
ISOWEEK_EVIDENCE_DIR = (
    "docs/research/evidence/csi800_n5_runs/"
    "csi800_cadence5_conservative_isoweek")
_ANCHOR_REF = "origin/main"

# The degeneracy scan is shared verbatim with the ④-style guard eval —
# one scanner, one straddle-materiality rule (its import-time profile
# assertion also cross-checks eval_profiles for free).
from scripts.eval_frozen_model_oos import (  # noqa: E402
    _degeneracy_scan,
)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_show(ref_path: str) -> bytes:
    proc = subprocess.run(
        ["git", "show", ref_path], cwd=PROJECT_ROOT,
        capture_output=True, check=False)
    if proc.returncode != 0:
        raise SystemExit(
            f"git show {ref_path} failed: "
            f"{proc.stderr.decode(errors='replace').strip()}")
    return proc.stdout


def _resolve_anchor_rev() -> str:
    """Pin the anchor ref to ONE commit id (codex #391 r28).

    ``origin/main`` moves: enumerating the fold reports through it and
    then reading each report/positions blob through it again lets a
    concurrent fetch pool the veto3 turnover anchor from MIXED
    revisions — an anchor that never existed as a certified artifact.
    Every anchor read below uses the id returned here."""
    proc = subprocess.run(
        ["git", "rev-parse", f"{_ANCHOR_REF}^{{commit}}"],
        cwd=PROJECT_ROOT, capture_output=True, check=False)
    rev = proc.stdout.decode("utf-8", errors="replace").strip()
    if proc.returncode != 0 or not rev:
        raise SystemExit(
            f"cannot resolve {_ANCHOR_REF} to a commit: "
            f"{proc.stderr.decode(errors='replace').strip()}")
    return rev


def _anchor_turnover_daily_mean() -> tuple[float, dict[str, Any]]:
    """Pooled daily one-way turnover of the anchored iso_week re-check
    run: for every fold the run's AGGREGATE report declares, resolve
    its positions series (bound by the report's ``positions_sha256``),
    recompute single-bucket one-way turnover with the SAME pure
    function the campaign attach used, and pool
    ``sum(total) / sum(transitions)``.

    The fold set comes from ``walk_forward_report.json``'s ``folds[]``
    (codex #391 r29) — NOT from globbing fold-shaped filenames: a
    stale or accidentally committed extra ``fold_*_report.json`` beside
    the certified run would otherwise silently move the veto3
    threshold."""
    rev = _resolve_anchor_rev()
    aggregate = json.loads(_git_show(
        f"{rev}:{ISOWEEK_EVIDENCE_DIR}/walk_forward_report.json"
    ).decode("utf-8"))
    declared = aggregate.get("folds")
    num_folds = aggregate.get("num_folds")
    if not isinstance(declared, list) or not declared:
        raise SystemExit(
            f"{ISOWEEK_EVIDENCE_DIR}/walk_forward_report.json declares "
            "no folds — anchored evidence missing.")
    if not isinstance(num_folds, int) or num_folds != len(declared):
        raise SystemExit(
            f"anchored aggregate declares num_folds={num_folds!r} but "
            f"carries {len(declared)} fold rows — torn evidence, "
            "refusing.")
    report_names: list[tuple[int, str]] = []
    seen_indexes: set[int] = set()
    seen_names: set[str] = set()
    for row in declared:
        if not isinstance(row, dict):
            raise SystemExit(
                "anchored aggregate fold row is not an object — "
                "refusing.")
        idx = row.get("fold_index")
        path = row.get("report_path")
        if not isinstance(idx, int) or idx in seen_indexes:
            raise SystemExit(
                f"anchored aggregate fold_index {idx!r} is missing or "
                "duplicated — refusing.")
        seen_indexes.add(idx)
        if not isinstance(path, str) or not path:
            raise SystemExit(
                f"anchored aggregate fold {idx} declares no "
                "report_path — refusing.")
        # The declared path is the PRODUCER's output location; the
        # committed evidence lives under the evidence dir by basename
        # (the campaign's confined-resolution convention).
        base = path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        if base in seen_names:
            # Two unique fold_index rows pointing at ONE report would
            # pool that fold twice and move the anchor (codex #391
            # r30) — the index checks above cannot see it.
            raise SystemExit(
                f"anchored aggregate declares {base!r} for more than "
                "one fold_index — torn evidence, refusing.")
        seen_names.add(base)
        report_names.append((idx, f"{ISOWEEK_EVIDENCE_DIR}/{base}"))
    total = 0.0
    transitions = 0.0
    folds = 0
    for idx, name in report_names:
        rep = json.loads(
            _git_show(f"{rev}:{name}").decode("utf-8"))
        # The fold report must be the one the aggregate declared for
        # THIS index (codex #391 r30): a renamed/misdeclared file
        # cannot silently substitute a different fold.
        if rep.get("fold_index") != idx:
            raise SystemExit(
                f"{name}: fold report declares fold_index "
                f"{rep.get('fold_index')!r} but the aggregate lists it "
                f"as fold {idx} — torn evidence, refusing.")
        declared = rep.get("positions_path")
        recorded_digest = rep.get("positions_sha256")
        if not isinstance(declared, str) or not declared:
            raise SystemExit(
                f"{name}: no positions_path in anchored fold report.")
        if not isinstance(recorded_digest, str) or not recorded_digest:
            raise SystemExit(
                f"{name}: anchored fold report carries no "
                "positions_sha256 — unattested series cannot anchor "
                "the turnover reference, refusing.")
        pos_name = declared.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        raw = _git_show(
            f"{rev}:{ISOWEEK_EVIDENCE_DIR}/{pos_name}")
        actual_digest = hashlib.sha256(raw).hexdigest()
        if actual_digest != recorded_digest:
            raise SystemExit(
                f"{name}: positions bytes hash {actual_digest} != "
                f"attested {recorded_digest} — refusing.")
        positions = json.loads(raw.decode("utf-8"))
        if not isinstance(positions, dict) or len(positions) < 2:
            raise SystemExit(
                f"{name}: positions series empty/single-day — cannot "
                "measure a transition, refusing.")
        block = sleeve_turnover(positions, {})  # single honest bucket
        if not block:
            raise SystemExit(
                f"{name}: no holdings on any day — degenerate anchor "
                "evidence, refusing.")
        fold_total = sum(row["total_oneway"] for row in block.values())
        fold_trans = max(float(v["n_transitions"])
                         for v in block.values())
        if not math.isfinite(fold_total) or fold_trans <= 0:
            raise SystemExit(
                f"{name}: non-finite/transition-less recomputed "
                "turnover — corrupted anchor evidence, refusing.")
        total += fold_total
        transitions += fold_trans
        folds += 1
    daily_mean = total / transitions
    return daily_mean, {
        "ref": _ANCHOR_REF,
        # The RESOLVED commit every anchor read used (codex #391 r28)
        # — the audit trail must name the exact evidence revision,
        # not the moving ref it came from.
        "rev": rev,
        "evidence_dir": ISOWEEK_EVIDENCE_DIR,
        "folds_pooled": folds,
        "total_oneway": total,
        "transitions": transitions,
        "daily_mean_oneway": daily_mean,
    }


def _build_dataset(args: argparse.Namespace, profile: dict[str, Any],
                   fit_start: str, fit_end: str,
                   valid_start: str, valid_end: str,
                   test_start: str, test_end: str) -> Any:
    from src.data.feature_dataset_builder import (
        FeatureDatasetBuilder,
        FeatureDatasetConfig,
    )

    init_qlib_canonical(QlibRuntimeConfig(
        provider_uri=args.provider, region="cn",
        data_adjust_mode=ADJUST_MODE_PRE))
    return FeatureDatasetBuilder.build(FeatureDatasetConfig(
        instruments=profile["instruments"],
        feature_handler=args.handler,
        train_start=fit_start, train_end=fit_end,
        valid_start=valid_start, valid_end=valid_end,
        test_start=test_start, test_end=test_end,
    ))


def _member_scope(args: argparse.Namespace,
                  profile: dict[str, Any]) -> dict[str, Any]:
    """Gates (a) + (d) for one freshly trained member."""
    # The sidecar bytes are read ONCE: the digest in the subject block
    # and the parsed payload the integrity gate judges come from the
    # same buffer. A missing/unreadable sidecar is a GATE FAIL state —
    # the artifact must still be written (failures leave a trace,
    # codex #391 r1), so the digest becomes an honest null instead of
    # a second read raising a tool error after the gate already
    # concluded FAIL.
    meta_path = Path(args.member_meta)
    meta_raw: bytes | None
    try:
        meta_raw = meta_path.read_bytes()
    except OSError:
        meta_raw = None
    sidecar: Any = None
    if meta_raw is not None:
        try:
            sidecar = json.loads(meta_raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            sidecar = None  # gate lib fails it closed with the reason
    integrity = gate_trainer_integrity(sidecar)

    # Gate (d): predict the member's OWN valid window (test segment =
    # valid window; normalization fit = the member's training window)
    # and read the plain daily IC(1d) mean — the spec's degeneracy gate
    # names executable stamps explicitly, the IC gate does not: it is a
    # trainer-level directional sanity check on the valid segment.
    build = _build_dataset(
        args, profile,
        fit_start=args.fit_start, fit_end=args.fit_end,
        valid_start=args.valid_start, valid_end=args.valid_end,
        test_start=args.valid_start, test_end=args.valid_end)
    import pickle

    # Pickle bytes are read ONCE (codex #391 r2): the object the IC
    # gate scores and the digest the artifact binds must come from the
    # same buffer — a second file read could hash bytes that were
    # never scored, and the rotation executor trusts that binding.
    # (A missing pickle is a TOOL error: there is nothing to gate.)
    pkl_raw = Path(args.member_pkl).read_bytes()
    model = pickle.loads(pkl_raw)
    if not hasattr(model, "predict"):
        raise SystemExit(
            f"loaded object {type(model).__name__} has no .predict")
    preds = model.predict(build.dataset, segment="test")
    if not isinstance(preds, pd.Series):
        preds = pd.Series(preds)
    preds = preds.dropna()
    signal = SignalAnalyzer.analyze(
        predictions=preds,
        config=SignalAnalysisConfig(forward_periods=(1,), topk=TOPK))
    ic = gate_ic_direction(float(signal.ic_summary[1]["mean_ic"]))

    subject = {
        "pkl_path": str(args.member_pkl),
        "pkl_sha256": hashlib.sha256(pkl_raw).hexdigest(),
        "meta_path": str(args.member_meta),
        # Honest null when the sidecar was unreadable — the FAIL
        # artifact still records everything measurable, and the null
        # digest can never satisfy the rotation executor's meta
        # binding.
        "meta_sha256": (hashlib.sha256(meta_raw).hexdigest()
                        if meta_raw is not None else None),
        "fit_start": args.fit_start, "fit_end": args.fit_end,
    }
    return assemble_gate_artifact(
        scope=SCOPE_MEMBER,
        gates={"trainer_integrity": integrity, "ic_direction": ic},
        subject=subject,
        window={"valid_start": args.valid_start,
                "valid_end": args.valid_end},
        anchor=None,
        generated_at=_now_iso(),
    )


def _ensemble_scope(args: argparse.Namespace,
                    profile: dict[str, Any]) -> dict[str, Any]:
    """Gates (b) + (c) + (e) for a CANDIDATE manifest over the
    trailing-quarter window."""
    try:
        members, manifest_sha = load_ensemble_manifest(args.manifest)
        loaded = load_member_models(members)
    except EnsembleServingError as exc:
        raise SystemExit(f"candidate manifest refused: {exc}") from exc
    newest = members[-1]
    build = _build_dataset(
        args, profile,
        # Serving parity: the dataset fit window is the NEWEST member's
        # training window (daily_recommend forces the same equality).
        fit_start=newest.fit_start, fit_end=newest.fit_end,
        valid_start=args.valid_start, valid_end=args.valid_end,
        test_start=args.window_start, test_end=args.window_end)
    try:
        preds = ensemble_predict(loaded, build.dataset, segment="test")
    except EnsembleServingError as exc:
        raise SystemExit(f"ensemble dry-run predict refused: {exc}") from exc
    preds = preds.dropna()

    # Gate (b): degeneracy on the EXECUTABLE stamp set (profile-thinned
    # via the same canonical helper the backtest uses).
    from qlib.data import D

    cal = list(D.calendar(
        start_time=args.window_start, end_time=args.window_end))
    exec_preds = BacktestRunner._thin_predictions(
        preds,
        cadence_days=profile["rebalance_cadence_days"],
        phase=profile["rebalance_phase"],
        anchor=profile["rebalance_anchor"],
        trading_calendar=cal,
    )
    if not isinstance(exec_preds, pd.Series):
        exec_preds = pd.Series(exec_preds)
    degen_scan = _degeneracy_scan(exec_preds)
    degeneracy = gate_degeneracy(
        int(degen_scan["n_degenerate_days"]),
        int(degen_scan["n_cutoff_straddle_days"]))

    # Gate (c): campaign_v1 dry run — a RAISE inside the runner is the
    # gate's FAIL evidence, not tool breakage (cause-chain recognition,
    # codex #387 r8).
    request = CanonicalBacktestInput(
        predictions_ref=f"retrain_gate:{Path(args.manifest).name}",
        evaluation_start=args.window_start,
        evaluation_end=args.window_end,
        account_config=CanonicalAccountConfig(init_cash=INIT_CASH),
        exchange_config=CanonicalExchangeConfig(
            freq="day",
            execution_price_kind=EXEC_PRICE,
            cost_model=CanonicalExchangeCostModel(
                commission_rate=COMMISSION,
                stamp_tax_schedule=CN_STAMP_TAX_SCHEDULE_DEFAULT,
                slippage_bps=profile["slippage_bps"],
                min_cost=MIN_COST,
            ),
            limit_threshold=LIMIT_THRESHOLD,
        ),
        adjust_mode=ADJUST_MODE_PRE,
        signal_to_execution_lag=LAG,
        benchmark_code=profile["benchmark_code"],
    )
    constraint_veto: str | None = None
    output = None
    try:
        output = BacktestRunner.run(
            request=request,
            predictions=preds,
            topk=TOPK,
            n_drop=N_DROP,
            compute_baselines=False,
            namechange_path=args.namechange,
            require_st_mask=True,
            rebalance_cadence_days=profile["rebalance_cadence_days"],
            rebalance_phase=profile["rebalance_phase"],
            rebalance_anchor=profile["rebalance_anchor"],
            risk_constraint_scope=profile["risk_constraint_scope"],
            risk_constraints=campaign_risk_constraints_v1(),
            universe_hint=profile["instruments"],
        )
    except (RiskConstraintError, BacktestRunnerError) as exc:
        cause: BaseException | None = exc
        while cause is not None and not isinstance(
                cause, RiskConstraintError):
            cause = cause.__cause__
        if cause is None:
            raise  # genuine tool failure, not a constraint veto
        constraint_veto = str(exc)
    constraint = gate_constraint_dry_run(constraint_veto)

    # Gate (e): veto faces need positions + sleeve attribution; when
    # the constraint dry-run already vetoed there is no authoritative
    # positions series — record the faces as unmeasured FAIL reasons
    # via the pure gate (non-finite inputs fail closed), keeping ONE
    # artifact with every gate present.
    if output is not None and output.positions:
        try:
            sleeves = resolve_sleeve_map(
                args.provider, args.window_start)
        except SleeveResolutionError as exc:
            raise SystemExit(f"sleeve resolution failed: {exc}") from exc
        turn_block = sleeve_turnover(output.positions, {})
        if not turn_block:
            raise SystemExit(
                "dry-run positions have no holdings on any day — "
                "cannot measure veto3, refusing.")
        dry_total = sum(r["total_oneway"] for r in turn_block.values())
        dry_trans = max(float(v["n_transitions"])
                        for v in turn_block.values())
        # A single dated snapshot has NO transition to measure —
        # veto3 is then unmeasurable, which must fail closed rather
        # than read as "zero turnover, clean pass" (codex #391 r29;
        # the anchor side already refuses transition-less evidence).
        dry_daily_mean = ((dry_total / dry_trans) if dry_trans > 0
                          else math.nan)

        # The engine call mirrors the walk-forward fold path exactly
        # (sleeve grouping = Brinson over membership sleeves).
        attribution = PerformanceAttribution.analyze(
            return_series=output.return_series,
            predictions=preds,
            config=AttributionConfig(
                start_date=args.window_start,
                end_date=args.window_end,
                industry_map_override=sleeves.sleeve_map,
                industry_taxonomy_id=sleeves.taxonomy_id,
            ),
            positions=output.positions,
        )
        # This attribution is produced IN-PROCESS over the sleeve map —
        # an absent row is a TRUE zero (no holdings in that bucket),
        # not the omitted-producer-field situation the campaign attach
        # refuses (there the evidence was a mutable committed file).
        csi500_weight = 0.0
        csi500_effect = 0.0
        unknown_weight = 0.0
        rows = {row.sector: row for row in attribution.sector_attribution}
        row500 = rows.get("csi500_sleeve")
        if row500 is not None:
            csi500_weight = float(row500.portfolio_weight)
            csi500_effect = float(row500.total_effect)
        row_unknown = rows.get("unknown")
        if row_unknown is not None:
            unknown_weight = float(row_unknown.portfolio_weight)
        # The denominator is the PRODUCER's exact
        # ``sector_effects_sum`` (codex #391 r27) — the same field the
        # campaign attach reads. Summing the per-sector rows would use
        # their ROUNDED display values and could move a
        # threshold-adjacent candidate across the 0.80 / <= 0 branch
        # relative to the certified semantics.
        effects_sum = float(attribution.sector_effects_sum)
        # Corrupted (non-finite) attribution must FAIL the veto, never
        # borrow the "gross effect <= 0 → cannot trigger" semantics
        # (codex #391 r26) — the distinction lives in the pure lib.
        share = serving_veto_share(csi500_effect, effects_sum)
        anchor_daily_mean, anchor_info = _anchor_turnover_daily_mean()
        veto = gate_serving_veto(
            csi500_effect_share=share,
            csi500_weight=csi500_weight,
            unknown_weight=unknown_weight,
            dryrun_daily_mean_oneway=dry_daily_mean,
            anchor_daily_mean_oneway=anchor_daily_mean,
        )
        if dry_trans <= 0:
            veto["notes"].append(
                "veto3 unmeasurable: the dry-run positions series has "
                "no transition (single dated snapshot) — failed closed")
    else:
        anchor_info = None
        veto = gate_serving_veto(
            csi500_effect_share=float("nan"),
            csi500_weight=float("nan"),
            unknown_weight=float("nan"),
            dryrun_daily_mean_oneway=float("nan"),
            anchor_daily_mean_oneway=float("nan"),
        )
        veto["notes"].append(
            "veto faces unmeasured: the constraint dry-run vetoed "
            "before an authoritative positions series existed")

    subject = {
        "manifest_path": str(args.manifest),
        "manifest_sha256": manifest_sha,
        "members": [
            {"pkl_sha256": m.pkl_sha256, "fit_start": m.fit_start,
             "fit_end": m.fit_end}
            for m in members],
    }
    return assemble_gate_artifact(
        scope=SCOPE_ENSEMBLE,
        gates={"degeneracy": degeneracy,
               "constraint_dry_run": constraint,
               "serving_veto": veto},
        subject=subject,
        window={"window_start": args.window_start,
                "window_end": args.window_end},
        anchor=anchor_info,
        generated_at=_now_iso(),
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--scope", choices=(SCOPE_MEMBER, SCOPE_ENSEMBLE),
                   required=True)
    # choices pinned to the ONE profile these gates exist for (codex
    # #391 r12): a csi300_daily measurement stamped into a v1 gate
    # artifact would be accepted by the rotation executor on
    # scope/digest alone — the CLI refuses the combination outright
    # and the artifact additionally stamps its profile for the
    # executor to verify.
    p.add_argument("--profile", choices=("csi800_n5",),
                   default="csi800_n5",
                   help="Pre-registered semantic knob set; the retrain "
                        "gates exist only for the csi800_n5 protocol.")
    p.add_argument("--provider", default="D:/qlib_data/my_cn_data_pit")
    p.add_argument("--namechange",
                   default="D:/qlib_data/tushare_raw/all_namechanges.parquet")
    p.add_argument("--handler", default="Alpha158")
    # member scope: the member's own windows (mirror its preset).
    p.add_argument("--member-pkl", default=None)
    p.add_argument("--member-meta", default=None)
    p.add_argument("--fit-start", default=None)
    p.add_argument("--fit-end", default=None)
    p.add_argument("--valid-start", default=None)
    p.add_argument("--valid-end", default=None)
    # ensemble scope: candidate manifest + trailing-quarter window.
    p.add_argument("--manifest", default=None)
    p.add_argument("--window-start", default=None)
    p.add_argument("--window-end", default=None)
    p.add_argument("--out", required=True, help="Gate artifact path.")
    args = p.parse_args(argv)
    profile = resolve_profile(args.profile)

    required: tuple[str, ...]
    if args.scope == SCOPE_MEMBER:
        required = ("member_pkl", "member_meta", "fit_start", "fit_end",
                    "valid_start", "valid_end")
    else:
        required = ("manifest", "window_start", "window_end",
                    "valid_start", "valid_end")
    missing = [name for name in required
               if getattr(args, name) in (None, "")]
    if missing:
        raise SystemExit(
            f"--scope {args.scope} requires: "
            + ", ".join("--" + name.replace("_", "-")
                        for name in missing))

    artifact = (_member_scope(args, profile)
                if args.scope == SCOPE_MEMBER
                else _ensemble_scope(args, profile))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(artifact, indent=2, default=str),
                   encoding="utf-8")
    print(json.dumps(artifact, indent=2, default=str))
    print(f"[retrain-gate] wrote {out}")
    if artifact["overall"] != "PASS":
        print("[retrain-gate] RESULT: FAIL — artifact recorded; the "
              "member/ensemble does not proceed (exit 1).")
        return 1
    print("[retrain-gate] RESULT: PASS")
    return 0


def _cli() -> int:
    """Honor the documented exit-code contract: 0 = all gates PASS,
    1 = a gate FAILED (artifact written), 2 = producer/tool error (no
    verdict). ``SystemExit`` raised with a message string would exit 1
    — indistinguishable from a gate FAIL — so tool errors are remapped
    to 2 here."""
    try:
        return main()
    except SystemExit as exc:
        if isinstance(exc.code, int) or exc.code is None:
            raise
        print(f"[retrain-gate] TOOL ERROR: {exc.code}", file=sys.stderr)
        return 2
    except Exception:  # noqa: BLE001 — traceback preserved on stderr
        import traceback

        traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(_cli())
