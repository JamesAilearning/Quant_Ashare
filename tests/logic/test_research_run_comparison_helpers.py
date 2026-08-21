from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict

from src.core.canonical_backtest_contract import (
    CANONICAL_OFFICIAL_BACKTEST_PATH,
    PREDICTIONS_ONLY_METRIC_STATUS,
)
from src.core.pipeline import PipelineConfig
from src.core.walk_forward.config import WalkForwardConfig
from web.operator_ui.job_io import JobSummary
from web.operator_ui.pages._research_run_comparison_helpers import (
    _has_complete_pipeline_config_artifact,
    _has_complete_walk_forward_config_artifact,
    assess_comparability,
    build_comparison_run,
    duplicate_run_ids,
    parse_selected_run_ids,
    selectable_catalog,
    selectable_catalog_rows,
)


def _pipeline_report(*, information_ratio: float = 0.4) -> dict[str, object]:
    backtest_provenance: dict[str, object] = {
        "config_fingerprint": "c" * 16,
        "execution_timing_semantics": "lag_total_v2",
        "price_limit_semantics": "close_expr_v1",
        "official_backtest_path": CANONICAL_OFFICIAL_BACKTEST_PATH,
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
                        {"effective_from": "2008-09-19", "bps": 10.0},
                        {"effective_from": "2023-08-28", "bps": 5.0},
                    ],
                    "slippage_bps": 5.0,
                    "min_cost": 5.0,
                },
            },
            "runtime": {
                "provider_uri": "data/qlib_cn",
                "region": "cn",
                "data_adjust_mode": "pre_adjusted",
                "bundle_identity": "2026-08-20@sha256:" + "b" * 64,
                "bundle_build_identity": "fetch-integrity@2026-08-20T00:00:00+00:00",
            },
        },
    }
    return {
        "metric_status": "official",
        "metrics_purpose": "official",
        "official_backtest_path": CANONICAL_OFFICIAL_BACKTEST_PATH,
        "config": {
            "instruments": "csi300",
            "train_period": "2020-01-01 ~ 2022-12-31",
            "valid_period": "2023-01-01 ~ 2023-06-30",
            "test_period": "2023-07-01 ~ 2023-12-31",
            "model_type": "LGBModel",
        },
        "backtest": {"provenance": backtest_provenance},
        "comparison_provenance": {
            "status": "consistent",
            "execution_timing_semantics": backtest_provenance[
                "execution_timing_semantics"
            ],
            "price_limit_semantics": backtest_provenance["price_limit_semantics"],
            "official_backtest_path": backtest_provenance[
                "official_backtest_path"
            ],
            "config": deepcopy(backtest_provenance["config"]),
        },
        "risk_analysis": {
            "excess_return_with_cost": {
                "annualized_return": 0.12,
                "max_drawdown": -0.08,
                "information_ratio": information_ratio,
            },
        },
    }


def _walk_forward_config(**overrides: object) -> dict[str, object]:
    config = asdict(WalkForwardConfig(overall_start="2020-01-01", overall_end="2023-12-31"))
    config.update(overrides)
    return config


def _walk_forward_report(*, config: dict[str, object] | None = None) -> dict[str, object]:
    pipeline_provenance = deepcopy(_pipeline_report()["backtest"]["provenance"])  # type: ignore[index]
    pipeline_provenance["config"]["st_mask"]["namechange_path"] = None  # type: ignore[index]
    return {
        "metric_status": "official",
        "metrics_purpose": "official",
        "num_folds": 1,
        "config": config if config is not None else _walk_forward_config(),
        "comparison_provenance": {
            "status": "consistent",
            "execution_timing_semantics": pipeline_provenance["execution_timing_semantics"],
            "price_limit_semantics": pipeline_provenance["price_limit_semantics"],
            "official_backtest_path": pipeline_provenance["official_backtest_path"],
            "config": pipeline_provenance["config"],
        },
        "aggregate_metrics": {
            "mean_annualized_return": 0.1,
            "worst_drawdown": -0.12,
            "mean_information_ratio": 0.35,
            "std_information_ratio": 0.11,
            "valid_folds_information_ratio": 1,
        },
        "test_window_coverage": {
            "mode": "continuous",
            "gap_count": 0,
            "max_gap_days": 0,
            "overlap_count": 0,
            "max_overlap_days": 0,
            "max_overlap_depth": 0,
        },
        "folds": [
            {
                "fold_index": 0,
                "information_ratio": 0.35,
                "prediction_shape": [10],
                "metric_status": "official",
            }
        ],
    }


