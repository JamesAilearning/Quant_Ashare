from __future__ import annotations

from copy import deepcopy

from src.core.canonical_backtest_contract import CANONICAL_OFFICIAL_BACKTEST_PATH
from web.operator_ui.job_io import JobSummary
from web.operator_ui.pages._research_run_comparison_helpers import (
    assess_comparability,
    build_comparison_run,
    duplicate_run_ids,
    parse_selected_run_ids,
    selectable_catalog,
    selectable_catalog_rows,
)


def _pipeline_report(*, information_ratio: float = 0.4) -> dict[str, object]:
    return {
        "metric_status": "official",
        "official_backtest_path": CANONICAL_OFFICIAL_BACKTEST_PATH,
        "config": {
            "instruments": "csi300",
            "train_period": "2020-01-01 ~ 2022-12-31",
            "valid_period": "2023-01-01 ~ 2023-06-30",
            "test_period": "2023-07-01 ~ 2023-12-31",
            "model_type": "LGBModel",
        },
        "backtest": {
            "provenance": {
                "config_fingerprint": "c" * 16,
                "execution_timing_semantics": "lag_total_v2",
                "price_limit_semantics": "close_expr_v1",
                "config": {
                    "benchmark_code": "SH000300TR",
                    "signal_to_execution_lag": 1,
                    "account_config": {"init_cash": 100_000_000.0},
                    "st_mask": {
                        "namechange_sha256": "a" * 16,
                    },
                    "adjust_mode": "pre_adjusted",
                    "exchange_config": {
                        "execution_price_kind": "close",
                        "limit_threshold": 0.095,
                        "cost_model": {
                            "commission_rate": 0.0005,
                            "stamp_tax_schedule": [
                                {"effective_from": "2008-09-19", "bps": 10.0}
                            ],
                            "slippage_bps": 5.0,
                            "min_cost": 5.0,
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
    provenance = changed["backtest"]["provenance"]["config"]  # type: ignore[index]
    provenance["signal_to_execution_lag"] = 2  # type: ignore[index]
    second = _pipeline_run("run-b", report=changed)

    result = assess_comparability((first, second))

    assert result.eligible is False
    assert result.ranked_run_ids == ()
    assert any("信号至成交滞后不一致" in reason for reason in result.reasons)


def test_explicit_execution_semantics_are_required_for_ranking() -> None:
    first = _pipeline_run("run-a")
    changed = _pipeline_report()
    changed["backtest"]["provenance"]["price_limit_semantics"] = "close_expr_v2"  # type: ignore[index]
    second = _pipeline_run("run-b", report=changed)

    result = assess_comparability((first, second))

    assert result.eligible is False
    assert any("执行时序与涨跌停语义指纹不一致" in reason for reason in result.reasons)

    legacy = _pipeline_report()
    del legacy["backtest"]["provenance"]["execution_timing_semantics"]  # type: ignore[index]
    del legacy["backtest"]["provenance"]["price_limit_semantics"]  # type: ignore[index]
    legacy_result = assess_comparability((first, _pipeline_run("legacy", report=legacy)))
    assert legacy_result.eligible is False
    assert any("执行时序与涨跌停语义指纹" in reason for reason in legacy_result.reasons)


def test_malformed_execution_lag_blocks_controlled_ranking() -> None:
    complete = _pipeline_run("run-complete")

    for invalid_lag in (0, False, "1"):
        malformed = _pipeline_report()
        provenance = malformed["backtest"]["provenance"]["config"]  # type: ignore[index]
        provenance["signal_to_execution_lag"] = invalid_lag  # type: ignore[index]
        result = assess_comparability((complete, _pipeline_run("run-malformed", report=malformed)))

        assert result.eligible is False
        assert result.ranked_run_ids == ()
        assert any("信号至成交滞后" in reason for reason in result.reasons)


def test_malformed_required_contract_fields_block_controlled_ranking() -> None:
    complete = _pipeline_run("run-complete")

    for key, value in (
        ("instruments", False),
        ("train_period", "not a period"),
        ("valid_period", ["2023-01-01", "2023-06-30"]),
        ("test_period", "2023-12-31 ~ 2023-07-01"),
    ):
        malformed = _pipeline_report()
        malformed["config"][key] = value  # type: ignore[index]
        result = assess_comparability((complete, _pipeline_run("run-malformed", report=malformed)))

        assert result.eligible is False
        assert result.ranked_run_ids == ()

    malformed_benchmark = _pipeline_report()
    provenance = malformed_benchmark["backtest"]["provenance"]["config"]  # type: ignore[index]
    provenance["benchmark_code"] = {"code": "SH000300TR"}  # type: ignore[index]
    result = assess_comparability((complete, _pipeline_run("run-malformed-benchmark", report=malformed_benchmark)))

    assert result.eligible is False
    assert result.ranked_run_ids == ()

    malformed_cost = _pipeline_report()
    cost_model = malformed_cost["backtest"]["provenance"]["config"]["exchange_config"]["cost_model"]  # type: ignore[index]
    cost_model["commission_rate"] = False  # type: ignore[index]
    result = assess_comparability((complete, _pipeline_run("run-malformed-cost", report=malformed_cost)))

    assert result.eligible is False
    assert result.ranked_run_ids == ()

    different_cash = _pipeline_report()
    different_cash["backtest"]["provenance"]["config"]["account_config"]["init_cash"] = 10_000_000.0  # type: ignore[index]
    result = assess_comparability((complete, _pipeline_run("run-different-cash", report=different_cash)))

    assert result.eligible is False
    assert result.ranked_run_ids == ()
    assert any("交易所与成本模型不一致" in reason for reason in result.reasons)


def test_mismatched_st_mask_content_identity_blocks_controlled_ranking() -> None:
    first = _pipeline_run("run-a")
    changed = _pipeline_report()
    provenance = changed["backtest"]["provenance"]["config"]  # type: ignore[index]
    provenance["st_mask"]["namechange_sha256"] = "b" * 16  # type: ignore[index]
    second = _pipeline_run("run-b", report=changed)

    result = assess_comparability((first, second))

    assert result.eligible is False
    assert result.ranked_run_ids == ()
    assert any("ST 掩码输入内容不一致" in reason for reason in result.reasons)


def test_missing_runtime_provenance_is_visible_and_blocks_comparison() -> None:
    complete = _pipeline_run("run-complete")
    incomplete_report = deepcopy(_pipeline_report())
    del incomplete_report["backtest"]["provenance"]["config"]["runtime"]  # type: ignore[index]
    incomplete = _pipeline_run("run-incomplete", report=incomplete_report)

    result = assess_comparability((complete, incomplete))

    assert result.eligible is False
    assert any("数据来源 / 运行时快照" in issue.message for issue in incomplete.issues)
    assert any("run-incomplete" in reason for reason in result.reasons)


def test_malformed_runtime_provenance_values_block_controlled_ranking() -> None:
    complete = _pipeline_run("run-complete")

    for field, value in (("region", "mars"), ("data_adjust_mode", "unknown")):
        malformed_report = _pipeline_report()
        runtime = malformed_report["backtest"]["provenance"]["config"]["runtime"]  # type: ignore[index]
        runtime[field] = value  # type: ignore[index]
        result = assess_comparability(
            (complete, _pipeline_run("run-malformed", report=malformed_report))
        )

        assert result.eligible is False
        assert result.ranked_run_ids == ()
        assert any("数据来源 / 运行时快照" in reason for reason in result.reasons)


def test_non_official_or_unstamped_metrics_cannot_receive_a_research_rank() -> None:
    official = _pipeline_run("official")
    predictions_only_report = _pipeline_report()
    predictions_only_report["metric_status"] = "predictions_only_non_canonical"
    unstamped_report = _pipeline_report()
    del unstamped_report["metric_status"]
    purpose_downgraded_report = _pipeline_report()
    purpose_downgraded_report["metrics_purpose"] = "predictions_only"

    for run in (
        _pipeline_run("predictions-only", report=predictions_only_report),
        _pipeline_run("unstamped", report=unstamped_report),
        _pipeline_run("purpose-downgraded", report=purpose_downgraded_report),
    ):
        result = assess_comparability((official, run))

        assert result.eligible is False
        assert result.ranked_run_ids == ()
        assert any("指标状态" in reason for reason in result.reasons)


def test_official_pipeline_metrics_require_the_canonical_backtest_path() -> None:
    official = _pipeline_run("official")

    for path in (None, "custom.backtest.path"):
        report = _pipeline_report()
        if path is None:
            del report["official_backtest_path"]
        else:
            report["official_backtest_path"] = path
        result = assess_comparability((official, _pipeline_run("unverified", report=report)))

        assert result.eligible is False
        assert result.ranked_run_ids == ()
        assert any("canonical qlib 回测路径" in reason for reason in result.reasons)


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
            "step_months": 3,
            "benchmark_code": "SH000300TR",
            "signal_to_execution_lag": 1,
            "execution_price_kind": "close",
            "commission_rate": 0.0005,
            "stamp_tax_schedule": None,
            "slippage_bps": 5.0,
            "min_cost": 5.0,
            "init_cash": 100_000_000.0,
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
        "test_window_coverage": {
            "mode": "continuous",
            "gap_count": 0,
            "max_gap_days": 0,
            "overlap_count": 0,
            "max_overlap_days": 0,
            "max_overlap_depth": 0,
        },
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
    assert run.data_provenance_source is None
    assert any("数据来源 / 运行时快照" in issue.message for issue in run.issues)

    changed = deepcopy(report)
    changed["config"]["step_months"] = 6  # type: ignore[index]
    different_schedule = build_comparison_run(
        run_id="wf-different-schedule",
        engine="walk_forward",
        status="completed",
        created_at="",
        config_path="config.yaml",
        report_path="walk_forward_report.json",
        log_paths=(),
        config=config,
        report=changed,
    )
    result = assess_comparability((run, different_schedule))

    assert result.eligible is False
    assert any("测试窗口不一致" in reason for reason in result.reasons)


def test_walk_forward_without_runtime_snapshot_cannot_be_ranked() -> None:
    config = {"provider_uri": "data/qlib_cn", "region": "cn", "adjust_mode": "pre_adjusted"}
    report = {
        "config": {
            "instruments": "csi300",
            "overall_start": "2020-01-01",
            "overall_end": "2023-12-31",
            "train_months": 24,
            "valid_months": 3,
            "test_months": 3,
            "step_months": 3,
            "benchmark_code": "SH000300TR",
            "signal_to_execution_lag": 1,
            "execution_price_kind": "close",
            "commission_rate": 0.0005,
            "stamp_tax_schedule": None,
            "slippage_bps": 5.0,
            "min_cost": 5.0,
            "init_cash": 100_000_000.0,
            "limit_threshold": 0.095,
            "adjust_mode": "pre_adjusted",
        },
        "aggregate_metrics": {"mean_information_ratio": 0.35},
        "test_window_coverage": {"mode": "continuous"},
    }
    first = build_comparison_run(
        run_id="wf-a",
        engine="walk_forward",
        status="completed",
        created_at="",
        config_path="",
        report_path="",
        log_paths=(),
        config=config,
        report=report,
    )
    second = build_comparison_run(
        run_id="wf-b",
        engine="walk_forward",
        status="completed",
        created_at="",
        config_path="",
        report_path="",
        log_paths=(),
        config=config,
        report=report,
    )

    result = assess_comparability((first, second))

    assert result.eligible is False
    assert any("数据来源 / 运行时快照" in reason for reason in result.reasons)


def test_selectable_catalog_folds_superseded_artifact_directories() -> None:
    rows = (
        JobSummary(
            "new",
            "pipeline",
            "completed",
            "cli",
            "output/runs/shared",
            "2026-08-20T10:00:00+08:00",
            "",
            "",
            None,
            "",
            "",
            {},
        ),
        JobSummary(
            "old",
            "pipeline",
            "completed",
            "cli",
            "output/runs/shared",
            "2026-08-19T10:00:00+08:00",
            "",
            "",
            None,
            "",
            "",
            {},
        ),
    )

    assert [job.run_id for job in selectable_catalog_rows(rows)] == ["new"]


def test_selectable_catalog_prefers_ui_owner_and_aliases_cli_mirror() -> None:
    ui_job = JobSummary(
        "ui-run",
        "pipeline",
        "completed",
        "ui",
        "output/runs/shared",
        "2026-08-20T09:00:00+08:00",
        "2026-08-20T09:00:00+08:00",
        "2026-08-20T10:01:00+08:00",
        None,
        "",
        "",
        {},
    )
    cli_mirror = JobSummary(
        "cli-run",
        "pipeline",
        "completed",
        "cli",
        "output/runs/shared",
        "2026-08-20T10:00:00+08:00",
        "2026-08-20T09:01:00+08:00",
        "2026-08-20T10:00:00+08:00",
        None,
        "",
        "",
        {},
    )

    catalog = selectable_catalog((cli_mirror, ui_job))

    assert [job.run_id for job in catalog.rows] == ["ui-run"]
    assert catalog.run_id_alias == {"cli-run": "ui-run"}


def test_selectable_catalog_keeps_newer_independent_cli_run_for_reused_directory() -> None:
    ui_job = JobSummary(
        "ui-run", "walk_forward", "completed", "ui", "output/runs/shared",
        "2026-08-20T09:00:00+08:00", "2026-08-20T09:00:00+08:00",
        "2026-08-20T09:10:00+08:00",
    )
    later_cli = JobSummary(
        "cli-later", "walk_forward", "completed", "cli", "output/runs/shared",
        "2026-08-20T10:05:00+08:00", "2026-08-20T10:00:00+08:00",
        "2026-08-20T10:05:00+08:00",
    )

    catalog = selectable_catalog((ui_job, later_cli))

    assert [job.run_id for job in catalog.rows] == ["cli-later"]
    assert catalog.run_id_alias == {}


def test_alias_collapsed_run_ids_are_reported_as_duplicates() -> None:
    catalog = selectable_catalog(
        (
            JobSummary(
                "ui-run", "pipeline", "completed", "ui", "output/runs/shared",
                "2026-08-20T09:00:00+08:00", "2026-08-20T09:00:00+08:00",
                "2026-08-20T10:01:00+08:00",
            ),
            JobSummary(
                "cli-run", "pipeline", "completed", "cli", "output/runs/shared",
                "2026-08-20T10:00:00+08:00", "2026-08-20T09:01:00+08:00",
                "2026-08-20T10:00:00+08:00",
            ),
        )
    )
    resolved = tuple(
        catalog.run_id_alias.get(run_id, run_id)
        for run_id in ("ui-run", "cli-run")
    )

    assert duplicate_run_ids(resolved) == ("ui-run",)


def test_parse_selected_ids_preserves_full_order_after_url_validation() -> None:
    assert parse_selected_run_ids("pipeline.a-1,wf_b.2") == ("pipeline.a-1", "wf_b.2")
