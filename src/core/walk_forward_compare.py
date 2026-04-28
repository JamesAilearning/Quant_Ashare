"""Compare two walk-forward runs side by side.

Why this module exists
----------------------
After PR #30 ``WalkForwardEngine`` writes a ``walk_forward_report.json``
per run with the full config snapshot, every fold's headline metrics,
and cross-fold aggregates. That made it possible to iterate on
hyperparameters (PR #32: LGB regularisation tuning) -- but to actually
compare a new run against a baseline, an operator was still doing it
by hand: open both JSONs in a text editor, eyeball the numbers, hope
they read the right column. With even two metric columns x eight folds
that's 32 numbers to track per pair, and silent regressions slip
through (P2 actually had ``mean_information_ratio`` go from -0.18 to
-0.31 -- a real degradation that was easy to miss while celebrating
``best_iteration`` jumping from 6 to 156).

This module ingests two reports and emits a structured diff:

- :class:`ConfigDiff` -- every ``WalkForwardConfig`` field that changed.
- :class:`FoldDiff` -- per-fold metrics with deltas + improvement flag.
- :class:`AggregateDiff` -- same shape for cross-fold aggregates.
- :class:`WalkForwardComparison` -- the whole bundle.

Pure data, no I/O. The CLI in ``scripts/compare_walk_forward.py``
handles file loading and stdout/JSON sinks.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from src.core._json_utils import _sanitize_for_json
from src.core.logger import get_logger

_logger = get_logger(__name__)


class WalkForwardCompareError(ValueError):
    """Raised on structural problems with input reports."""


# ---------------------------------------------------------------------
# Metric direction map: which way is "improvement"?
#
# - higher_is_better: a positive delta is improvement (IC, return, IR).
#   Includes max_drawdown / worst_drawdown -- those are reported as
#   negative numbers, so a *less negative* delta (closer to zero) means
#   the drawdown got smaller in magnitude.
# - lower_is_better: a negative delta is improvement (std_ic_*: lower
#   variance of IC is more stable signal).
#
# Metrics not in this map are reported with a delta but no
# improvement classification -- the consumer can decide.
# ---------------------------------------------------------------------
_HIGHER_IS_BETTER = frozenset({
    "ic_1d", "ic_5d",
    "annualized_return", "information_ratio",
    "max_drawdown",
    "mean_ic_1d", "mean_ic_5d",
    "mean_annualized_return", "mean_information_ratio",
    "worst_drawdown",
})
_LOWER_IS_BETTER = frozenset({
    "std_ic_1d", "std_ic_5d",
})

# Floats whose absolute delta is <= this threshold count as "unchanged"
# in summary counts. Below noise / floating-point fuzz.
_UNCHANGED_EPSILON = 1e-9

# Per-fold metrics rendered in the comparison table. Order matters --
# this is the column order in :func:`format_comparison`.
_FOLD_METRIC_KEYS = (
    "ic_1d", "ic_5d",
    "annualized_return", "max_drawdown", "information_ratio",
)


@dataclass(frozen=True)
class ConfigDiff:
    """A single ``WalkForwardConfig`` field that differs between runs."""

    field: str
    baseline_value: Any
    variant_value: Any


@dataclass(frozen=True)
class MetricDiff:
    """One metric's value pair + signed delta + improvement flag.

    ``improved`` is :data:`None` when the metric is not in the direction
    map (consumer cannot judge from a delta alone). It is :data:`True` /
    :data:`False` only when the direction is known.
    """

    name: str
    baseline: float
    variant: float
    delta: float
    improved: bool | None


@dataclass(frozen=True)
class FoldDiff:
    """Diff for a single (baseline, variant) fold pair.

    ``test_period_match`` is :data:`False` when the baseline and variant
    folds carry different ``test_period`` strings -- this signals the
    two runs covered different OOS windows so the per-metric deltas
    are not strictly comparable. The diffs are still computed; the
    flag exists so dashboards / CLI output can warn loudly.
    """

    fold_index: int
    baseline_test_period: str
    variant_test_period: str
    test_period_match: bool
    metrics: Mapping[str, MetricDiff]


@dataclass(frozen=True)
class AggregateDiff:
    """Cross-fold aggregate metric pair + signed delta + improvement flag."""

    name: str
    baseline: float
    variant: float
    delta: float
    improved: bool | None


@dataclass(frozen=True)
class FoldCountSummary:
    """Counts of folds present on each side, plus how many overlap.

    ``overlap`` is the number of (baseline, variant) pairs the diff
    actually computed. Folds that exist on only one side are reported
    as orphans so the operator notices the runs are not directly
    comparable.
    """

    baseline_folds: int
    variant_folds: int
    overlap: int
    baseline_only_indices: tuple[int, ...]
    variant_only_indices: tuple[int, ...]


@dataclass(frozen=True)
class WalkForwardComparison:
    """The full diff between two walk-forward runs."""

    baseline_path: str
    variant_path: str
    config_diffs: tuple[ConfigDiff, ...]
    fold_diffs: tuple[FoldDiff, ...]
    aggregate_diffs: Mapping[str, AggregateDiff]
    fold_summary: FoldCountSummary


# ---------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------


def load_report(path: str | Path) -> Mapping[str, Any]:
    """Load and shape-check a ``walk_forward_report.json``.

    Reads the file and verifies the top-level keys the comparator relies
    on (``config``, ``folds``, ``aggregate_metrics``). A missing key
    raises here rather than producing a misleading "no diffs" output
    later.
    """
    p = Path(path)
    if not p.exists():
        raise WalkForwardCompareError(f"Report file does not exist: {p}")
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise WalkForwardCompareError(
            f"Report at {p} must be a JSON object; got {type(data).__name__}."
        )
    for required in ("config", "folds", "aggregate_metrics"):
        if required not in data:
            raise WalkForwardCompareError(
                f"Report at {p} is missing required key {required!r}. "
                "Keys present: " + ", ".join(sorted(data.keys()))
            )
    return data


# ---------------------------------------------------------------------
# Core comparison
# ---------------------------------------------------------------------


def _classify_improvement(name: str, delta: float) -> bool | None:
    """Return ``True``/``False`` per the metric direction map, or
    ``None`` if the metric is not classified.

    A delta whose absolute value is below :data:`_UNCHANGED_EPSILON`
    (or NaN delta) returns ``None`` -- neither improved nor degraded.
    """
    if not math.isfinite(delta) or abs(delta) <= _UNCHANGED_EPSILON:
        return None
    if name in _HIGHER_IS_BETTER:
        return delta > 0
    if name in _LOWER_IS_BETTER:
        return delta < 0
    return None


def _safe_float(value: Any) -> float:
    """Coerce a JSON-loaded value to ``float``, mapping ``None`` to NaN.

    ``None`` shows up because the report writer routes NaN/Inf through
    :func:`_sanitize_for_json` (PR #30) so the JSON stays standard. In
    the diff we still want to do arithmetic, so we lift it back to NaN.
    """
    if value is None:
        return float("nan")
    return float(value)


def _diff_metric(name: str, baseline: Any, variant: Any) -> MetricDiff:
    b = _safe_float(baseline)
    v = _safe_float(variant)
    delta = v - b if math.isfinite(v) and math.isfinite(b) else float("nan")
    return MetricDiff(
        name=name, baseline=b, variant=v,
        delta=delta, improved=_classify_improvement(name, delta),
    )


def _diff_configs(
    baseline_cfg: Mapping[str, Any], variant_cfg: Mapping[str, Any],
) -> tuple[ConfigDiff, ...]:
    """List every config field whose value differs.

    Iterates the union of keys so a field present on one side and not
    the other is reported (with the missing side as ``None``). This is
    the only place we tolerate type asymmetry -- every other diff path
    operates on numeric metrics where missing values are NaN.
    """
    keys = sorted(set(baseline_cfg) | set(variant_cfg))
    diffs: list[ConfigDiff] = []
    for key in keys:
        b = baseline_cfg.get(key)
        v = variant_cfg.get(key)
        if b != v:
            diffs.append(ConfigDiff(field=key, baseline_value=b, variant_value=v))
    return tuple(diffs)


def _diff_folds(
    baseline_folds: Sequence[Mapping[str, Any]],
    variant_folds: Sequence[Mapping[str, Any]],
) -> tuple[tuple[FoldDiff, ...], FoldCountSummary]:
    """Pair folds by ``fold_index`` and diff each pair.

    Folds present in only one of the two reports are surfaced via
    :class:`FoldCountSummary` rather than silently dropped. We pair by
    ``fold_index`` (not by list position) so a partial run that skipped
    early folds still aligns correctly with a full run.
    """
    by_idx_b = {int(f["fold_index"]): f for f in baseline_folds}
    by_idx_v = {int(f["fold_index"]): f for f in variant_folds}
    common = sorted(set(by_idx_b) & set(by_idx_v))
    only_b = tuple(sorted(set(by_idx_b) - set(by_idx_v)))
    only_v = tuple(sorted(set(by_idx_v) - set(by_idx_b)))

    fold_diffs: list[FoldDiff] = []
    for idx in common:
        b_fold = by_idx_b[idx]
        v_fold = by_idx_v[idx]
        b_period = str(b_fold.get("test_period", ""))
        v_period = str(v_fold.get("test_period", ""))
        metrics = {
            k: _diff_metric(k, b_fold.get(k), v_fold.get(k))
            for k in _FOLD_METRIC_KEYS
        }
        fold_diffs.append(FoldDiff(
            fold_index=idx,
            baseline_test_period=b_period,
            variant_test_period=v_period,
            test_period_match=(b_period == v_period),
            metrics=metrics,
        ))

    summary = FoldCountSummary(
        baseline_folds=len(baseline_folds),
        variant_folds=len(variant_folds),
        overlap=len(common),
        baseline_only_indices=only_b,
        variant_only_indices=only_v,
    )
    return tuple(fold_diffs), summary


def _diff_aggregates(
    baseline_aggs: Mapping[str, Any], variant_aggs: Mapping[str, Any],
) -> dict[str, AggregateDiff]:
    """Diff every metric present on either side of the aggregates dict.

    A metric present only on one side gets a NaN delta and
    ``improved=None``; the operator can read it as "this metric was
    introduced or removed between the two runs".
    """
    keys = sorted(set(baseline_aggs) | set(variant_aggs))
    out: dict[str, AggregateDiff] = {}
    for key in keys:
        m = _diff_metric(key, baseline_aggs.get(key), variant_aggs.get(key))
        out[key] = AggregateDiff(
            name=key, baseline=m.baseline, variant=m.variant,
            delta=m.delta, improved=m.improved,
        )
    return out


def compare_reports(
    baseline_path: str | Path,
    variant_path: str | Path,
) -> WalkForwardComparison:
    """Load two ``walk_forward_report.json`` files and produce a
    :class:`WalkForwardComparison`.

    Pure read-only -- never writes to disk.
    """
    baseline = load_report(baseline_path)
    variant = load_report(variant_path)

    config_diffs = _diff_configs(
        baseline.get("config", {}) or {},
        variant.get("config", {}) or {},
    )
    fold_diffs, fold_summary = _diff_folds(
        baseline.get("folds", []) or [],
        variant.get("folds", []) or [],
    )
    aggregate_diffs = _diff_aggregates(
        baseline.get("aggregate_metrics", {}) or {},
        variant.get("aggregate_metrics", {}) or {},
    )

    return WalkForwardComparison(
        baseline_path=str(baseline_path),
        variant_path=str(variant_path),
        config_diffs=config_diffs,
        fold_diffs=fold_diffs,
        aggregate_diffs=aggregate_diffs,
        fold_summary=fold_summary,
    )


# ---------------------------------------------------------------------
# Console rendering
# ---------------------------------------------------------------------


def _fmt_value(value: Any) -> str:
    """Compact rendering for config values in the diff table."""
    if value is None:
        return "(absent)"
    if isinstance(value, float):
        return f"{value:.6g}"
    return repr(value)


def _fmt_metric(value: float) -> str:
    """Render a metric, NaN as 'nan' (mirrors logging convention)."""
    if not math.isfinite(value):
        return " nan "
    return f"{value:+.4f}"


def _fmt_delta(delta: float, improved: bool | None) -> str:
    """Render a delta with an ASCII indicator when classified.

    Sticks to ASCII arrows (``[+]`` / ``[-]``) rather than Unicode
    +- so the rendering doesn't fail on Windows consoles whose
    default code page is GBK / cp936 -- the same handler that logs
    the rest of the pipeline output. UnicodeEncodeError there is
    silent in production but a noisy traceback in dev.
    """
    base = _fmt_metric(delta)
    if improved is True:
        return f"{base} [+]"
    if improved is False:
        return f"{base} [-]"
    return f"{base}    "


def format_comparison(comparison: WalkForwardComparison) -> str:
    """Build a multi-line string suitable for stdout / log INFO.

    Kept separate from the I/O so tests can pin the rendering by
    asserting against the returned text.
    """
    lines: list[str] = []
    sep = "=" * 76
    lines.append(sep)
    lines.append("WALK-FORWARD COMPARISON")
    lines.append(f"  baseline: {comparison.baseline_path}")
    lines.append(f"  variant:  {comparison.variant_path}")
    lines.append(sep)

    # Config diffs
    lines.append("")
    lines.append("Configuration changes (variant vs baseline):")
    if not comparison.config_diffs:
        lines.append("  (no config differences)")
    else:
        for diff in comparison.config_diffs:
            lines.append(
                f"  {diff.field}: {_fmt_value(diff.baseline_value)} -> "
                f"{_fmt_value(diff.variant_value)}"
            )

    # Fold count summary
    fs = comparison.fold_summary
    lines.append("")
    lines.append(
        f"Fold coverage: baseline={fs.baseline_folds}, "
        f"variant={fs.variant_folds}, overlap={fs.overlap}"
    )
    if fs.baseline_only_indices:
        lines.append(
            f"  WARNING: folds only in baseline: "
            f"{list(fs.baseline_only_indices)}"
        )
    if fs.variant_only_indices:
        lines.append(
            f"  WARNING: folds only in variant: "
            f"{list(fs.variant_only_indices)}"
        )

    # Per-fold deltas
    lines.append("")
    lines.append("Per-fold metrics (delta = variant - baseline):")
    header = (
        f"  {'Fold':>4} | {'Test period':<23} |"
        + "".join(f" {key:>16} |" for key in _FOLD_METRIC_KEYS)
    )
    lines.append(header)
    lines.append("  " + "-" * (len(header) - 2))
    for fold in comparison.fold_diffs:
        period = fold.baseline_test_period
        if not fold.test_period_match:
            period = f"{period} [!]"
        row = f"  {fold.fold_index:>4} | {period:<23} |"
        for key in _FOLD_METRIC_KEYS:
            m = fold.metrics[key]
            row += f" {_fmt_delta(m.delta, m.improved):>16} |"
        lines.append(row)

    # Per-fold improvement summary
    lines.append("")
    lines.append("Per-fold improvement counts:")
    for key in _FOLD_METRIC_KEYS:
        improved = sum(
            1 for f in comparison.fold_diffs if f.metrics[key].improved is True
        )
        degraded = sum(
            1 for f in comparison.fold_diffs if f.metrics[key].improved is False
        )
        unchanged = len(comparison.fold_diffs) - improved - degraded
        lines.append(
            f"  {key:>20}: {improved} improved, {degraded} degraded, "
            f"{unchanged} unchanged"
        )

    # Aggregate diffs
    lines.append("")
    lines.append("Aggregate metrics (variant vs baseline):")
    for name in sorted(comparison.aggregate_diffs):
        d = comparison.aggregate_diffs[name]
        lines.append(
            f"  {name:>30}: {_fmt_metric(d.baseline)} -> "
            f"{_fmt_metric(d.variant)}  "
            f"delta {_fmt_delta(d.delta, d.improved)}"
        )

    lines.append(sep)
    return "\n".join(lines)


def print_comparison(comparison: WalkForwardComparison) -> None:
    """Log the formatted comparison at INFO level.

    Uses the module logger so output integrates with the rest of the
    pipeline log stream rather than going straight to ``print``.
    """
    for line in format_comparison(comparison).splitlines():
        _logger.info(line)


# ---------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------


def to_dict(comparison: WalkForwardComparison) -> dict[str, Any]:
    """Build the JSON-report dict for a :class:`WalkForwardComparison`.

    Extracted so the JSON contract is unit-testable without touching
    the filesystem (mirrors the same pattern used by
    :func:`walk_forward._build_aggregate_report`).
    """
    return {
        "baseline_path": comparison.baseline_path,
        "variant_path": comparison.variant_path,
        "config_diffs": [
            {
                "field": d.field,
                "baseline_value": d.baseline_value,
                "variant_value": d.variant_value,
            }
            for d in comparison.config_diffs
        ],
        "fold_summary": asdict(comparison.fold_summary),
        "fold_diffs": [
            {
                "fold_index": f.fold_index,
                "baseline_test_period": f.baseline_test_period,
                "variant_test_period": f.variant_test_period,
                "test_period_match": f.test_period_match,
                "metrics": {
                    name: {
                        "baseline": m.baseline,
                        "variant": m.variant,
                        "delta": m.delta,
                        "improved": m.improved,
                    }
                    for name, m in f.metrics.items()
                },
            }
            for f in comparison.fold_diffs
        ],
        "aggregate_diffs": {
            name: {
                "baseline": d.baseline,
                "variant": d.variant,
                "delta": d.delta,
                "improved": d.improved,
            }
            for name, d in comparison.aggregate_diffs.items()
        },
    }


def write_comparison(comparison: WalkForwardComparison, path: str | Path) -> None:
    """Persist the comparison as standard JSON.

    Same NaN handling as the rest of the report-writing surface -- route
    through :func:`_sanitize_for_json` and pass ``allow_nan=False`` so
    any non-finite leak surfaces as an error rather than producing
    non-standard JSON tokens.
    """
    payload = _sanitize_for_json(to_dict(comparison))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            payload, f, indent=2, ensure_ascii=False,
            default=str, allow_nan=False,
        )
