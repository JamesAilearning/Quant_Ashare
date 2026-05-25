"""Numeric-tolerance comparison helper for regression baseline tests.

Both ``tests/regression/test_fold0_baseline`` (fold-level backtest
re-run) and ``tests/regression/test_walk_forward_aggregate_baseline``
(full walk-forward aggregate re-run) need the same primitive: "given
``actual`` and ``baseline`` metric dicts, which keys drifted outside
tolerance?". Extracting it here keeps the tolerance semantics
consistent across regression tests + lets the comparison logic be
unit-tested without the E2E suite.

Audit FU-5. No qlib import, no I/O — pure dict arithmetic.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

# Default relative tolerance for headline metrics. ±5% chosen because
# a true model regression typically drops IR / IC by 10%+ while
# numerical noise from data refreshes (Tushare snapshot jitter,
# pandas version bumps, RNG seed drift) sits well below 5%. Operators
# who need a stricter floor can pass ``tolerance=0.02`` etc.
DEFAULT_RELATIVE_TOLERANCE = 0.05

# When the baseline value is near zero, relative tolerance is
# meaningless (``actual / 0`` is undefined / explosive). Fall back
# to absolute tolerance below this threshold.
_NEAR_ZERO_THRESHOLD = 1e-12


def compare_metrics(
    actual: Mapping[str, Any],
    baseline: Mapping[str, Any],
    *,
    tolerance: float = DEFAULT_RELATIVE_TOLERANCE,
    absolute_tolerance: float | None = None,
    keys: tuple[str, ...] | None = None,
) -> list[str]:
    """Compare ``actual`` against ``baseline`` and return human-readable
    drift messages for any metric that broke tolerance.

    Empty list = all checked metrics are within tolerance. The caller
    decides whether to ``fail`` (unittest) / ``assert`` (pytest) /
    log (operator script) on a non-empty result.

    Parameters
    ----------
    actual, baseline
        Metric mappings. Numeric values are compared; non-numeric
        values (strings, lists, mappings) are skipped silently so
        the same baseline JSON can carry both metrics and provenance.
    tolerance
        Relative tolerance for non-near-zero baselines. Default
        ``0.05`` (±5%). A metric drifts when
        ``|actual - baseline| / |baseline| > tolerance``.
    absolute_tolerance
        Falls back to this when the baseline is within
        :data:`_NEAR_ZERO_THRESHOLD` of zero. Defaults to
        ``tolerance`` (so a 5% relative knob also flags 0.05 abs
        drift on a near-zero baseline — same order of magnitude).
    keys
        If supplied, only these metric keys are checked; otherwise
        every numeric key in ``baseline`` is checked. Useful when
        the baseline JSON also carries provenance keys (e.g.
        ``"generated_at"``, ``"bundle_tag"``) that aren't metrics.

    Notes
    -----
    * **Baseline value ``NaN`` is silently skipped.** The convention
      is "NaN baseline = no expectation"; use it for fields whose
      acceptable value depends on context the comparator can't see
      (e.g. ``bootstrap_seed`` doesn't have a "correct" value, just
      a "current" value).
    * **Actual ``NaN`` with non-NaN baseline IS a drift.** A run that
      now reports NaN where the baseline had a real number is a
      regression — the comparator flags it.
    * **Missing keys ARE drifts.** If ``baseline`` has ``mean_ic_1d``
      but ``actual`` doesn't, the comparator flags it as missing.
      The opposite (actual has keys baseline doesn't) is silently
      ignored — new metrics are additive, not regressions.
    """
    abs_tol = tolerance if absolute_tolerance is None else absolute_tolerance
    keys_to_check = (
        tuple(keys) if keys is not None else tuple(baseline.keys())
    )
    drifts: list[str] = []
    for key in keys_to_check:
        if key not in baseline:
            # Caller specified a key that's not even in the baseline;
            # treat as a usage error rather than a drift.
            drifts.append(
                f"{key}: not present in baseline (caller listed it in `keys`)"
            )
            continue
        baseline_value = baseline[key]
        if not _is_numeric(baseline_value):
            continue  # provenance / config fields, not metrics
        if isinstance(baseline_value, float) and math.isnan(baseline_value):
            continue  # explicit "no expectation"
        if key not in actual:
            drifts.append(
                f"{key}: missing from actual (baseline={baseline_value!r})"
            )
            continue
        actual_value = actual[key]
        if not _is_numeric(actual_value):
            drifts.append(
                f"{key}: actual is non-numeric ({type(actual_value).__name__}: "
                f"{actual_value!r}); baseline={baseline_value!r}"
            )
            continue
        if isinstance(actual_value, float) and math.isnan(actual_value):
            drifts.append(
                f"{key}: actual is NaN; baseline={baseline_value!r}"
            )
            continue
        # Both numeric + finite.
        b = float(baseline_value)
        a = float(actual_value)
        if abs(b) < _NEAR_ZERO_THRESHOLD:
            # Absolute-tolerance branch for near-zero baselines.
            drift_abs = abs(a - b)
            if drift_abs > abs_tol:
                drifts.append(
                    f"{key}: actual={a:.6g}, baseline≈0, absolute "
                    f"drift {drift_abs:.6g} > tolerance {abs_tol:.6g}"
                )
        else:
            relative_drift = abs(a - b) / abs(b)
            if relative_drift > tolerance:
                drifts.append(
                    f"{key}: actual={a:.6g}, baseline={b:.6g}, "
                    f"relative drift {relative_drift:.2%} > tolerance "
                    f"{tolerance:.2%}"
                )
    return drifts


def _is_numeric(value: Any) -> bool:
    """True iff ``value`` is a plain numeric type (int/float, but not
    bool — bool is an int subclass and lets through ``True``/``False``
    which are never legitimate metric values)."""
    if isinstance(value, bool):
        return False
    return isinstance(value, (int, float))


__all__ = [
    "DEFAULT_RELATIVE_TOLERANCE",
    "compare_metrics",
]
