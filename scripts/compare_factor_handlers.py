"""Compare two walk-forward runs side by side.

Reads two ``walk_forward_report.json`` files (typically one Alpha158
baseline + one MinedFactor candidate) and produces a per-metric diff
plus a design-doc-aligned IR threshold flag.

Usage::

    python scripts/compare_factor_handlers.py BASELINE_REPORT CANDIDATE_REPORT \\
        [--out OUTPUT_JSON] [--metrics LIST] [--baseline-label NAME] \\
        [--candidate-label NAME]

The default metric set matches factor_mining_claude_code_design.md §10
success criteria: ``mean_information_ratio``, ``mean_ic_1d``,
``mean_annualized_return``, ``worst_drawdown``.

The ``summary.design_doc_ir_threshold_met`` boolean encodes the
canonical "OOS Sharpe >= 10% vs Alpha158 baseline" rule:
``candidate.mean_information_ratio >= 1.10 * baseline.mean_information_ratio``.

No data access; this is pure JSON arithmetic.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_DEFAULT_METRICS: tuple[str, ...] = (
    "mean_information_ratio",
    "mean_ic_1d",
    "mean_annualized_return",
    "worst_drawdown",
)

_IR_THRESHOLD_MULTIPLIER = 1.10  # design doc §10: ">= 10% above baseline"
_IR_THRESHOLD_METRIC = "mean_information_ratio"


class CompareError(RuntimeError):
    """Raised on malformed reports or unparseable JSON."""


@dataclass(frozen=True)
class MetricDiff:
    baseline: float | None
    candidate: float | None
    abs_delta: float | None
    rel_delta: float | None


def _load_report(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise CompareError(f"walk-forward report does not exist: {path}")
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise CompareError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise CompareError(f"{path}: expected a JSON object, got {type(data).__name__}")
    if "aggregate_metrics" not in data or not isinstance(data["aggregate_metrics"], dict):
        raise CompareError(
            f"{path}: report has no top-level 'aggregate_metrics' object; "
            "is this really a walk_forward_report.json?"
        )
    return data


def _label_from_report(report: dict[str, Any], explicit: str | None) -> str:
    if explicit:
        return explicit
    cfg = report.get("config") or {}
    handler = cfg.get("feature_handler")
    if isinstance(handler, str) and handler.strip():
        return handler.strip()
    return "unknown"


def _diff_metric(
    baseline_metrics: dict[str, Any],
    candidate_metrics: dict[str, Any],
    name: str,
) -> tuple[MetricDiff | None, str | None]:
    """Compute the diff for one metric. Returns ``(None, reason)`` when
    the metric is missing from either side."""
    if name not in baseline_metrics and name not in candidate_metrics:
        return None, "missing_in_both"
    if name not in baseline_metrics:
        return None, "missing_in_baseline"
    if name not in candidate_metrics:
        return None, "missing_in_candidate"
    b = baseline_metrics[name]
    c = candidate_metrics[name]
    if not isinstance(b, (int, float)) or not isinstance(c, (int, float)):
        return None, "non_numeric"
    b_f = float(b)
    c_f = float(c)
    abs_delta = c_f - b_f
    if b_f == 0.0:
        rel_delta = None
    else:
        rel_delta = abs_delta / b_f
    return (
        MetricDiff(baseline=b_f, candidate=c_f, abs_delta=abs_delta, rel_delta=rel_delta),
        None,
    )


def _design_doc_ir_threshold_met(
    baseline_metrics: dict[str, Any], candidate_metrics: dict[str, Any],
) -> bool | None:
    """Encode design doc §10: ``candidate IR >= 1.10 * baseline IR``.

    Returns ``None`` when the IR metric is missing from either side.
    The zero-baseline edge case is treated as "any non-negative
    candidate clears the threshold" (because ``1.10 * 0 == 0``).
    """
    b = baseline_metrics.get(_IR_THRESHOLD_METRIC)
    c = candidate_metrics.get(_IR_THRESHOLD_METRIC)
    if not isinstance(b, (int, float)) or not isinstance(c, (int, float)):
        return None
    return float(c) >= _IR_THRESHOLD_MULTIPLIER * float(b)


def compare(
    baseline_path: Path,
    candidate_path: Path,
    *,
    metrics: tuple[str, ...] = _DEFAULT_METRICS,
    baseline_label: str | None = None,
    candidate_label: str | None = None,
) -> dict[str, Any]:
    """Return the diff manifest as a dict (caller writes JSON if needed)."""
    baseline_report = _load_report(baseline_path)
    candidate_report = _load_report(candidate_path)
    baseline_metrics = baseline_report["aggregate_metrics"]
    candidate_metrics = candidate_report["aggregate_metrics"]

    diffs: dict[str, dict[str, Any]] = {}
    unavailable: dict[str, str] = {}
    candidate_better = 0
    baseline_better = 0
    for m in metrics:
        diff, reason = _diff_metric(baseline_metrics, candidate_metrics, m)
        if diff is None:
            unavailable[m] = reason or "unknown"
            continue
        diffs[m] = {
            "baseline": diff.baseline,
            "candidate": diff.candidate,
            "abs_delta": diff.abs_delta,
            "rel_delta": diff.rel_delta,
        }
        # For `worst_drawdown` a value closer to zero (greater) is
        # better — drawdowns are typically negative.  For IC / IR /
        # annualized_return, larger is better.  In both cases
        # "candidate > baseline" means candidate wins; the comparison
        # is the same after the sign convention is fixed in the
        # underlying metric.
        # ``_diff_metric`` returns ``MetricDiff`` only when both
        # baseline and candidate are numeric (``None`` → diff is None,
        # which we handled above with ``continue``). The narrow here
        # is for mypy; the runtime guarantee is upstream.
        if diff.baseline is None or diff.candidate is None:
            continue
        if diff.candidate > diff.baseline:
            candidate_better += 1
        elif diff.candidate < diff.baseline:
            baseline_better += 1

    threshold_met = _design_doc_ir_threshold_met(baseline_metrics, candidate_metrics)

    return {
        "baseline_report": str(baseline_path),
        "candidate_report": str(candidate_path),
        "baseline_label": _label_from_report(baseline_report, baseline_label),
        "candidate_label": _label_from_report(candidate_report, candidate_label),
        "metrics": diffs,
        "unavailable_metrics": unavailable,
        "summary": {
            "candidate_better_count": candidate_better,
            "baseline_better_count": baseline_better,
            "design_doc_ir_threshold_met": threshold_met,
        },
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_metrics(spec: str | None) -> tuple[str, ...]:
    if spec is None or not spec.strip():
        return _DEFAULT_METRICS
    parts = tuple(p.strip() for p in spec.split(",") if p.strip())
    if not parts:
        return _DEFAULT_METRICS
    return parts


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Diff two walk-forward reports side by side.",
    )
    parser.add_argument("baseline", type=Path, help="path to baseline walk_forward_report.json")
    parser.add_argument("candidate", type=Path, help="path to candidate walk_forward_report.json")
    parser.add_argument(
        "--out", type=Path, default=None,
        help="optional path; if set, writes the JSON manifest there",
    )
    parser.add_argument(
        "--metrics", type=str, default=None,
        help=(
            "comma-separated metric names to compare; defaults to "
            "mean_information_ratio,mean_ic_1d,mean_annualized_return,worst_drawdown"
        ),
    )
    parser.add_argument("--baseline-label", type=str, default=None)
    parser.add_argument("--candidate-label", type=str, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        report = compare(
            args.baseline,
            args.candidate,
            metrics=_parse_metrics(args.metrics),
            baseline_label=args.baseline_label,
            candidate_label=args.candidate_label,
        )
    except CompareError as exc:
        print(f"compare_factor_handlers: {exc}", file=sys.stderr)
        return 1
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, sort_keys=False), encoding="utf-8")
    # ASCII-only summary so Windows cp1252 stdout doesn't blow up
    ir_diff = report["metrics"].get(_IR_THRESHOLD_METRIC, {})
    rel_delta_pct = ir_diff.get("rel_delta")
    rel_str = (
        f"{rel_delta_pct * 100:+.2f}%" if isinstance(rel_delta_pct, (int, float)) else "n/a"
    )
    threshold_met = report["summary"]["design_doc_ir_threshold_met"]
    threshold_str = (
        "PASS" if threshold_met is True else "FAIL" if threshold_met is False else "n/a"
    )
    print(
        f"Compare {report['baseline_label']} vs {report['candidate_label']}: "
        f"IR rel_delta = {rel_str}, design_doc_ir_threshold_met = {threshold_str}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
