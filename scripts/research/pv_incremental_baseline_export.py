"""pv_incremental_v1 baseline-prediction exporter (PV-DP-3).

Consumes a COMPLETED walk-forward run directory (the one produced by
``config/presets/pv_incremental_baseline.yaml``) and emits the two
artifacts the campaign binds to:

* ``<out>/baseline_preds.parquet`` — wide (date × instrument)
  out-of-fold predictions, concatenated across folds;
* ``<out>/baseline_preds.parquet.provenance.json`` — the sidecar the
  GP miner and the OOS evaluator both REQUIRE before a baseline may
  key the campaign's only incremental criterion.

Every check below refuses rather than exports: a baseline that
silently carries holdout rows, mixed commits, a dirty tree, a
tampered fold pickle, or a non-frozen ensemble semantics would
invalidate every downstream fitness score and OOS verdict.

Ignition (operator): run the walk-forward FIRST in one uninterrupted
invocation, then this exporter. Neither is auto-run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pickle
import re
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

PROTOCOL_ID = "pv_incremental_v1"
PLAN_PATH = _REPO_ROOT / "docs" / "prereg" / "pv_incremental.yaml"


class PVBaselineError(RuntimeError):
    """Domain error: the baseline export refuses."""


def load_frozen_plan(path: Path = PLAN_PATH) -> dict[str, Any]:
    """Load + verify the frozen plan (same discipline as the
    evaluator): foreign protocol or an unblinded holdout refuses."""
    plan = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict):
        raise PVBaselineError(f"frozen plan {path} is not a mapping.")
    if plan.get("protocol_id") != PROTOCOL_ID:
        raise PVBaselineError(
            f"frozen plan carries protocol_id "
            f"{plan.get('protocol_id')!r} — refusing.")
    if plan.get("holdout_unblinded") is not False:
        raise PVBaselineError(
            "frozen plan holdout_unblinded is not False — the baseline "
            "must be generated under a BLINDED holdout; refusing.")
    return plan


def check_run_provenance(agg_report: dict[str, Any]) -> dict[str, Any]:
    """The baseline run's git provenance must be clean and singular.

    A resumed run mixing commits resolves ``git_commit`` to null (the
    engine warns), and a dirty tree means the exported numbers cannot
    be reproduced from any commit. Both refuse: the ledger entry would
    be unfalsifiable.
    """
    commit = agg_report.get("git_commit")
    dirty = agg_report.get("git_dirty")
    if not isinstance(commit, str) or not re.fullmatch(
            r"[0-9a-f]{40}", commit):
        raise PVBaselineError(
            f"walk-forward report git_commit={commit!r} is not a full "
            "40-hex sha — a resumed run mixing commits resolves to "
            "null; re-run ALL folds in ONE invocation; refusing.")
    if dirty is not False:
        raise PVBaselineError(
            f"walk-forward report git_dirty={dirty!r} — the baseline "
            "must come from a clean tree so the ledger entry is "
            "reproducible; refusing.")
    return {"source_git": commit, "source_git_dirty": False}


def check_fold_windows(plan: dict[str, Any],
                       folds: list[dict[str, Any]]) -> dict[str, Any]:
    """Window discipline for the baseline run itself.

    Every fold's TEST window (the out-of-fold rows being exported)
    must lie inside the campaign's IS ∪ OOS span. A fold touching the
    blinded holdout year or the forbidden 2026 period refuses — that
    is the sacred invariant, and a baseline carrying such rows would
    smuggle them into every downstream correlation.
    """
    w = plan["windows"]
    lo, hi = w["is_start"], w["oos_end"]
    offending = [
        f"fold {f['fold_index']}: {f['test_start']}..{f['test_end']}"
        for f in folds
        if not (lo <= f["test_start"] <= hi and lo <= f["test_end"] <= hi)
    ]
    if offending:
        raise PVBaselineError(
            f"fold test windows outside the campaign span {lo}..{hi}: "
            f"{offending} — the blinded {w['holdout_year']} holdout and "
            f"the forbidden period from {w['forbidden_from']} must "
            "never enter the baseline; fix the preset's overall_end and "
            "re-run; refusing.")
    return {"span_start": lo, "span_end": hi}


def resolve_fold_reports(run_dir: Path,
                         agg: dict[str, Any]) -> list[tuple[int, Path]]:
    """Resolve the AUTHORITATIVE fold list from the aggregate report.

    Globbing the directory is not evidence (codex #401 r2): a run dir
    missing one declared fold while carrying a stale
    ``fold_99_report.json`` from another run has the same file COUNT,
    so a count check passes and the stray fold is exported — keying
    downstream orthogonality to rows the run never declared. The
    aggregate report's ``folds[]`` is the record of what the run
    actually produced; every entry must exist, its payload's
    ``fold_index`` must match, and nothing outside the list may be
    exported.
    """
    declared = agg.get("folds")
    if not isinstance(declared, list) or not declared:
        raise PVBaselineError(
            "walk_forward_report.json carries no folds[] list — cannot "
            "establish which folds this run produced; refusing.")
    resolved: list[tuple[int, Path]] = []
    seen: set[int] = set()
    for entry in declared:
        if not isinstance(entry, dict) or "fold_index" not in entry:
            raise PVBaselineError(
                f"malformed folds[] entry {entry!r}; refusing.")
        idx = int(entry["fold_index"])
        if idx in seen:
            raise PVBaselineError(
                f"aggregate report declares fold {idx} twice; refusing.")
        seen.add(idx)
        # Prefer the declared path; fall back to the canonical name so
        # a run dir moved wholesale still resolves.
        candidates = []
        declared_path = entry.get("report_path")
        if isinstance(declared_path, str) and declared_path:
            candidates.append(Path(declared_path))
            candidates.append(run_dir / Path(declared_path).name)
        candidates.append(run_dir / f"fold_{idx:02d}_report.json")
        report_path = next((c for c in candidates if c.is_file()), None)
        if report_path is None:
            raise PVBaselineError(
                f"aggregate report declares fold {idx} but none of "
                f"{[str(c) for c in candidates]} exists — incomplete run "
                "directory; refusing.")
        resolved.append((idx, report_path))
    # Anything on disk beyond the declared set is a leftover from
    # another run: refuse rather than quietly ignore it, because the
    # same directory is the evidence base for the ledger entry.
    on_disk = {int(p.name.split("_")[1])
               for p in run_dir.glob("fold_*_report.json")}
    stray = sorted(on_disk - seen)
    if stray:
        raise PVBaselineError(
            f"run directory carries fold reports {stray} that the "
            "aggregate report does not declare — leftovers from another "
            "run; export from a clean directory; refusing.")
    return resolved


def _load_fold(run_dir: Path, report_path: Path,
               expected_index: int) -> dict[str, Any]:
    """Read one fold's predictions, verifying the pickle against the
    sha256 the fold report recorded at write time."""
    report = json.loads(report_path.read_text(encoding="utf-8"))
    fold_index = int(report["fold_index"])
    if fold_index != expected_index:
        raise PVBaselineError(
            f"{report_path.name} carries fold_index={fold_index} but the "
            f"aggregate report declares {expected_index} at this slot — "
            "mismatched/stale fold report; refusing.")
    ens = report.get("ensemble") or {}
    declared_sha = ens.get("prediction_artifact_sha256")
    if not isinstance(declared_sha, str) or not re.fullmatch(
            r"[0-9a-f]{64}", declared_sha):
        raise PVBaselineError(
            f"fold {fold_index}: report carries no valid "
            f"prediction_artifact_sha256 ({declared_sha!r}) — cannot "
            "verify the pickle; refusing.")
    pred_path = run_dir / f"fold_{fold_index:02d}_predictions.pkl"
    if not pred_path.is_file():
        raise PVBaselineError(
            f"fold {fold_index}: missing {pred_path.name}; refusing.")
    raw = pred_path.read_bytes()
    actual_sha = hashlib.sha256(raw).hexdigest()
    if actual_sha != declared_sha:
        raise PVBaselineError(
            f"fold {fold_index}: {pred_path.name} digests to "
            f"{actual_sha[:12]}… but the report recorded "
            f"{declared_sha[:12]}… — the artifact changed after the "
            "run; refusing.")
    scores = pickle.loads(raw)
    if not isinstance(scores, pd.Series) or scores.empty:
        raise PVBaselineError(
            f"fold {fold_index}: predictions are not a non-empty "
            f"Series ({type(scores).__name__}); refusing.")
    if list(scores.index.names) != ["datetime", "instrument"]:
        raise PVBaselineError(
            f"fold {fold_index}: unexpected index names "
            f"{list(scores.index.names)}; refusing.")
    windows = report["windows"]
    test_start = str(windows["test"]["start"])[:10]
    test_end = str(windows["test"]["end"])[:10]
    # Trust the ROWS, not only the declaration (codex #401 r1): a
    # report can declare an in-range test window while the pickle
    # carries extra rows — those would pass the sha check (it verifies
    # the file is unmodified since the run, not that the run's
    # geometry was right) and land blinded/forbidden predictions in
    # the baseline. Verify every exported date against the fold's own
    # declared window.
    row_dates = pd.to_datetime(
        scores.index.get_level_values("datetime")).normalize()
    lo, hi = pd.Timestamp(test_start), pd.Timestamp(test_end)
    stray = sorted({str(d)[:10] for d in row_dates[(row_dates < lo)
                                                   | (row_dates > hi)]})
    if stray:
        raise PVBaselineError(
            f"fold {fold_index}: prediction rows dated {stray[:3]} fall "
            f"outside the fold's own test window {test_start}.."
            f"{test_end} — the artifact's geometry disagrees with its "
            "report; refusing (a stray blinded/forbidden row must "
            "never reach the baseline).")
    return {
        "fold_index": fold_index,
        "scores": scores,
        "test_start": test_start,
        "test_end": test_end,
        "ensemble_window": ens.get("window"),
        "prediction_artifact_sha256": declared_sha,
    }


def check_ensemble_semantics(plan: dict[str, Any],
                             folds: list[dict[str, Any]]) -> int:
    """Every fold must carry the FROZEN ensemble window (decision ②:
    the baseline is the production-equivalent warm ensemble, not a
    single-model output)."""
    frozen = int(plan["fitness"]["baseline"]["ensemble_window"])
    mismatched = [
        f"fold {f['fold_index']}: {f['ensemble_window']!r}"
        for f in folds if f["ensemble_window"] != frozen
    ]
    if mismatched:
        raise PVBaselineError(
            f"ensemble window must be the frozen {frozen} on every "
            f"fold (decision ②: production-equivalent warm ensemble); "
            f"got {mismatched}; refusing.")
    return frozen


def assemble_wide(folds: list[dict[str, Any]]) -> pd.DataFrame:
    """Concatenate per-fold out-of-fold predictions into one wide
    (date × instrument) frame.

    Fold test windows are disjoint by construction (step == test
    months), so a repeated (date, instrument) pair means overlapping
    folds or a duplicated directory — refuse rather than let one fold
    silently overwrite another.
    """
    stacked = pd.concat([f["scores"] for f in folds])
    dupes = stacked.index[stacked.index.duplicated()]
    if len(dupes) > 0:
        raise PVBaselineError(
            f"{len(dupes)} duplicated (date, instrument) rows across "
            f"folds (e.g. {list(dupes[:3])}) — overlapping folds or a "
            "mixed run directory; refusing.")
    wide = stacked.unstack("instrument").sort_index()
    wide.index = pd.to_datetime(wide.index)
    if wide.empty:
        raise PVBaselineError("assembled baseline frame is empty.")
    return wide


def build_sidecar(plan: dict[str, Any], *, wide: pd.DataFrame,
                  file_sha256: str, run_config_rel: str,
                  run_config_sha256: str, provenance: dict[str, Any],
                  ensemble_window: int, run_dir: str,
                  folds: list[dict[str, Any]]) -> dict[str, Any]:
    """The provenance sidecar the miner and the OOS evaluator verify.

    ``model`` / ``file_sha256`` / ``run_config_sha256`` / ``source_git``
    are the four fields both consumers require; the rest is disclosure
    — notably the IS coverage gap that operator decision A accepted
    (the baseline keeps the production fold geometry, so its first
    out-of-fold date is later than the IS window start).
    """
    w = plan["windows"]
    is_days = int(((wide.index >= w["is_start"])
                   & (wide.index <= w["is_end"])).sum())
    oos_days = int(((wide.index >= w["oos_start"])
                    & (wide.index <= w["oos_end"])).sum())
    return {
        "protocol_id": PROTOCOL_ID,
        "model": plan["fitness"]["baseline"]["model"],
        "file_sha256": file_sha256,
        "run_config": run_config_rel,
        "run_config_sha256": run_config_sha256,
        "source_git": provenance["source_git"],
        "source_git_dirty": provenance["source_git_dirty"],
        "walk_forward_run_dir": run_dir,
        "ensemble_window": ensemble_window,
        "n_folds": len(folds),
        "fold_prediction_sha256": {
            str(f["fold_index"]): f["prediction_artifact_sha256"]
            for f in folds
        },
        "coverage": {
            # Decision A disclosure: the IS window starts before the
            # first out-of-fold date, so IS coverage is partial BY
            # DESIGN — recorded here so no reader mistakes it for a
            # data defect or assumes full-window orthogonality.
            "is_window": [w["is_start"], w["is_end"]],
            "oos_window": [w["oos_start"], w["oos_end"]],
            "first_baseline_date": str(wide.index.min())[:10],
            "last_baseline_date": str(wide.index.max())[:10],
            "is_days_covered": is_days,
            "oos_days_covered": oos_days,
            "is_coverage_policy":
                plan["fitness"]["orthogonality"]["is_coverage_policy"],
        },
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run-dir", required=True,
                   help="Completed walk-forward run directory.")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--run-config",
                   default="config/presets/pv_incremental_baseline.yaml",
                   help="Repo-relative preset that drove the run.")
    args = p.parse_args(argv)

    try:
        plan = load_frozen_plan()
        run_dir = Path(args.run_dir)
        agg_path = run_dir / "walk_forward_report.json"
        if not agg_path.is_file():
            raise PVBaselineError(
                f"{agg_path} not found — point --run-dir at a COMPLETED "
                "walk-forward run; refusing.")
        agg = json.loads(agg_path.read_text(encoding="utf-8"))
        provenance = check_run_provenance(agg)

        # Authoritative fold list from the aggregate report — never
        # the directory listing (codex #401 r2).
        resolved = resolve_fold_reports(run_dir, agg)
        folds = [_load_fold(run_dir, rp, idx) for idx, rp in resolved]
        declared_folds = agg.get("num_folds")
        if isinstance(declared_folds, int) and declared_folds != len(folds):
            raise PVBaselineError(
                f"walk-forward report declares num_folds="
                f"{declared_folds} but folds[] resolved {len(folds)} "
                "entries — inconsistent aggregate report; refusing.")
        ensemble_window = check_ensemble_semantics(plan, folds)
        check_fold_windows(plan, folds)
        wide = assemble_wide(folds)
        # Defence in depth after assembly: per-fold row checks already
        # ran, so anything out of span here means a fold declared a
        # window outside the campaign that slipped both gates. Refuse
        # before writing a single byte.
        w = plan["windows"]
        span_lo, span_hi = pd.Timestamp(w["is_start"]), pd.Timestamp(
            w["oos_end"])
        out_of_span = sorted(
            {str(d)[:10] for d in wide.index
             if d < span_lo or d > span_hi})
        if out_of_span:
            raise PVBaselineError(
                f"assembled baseline carries rows outside "
                f"{w['is_start']}..{w['oos_end']} (e.g. "
                f"{out_of_span[:3]}) — blinded holdout / forbidden "
                "period rows must never be exported; refusing.")

        config_path = _REPO_ROOT / args.run_config
        if not config_path.is_file():
            raise PVBaselineError(
                f"run config {config_path} not found; refusing.")
        run_config_sha = hashlib.sha256(
            config_path.read_bytes()).hexdigest()

        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_parquet = out_dir / "baseline_preds.parquet"
        if out_parquet.exists():
            raise PVBaselineError(
                f"{out_parquet} already exists — exports never "
                "overwrite (a silently replaced baseline would "
                "invalidate every score keyed to the old one); use a "
                "fresh --out-dir; refusing.")
        wide.to_parquet(out_parquet)
        file_sha = hashlib.sha256(out_parquet.read_bytes()).hexdigest()

        sidecar = build_sidecar(
            plan, wide=wide, file_sha256=file_sha,
            run_config_rel=args.run_config,
            run_config_sha256=run_config_sha, provenance=provenance,
            ensemble_window=ensemble_window, run_dir=str(run_dir),
            folds=folds)
        (out_dir / "baseline_preds.parquet.provenance.json").write_text(
            json.dumps(sidecar, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")

        cov = sidecar["coverage"]
        print(f"[pv-baseline] exported {out_parquet}")
        print(f"[pv-baseline] shape={wide.shape} folds={len(folds)} "
              f"ensemble_window={ensemble_window}")
        print(f"[pv-baseline] span {cov['first_baseline_date']}.."
              f"{cov['last_baseline_date']} | IS days covered="
              f"{cov['is_days_covered']} OOS days covered="
              f"{cov['oos_days_covered']}")
        print(f"[pv-baseline] file_sha256={file_sha}")
        print("[pv-baseline] register BOTH the intent and this result "
              "in docs/prereg/pv_incremental_ledger.yaml before the "
              "baseline keys any GP run.")
    except PVBaselineError as exc:
        print(f"[pv-baseline] REFUSED: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
