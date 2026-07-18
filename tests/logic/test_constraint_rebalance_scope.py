"""Constraint scope under a non-daily cadence (revision R1, 2026-07-18).

The N5 ignition (2026-07-18, result-blind) hit a 0.04-0.05pp
``max_per_name`` overage on a single HOLD day — market drift, not an
allocation decision. Validating hold days makes the same numeric cap
strictly harsher under N=5 than under N=1 (whose daily rebalance resets
weights before every check), breaking same-config comparability. The
revision scopes constraint validation to REBALANCE-EFFECT days
(thinned stamp + total lag); numbers, RAISE mode and every veto stay
untouched.

Coverage matrix (>=1 case per dimension):
  fill mapping    — stamp + lag trading days, calendar-driven.
  edge handling   — stamps off-calendar skipped; fills beyond the
                    calendar end skipped.
  scope filtering — only scoped days reach the constraint check
                    (drift-day violation ignored; scoped-day violation
                    still RAISEs).
  N=1 identity    — the daily path passes the FULL map (byte-identical
                    behaviour is exercised by the existing #336
                    contract tests; here we pin the helper contract).
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.core.backtest_runner import BacktestRunner  # noqa: E402
from src.core.risk_constraints import (  # noqa: E402
    MinimalRiskConstraints,
    RiskConstraintError,
)

_CAL = [date(2024, 1, d) for d in (2, 3, 4, 5, 8, 9, 10, 11, 12, 15)]


def test_fill_mapping_stamp_plus_lag() -> None:
    days = BacktestRunner._constraint_scope_days(
        [date(2024, 1, 2), date(2024, 1, 9)], _CAL, lag=1)
    assert days == {"2024-01-03", "2024-01-10"}


def test_off_calendar_stamp_and_overflow_fill_skipped() -> None:
    days = BacktestRunner._constraint_scope_days(
        [date(2024, 1, 6),      # not a trading day -> skipped
         date(2024, 1, 15)],    # fill would fall beyond calendar end
        _CAL, lag=1)
    assert days == set()


def test_scope_filtering_ignores_drift_day_violation() -> None:
    # a violation on a HOLD day is drift; the same violation on a
    # rebalance-effect day still RAISEs.
    constraints = MinimalRiskConstraints()  # defaults: max_per_name 0.05
    positions = {
        "2024-01-03": {"SH600000": 0.04, "SZ000001": 0.04},   # scoped, ok
        "2024-01-04": {"SH600000": 0.09, "SZ000001": 0.04},   # drift day
    }
    scope = BacktestRunner._constraint_scope_days(
        [date(2024, 1, 2)], _CAL, lag=1)
    scoped = {d: w for d, w in positions.items() if d in scope}
    assert set(scoped) == {"2024-01-03"}
    constraints.apply(scoped)          # drift-day violation not checked
    with pytest.raises(RiskConstraintError):
        constraints.apply(positions)   # unscoped map still raises


def test_multi_cycle_scope_covers_every_rebalance() -> None:
    # N=5 over ten trading days -> two stamps -> two fill days.
    stamps = [_CAL[0], _CAL[5]]
    days = BacktestRunner._constraint_scope_days(stamps, _CAL, lag=1)
    assert days == {"2024-01-03", "2024-01-10"}


def test_masked_out_stamp_day_excluded_from_scope() -> None:
    # codex #378 r1: a scheduled stamp fully removed by the pre-strategy
    # masks emits no signal — its would-be fill day is drift-only and
    # must not be scoped. The wiring feeds the FINAL post-mask shifted
    # stamps (+1 trading-day qlib shift); with the masked stamp absent
    # from that index, no scope day is derived for it.
    surviving_shifted_stamps = [date(2024, 1, 2)]       # 01-09 masked out
    days = BacktestRunner._constraint_scope_days(
        surviving_shifted_stamps, _CAL, lag=1)
    assert days == {"2024-01-03"}


def test_merge_clipped_preserves_hold_days() -> None:
    # codex #378 r1: positions_clipped keeps its full-map contract —
    # clipped scoped days overlaid, hold days retained.
    full = {
        "2024-01-03": {"SH600000": 0.09, "SZ000001": 0.04},
        "2024-01-04": {"SH600000": 0.10, "SZ000001": 0.04},  # hold day
    }
    clipped_scoped = {"2024-01-03": {"SH600000": 0.05, "SZ000001": 0.04}}
    merged = BacktestRunner._merge_clipped(full, clipped_scoped)
    assert merged["2024-01-03"]["SH600000"] == 0.05
    assert merged["2024-01-04"] == full["2024-01-04"]
    assert set(merged) == set(full)


def test_pipeline_report_config_mirrors_risk_constraint_scope() -> None:
    # codex #378 r4: walk-forward artifacts disclose the scope through
    # asdict(config); the pipeline's CURATED config projection must
    # mirror the key so an official pipeline run can prove it used the
    # canonical all_days semantics (two engines, one schema).
    import json
    import tempfile
    from types import SimpleNamespace

    from src.core.pipeline import Pipeline
    from src.core.signal_analyzer import SignalAnalysisResult

    config = SimpleNamespace(
        instruments="csi300", feature_handler="alpha158",
        label_horizon_days=1,
        train_start="2022-01-01", train_end="2022-12-31",
        valid_start="2023-01-01", valid_end="2023-03-31",
        test_start="2023-04-01", test_end="2023-06-30",
        model_type="LGBModel", benchmark_code="SH000300",
        topk=50, n_drop=5, industry_taxonomy_id=None,
        attribution_sleeve_grouping=False,
        risk_constraints_enabled=False,
        risk_constraints_calibration="default",
        risk_constraint_scope="all_days",
        delisted_registry_path="",
    )
    feature_result = SimpleNamespace(
        train_shape=(10, 5), valid_shape=(5, 5), test_shape=(5, 5))
    model_result = SimpleNamespace(
        prediction_shape=(5, 1), model_artifact_path="m.pkl")
    signal_result = SignalAnalysisResult(
        ic_summary={1: {"mean_ic": 0.01, "std_ic": 0.02, "ir": 0.5,
                        "num_days": 5}},
        ic_series={}, ic_decay=[0.01],
        turnover_stats={"mean_turnover": 0.1})
    backtest_output = SimpleNamespace(
        metric_status="ok", official_backtest_path="official",
        report={}, provenance={}, risk_analysis={})
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "pipeline_report.json"
        Pipeline._write_report(
            str(path), config, feature_result, model_result,
            signal_result, backtest_output,
            factor_skipped_reason="unit-test",
            git_provenance={"commit": "cafebabe" * 5, "dirty": False})
        data = json.loads(path.read_text(encoding="utf-8"))
    assert data["config"]["risk_constraint_scope"] == "all_days"

    # walk-forward side of the same pin: asdict(config) carries the key.
    from src.core.walk_forward.aggregate import build_aggregate_report
    from src.core.walk_forward.config import WalkForwardConfig

    wf = build_aggregate_report(
        config=WalkForwardConfig(output_dir="output/wf"), folds=[],
        aggregate_metrics={},
        git_provenance={"commit": "x", "dirty": True})
    assert wf["config"]["risk_constraint_scope"] == "all_days"