def _pipeline_run(
    run_id: str,
    *,
    information_ratio: float = 0.4,
    report: dict[str, object] | None = None,
    config: dict[str, object] | None = None,
):
    return build_comparison_run(
        run_id=run_id,
        engine="pipeline",
        status="completed",
        created_at="2026-08-19T10:00:00Z",
        config_path=f"output/runs/{run_id}/config.yaml",
        report_path=f"output/runs/{run_id}/pipeline_report.json",
        log_paths=(),
        config=(
            config
            if config is not None
            else {
                "provider_uri": "data/qlib_cn",
                "region": "cn",
                "adjust_mode": "pre_adjusted",
            }
        ),
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
    assert any("执行时序与涨跌停语义指纹" in reason for reason in result.reasons)

    legacy = _pipeline_report()
    del legacy["backtest"]["provenance"]["execution_timing_semantics"]  # type: ignore[index]
    del legacy["backtest"]["provenance"]["price_limit_semantics"]  # type: ignore[index]
    legacy_result = assess_comparability((first, _pipeline_run("legacy", report=legacy)))
    assert legacy_result.eligible is False
    assert any("执行时序与涨跌停语义指纹" in reason for reason in legacy_result.reasons)

    invented = _pipeline_report()
    invented["backtest"]["provenance"]["execution_timing_semantics"] = "made_up_v1"  # type: ignore[index]
    invented_result = assess_comparability((first, _pipeline_run("invented", report=invented)))
    assert invented_result.eligible is False
    assert any("执行时序与涨跌停语义指纹" in reason for reason in invented_result.reasons)


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
        ("train_period", "2023-01-01 ~ 2023-01-01"),
        ("valid_period", "2022-12-31 ~ 2023-06-30"),
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

    oversized_cost = _pipeline_report()
    oversized_cost["backtest"]["provenance"]["config"]["exchange_config"]["cost_model"]["commission_rate"] = 10 ** 10000  # type: ignore[index]
    oversized_result = assess_comparability(
        (complete, _pipeline_run("run-oversized-cost", report=oversized_cost))
    )
    assert oversized_result.eligible is False
    assert oversized_result.ranked_run_ids == ()


def test_comparison_config_requires_complete_producer_pipeline_shape() -> None:
    complete = asdict(PipelineConfig(provider_uri="data/qlib_cn"))
    with_unknown_key = {**complete, "operator_note": "not producer schema"}

    assert _has_complete_pipeline_config_artifact(complete)
    assert not _has_complete_pipeline_config_artifact({})
    assert not _has_complete_pipeline_config_artifact({"unrelated": True})
    assert not _has_complete_pipeline_config_artifact({"provider_uri": "  "})
    assert not _has_complete_pipeline_config_artifact({"provider_uri": "data/qlib_cn"})
    assert not _has_complete_pipeline_config_artifact(with_unknown_key)


def test_comparison_config_rejects_semantically_invalid_pipeline_values() -> None:
    complete = asdict(PipelineConfig(provider_uri="data/qlib_cn"))

    equal_train_window = {**complete, "train_end": complete["train_start"]}
    invalid_execution_lag = {**complete, "signal_to_execution_lag": 0}

    assert not _has_complete_pipeline_config_artifact(equal_train_window)
    assert not _has_complete_pipeline_config_artifact(invalid_execution_lag)


def test_walk_forward_report_config_requires_complete_valid_producer_shape() -> None:
    complete = _walk_forward_config()
    missing_field = dict(complete)
    del missing_field["ensemble_window"]
    unknown_field = {**complete, "operator_note": "not producer schema"}
    invalid_ensemble_window = {**complete, "ensemble_window": 0}

    assert _has_complete_walk_forward_config_artifact(complete)
    assert not _has_complete_walk_forward_config_artifact(missing_field)
    assert not _has_complete_walk_forward_config_artifact(unknown_field)
    assert not _has_complete_walk_forward_config_artifact(invalid_ensemble_window)


def test_unrepresentable_numeric_metric_is_shown_as_unavailable() -> None:
    report = _pipeline_report()
    risk = report["risk_analysis"]["excess_return_with_cost"]  # type: ignore[index]
    risk["information_ratio"] = 10 ** 10000  # type: ignore[index]

    run = _pipeline_run("huge-information-ratio", report=report)

    metric = next(item for item in run.metrics if "信息比率" in item.label)
    assert metric.value is None


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

    for field, value in (
        ("region", "mars"),
        ("data_adjust_mode", "unknown"),
        ("bundle_identity", "tushare:not-a-date@not-a-timestamp"),
        ("bundle_build_identity", "fetch-integrity@not-a-timestamp"),
    ):
        malformed_report = _pipeline_report()
        runtime = malformed_report["backtest"]["provenance"]["config"]["runtime"]  # type: ignore[index]
        runtime[field] = value  # type: ignore[index]
        result = assess_comparability(
            (complete, _pipeline_run("run-malformed", report=malformed_report))
        )

        assert result.eligible is False
        assert result.ranked_run_ids == ()
        assert any("数据来源 / 运行时快照" in reason for reason in result.reasons)

    valid_tushare = _pipeline_report()
    valid_tushare_runtime = valid_tushare["backtest"]["provenance"]["config"]["runtime"]  # type: ignore[index]
    valid_tushare_runtime["bundle_identity"] = "tushare:2026-08-20@2026-08-20T00:00:00Z"  # type: ignore[index]
    valid_tushare_runtime["bundle_build_identity"] = "tushare-manifest@2026-08-20T00:00:00Z"  # type: ignore[index]
    valid_tushare_result = assess_comparability(
        (
            _pipeline_run("run-tushare-a", report=valid_tushare),
            _pipeline_run("run-tushare-b", report=deepcopy(valid_tushare)),
        )
    )
    assert valid_tushare_result.eligible is True

    malformed_tushare = deepcopy(valid_tushare)
    malformed_tushare_runtime = malformed_tushare["backtest"]["provenance"]["config"]["runtime"]  # type: ignore[index]
    malformed_tushare_runtime["bundle_build_identity"] = "tushare-manifest@not-a-timestamp"  # type: ignore[index]
    malformed_tushare_result = assess_comparability(
        (
            _pipeline_run("run-tushare-valid", report=valid_tushare),
            _pipeline_run("run-tushare-malformed", report=malformed_tushare),
        )
    )
    assert malformed_tushare_result.eligible is False
    assert any("数据来源 / 运行时快照" in reason for reason in malformed_tushare_result.reasons)


def test_missing_or_changed_bundle_provenance_blocks_controlled_ranking() -> None:
    complete = _pipeline_run("run-complete")
    missing_identity = _pipeline_report()
    runtime = missing_identity["backtest"]["provenance"]["config"]["runtime"]  # type: ignore[index]
    del runtime["bundle_identity"]  # type: ignore[index]

    missing_result = assess_comparability(
        (complete, _pipeline_run("run-missing-bundle", report=missing_identity))
    )
    assert missing_result.eligible is False
    assert any("数据来源 / 运行时快照" in reason for reason in missing_result.reasons)

    missing_rebuild_identity = _pipeline_report()
    missing_rebuild_runtime = missing_rebuild_identity["backtest"]["provenance"]["config"]["runtime"]  # type: ignore[index]
    del missing_rebuild_runtime["bundle_build_identity"]  # type: ignore[index]
    missing_rebuild_result = assess_comparability(
        (complete, _pipeline_run("run-missing-rebuild-id", report=missing_rebuild_identity))
    )
    assert missing_rebuild_result.eligible is False
    assert any("数据来源 / 运行时快照" in reason for reason in missing_rebuild_result.reasons)

    changed_identity = _pipeline_report()
    changed_runtime = changed_identity["backtest"]["provenance"]["config"]["runtime"]  # type: ignore[index]
    changed_runtime["bundle_identity"] = "2026-08-20@sha256:" + "c" * 64  # type: ignore[index]
    changed_result = assess_comparability(
        (complete, _pipeline_run("run-changed-bundle", report=changed_identity))
    )
    assert changed_result.eligible is False
    assert any("数据来源 / 运行时快照不一致" in reason for reason in changed_result.reasons)

    rebuilt = _pipeline_report()
    rebuilt_runtime = rebuilt["backtest"]["provenance"]["config"]["runtime"]  # type: ignore[index]
    rebuilt_runtime["bundle_build_identity"] = "fetch-integrity@2026-08-21T00:00:00+00:00"  # type: ignore[index]
    rebuilt_result = assess_comparability(
        (complete, _pipeline_run("run-rebuilt-bundle", report=rebuilt))
    )
    assert rebuilt_result.eligible is False
    assert any("数据来源 / 运行时快照不一致" in reason for reason in rebuilt_result.reasons)


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


def test_official_metric_status_requires_supported_metrics_purpose() -> None:
    official = _pipeline_run("official")
    missing_purpose = _pipeline_report()
    del missing_purpose["metrics_purpose"]
    unsupported_purpose = _pipeline_report()
    unsupported_purpose["metrics_purpose"] = "untracked"
    predictions_only = _pipeline_report()
    predictions_only["metrics_purpose"] = "predictions_only"

    for run, expected_status in (
        (_pipeline_run("missing-purpose", report=missing_purpose), None),
        (_pipeline_run("unsupported-purpose", report=unsupported_purpose), None),
        (
            _pipeline_run("predictions-only-purpose", report=predictions_only),
            PREDICTIONS_ONLY_METRIC_STATUS,
        ),
    ):
        result = assess_comparability((official, run))

        assert run.metric_status == expected_status
        assert result.eligible is False
        assert result.ranked_run_ids == ()
        assert any("指标状态" in reason for reason in result.reasons)


def test_official_pipeline_metrics_require_the_canonical_backtest_path() -> None:
    official = _pipeline_run("official")

    for path_location, path in (
        ("official_backtest_path", None),
        ("official_backtest_path", "custom.backtest.path"),
        ("nested", None),
        ("nested", "custom.backtest.path"),
        ("projected", None),
        ("projected", "custom.backtest.path"),
    ):
        report = _pipeline_report()
        if path_location == "official_backtest_path":
            target = report
        elif path_location == "nested":
            target = report["backtest"]["provenance"]  # type: ignore[index]
        else:
            target = report["comparison_provenance"]  # type: ignore[index]
        if path is None:
            del target["official_backtest_path"]  # type: ignore[index]
        else:
            target["official_backtest_path"] = path  # type: ignore[index]
        result = assess_comparability((official, _pipeline_run("unverified", report=report)))

        assert result.eligible is False
        assert result.ranked_run_ids == ()
        assert any("canonical qlib 回测路径" in reason for reason in result.reasons)


def test_pipeline_config_artifact_must_match_its_reported_run() -> None:
    report = _pipeline_report()
    resolved_config = asdict(
        PipelineConfig(
            provider_uri="data/qlib_cn",
            instruments="csi300",
            train_start="2020-01-01",
            train_end="2022-12-31",
            valid_start="2023-01-01",
            valid_end="2023-06-30",
            test_start="2023-07-01",
            test_end="2023-12-31",
        )
    )
    for field, value, expected_conflict in (
        ("instruments", "csi500", "instruments"),
        ("train_start", "2019-01-01", "train_period"),
        ("signal_to_execution_lag", 2, "signal_to_execution_lag"),
    ):
        copied_config = {**resolved_config, field: value}
        result = assess_comparability(
            (
                _pipeline_run("matching", report=report, config=resolved_config),
                _pipeline_run("copied", report=report, config=copied_config),
            )
        )

        assert result.eligible is False
        assert any("config.yaml 与 pipeline_report.json" in reason for reason in result.reasons)
        assert any(expected_conflict in reason for reason in result.reasons)


def test_pipeline_config_report_crosscheck_normalizes_runtime_values(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.chdir(tmp_path)
    config = asdict(
        PipelineConfig(
            provider_uri=".",
            region="CN",
            instruments="csi300",
            train_start="2020-01-01",
            train_end="2022-12-31",
            valid_start="2023-01-01",
            valid_end="2023-06-30",
            test_start="2023-07-01",
            test_end="2023-12-31",
        )
    )
    report = _pipeline_report()
    runtime = report["backtest"]["provenance"]["config"]["runtime"]  # type: ignore[index]
    runtime["provider_uri"] = str(tmp_path)  # type: ignore[index]
    runtime["region"] = "cn"  # type: ignore[index]

    run = _pipeline_run("normalized-runtime", config=config, report=report)

    assert not any(issue.code == "config_report_mismatch" for issue in run.issues)


def test_pipeline_config_report_crosscheck_compares_default_stamp_tax_schedule() -> None:
    config = asdict(
        PipelineConfig(
            provider_uri="data/qlib_cn",
            instruments="csi300",
            train_start="2020-01-01",
            train_end="2022-12-31",
            valid_start="2023-01-01",
            valid_end="2023-06-30",
            test_start="2023-07-01",
            test_end="2023-12-31",
        )
    )
    report = _pipeline_report()
    cost_model = report["backtest"]["provenance"]["config"]["exchange_config"]["cost_model"]  # type: ignore[index]
    cost_model["stamp_tax_schedule"] = [  # type: ignore[index]
        {"effective_from": "2008-09-19", "bps": 10.0}
    ]

    run = _pipeline_run("custom-schedule", config=config, report=report)

    mismatch = next(issue for issue in run.issues if issue.code == "config_report_mismatch")
    assert "stamp_tax_schedule" in mismatch.message


def test_walk_forward_uses_existing_aggregate_and_fold_evidence_without_recalculation() -> None:
    config = {
        "provider_uri": "data/qlib_cn",
        "region": "cn",
        "adjust_mode": "pre_adjusted",
    }
    report = {
        "metric_status": "official",
        "metrics_purpose": "official",
        "num_folds": 2,
        "config": _walk_forward_config(),
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
        "folds": [
            {
                "fold_index": 0,
                "information_ratio": 0.2,
                "prediction_shape": [10],
                "metric_status": "official",
            },
            {
                "fold_index": 1,
                "information_ratio": 0.5,
                "prediction_shape": [10],
                "metric_status": "official",
            },
        ],
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

    for invalid_count in (True, -1):
        malformed_counts = deepcopy(report)
        malformed_counts["num_folds"] = invalid_count
        malformed_counts["aggregate_metrics"]["valid_folds_information_ratio"] = invalid_count  # type: ignore[index]
        malformed_count_run = build_comparison_run(
            run_id="wf-malformed-counts",
            engine="walk_forward",
            status="completed",
            created_at="",
            config_path="config.yaml",
            report_path="walk_forward_report.json",
            log_paths=(),
            config=config,
            report=malformed_counts,
        )
        assert malformed_count_run.fold_evidence is None
        assert any(issue.code == "invalid_fold_evidence" for issue in malformed_count_run.issues)

    contradictory_counts = deepcopy(report)
    contradictory_counts["num_folds"] = 3
    contradictory_count_run = build_comparison_run(
        run_id="wf-contradictory-counts",
        engine="walk_forward",
        status="completed",
        created_at="",
        config_path="config.yaml",
        report_path="walk_forward_report.json",
        log_paths=(),
        config=config,
        report=contradictory_counts,
    )
    assert contradictory_count_run.fold_evidence is None
    assert any(issue.code == "invalid_fold_evidence" for issue in contradictory_count_run.issues)

    impossible_valid_count = deepcopy(report)
    impossible_valid_count["aggregate_metrics"]["valid_folds_information_ratio"] = 3  # type: ignore[index]
    impossible_valid_count_run = build_comparison_run(
        run_id="wf-impossible-valid-count",
        engine="walk_forward",
        status="completed",
        created_at="",
        config_path="config.yaml",
        report_path="walk_forward_report.json",
        log_paths=(),
        config=config,
        report=impossible_valid_count,
    )
    assert impossible_valid_count_run.fold_evidence is None
    assert any(issue.code == "invalid_fold_evidence" for issue in impossible_valid_count_run.issues)

    malformed_folds = deepcopy(report)
    malformed_folds["folds"] = [{"fold_index": 0}, "not-a-fold"]
    malformed_fold_run = build_comparison_run(
        run_id="wf-malformed-folds",
        engine="walk_forward",
        status="completed",
        created_at="",
        config_path="config.yaml",
        report_path="walk_forward_report.json",
        log_paths=(),
        config=config,
        report=malformed_folds,
    )
    assert malformed_fold_run.fold_evidence is None
    assert any(issue.code == "invalid_fold_evidence" for issue in malformed_fold_run.issues)

    missing_information_ratio = deepcopy(report)
    del missing_information_ratio["folds"][1]["information_ratio"]  # type: ignore[index]
    missing_information_ratio_run = build_comparison_run(
        run_id="wf-missing-information-ratio",
        engine="walk_forward",
        status="completed",
        created_at="",
        config_path="config.yaml",
        report_path="walk_forward_report.json",
        log_paths=(),
        config=config,
        report=missing_information_ratio,
    )
    assert missing_information_ratio_run.fold_evidence is None
    assert any(issue.code == "invalid_fold_evidence" for issue in missing_information_ratio_run.issues)

    inconsistent_valid_count = deepcopy(report)
    inconsistent_valid_count["folds"][1]["information_ratio"] = None  # type: ignore[index]
    inconsistent_valid_count_run = build_comparison_run(
        run_id="wf-inconsistent-valid-count",
        engine="walk_forward",
        status="completed",
        created_at="",
        config_path="config.yaml",
        report_path="walk_forward_report.json",
        log_paths=(),
        config=config,
        report=inconsistent_valid_count,
    )
    assert inconsistent_valid_count_run.fold_evidence is None
    assert any(issue.code == "invalid_fold_evidence" for issue in inconsistent_valid_count_run.issues)

    zero_folds = deepcopy(report)
    zero_folds["num_folds"] = 0
    zero_folds["folds"] = []
    zero_folds["aggregate_metrics"]["valid_folds_information_ratio"] = 0  # type: ignore[index]
    zero_folds_run = build_comparison_run(
        run_id="wf-zero-folds",
        engine="walk_forward",
        status="completed",
        created_at="",
        config_path="config.yaml",
        report_path="walk_forward_report.json",
        log_paths=(),
        config=config,
        report=zero_folds,
    )
    assert zero_folds_run.fold_evidence is None
    assert any(issue.code == "invalid_fold_evidence" for issue in zero_folds_run.issues)

    missing_fold_status = deepcopy(report)
    del missing_fold_status["folds"][1]["metric_status"]  # type: ignore[index]
    missing_fold_status_run = build_comparison_run(
        run_id="wf-missing-fold-status",
        engine="walk_forward",
        status="completed",
        created_at="",
        config_path="config.yaml",
        report_path="walk_forward_report.json",
        log_paths=(),
        config=config,
        report=missing_fold_status,
    )
    assert missing_fold_status_run.fold_evidence is None
    assert any(issue.code == "invalid_fold_evidence" for issue in missing_fold_status_run.issues)

    non_canonical_fold = deepcopy(report)
    non_canonical_fold["folds"][1]["metric_status"] = "predictions_only_non_canonical"  # type: ignore[index]
    non_canonical_fold_run = build_comparison_run(
        run_id="wf-non-canonical-fold",
        engine="walk_forward",
        status="completed",
        created_at="",
        config_path="config.yaml",
        report_path="walk_forward_report.json",
        log_paths=(),
        config=config,
        report=non_canonical_fold,
    )
    assert non_canonical_fold_run.fold_evidence is None
    assert any(issue.code == "invalid_fold_evidence" for issue in non_canonical_fold_run.issues)

    contradictory_aggregate_status = deepcopy(report)
    contradictory_aggregate_status["metric_status"] = "unverified_no_fold_stamp"
    contradictory_aggregate_status_run = build_comparison_run(
        run_id="wf-contradictory-aggregate-status",
        engine="walk_forward",
        status="completed",
        created_at="",
        config_path="config.yaml",
        report_path="walk_forward_report.json",
        log_paths=(),
        config=config,
        report=contradictory_aggregate_status,
    )
    assert contradictory_aggregate_status_run.fold_evidence is None
    assert any(
        issue.code == "invalid_fold_evidence"
        for issue in contradictory_aggregate_status_run.issues
    )

    failed_placeholder = deepcopy(report)
    failed_placeholder["folds"][0] = {
        "fold_index": 0,
        "information_ratio": None,
        "prediction_shape": [0],
        "metric_status": "failed_no_metrics",
    }
    failed_placeholder["aggregate_metrics"]["valid_folds_information_ratio"] = 1  # type: ignore[index]
    failed_placeholder_run = build_comparison_run(
        run_id="wf-failed-placeholder",
        engine="walk_forward",
        status="completed",
        created_at="",
        config_path="config.yaml",
        report_path="walk_forward_report.json",
        log_paths=(),
        config=config,
        report=failed_placeholder,
    )
    assert failed_placeholder_run.fold_evidence is not None
    assert not any(issue.code == "invalid_fold_evidence" for issue in failed_placeholder_run.issues)

    failed_placeholder_with_metric = deepcopy(failed_placeholder)
    failed_placeholder_with_metric["folds"][0]["information_ratio"] = 0.0  # type: ignore[index]
    failed_placeholder_with_metric["aggregate_metrics"]["valid_folds_information_ratio"] = 2  # type: ignore[index]
    failed_placeholder_with_metric_run = build_comparison_run(
        run_id="wf-failed-placeholder-with-metric",
        engine="walk_forward",
        status="completed",
        created_at="",
        config_path="config.yaml",
        report_path="walk_forward_report.json",
        log_paths=(),
        config=config,
        report=failed_placeholder_with_metric,
    )
    assert failed_placeholder_with_metric_run.fold_evidence is None
    assert any(
        issue.code == "invalid_fold_evidence"
        for issue in failed_placeholder_with_metric_run.issues
    )

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
        "config": _walk_forward_config(),
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


def test_walk_forward_uses_consistent_aggregate_comparison_provenance() -> None:
    pipeline_provenance = _pipeline_report()["backtest"]["provenance"]  # type: ignore[index]
    pipeline_provenance["config"]["st_mask"]["namechange_path"] = None  # type: ignore[index]
    report = {
        "metric_status": "official",
        "metrics_purpose": "official",
        "num_folds": 1,
        "config": _walk_forward_config(),
        "comparison_provenance": {
            "status": "consistent",
            "execution_timing_semantics": pipeline_provenance["execution_timing_semantics"],
            "price_limit_semantics": pipeline_provenance["price_limit_semantics"],
            "official_backtest_path": pipeline_provenance["official_backtest_path"],
            "config": pipeline_provenance["config"],
        },
        "aggregate_metrics": {
            "mean_annualized_return": 0.1,
            "worst_drawdown": -0.12,
            "mean_information_ratio": 0.35,
            "std_information_ratio": 0.11,
            "valid_folds_information_ratio": 1,
        },
        "test_window_coverage": {
            "mode": "continuous",
            "gap_count": 0,
            "max_gap_days": 0,
            "overlap_count": 0,
            "max_overlap_days": 0,
            "max_overlap_depth": 0,
        },
        "folds": [
            {
                "fold_index": 0,
                "information_ratio": 0.35,
                "prediction_shape": [10],
                "metric_status": "official",
            }
        ],
    }

    def build(run_id: str):
        return build_comparison_run(
            run_id=run_id,
            engine="walk_forward",
            status="completed",
            created_at="",
            config_path="config.yaml",
            report_path="walk_forward_report.json",
            log_paths=(),
            config={},
            report=report,
        )

    first, second = build("wf-a"), build("wf-b")
    result = assess_comparability((first, second))

    assert result.eligible is True
    assert first.data_provenance_source == (
        "walk_forward_report.json:comparison_provenance.config.runtime"
    )

    mixed_report = deepcopy(report)
    mixed_report["comparison_provenance"] = {"status": "mixed"}
    mixed = build_comparison_run(
        run_id="wf-mixed",
        engine="walk_forward",
        status="completed",
        created_at="",
        config_path="config.yaml",
        report_path="walk_forward_report.json",
        log_paths=(),
        config={},
        report=mixed_report,
    )
    mixed_result = assess_comparability((first, mixed))

    assert mixed_result.eligible is False
    assert any("逐折回测溯源不一致" in reason for reason in mixed_result.reasons)

    unverified_path_report = deepcopy(report)
    unverified_path_report["comparison_provenance"]["official_backtest_path"] = (  # type: ignore[index]
        "custom.backtest.path"
    )
    unverified_path = build_comparison_run(
        run_id="wf-unverified-path",
        engine="walk_forward",
        status="completed",
        created_at="",
        config_path="config.yaml",
        report_path="walk_forward_report.json",
        log_paths=(),
        config={},
        report=unverified_path_report,
    )
    unverified_path_result = assess_comparability((first, unverified_path))

    assert unverified_path_result.eligible is False
    assert any("canonical qlib 回测路径" in reason for reason in unverified_path_result.reasons)


def test_invalid_walk_forward_aggregate_config_cannot_supply_a_contract() -> None:
    report = _walk_forward_report()
    complete = build_comparison_run(
        run_id="wf-complete",
        engine="walk_forward",
        status="completed",
        created_at="",
        config_path="config.yaml",
        report_path="walk_forward_report.json",
        log_paths=(),
        config={},
        report=report,
    )
    invalid_report = deepcopy(report)
    invalid_report["config"]["ensemble_window"] = 0  # type: ignore[index]
    invalid = build_comparison_run(
        run_id="wf-invalid-config",
        engine="walk_forward",
        status="completed",
        created_at="",
        config_path="config.yaml",
        report_path="walk_forward_report.json",
        log_paths=(),
        config={"instruments": "external-config-must-not-be-used"},
        report=invalid_report,
    )

    assert any(issue.code == "invalid_walk_forward_config" for issue in invalid.issues)
    assert all(value is None for value in invalid.contract.values())
    assert assess_comparability((complete, invalid)).eligible is False


def test_walk_forward_embedded_config_must_match_fold_provenance() -> None:
    report = _walk_forward_report(config=_walk_forward_config(namechange_path="data/namechange-a.parquet"))
    provenance = report["comparison_provenance"]["config"]  # type: ignore[index]
    provenance["st_mask"]["namechange_path"] = "data/namechange-a.parquet"  # type: ignore[index]

    def build(run_id: str, candidate: dict[str, object]):
        return build_comparison_run(
            run_id=run_id,
            engine="walk_forward",
            status="completed",
            created_at="",
            config_path="config.yaml",
            report_path="walk_forward_report.json",
            log_paths=(),
            config={},
            report=candidate,
        )

    complete = build("wf-complete", report)
    assert not any(issue.code == "report_provenance_mismatch" for issue in complete.issues)

    changed_benchmark = deepcopy(report)
    changed_benchmark["comparison_provenance"]["config"]["benchmark_code"] = "csi500"  # type: ignore[index]
    changed_lag = deepcopy(report)
    changed_lag["comparison_provenance"]["config"]["signal_to_execution_lag"] = 2  # type: ignore[index]
    changed_cash = deepcopy(report)
    changed_cash["comparison_provenance"]["config"]["account_config"]["init_cash"] = 10_000_000.0  # type: ignore[index]
    changed_st_path = deepcopy(report)
    changed_st_path["comparison_provenance"]["config"]["st_mask"]["namechange_path"] = "data/namechange-b.parquet"  # type: ignore[index]
    missing_st_path = deepcopy(report)
    del missing_st_path["comparison_provenance"]["config"]["st_mask"]["namechange_path"]  # type: ignore[index]
    missing_null_st_path = _walk_forward_report()
    del missing_null_st_path["comparison_provenance"]["config"]["st_mask"]["namechange_path"]  # type: ignore[index]
    changed_adjust_mode = deepcopy(report)
    changed_adjust_mode["comparison_provenance"]["config"]["adjust_mode"] = "post_adjusted"  # type: ignore[index]
    changed_runtime_adjust_mode = deepcopy(report)
    changed_runtime_adjust_mode["comparison_provenance"]["config"]["runtime"]["data_adjust_mode"] = "post_adjusted"  # type: ignore[index]
    changed_execution_price = deepcopy(report)
    changed_execution_price["comparison_provenance"]["config"]["exchange_config"]["execution_price_kind"] = "open"  # type: ignore[index]
    changed_limit = deepcopy(report)
    changed_limit["comparison_provenance"]["config"]["exchange_config"]["limit_threshold"] = 0.1  # type: ignore[index]
    changed_commission = deepcopy(report)
    changed_commission["comparison_provenance"]["config"]["exchange_config"]["cost_model"]["commission_rate"] = 0.001  # type: ignore[index]
    changed_slippage = deepcopy(report)
    changed_slippage["comparison_provenance"]["config"]["exchange_config"]["cost_model"]["slippage_bps"] = 6.0  # type: ignore[index]
    changed_min_cost = deepcopy(report)
    changed_min_cost["comparison_provenance"]["config"]["exchange_config"]["cost_model"]["min_cost"] = 6.0  # type: ignore[index]
    changed_schedule = deepcopy(report)
    changed_schedule["comparison_provenance"]["config"]["exchange_config"]["cost_model"]["stamp_tax_schedule"] = [  # type: ignore[index]
        {"effective_from": "2008-09-19", "bps": 10.0}
    ]

    for expected_field, candidate in (
        ("benchmark_code", changed_benchmark),
        ("signal_to_execution_lag", changed_lag),
        ("init_cash", changed_cash),
        ("namechange_path", changed_st_path),
        ("namechange_path", missing_st_path),
        ("namechange_path", missing_null_st_path),
        ("adjust_mode", changed_adjust_mode),
        ("data_adjust_mode", changed_runtime_adjust_mode),
        ("execution_price_kind", changed_execution_price),
        ("limit_threshold", changed_limit),
        ("commission_rate", changed_commission),
        ("slippage_bps", changed_slippage),
        ("min_cost", changed_min_cost),
        ("stamp_tax_schedule", changed_schedule),
    ):
        mismatched = build(f"wf-mismatch-{expected_field}", candidate)

        issue = next(issue for issue in mismatched.issues if issue.code == "report_provenance_mismatch")
        assert expected_field in issue.message
        assert assess_comparability((complete, mismatched)).eligible is False


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
        operator_ui_job_id="ui-run",
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


def test_selectable_catalog_keeps_unlinked_overlapping_cli_run() -> None:
    ui_job = JobSummary(
        "ui-run", "pipeline", "completed", "ui", "output/runs/shared",
        "2026-08-20T09:00:00+08:00", "2026-08-20T09:00:00+08:00",
        "2026-08-20T10:01:00+08:00",
    )
    cli_run = JobSummary(
        "cli-run", "pipeline", "completed", "cli", "output/runs/shared",
        "2026-08-20T10:00:00+08:00", "2026-08-20T09:01:00+08:00",
        "2026-08-20T10:00:00+08:00",
    )

    catalog = selectable_catalog((ui_job, cli_run))

    assert [job.run_id for job in catalog.rows] == ["cli-run"]
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
                operator_ui_job_id="ui-run",
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
