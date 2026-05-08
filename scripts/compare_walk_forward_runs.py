"""Compare two walk-forward runs side by side.

Reads ``walk_forward_report.json`` from each run dir and prints:

* Per-fold IC, return, IR, drawdown, ensemble n_models — paired by
  ``test_period``.
* Aggregate metric deltas.
* The ensemble audit trail (which folds contributed to which fold's
  averaged predictions) — only meaningful for the second run, but
  showing the column with all 1's for the baseline makes the contrast
  obvious.

Usage::

    python scripts/compare_walk_forward_runs.py \
        output/walk_forward_industry \
        output/walk_forward_industry_n3

Stdlib-only (no pandas) so this stays runnable in environments that
don't carry the full data-science stack.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def _load_aggregate(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "walk_forward_report.json"
    if not path.exists():
        raise SystemExit(f"Missing aggregate report: {path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _load_fold(run_dir: Path, fold_index: int) -> dict[str, Any]:
    """Load ``fold_NN_report.json``. Returns ``{}`` if absent (the
    aggregate may reference folds whose per-fold report write was
    interrupted; we still want the comparison to render the rows)."""
    path = run_dir / f"fold_{fold_index:02d}_report.json"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


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


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2

    baseline_dir = Path(sys.argv[1])
    treatment_dir = Path(sys.argv[2])

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

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
