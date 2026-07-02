"""Compare two walk-forward runs side by side.

Reads ``walk_forward_report.json`` from each run dir and prints:

* Per-fold IC, return, IR, drawdown, ensemble n_models — paired by
  ``test_period``.
* Aggregate metric deltas.
* A **run-comparison verdict** from the trustworthy ruler
  (``src.core.comparison``): the pooled IR, the paired moving-block-bootstrap
  95% CI on the daily net-excess difference, a fail-loud three-state verdict,
  the pooling-seam bound, any backtest-vs-IC contradiction, and the
  honesty-envelope caveats. This section needs the per-fold ``daily_series``
  substrate (walk-forward runs from PR-1 onward) and a ``--prereg`` reference;
  without either it prints an ACTIONABLE note and the per-fold table above
  still renders (fail-loud — never a fabricated verdict).

Usage::

    python scripts/compare_walk_forward_runs.py \
        output/walk_forward_industry \
        output/walk_forward_industry_n3 \
        --prereg <hypothesis-commit-hash>

The per-fold table + aggregate deltas are stdlib-only. The verdict section
lazily imports numpy via ``src.core.comparison``; if that import fails (or a
run lacks the daily-series substrate) the table still renders.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _load_aggregate(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "walk_forward_report.json"
    if not path.exists():
        raise SystemExit(f"Missing aggregate report: {path}")
    with open(path, encoding="utf-8") as f:
        loaded: dict[str, Any] = json.load(f)
        return loaded


def _load_fold(run_dir: Path, fold_index: int) -> dict[str, Any]:
    """Load ``fold_NN_report.json``. Returns ``{}`` if absent (the
    aggregate may reference folds whose per-fold report write was
    interrupted; we still want the comparison to render the rows)."""
    path = run_dir / f"fold_{fold_index:02d}_report.json"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        loaded: dict[str, Any] = json.load(f)
        return loaded


def _fmt_float(value: Any, *, pct: bool = False, places: int = 4) -> str:
    if value is None:
        return "  n/a"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return f"{value!r}"
    if pct:
        return f"{v * 100:+.2f}%"
    return f"{v:+.{places}f}"


def _delta(a: Any, b: Any) -> str:
    """Render b - a as a signed float; ``n/a`` if either side missing."""
    if a is None or b is None:
        return "  n/a"
    try:
        return f"{float(b) - float(a):+.4f}"
    except (TypeError, ValueError):
        return "  n/a"


def build_ruler_report(
    baseline_dir: Path,
    treatment_dir: Path,
    *,
    prereg: str | None,
    overlap_floor: float | None = None,
    min_paired_days: int | None = None,
    block_length: int | None = None,
    n_boot: int | None = None,
    seed: int | None = None,
) -> list[str]:
    """Render the trustworthy run-comparison verdict as text lines.

    NEVER raises: a missing ``--prereg``, an unavailable numpy/comparison module,
    a non-comparable substrate (old runs without ``daily_series``), too little
    date overlap, etc. each return an ACTIONABLE note — fail-loud is preserved
    (no fabricated verdict), and the caller can still print the per-fold table.
    """
    title = "RUN-COMPARISON VERDICT (ruler: pooled IR + paired block-bootstrap)"
    if prereg is None or not prereg.strip():
        return [
            title,
            "  skipped — pass --prereg <hypothesis-commit-hash> for a significance",
            "  verdict (every comparison must carry a pre-registered hypothesis).",
        ]
    try:
        from src.core.comparison import (
            DEFAULT_MIN_PAIRED_DAYS,
            DEFAULT_N_BOOT,
            DEFAULT_OVERLAP_FLOOR,
            DEFAULT_SEED,
            ComparisonError,
            compare_runs,
        )
    except ImportError as exc:  # numpy / module unavailable — the table still renders
        return [title, f"  unavailable — {exc} (needs numpy + src.core.comparison)."]

    try:
        r = compare_runs(
            baseline_dir,
            treatment_dir,
            pre_registration_ref=prereg,
            overlap_floor=DEFAULT_OVERLAP_FLOOR if overlap_floor is None else overlap_floor,
            min_paired_days=(
                DEFAULT_MIN_PAIRED_DAYS if min_paired_days is None else min_paired_days
            ),
            block_length=block_length,
            n_boot=DEFAULT_N_BOOT if n_boot is None else n_boot,
            seed=DEFAULT_SEED if seed is None else seed,
        )
    except ComparisonError as exc:  # fail-loud, actionable — never a fabricated verdict
        return [title, "  NO VERDICT (fail-loud):", *(f"    {ln}" for ln in str(exc).splitlines())]

    lo, hi = r.paired_net_ci95
    d = r.diagnostics
    sb = r.seam_bound
    lines = [
        title,
        f"  VERDICT: {r.verdict.upper()}   "
        f"(n_paired={r.n_paired_days}, overlap={r.overlap_fraction:.1%})",
        f"  paired net annualized diff (treatment - baseline): "
        f"{r.paired_net_ann_diff:+.4f}   95% CI [{lo:+.4f}, {hi:+.4f}]   "
        f"SE {r.paired_net_se:.4f}",
        f"  block_length={r.block_length} ({r.block_length_source})",
        "  pooled IR (study-protocol, each run's full OOS series):",
        f"    net   baseline={r.pooled_net_ir_baseline:+.3f}  "
        f"treatment={r.pooled_net_ir_treatment:+.3f}",
        f"    gross baseline={r.pooled_gross_ir_baseline:+.3f}  "
        f"treatment={r.pooled_gross_ir_treatment:+.3f}",
        "  diagnostics:",
        f"    gross IR  baseline={d['gross_ir_baseline']:+.3f}  "
        f"treatment={d['gross_ir_treatment']:+.3f}",
        f"    mean IC   baseline={d['mean_ic_baseline']:+.4f}  "
        f"treatment={d['mean_ic_treatment']:+.4f}   "
        f"(IC verdict: {d['ic_verdict']}, shared_days={d['n_ic_shared_days']})",
        f"    direction: {d['direction']}",
        "  seam bound (pooled net IR, fold-boundary days incl vs excl):",
        f"    baseline  incl={sb['baseline_pooled_net_ir_incl_boundary']:+.3f}  "
        f"excl={sb['baseline_pooled_net_ir_excl_boundary']:+.3f}  "
        f"impact={sb['baseline_seam_impact']:+.3f}",
        f"    treatment incl={sb['treatment_pooled_net_ir_incl_boundary']:+.3f}  "
        f"excl={sb['treatment_pooled_net_ir_excl_boundary']:+.3f}  "
        f"impact={sb['treatment_seam_impact']:+.3f}",
    ]
    if r.verdict == "indistinguishable":
        # the MANDATED companion of an indistinguishable verdict (spec + the comparison
        # tests): 'indistinguishable' is NOT 'equivalent'. Surface it prominently, right
        # under the VERDICT line, so the CLI can't show it stripped of the warning.
        lines.insert(2, f"  NOTE: {d['note']}")
    if r.contradiction_flag:
        lines += ["  ** CONTRADICTION (backtest is authoritative):", f"    {r.contradiction_flag}"]
    lines.append(f"  pre-registration ref: {r.pre_registration_ref}")
    lines.append("  caveats:")
    lines += [f"    - {c}" for c in r.caveats]
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare two walk-forward runs side by side (per-fold table + "
        "aggregate deltas + the trustworthy run-comparison verdict)."
    )
    parser.add_argument("baseline_dir", type=Path, help="baseline run dir (A)")
    parser.add_argument("treatment_dir", type=Path, help="treatment run dir (B)")
    parser.add_argument(
        "--prereg",
        default=None,
        help="git commit hash of the committed pre-registered hypothesis "
        "(required for a significance verdict; every comparison must carry one)",
    )
    parser.add_argument("--overlap-floor", type=float, default=None,
                        help="min date-overlap fraction of the shorter series (default 0.90)")
    parser.add_argument("--min-paired-days", type=int, default=None,
                        help="min shared finite days for the paired bootstrap (default 20)")
    parser.add_argument("--block-length", type=int, default=None,
                        help="moving-block length override (default: ACF-calibrated)")
    parser.add_argument("--n-boot", type=int, default=None,
                        help="bootstrap resamples (default 10000, floor 1000)")
    parser.add_argument("--seed", type=int, default=None, help="bootstrap RNG seed (default 42)")
    args = parser.parse_args(argv)

    baseline_dir: Path = args.baseline_dir
    treatment_dir: Path = args.treatment_dir

    base = _load_aggregate(baseline_dir)
    treat = _load_aggregate(treatment_dir)

    base_folds = {f["test_period"]: f for f in base["folds"]}
    treat_folds = {f["test_period"]: f for f in treat["folds"]}
    test_periods = sorted(set(base_folds) | set(treat_folds))

    print("=" * 110)
    print(
        f"BASELINE : {baseline_dir}  ({base['num_folds']} folds, "
        f"generated {base['generated_at']})"
    )
    print(
        f"TREATMENT: {treatment_dir}  ({treat['num_folds']} folds, "
        f"generated {treat['generated_at']})"
    )
    print("=" * 110)

    # Per-fold table
    header = (
        f"{'fold':<25}"
        f"{'IC1d (b → t)':<26}"
        f"{'Return (b → t)':<28}"
        f"{'IR (b → t)':<24}"
        f"{'n_models (t)':<14}"
    )
    print(header)
    print("-" * len(header))
    for period in test_periods:
        b = base_folds.get(period, {})
        t = treat_folds.get(period, {})
        # Pull ensemble meta from per-fold report (aggregate file is
        # compact and intentionally omits it).
        idx = t.get("fold_index")
        t_fold_report = (
            _load_fold(treatment_dir, idx) if idx is not None else {}
        )
        ensemble = t_fold_report.get("ensemble", {})
        n_models = ensemble.get("n_models", "?")
        contributing = ensemble.get("contributing_folds")

        ic_b = b.get("ic_1d")
        ic_t = t.get("ic_1d")
        ret_b = b.get("annualized_return")
        ret_t = t.get("annualized_return")
        ir_b = b.get("information_ratio")
        ir_t = t.get("information_ratio")

        print(
            f"{period:<25}"
            f"{_fmt_float(ic_b)} → {_fmt_float(ic_t)}  "
            f"({_delta(ic_b, ic_t)})  "
            f"{_fmt_float(ret_b, pct=True)} → {_fmt_float(ret_t, pct=True)}  "
            f"{_fmt_float(ir_b, places=2)} → {_fmt_float(ir_t, places=2)}  "
            f"{n_models}"
            + (f"  {contributing}" if contributing else "")
        )

    # Aggregate metrics
    print("-" * len(header))
    print("AGGREGATE METRICS")
    base_agg = base.get("aggregate_metrics", {})
    treat_agg = treat.get("aggregate_metrics", {})
    keys = sorted(set(base_agg) | set(treat_agg))
    for key in keys:
        b = base_agg.get(key)
        t = treat_agg.get(key)
        print(
            f"  {key:<35} "
            f"baseline={_fmt_float(b)}  treatment={_fmt_float(t)}  "
            f"Δ={_delta(b, t)}"
        )

    # Trustworthy run-comparison verdict (pooled IR + paired block-bootstrap).
    print("-" * len(header))
    for line in build_ruler_report(
        baseline_dir,
        treatment_dir,
        prereg=args.prereg,
        overlap_floor=args.overlap_floor,
        min_paired_days=args.min_paired_days,
        block_length=args.block_length,
        n_boot=args.n_boot,
        seed=args.seed,
    ):
        print(line)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
