from __future__ import annotations

import hashlib
import json
import math
import warnings
from collections.abc import Mapping
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path, PureWindowsPath
from typing import TYPE_CHECKING, Any

from src.core._json_utils import _sanitize_for_json
from src.core.logger import get_logger
from src.core.walk_forward._types import WalkForwardFold
from src.core.walk_forward.config import WalkForwardError

if TYPE_CHECKING:
    from src.core.canonical_backtest_contract import CanonicalBacktestOutput
    from src.core.model_trainer import ModelTrainResult
    from src.core.performance_attribution import AttributionResult
    from src.core.signal_analyzer import SignalAnalysisResult
    from src.core.walk_forward.config import WalkForwardConfig

_logger = get_logger(__name__)

# NOTE: git provenance (git_commit / git_dirty) comes from the shared
# src.core.git_provenance and is captured by each ENGINE at RUN START (not at report-write
# time — a run can take hours / resume, and HEAD can advance mid-run), then injected here.
# Two engines, one schema: pipeline_report.json records the same fields the same way.


def build_aggregate_report(
    *,
    config: WalkForwardConfig,
    folds: list[WalkForwardFold],
    aggregate_metrics: Mapping[str, float],
    git_provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the aggregate JSON report dict.

    Schema:

    - ``config``: full ``WalkForwardConfig`` snapshot so the run is
      reproducible from the report alone (no peeking at ``config.yaml``).
    - ``folds``: list of compact per-fold summaries (``fold_index``,
      test period, headline metrics, path to the per-fold report).
      Mirrors what dashboards typically render in a fold-level table.
    - ``aggregate_metrics``: cross-fold aggregates from
      ``_compute_aggregate``.
    - ``num_folds``, ``generated_at``: provenance.
    - ``git_commit``, ``git_dirty``: provenance of the CODE that produced
      the run, for the run-comparison pre-registration gate (both ``None``
      when the caller does not supply ``git_provenance`` — e.g. a synthetic
      report — or when git is unavailable). Purely additive.
    """
    gp = git_provenance or {}
    return {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "git_commit": gp.get("commit"),
        "git_dirty": gp.get("dirty"),
        "config": asdict(config),
        "folds": [
            {
                "fold_index": f.fold_index,
                "train_period": f.train_period,
                "valid_period": f.valid_period,
                "test_period": f.test_period,
                "ic_1d": f.ic_1d,
                "ic_5d": f.ic_5d,
                "annualized_return": f.annualized_return,
                "max_drawdown": f.max_drawdown,
                "information_ratio": f.information_ratio,
                "prediction_shape": list(f.prediction_shape),
                # POSIX form (codex #379 P1): the campaign verifiers
                # (pair/attach/certify) resolve this value's basename on
                # any OS — a Windows-separator string is one giant path
                # component on POSIX and never resolves. PureWindowsPath
                # (codex #380 r1) splits BOTH separators on any OS, so a
                # fold resumed from a pre-fix Windows manifest is also
                # normalized; a plain Path would keep the backslashes
                # when this code runs on POSIX. None (failed /
                # placeholder folds) passes through untouched.
                "report_path": (
                    PureWindowsPath(f.report_path).as_posix()
                    if f.report_path is not None else None),
                # FU-4 per-fold timing. ``None`` for folds resumed
                # from a pre-timing manifest or constructed without
                # going through the engine; dashboards should treat
                # missing values as "not measured" rather than 0.
                "duration_seconds": f.duration_seconds,
                "started_at": f.started_at,
                "finished_at": f.finished_at,
            }
            for f in folds
        ],
        "aggregate_metrics": dict(aggregate_metrics),
        "test_window_coverage": compute_test_window_coverage(folds),
        "num_folds": len(folds),
    }


def write_aggregate_report(
    *,
    path: Path,
    config: WalkForwardConfig,
    folds: list[WalkForwardFold],
    aggregate_metrics: Mapping[str, float],
    git_provenance: Mapping[str, Any] | None = None,
) -> None:
    """Build and persist the aggregate JSON report.

    ``git_provenance`` must be captured by the CALLER at RUN START (the engine
    does this next to ``started_at``), NOT at write time: a walk-forward run
    takes hours and can be resumed, so a write-time capture would attribute the
    fold artifacts to a HEAD that advanced mid-run — and the pre-registration
    ancestor gate could mistake a plan committed mid-run for one predating the
    run (codex P1 on #313). A caller that omits it writes null fields and the
    gate fails loud on that run.

    Same NaN handling as ``_write_fold_report`` — the aggregate
    metrics include ``mean_ic_1d`` etc. which are intentionally NaN
    when no fold produced a valid IC, and ``json.dump(..., allow_nan=False)``
    on a sanitised payload turns those into ``null`` rather than the
    non-standard ``NaN`` token.
    """
    report = build_aggregate_report(
        config=config, folds=folds, aggregate_metrics=aggregate_metrics,
        git_provenance=git_provenance,
    )
    sanitised = _sanitize_for_json(report)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            sanitised, f, indent=2, ensure_ascii=False,
            default=str, allow_nan=False,
        )


def write_positions(
    path: Path,
    positions: Mapping[str, Mapping[str, float]],
) -> str:
    """Persist the per-day portfolio weights produced by the backtest
    and return the sha256 of the PERSISTED bytes (attestation digest).

    Mirrors ``Pipeline.run`` step 7b: no ``default=str`` fallback —
    the contract is ``{date_str: {instrument: float}}`` and a leak of
    any other type should surface here at write-time, not weeks later
    in a dashboard.

    The digest is computed by re-reading the file AFTER the write —
    "stamp what was written" — so it is a content binding of the bytes
    on disk, not of an in-memory serialization that could diverge from
    them (2026-07-17-csi800-cadence-campaign DP-5).
    """
    # NaN-safe via ``_sanitize_for_json`` + ``allow_nan=False`` —
    # same convention as the per-fold and aggregate reports. A
    # leaked non-finite weight would otherwise produce the
    # non-standard ``NaN`` JSON token that strict parsers reject.
    # ``ensure_ascii=False`` matches the per-fold / aggregate report
    # writers in this file (lines ~90 and ~365); without it,
    # instrument identifiers with CJK characters would round-trip
    # through ``\uXXXX`` escapes that downstream readers have to
    # decode. (bug.md P3-27 — consistency with sibling write methods.)
    sanitised = _sanitize_for_json(dict(positions))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sanitised, f, indent=2, ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compute_test_window_coverage(folds: list[WalkForwardFold]) -> dict[str, Any]:
    """Summarise test-period continuity, gaps, and overlaps.

    The diagnostics are informational: sparse or overlapping walk-forward
    schedules are valid operator choices, but aggregate consumers should not
    have to infer those caveats from raw period strings.
    """
    if not folds:
        return {
            "mode": "none",
            "gap_count": 0,
            "max_gap_days": 0,
            "overlap_count": 0,
            "max_overlap_days": 0,
            "max_overlap_depth": 0,
        }

    periods = sorted(_parse_test_period(f.test_period) for f in folds)
    gap_count = 0
    max_gap_days = 0
    overlap_count = 0
    max_overlap_days = 0

    for (_, prev_end), (next_start, next_end) in zip(
        periods, periods[1:], strict=False,
    ):
        if next_start > prev_end + timedelta(days=1):
            gap_days = (next_start - prev_end).days - 1
            gap_count += 1
            max_gap_days = max(max_gap_days, gap_days)
        elif next_start <= prev_end:
            overlap_days = (min(prev_end, next_end) - next_start).days + 1
            overlap_count += 1
            max_overlap_days = max(max_overlap_days, overlap_days)

    max_overlap_depth = _max_overlap_depth(periods)
    if gap_count and overlap_count:
        mode = "mixed"
    elif overlap_count:
        mode = "overlapping"
    elif gap_count:
        mode = "gapped"
    else:
        mode = "continuous"

    return {
        "mode": mode,
        "gap_count": gap_count,
        "max_gap_days": max_gap_days,
        "overlap_count": overlap_count,
        "max_overlap_days": max_overlap_days,
        "max_overlap_depth": max_overlap_depth,
    }


def _parse_test_period(period: str) -> tuple[date, date]:
    parts = [part.strip() for part in period.split("~", maxsplit=1)]
    if len(parts) != 2:
        raise WalkForwardError(
            f"Invalid fold test_period {period!r}; expected 'YYYY-MM-DD ~ YYYY-MM-DD'."
        )
    try:
        start = date.fromisoformat(parts[0])
        end = date.fromisoformat(parts[1])
    except ValueError as exc:
        raise WalkForwardError(
            f"Invalid fold test_period {period!r}; expected ISO dates."
        ) from exc
    if start > end:
        raise WalkForwardError(
            f"Invalid fold test_period {period!r}; start date is after end date."
        )
    return start, end


def _max_overlap_depth(periods: list[tuple[date, date]]) -> int:
    events: list[tuple[date, int]] = []
    for start, end in periods:
        events.append((start, 1))
        events.append((end + timedelta(days=1), -1))

    active = 0
    max_depth = 0
    for _, delta in sorted(events, key=lambda item: (item[0], item[1])):
        active += delta
        max_depth = max(max_depth, active)
    return max_depth


# Bumped whenever the fold-report SCHEMA changes in a way that affects
# comparability. Written into every fold report AND folded into the resume
# fingerprint (_resume.py), so a run resumed across this boundary re-runs the
# stale folds instead of silently mixing schemas in one aggregate.
# History: "2-daily-series" added the daily_series substrate;
# "3-sleeve-turnover" added the explicit ``sleeve_turnover`` field
# (CSI800 guard-2, codex P2 on #370 — pre-change folds must regenerate,
# not coexist without the field);
# "4-positions-attestation" added ``positions_sha256`` — the producer
# stamps a content digest of the PERSISTED positions bytes so downstream
# certification can bind the series to the fold report
# (2026-07-17-csi800-cadence-campaign DP-5, #373 codex r10 prerequisite).
FOLD_REPORT_SCHEMA_VERSION = "4-positions-attestation"


def _ic_series_to_map(series: Any) -> dict[str, float]:
    """Serialize a daily-IC pandas Series (date index) to ``{date_str: float}``,
    dropping non-finite days (an NaN IC day carries no signal and would otherwise be
    sanitized to null; dropping keeps a clean date->float map for paired comparison)."""
    out: dict[str, float] = {}
    if series is None or not hasattr(series, "items"):
        return out
    for k, v in series.items():
        fv = float(v)
        if not math.isfinite(fv):
            continue
        out[str(k.date()) if hasattr(k, "date") else str(k)] = fv
    return out


def _daily_series_block(
    signal_result: SignalAnalysisResult,
    backtest_output: CanonicalBacktestOutput,
) -> dict[str, Any]:
    """The run-comparison SUBSTRATE (add-run-comparison-methodology): the per-day
    excess-return series ``return − bench − cost`` — the exact series
    ``BacktestRunner`` feeds to ``risk_analysis`` for the ``excess_return_with_cost``
    scalar, so it reconciles LOSSLESSLY back to this fold's ``information_ratio`` /
    ``max_drawdown`` — plus its components and the per-period daily IC series.

    Purely ADDITIVE: the existing ``signal_analysis`` / ``backtest`` / ``metrics``
    blocks are untouched, so a re-run's pre-existing fields are byte-identical."""
    rs = backtest_output.return_series or {}
    missing = [c for c in ("return", "bench", "cost") if c not in rs]
    if missing:
        raise WalkForwardError(
            f"backtest_output.return_series is missing required channel(s) {missing}: "
            "cannot persist the run-comparison daily_series substrate. Fail loud at the "
            "producer boundary rather than shipping an empty/all-null excess that would "
            "hide a non-comparable fold (codex P2)."
        )
    ret = {str(d): float(v) for d, v in dict(rs["return"]).items()}
    bench = {str(d): float(v) for d, v in dict(rs["bench"]).items()}
    cost = {str(d): float(v) for d, v in dict(rs["cost"]).items()}
    # Match the canonical scalar semantics EXACTLY: BacktestRunner feeds
    # ``risk_analysis`` a pandas union-index subtraction return-bench-cost, where a
    # day missing from bench/cost is NaN (which risk_analysis drops). Emit NaN for a
    # gap day rather than masking it as 0.0, so the persisted excess reconciles to
    # the fold scalar for ANY producer, not only the single-DataFrame live path
    # (where the three share one index). NaN -> null via _sanitize_for_json.
    excess = {
        d: (ret[d] - bench[d] - cost[d]) if (d in bench and d in cost) else float("nan")
        for d in ret
    }
    ic = {
        str(period): _ic_series_to_map(series)
        for period, series in signal_result.ic_series.items()
    }
    return {
        "excess_return": excess,
        "components": {"return": ret, "bench": bench, "cost": cost},
        "ic": ic,
    }


def build_fold_report(
    *,
    fold_index: int,
    train_start: str, train_end: str,
    valid_start: str, valid_end: str,
    test_start: str, test_end: str,
    model_artifact_path: str,
    model_result: ModelTrainResult,
    signal_result: SignalAnalysisResult,
    backtest_output: CanonicalBacktestOutput,
    positions_path: Path | None,
    positions_sha256: str | None = None,
    ic_1d: float, ic_5d: float,
    annualized_return: float, max_drawdown: float,
    information_ratio: float,
    attribution_result: AttributionResult | None = None,
    attribution_skipped_reason: str | None = None,
    ensemble_meta: Mapping[str, Any] | None = None,
    sleeve_turnover: Mapping[str, Mapping[str, float]] | None = None,
) -> dict[str, Any]:
    """Build the per-fold report dict.

    Extracted from :meth:`_write_fold_report` so the schema is unit-
    testable without touching the filesystem (mirrors the same split
    already in use for ``Pipeline._attribution_to_report_dict``).

    ``ensemble_meta`` (when supplied by :meth:`_run_single_fold`)
    carries the cross-fold averaging audit trail produced by
    :meth:`_maybe_apply_ensemble`. It always lands on the report
    under ``"ensemble"`` so the downstream comparison tooling
    (PR #29 walk-forward-compare) sees a uniform shape across
    ``ensemble_window=1`` runs (``used=False, n_models=1``) and
    ensembled runs.
    """
    # ``ic_summary`` is keyed by int (forward period); JSON keys must
    # be strings, so coerce up front.
    ic_summary_serialised = {
        str(period): dict(stats)
        for period, stats in signal_result.ic_summary.items()
    }
    return {
        "fold_index": fold_index,
        "windows": {
            "train": {"start": train_start, "end": train_end},
            "valid": {"start": valid_start, "end": valid_end},
            "test":  {"start": test_start,  "end": test_end},
        },
        "model": {
            "artifact_path": model_artifact_path,
            "best_iteration": model_result.best_iteration,
            "final_valid_loss": model_result.final_valid_loss,
            "prediction_shape": list(model_result.prediction_shape),
        },
        "signal_analysis": {
            "ic_summary": ic_summary_serialised,
            "ic_decay": list(signal_result.ic_decay),
            "turnover_stats": dict(signal_result.turnover_stats),
        },
        "backtest": {
            "metric_status": backtest_output.metric_status,
            "official_backtest_path": backtest_output.official_backtest_path,
            "report": dict(backtest_output.report),
            "risk_analysis": dict(backtest_output.risk_analysis),
            "provenance": dict(backtest_output.provenance),
        },
        "metrics": {
            "ic_1d": ic_1d,
            "ic_5d": ic_5d,
            "annualized_return": annualized_return,
            "max_drawdown": max_drawdown,
            "information_ratio": information_ratio,
        },
        # Run-comparison substrate (add-run-comparison-methodology). Purely additive;
        # reconciles losslessly to metrics.information_ratio / max_drawdown.
        "daily_series": _daily_series_block(signal_result, backtest_output),
        # Self-describing schema marker: lets the comparison layer detect a
        # pre-daily_series (non-comparable) report, and gates resume (see _resume.py).
        "schema_version": FOLD_REPORT_SCHEMA_VERSION,
        # Always emit the attribution block — same convention as
        # ``Pipeline._attribution_section``: ``status`` / ``skipped_reason``
        # are present whether or not the engine ran, so downstream
        # comparison tools see a uniform shape.
        "attribution": attribution_section_for_fold(
            attribution_result, attribution_skipped_reason,
        ),
        # CSI800 guard-2 (codex P1 on #370): per-sleeve one-way turnover
        # from the fold's authoritative positions — the turnover veto is
        # evaluated from run artifacts, so it must live here. ``None``
        # when sleeve grouping is off (schema stays explicit, never
        # silently absent-vs-zero ambiguous).
        "sleeve_turnover": (
            {k: dict(v) for k, v in sleeve_turnover.items()}
            if sleeve_turnover is not None else None
        ),
        # Default the ensemble block to a "no-op" shape when the caller
        # did not supply meta — this preserves report compatibility for
        # any test that constructs a report directly without going
        # through ``_run_single_fold``.
        "ensemble": (
            dict(ensemble_meta)
            if ensemble_meta is not None
            else {
                "window": 1,
                "used": False,
                "n_models": 1,
                "contributing_folds": [fold_index],
                "contributing_model_refs": [],
                "prior_models_attempted": 0,
                "prior_models_loaded": 0,
                "prior_models_index_mismatched": 0,
                "rejected_priors": [],
            }
        ),
        # POSIX form (codex #379 P1) — same portability constraint and
        # same both-separator normalization (codex #380 r1) as the
        # aggregate's fold-row report_path.
        "positions_path": (
            PureWindowsPath(positions_path).as_posix()
            if positions_path else None),
        # Attestation digest of the PERSISTED positions bytes (schema
        # "4-positions-attestation"): explicit None when no positions
        # were produced — absence-vs-null must be distinguishable.
        "positions_sha256": positions_sha256,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
    }


def attribution_section_for_fold(
    attribution_result: AttributionResult | None,
    skipped_reason: str | None,
) -> dict[str, Any]:
    """Build the per-fold attribution block.

    Mirrors :meth:`Pipeline._attribution_section` so the same
    downstream consumers (``walk-forward-compare`` PR #29,
    dashboards) read the same shape regardless of which engine
    produced the report.
    """
    if attribution_result is None:
        return {
            "status": "skipped",
            "skipped_reason": skipped_reason or "unknown_reason",
        }
    return {
        "status": "ok",
        "skipped_reason": None,
        "sector_taxonomy": attribution_result.sector_taxonomy,
        "attribution_method": attribution_result.attribution_method,
        "bench_weight_method": attribution_result.bench_weight_method,
        # WHERE the weights came from (codex P2 #332): without this, fold
        # reports conflate automatic PIT $circ_mv weights with
        # caller-supplied ones under the same market_cap label.
        "bench_weight_source": attribution_result.bench_weight_source,
        "total_portfolio_return": attribution_result.total_portfolio_return,
        "total_benchmark_return": attribution_result.total_benchmark_return,
        "total_excess_return": attribution_result.total_excess_return,
        "allocation_effect": attribution_result.total_allocation_effect,
        "selection_effect": attribution_result.total_selection_effect,
        "interaction_effect": attribution_result.total_interaction_effect,
        "sector_effects_sum": attribution_result.sector_effects_sum,
        "reconciliation_residual": attribution_result.reconciliation_residual,
        "sector_attribution": [
            {
                "sector": s.sector,
                "portfolio_weight": s.portfolio_weight,
                "benchmark_weight": s.benchmark_weight,
                "allocation_effect": s.allocation_effect,
                "selection_effect": s.selection_effect,
                "total_effect": s.total_effect,
            }
            for s in attribution_result.sector_attribution
        ],
    }


def write_fold_report(
    *,
    report_path: Path,
    **kwargs: Any,
) -> None:
    """Build and persist a per-fold report at ``report_path``.

    NaN-safe: routes through :func:`_sanitize_for_json` and uses
    ``allow_nan=False`` so any leaked non-finite float surfaces as
    an error rather than silently producing non-standard JSON
    (browsers, ``jq``, strict parsers reject the bare ``NaN`` token).
    """
    report = build_fold_report(**kwargs)
    sanitised = _sanitize_for_json(report)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(
            sanitised, f, indent=2, ensure_ascii=False,
            default=str, allow_nan=False,
        )


def extract_cost_metrics(
    risk_analysis: Mapping[str, Any],
    fold_index: int,
) -> tuple[float, float, float]:
    """Extract ``(annualized_return, max_drawdown, information_ratio)``
    from a qlib ``risk_analysis`` dict, raising loudly on any shape mismatch.

    Old code used ``cost_metrics.get("annualized_return", 0.0)``, which
    meant any qlib output shape change — or a normalizer that routed
    malformed data into ``{"raw": ...}`` — silently turned every fold
    into a zero-return run. We now require the three metrics to be
    present as floats and raise if not.
    """
    if "excess_return_with_cost" not in risk_analysis:
        raise WalkForwardError(
            f"Fold {fold_index}: backtest risk_analysis has no "
            f"'excess_return_with_cost' block. Available top-level keys: "
            f"{sorted(risk_analysis.keys())}. qlib output shape may "
            "have changed."
        )
    cost_metrics = risk_analysis["excess_return_with_cost"]
    if not isinstance(cost_metrics, dict):
        raise WalkForwardError(
            f"Fold {fold_index}: 'excess_return_with_cost' is "
            f"{type(cost_metrics).__name__}, expected dict. The backtest "
            "normalizer may have failed to parse the DataFrame."
        )
    required_metrics = ("annualized_return", "max_drawdown", "information_ratio")
    missing_metrics = [m for m in required_metrics if m not in cost_metrics]
    if missing_metrics:
        raise WalkForwardError(
            f"Fold {fold_index}: risk_analysis['excess_return_with_cost'] "
            f"is missing {missing_metrics}. Keys present: "
            f"{sorted(cost_metrics.keys())}. qlib output shape may have "
            "changed; refusing to substitute 0.0 for missing metrics."
        )
    return (
        float(cost_metrics["annualized_return"]),
        float(cost_metrics["max_drawdown"]),
        float(cost_metrics["information_ratio"]),
    )


def compute_aggregate(
    folds: list[WalkForwardFold], *, seed: int = 42,
) -> dict[str, Any]:
    """Compute aggregate metrics across all folds, NaN-safe.

    SignalAnalyzer now surfaces "no valid IC" as ``NaN`` rather than
    silently coercing to 0.0 (P2c, batch 6). With plain ``np.mean``,
    a single NaN fold poisons every downstream aggregate — the user
    would see ``mean_ic_1d=NaN`` across an entire walk-forward study
    because one fold happened to have too-short validation data to
    compute cross-sectional IC.

    The fix is "skip-but-disclose":

    - Aggregates are computed with ``np.nan{mean,std,min}`` so NaN
      folds are excluded rather than propagated.
    - A companion ``valid_folds_<metric>`` count is written into the
      result so the caller can tell a 5/5 study apart from a 1/5
      study. Same-shape output as before (all floats), but with
      explicit provenance on how many folds fed each statistic.
    - If *every* fold is NaN for a metric, the aggregator still
      returns ``NaN`` for that metric (``np.nanmean`` of all-NaN is
      NaN by numpy convention) — a loud signal rather than a false
      zero.
    """
    import numpy as np

    if not folds:
        return {}

    ic_1d = np.asarray([f.ic_1d for f in folds], dtype=float)
    ic_5d = np.asarray([f.ic_5d for f in folds], dtype=float)
    returns = np.asarray([f.annualized_return for f in folds], dtype=float)
    drawdowns = np.asarray([f.max_drawdown for f in folds], dtype=float)
    irs = np.asarray([f.information_ratio for f in folds], dtype=float)


    def _nan_agg(arr: np.ndarray[Any, Any], fn: Any) -> float:
        """np.nan{mean,std,min}(arr) with the all-NaN-slice
        RuntimeWarning silenced — NaN is exactly the result we want
        in those cases, the warning would just be noise.
        """
        if not arr.size:
            return float("nan")
        with np.errstate(invalid="ignore"), warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            return float(fn(arr))

    def _nanmean(arr: np.ndarray[Any, Any]) -> float:
        return _nan_agg(arr, np.nanmean)

    def _nanstd(arr: np.ndarray[Any, Any]) -> float:
        return _nan_agg(arr, np.nanstd)

    def _nanmin(arr: np.ndarray[Any, Any]) -> float:
        return _nan_agg(arr, np.nanmin)

    def _valid(arr: np.ndarray[Any, Any]) -> int:
        return int(np.count_nonzero(~np.isnan(arr)))

    def _bootstrap_mean_ci(
        arr: np.ndarray[Any, Any],
        *,
        n_boot: int = 10000,
        ci: float = 0.95,
        seed: int = 42,
    ) -> tuple[float, float]:
        """95% bootstrap CI for the sample mean.

        Folds are designed non-overlapping (window boundaries never
        share the same calendar month), so ``block_size=1`` (standard
        i.i.d. bootstrap) is appropriate.  If a future change
        introduces overlap (``step_months < test_months``) this
        function should be re-tuned with ``block_size`` to match the
        maximum overlap depth.

        Returns ``(NaN, NaN)`` when fewer than 2 finite observations
        are available — a single-fold CI is not meaningful.
        """
        finite = arr[~np.isnan(arr)]
        if finite.size < 2:
            return float("nan"), float("nan")
        rng = np.random.default_rng(seed)
        boots = rng.choice(
            finite, size=(n_boot, finite.size), replace=True
        ).mean(axis=1)
        lo = float(np.percentile(boots, 100 * (1 - ci) / 2))
        hi = float(np.percentile(boots, 100 * (1 + ci) / 2))
        return lo, hi

    ci_ic_1d_lo, ci_ic_1d_hi = _bootstrap_mean_ci(ic_1d, seed=seed)
    ci_ic_5d_lo, ci_ic_5d_hi = _bootstrap_mean_ci(ic_5d, seed=seed)
    ci_ir_lo, ci_ir_hi = _bootstrap_mean_ci(irs, seed=seed)
    ci_ret_lo, ci_ret_hi = _bootstrap_mean_ci(returns, seed=seed)

    # Per-fold timing aggregates. ``None`` values come from folds
    # resumed from a pre-timing manifest, or from unit tests that
    # construct ``WalkForwardFold`` directly without going through
    # the engine — both are legitimate, so we filter rather than
    # propagate NaN.
    durations = [
        f.duration_seconds for f in folds
        if f.duration_seconds is not None
    ]
    if durations:
        mean_fold_duration_seconds = float(np.mean(durations))
        total_duration_seconds = float(np.sum(durations))
        # Identify the slowest fold by index. We want to know
        # "fold 5 took 12 min" not just "the slowest fold took
        # 12 min" so the operator can drill into that fold's report.
        slowest_idx = max(
            (i for i, f in enumerate(folds) if f.duration_seconds is not None),
            key=lambda i: folds[i].duration_seconds or 0.0,
        )
        slowest_fold_index = folds[slowest_idx].fold_index
        # ``durations`` was built from folds with non-None
        # ``duration_seconds`` (see ``max`` filter above), so the
        # assert holds; spelling it out narrows ``float | None`` →
        # ``float`` for mypy.
        slowest_duration = folds[slowest_idx].duration_seconds
        assert slowest_duration is not None
        slowest_fold_duration_seconds = float(slowest_duration)
    else:
        mean_fold_duration_seconds = float("nan")
        total_duration_seconds = float("nan")
        slowest_fold_index = -1
        slowest_fold_duration_seconds = float("nan")
    # ``timing`` sub-dict mirrors pipeline's
    # ``metrics["timing"]`` namespace so shared consumers can do
    # ``report["timing"]["total_duration_seconds"]`` uniformly.
    # Codex P1 on PR #163: previously the keys lived flat at the
    # ``aggregate_metrics`` top level, which created cross-engine
    # schema drift (pipeline had no equivalent). Walk-forward-
    # specific keys (``mean_fold_duration_seconds``, ``slowest_*``,
    # ``valid_folds_duration``) live here too — pipeline reports
    # them as absent rather than as degenerate "1 fold" values.
    timing_block = {
        "total_duration_seconds": total_duration_seconds,
        "mean_fold_duration_seconds": mean_fold_duration_seconds,
        "slowest_fold_index": slowest_fold_index,
        "slowest_fold_duration_seconds": slowest_fold_duration_seconds,
        "valid_folds_duration": len(durations),
    }

    return {
        "mean_ic_1d": _nanmean(ic_1d),
        "std_ic_1d": _nanstd(ic_1d),
        "mean_ic_1d_ci_low": ci_ic_1d_lo,
        "mean_ic_1d_ci_high": ci_ic_1d_hi,
        "valid_folds_ic_1d": _valid(ic_1d),
        "mean_ic_5d": _nanmean(ic_5d),
        "std_ic_5d": _nanstd(ic_5d),
        "mean_ic_5d_ci_low": ci_ic_5d_lo,
        "mean_ic_5d_ci_high": ci_ic_5d_hi,
        "valid_folds_ic_5d": _valid(ic_5d),
        "mean_annualized_return": _nanmean(returns),
        "mean_annualized_return_ci_low": ci_ret_lo,
        "mean_annualized_return_ci_high": ci_ret_hi,
        "valid_folds_annualized_return": _valid(returns),
        "worst_drawdown": _nanmin(drawdowns),
        "valid_folds_max_drawdown": _valid(drawdowns),
        "mean_information_ratio": _nanmean(irs),
        "std_information_ratio": _nanstd(irs),
        "mean_information_ratio_ci_low": ci_ir_lo,
        "mean_information_ratio_ci_high": ci_ir_hi,
        "valid_folds_information_ratio": _valid(irs),
        "num_folds": len(folds),
        "bootstrap_seed": seed,
        "bootstrap_n": 10000,
        # Timing — added by FU-4 (per-fold timing). Nested under a
        # ``timing`` sub-dict (Codex P1 on PR #163) so the same key
        # path works across pipeline + walk-forward reports.
        "timing": timing_block,
    }
