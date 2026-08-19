from __future__ import annotations

from copy import deepcopy

from web.operator_ui.pages._research_run_comparison_helpers import (
    assess_comparability,
    build_comparison_run,
    parse_selected_run_ids,
)


def _pipeline_report(*, information_ratio: float = 0.4) -> dict[str, object]:
    return {
        "metric_status": "official",
        "config": {
            "instruments": "csi300",
            "train_period": "2020-01-01 ~ 2022-12-31",
            "valid_period": "2023-01-01 ~ 2023-06-30",
            "test_period": "2023-07-01 ~ 2023-12-31",
            "model_type": "LGBModel",
        },
        "backtest": {
            "provenance": {
                "config_fingerprint": "contract-1",
                "config": {
                    "request": {
                        "benchmark_code": "SH000300TR",
                        "signal_to_execution_lag": 1,
                        "adjust_mode": "pre_adjusted",
                        "exchange_config": {
                            "execution_price_kind": "close",
                            "limit_threshold": 0.095,
                            "cost_model": {
                                "commission_rate": 0.0005,
                                "stamp_tax_schedule": [{"start": "2008-09-19", "rate": 0.001}],
                                "slippage_bps": 5.0,
                                "min_cost": 5.0,
                            },
                        },
                    },
                    "runtime": {
                        "provider_uri": "data/qlib_cn",
                        "region": "cn",
                        "data_adjust_mode": "pre_adjusted",
                    },
                },
            },
        },
        "risk_analysis": {
            "excess_return_with_cost": {
                "annualized_return": 0.12,
                "max_drawdown": -0.08,
                "information_ratio": information_ratio,
            },
        },
    }


def _pipeline_run(run_id: str, *, information_ratio: float = 0.4, report: dict[str, object] | None = None):
    return build_comparison_run(
        run_id=run_id,
        engine="pipeline",
        status="completed",
        created_at="2026-08-19T10:00:00Z",
        config_path=f"output/runs/{run_id}/config.yaml",
        report_path=f"output/runs/{run_id}/pipeline_report.json",
        log_paths=(),
        config={"provider_uri": "data/qlib_cn", "region": "cn", "adjust_mode": "pre_adjusted"},
        report=report if report is not None else _pipeline_report(information_ratio=information_ratio),
    )


def test_complete_equal_pipeline_contract_is_ranked_by_existing_information_ratio() -> None:
    slow = _pipeline_run("run-slow", information_ratio=0.2)
    fast = _pipeline_run("run-fast", information_ratio=0.7)

    result = assess_comparability((slow, fast))

    assert result.eligible is True
    assert result.ranked_run_ids == ("run-fast", "run-slow")
    assert not result.reasons


def test_mismatched_execution_lag_blocks_controlled_ranking() -> None:
    first = _pipeline_run("run-a")
    changed = _pipeline_report()
    request = changed["backtest"]["provenance"]["config"]["request"]  # type: ignore[index]
    request["signal_to_execution_lag"] = 2  # type: ignore[index]
    second = _pipeline_run("run-b", report=changed)

    result = assess_comparability((first, second))

    assert result.eligible is False
    assert result.ranked_run_ids == ()
    assert any("信号至成交滞后不一致" in reason for reason in result.reasons)


def test_missing_runtime_provenance_is_visible_and_blocks_comparison() -> None:
    complete = _pipeline_run("run-complete")
    incomplete_report = deepcopy(_pipeline_report())
    del incomplete_report["backtest"]["provenance"]["config"]["runtime"]  # type: ignore[index]
    incomplete = _pipeline_run("run-incomplete", report=incomplete_report)

    result = assess_comparability((complete, incomplete))

    assert result.eligible is False
    assert any("数据来源 / 运行时快照" in issue.message for issue in incomplete.issues)
    assert any("run-incomplete" in reason for reason in result.reasons)


def test_walk_forward_uses_existing_aggregate_and_fold_evidence_without_recalculation() -> None:
    config = {
        "provider_uri": "data/qlib_cn",
        "region": "cn",
        "adjust_mode": "pre_adjusted",
    }
    report = {
        "metric_status": "official",
        "num_folds": 2,
        "config": {
            "instruments": "csi300",
            "overall_start": "2020-01-01",
            "overall_end": "2023-12-31",
            "train_months": 24,
            "valid_months": 3,
            "test_months": 3,
            "benchmark_code": "SH000300TR",
            "signal_to_execution_lag": 1,
            "execution_price_kind": "close",
            "commission_rate": 0.0005,
            "stamp_tax_schedule": None,
            "slippage_bps": 5.0,
            "min_cost": 5.0,
            "limit_threshold": 0.095,
            "adjust_mode": "pre_adjusted",
            "model_type": "LGBModel",
        },
        "aggregate_metrics": {
            "mean_annualized_return": 0.1,
            "worst_drawdown": -0.12,
            "mean_information_ratio": 0.35,
            "std_information_ratio": 0.11,
            "valid_folds_information_ratio": 2,
        },
        "test_window_coverage": {"mode": "continuous", "gap_count": 0},
        "folds": [{"fold_index": 0, "information_ratio": 0.2}, {"fold_index": 1, "information_ratio": 0.5}],
    }

    run = build_comparison_run(
        run_id="wf-run",
        engine="walk_forward",
        status="completed",
        created_at="",
        config_path="config.yaml",
        report_path="walk_forward_report.json",
        log_paths=(),
        config=config,
        report=report,
    )

    assert run.fold_evidence is not None
    assert run.fold_evidence.mean_information_ratio == 0.35
    assert run.fold_evidence.std_information_ratio == 0.11
    assert run.fold_evidence.folds == tuple(report["folds"])
    assert any(metric.value == 0.35 for metric in run.metrics if "信息比率" in metric.label)
    assert run.data_provenance_source == "config.yaml: provider_uri / region / adjust_mode"


def test_parse_selected_ids_preserves_full_order_after_url_validation() -> None:
    assert parse_selected_run_ids("pipeline.a-1,wf_b.2") == ("pipeline.a-1", "wf_b.2")
