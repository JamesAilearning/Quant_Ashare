"""pv_incremental_v1 full-batch FWER + tri-state adjudication.

Consumes the per-candidate artifacts ``pv_incremental_eval.py``
persisted and applies the FROZEN FWER mechanism (PV-DP-5):

  * family = every OOS-evaluated candidate whose daily-IC series has
    at least ``per_trial_min_n_days`` observations — sparser trials
    fall OUT of the family and are reported honestly WITHOUT
    adjudicating (the Gate-4A lesson: sparse block resampling fattens
    the null tail and inflates the bar for everyone else);
  * block bootstrap (``block_len_days``) of each family member's
    DEMEANED daily series, ``n_boot`` resamples; per resample the
    family MAX |t| forms the null; the bar = its ``quantile``;
  * dual threshold: a candidate survives iff observed t >=
    max(hard_floor_t, bootstrap bar) AND its orthogonality stayed
    within the frozen OOS hard band (the incremental criterion —
    standalone significance is not a pass);
  * tri-state verdict (frozen): survivors -> promote_gate;
    no survivors with a non-sparse family -> clean_negative
    (= reject_iff); only-sparse-signals -> no_verdict.

Protocol binding: artifacts with ``protocol_id != pv_incremental_v1``
are refused (never silently skipped); the verdict artifact carries
the plan digest and every input artifact's sha256. FAIL/negative
artifacts are never deleted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PLAN_PATH = PROJECT_ROOT / "docs" / "prereg" / "pv_incremental.yaml"
PROTOCOL_ID = "pv_incremental_v1"


class PVFwerError(RuntimeError):
    """Classified refusal."""


def load_plan(path: Path = PLAN_PATH) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    plan = yaml.safe_load(raw.decode("utf-8"))
    if not isinstance(plan, dict) or plan.get("protocol_id") != PROTOCOL_ID:
        raise PVFwerError(f"frozen plan is not {PROTOCOL_ID}: {path}")
    if plan.get("holdout_unblinded") is not False:
        raise PVFwerError("holdout_unblinded is not false — refusing.")
    return plan, hashlib.sha256(raw).hexdigest()


def t_stat(series: np.ndarray[Any, np.dtype[np.float64]]) -> float:
    n = series.shape[0]
    if n < 2:
        return float("nan")
    sd = float(series.std(ddof=1))
    if sd <= 0:
        return float("nan")
    return float(series.mean() / (sd / np.sqrt(n)))


def block_bootstrap_bar(family: dict[str, pd.Series], *, n_boot: int,
                        block_len: int, quantile: float,
                        seed: int) -> float:
    """q-quantile of the null max-|t| across the family — JOINT
    moving-block bootstrap (codex #399 r2, the Gate-4A template's
    semantics): every resample draws ONE set of block positions on
    the family's union date axis and applies the SAME positions to
    every member, preserving family co-movement. Independent per-
    member draws would break the dependence structure the
    max-statistic bar exists to respect and can flip verdicts for
    clusters of correlated expressions.

    Members are aligned on the union axis (NaN where a member lacks
    a day — legitimate: ts-window warmups differ per expression);
    each member's t is computed over its non-NaN picks of the shared
    draw. Series are DEMEANED per member (null: zero mean).
    Deterministic under the frozen seed."""
    rng = np.random.default_rng(seed)
    axis = sorted(set().union(*(set(s.index) for s in family.values())))
    n = len(axis)
    matrix = np.full((n, len(family)), np.nan)
    for j, (_, series) in enumerate(sorted(family.items())):
        aligned = series.reindex(axis)
        matrix[:, j] = (aligned - aligned.mean()).to_numpy(dtype=float)
    n_blocks = int(np.ceil(n / block_len))
    maxima = np.empty(n_boot)
    for b in range(n_boot):
        starts = rng.integers(0, n, size=n_blocks)      # ONE draw
        idx = ((starts[:, None] + np.arange(block_len)[None, :]) % n
               ).ravel()[:n]
        picked = matrix[idx, :]                          # shared axis
        best = 0.0
        for j in range(picked.shape[1]):
            col = picked[:, j]
            col = col[np.isfinite(col)]
            t = t_stat(col)
            if np.isfinite(t):
                best = max(best, abs(t))
        maxima[b] = best
    return float(np.quantile(maxima, quantile))


def adjudicate(plan: dict[str, Any],
               artifacts: list[dict[str, Any]],
               *, seed: int) -> dict[str, Any]:
    fw = plan["fwer"]
    min_n = int(fw["per_trial_min_n_days"])
    family: dict[str, pd.Series] = {}
    sparse: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    for art in artifacts:
        if art.get("protocol_id") != PROTOCOL_ID:
            raise PVFwerError(
                f"artifact {art.get('candidate_id')!r} carries "
                f"protocol_id {art.get('protocol_id')!r} — foreign "
                "protocol, refusing (never silently skipped).")
        series = pd.Series(
            {d["date"]: float(d["ic"]) for d in art["daily_ic"]},
            dtype=float).sort_index()
        if not np.isfinite(series.to_numpy()).all():
            # Defense in depth (codex #399 r2): the evaluator drops
            # degenerate days, so a NaN here is artifact corruption —
            # it would silently fall out of per-draw maxima and lower
            # the family bar. Refuse, never sanitize.
            raise PVFwerError(
                f"artifact {art['candidate_id']!r} carries non-finite "
                "daily IC values — corrupt artifact, refusing.")
        row = {"candidate_id": art["candidate_id"],
               "n_days": int(series.shape[0]),
               "ic_mean": art["ic_mean"], "t_stat": art["t_stat"],
               "orth_mean_abs_rho": art["orth_mean_abs_rho"],
               "orth_within_hard_band": art["orth_within_hard_band"]}
        if series.shape[0] < min_n:
            row["family_member"] = False
            sparse.append(row)
        else:
            row["family_member"] = True
            family[art["candidate_id"]] = series
        rows.append(row)

    if family:
        bar = block_bootstrap_bar(
            family, n_boot=int(fw["n_boot"]),
            block_len=int(fw["block_len_days"]),
            quantile=float(fw["quantile"]), seed=seed)
    else:
        bar = float("nan")
    floor = float(fw["hard_floor_t"])
    threshold = max(floor, bar) if np.isfinite(bar) else floor

    cleared_t = [
        r["candidate_id"] for r in rows
        if r["family_member"] and np.isfinite(r["t_stat"])
        and r["t_stat"] >= threshold]
    survivors = [
        r["candidate_id"] for r in rows
        if r["candidate_id"] in cleared_t
        and r["orth_within_hard_band"]]
    if survivors:
        verdict = "survivors"
    elif cleared_t:
        # Signal exists but is NOT incremental (codex #399 r1): this
        # is neither a promotion nor a clean closure of the direction
        # — the frozen plan routes it to an operator decision, and it
        # must never be laundered into clean_negative's reject_iff.
        verdict = "significant_non_incremental"
    elif family:
        verdict = "clean_negative"
    else:
        verdict = "no_verdict"

    return {
        "protocol_id": PROTOCOL_ID,
        "verdict": verdict,
        "threshold": {"hard_floor_t": floor,
                      "bootstrap_bar": bar if np.isfinite(bar) else None,
                      "binding": threshold},
        "survivors": survivors,
        "significant_non_incremental": [c for c in cleared_t
                                        if c not in survivors],
        "family_size": len(family),
        "sparse_excluded": [r["candidate_id"] for r in sparse],
        "trials": rows,
        "seed": seed,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--artifacts-dir", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--seed", type=int, default=20260805,
                   help="Frozen bootstrap seed (deterministic bar).")
    args = p.parse_args(argv)
    try:
        plan, plan_sha = load_plan()
        paths = sorted(Path(args.artifacts_dir).glob("*.json"))
        if not paths:
            raise PVFwerError(
                f"no candidate artifacts in {args.artifacts_dir}")
        artifacts, shas = [], {}
        for path in paths:
            raw = path.read_bytes()
            artifacts.append(json.loads(raw.decode("utf-8")))
            shas[path.name] = hashlib.sha256(raw).hexdigest()
        result = adjudicate(plan, artifacts, seed=args.seed)
        result["plan_sha256"] = plan_sha
        result["input_sha256"] = shas
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(result, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        print(f"[pv-fwer] verdict={result['verdict']} "
              f"threshold={result['threshold']['binding']:.3f} "
              f"survivors={result['survivors']} -> {out}")
    except PVFwerError as exc:
        print(f"[pv-fwer] REFUSED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
