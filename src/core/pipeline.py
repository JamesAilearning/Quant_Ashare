"""V2 Quantitative Trading Pipeline — orchestrates the full workflow.

init → features → model → backtest → report

All steps are wired through V2's contract and governance system.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from src.core.backtest_runner import BacktestRunner
from src.core.canonical_backtest_contract import (
    ADJUST_MODE_PRE,
    CanonicalAccountConfig,
    CanonicalBacktestInput,
    CanonicalBacktestOutput,
    CanonicalExchangeConfig,
    CanonicalExchangeCostModel,
)
from src.core.model_trainer import ModelTrainConfig, ModelTrainer, ModelTrainResult
from src.core.qlib_runtime import QlibRuntimeConfig, init_qlib_canonical, is_canonical_qlib_initialized
from src.data.feature_dataset_builder import FeatureDatasetBuilder, FeatureDatasetConfig, FeatureDatasetResult


class PipelineError(RuntimeError):
    """Raised on pipeline orchestration failures."""


@dataclass(frozen=True)
class PipelineConfig:
    """Complete pipeline configuration."""

    # qlib runtime
    provider_uri: str
    region: str = "cn"

    # features
    instruments: str = "csi300"
    feature_handler: str = "Alpha158"
    train_start: str = "2022-01-01"
    train_end: str = "2024-12-31"
    valid_start: str = "2025-01-01"
    valid_end: str = "2025-06-30"
    test_start: str = "2025-07-01"
    test_end: str = "2025-12-31"

    # model
    model_type: str = "LGBModel"
    num_boost_round: int = 1000
    early_stopping_rounds: int = 50
    learning_rate: float = 0.0421
    max_depth: int = 8
    num_leaves: int = 210

    # backtest
    benchmark_code: str = "SH000300"
    init_cash: float = 100_000_000
    commission_rate: float = 0.0005
    stamp_tax_bps: float = 10.0
    slippage_bps: float = 5.0
    min_cost: float = 5.0
    execution_price_kind: str = "close"
    adjust_mode: str = ADJUST_MODE_PRE
    signal_to_execution_lag: int = 1
    topk: int = 50
    n_drop: int = 5

    # output
    output_dir: str = "output"


@dataclass(frozen=True)
class PipelineResult:
    """Pipeline execution result."""

    feature_result: FeatureDatasetResult
    model_result: ModelTrainResult
    backtest_output: CanonicalBacktestOutput
    report_path: str


class Pipeline:
    """Orchestrates the full V2 quantitative trading pipeline."""

    @classmethod
    def run(cls, config: PipelineConfig) -> PipelineResult:
        output_dir = Path(config.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Step 1: Initialize qlib
        cls._log("Initializing qlib runtime...")
        if not is_canonical_qlib_initialized():
            init_qlib_canonical(QlibRuntimeConfig(
                provider_uri=config.provider_uri,
                region=config.region,
            ))

        # Step 2: Build feature dataset
        cls._log("Building feature dataset...")
        feature_result = FeatureDatasetBuilder.build(FeatureDatasetConfig(
            instruments=config.instruments,
            feature_handler=config.feature_handler,
            train_start=config.train_start,
            train_end=config.train_end,
            valid_start=config.valid_start,
            valid_end=config.valid_end,
            test_start=config.test_start,
            test_end=config.test_end,
        ))
        cls._log(
            f"  Train: {feature_result.train_shape}, "
            f"Valid: {feature_result.valid_shape}, "
            f"Test: {feature_result.test_shape}"
        )

        # Step 3: Train model
        cls._log("Training model...")
        model_artifact_path = str(output_dir / "model.pkl")
        model_result = ModelTrainer.train_and_predict(
            config=ModelTrainConfig(
                model_type=config.model_type,
                num_boost_round=config.num_boost_round,
                early_stopping_rounds=config.early_stopping_rounds,
                learning_rate=config.learning_rate,
                max_depth=config.max_depth,
                num_leaves=config.num_leaves,
            ),
            dataset=feature_result.dataset,
            model_artifact_path=model_artifact_path,
        )
        cls._log(f"  Predictions: {model_result.prediction_shape}")

        # Step 4: Run canonical backtest
        cls._log("Running canonical backtest...")
        backtest_request = CanonicalBacktestInput(
            predictions_ref=model_artifact_path,
            evaluation_start=config.test_start,
            evaluation_end=config.test_end,
            account_config=CanonicalAccountConfig(init_cash=config.init_cash),
            exchange_config=CanonicalExchangeConfig(
                freq="day",
                execution_price_kind=config.execution_price_kind,
                cost_model=CanonicalExchangeCostModel(
                    commission_rate=config.commission_rate,
                    stamp_tax_bps=config.stamp_tax_bps,
                    slippage_bps=config.slippage_bps,
                    min_cost=config.min_cost,
                ),
            ),
            adjust_mode=config.adjust_mode,
            signal_to_execution_lag=config.signal_to_execution_lag,
            benchmark_code=config.benchmark_code,
        )

        backtest_output = BacktestRunner.run(
            request=backtest_request,
            predictions=model_result.predictions,
            topk=config.topk,
            n_drop=config.n_drop,
        )

        # Step 5: Write report
        report_path = str(output_dir / "pipeline_report.json")
        cls._write_report(report_path, config, feature_result, model_result, backtest_output)
        cls._log(f"  Report: {report_path}")

        # Step 6: Print summary
        cls._print_summary(backtest_output)

        return PipelineResult(
            feature_result=feature_result,
            model_result=model_result,
            backtest_output=backtest_output,
            report_path=report_path,
        )

    @staticmethod
    def _log(msg: str) -> None:
        print(f"[Pipeline] {msg}")

    @staticmethod
    def _write_report(
        path: str,
        config: PipelineConfig,
        feature_result: FeatureDatasetResult,
        model_result: ModelTrainResult,
        backtest_output: CanonicalBacktestOutput,
    ) -> None:
        report = {
            "generated_at": datetime.now().isoformat(),
            "metric_status": backtest_output.metric_status,
            "official_backtest_path": backtest_output.official_backtest_path,
            "config": {
                "instruments": config.instruments,
                "feature_handler": config.feature_handler,
                "train_period": f"{config.train_start} ~ {config.train_end}",
                "valid_period": f"{config.valid_start} ~ {config.valid_end}",
                "test_period": f"{config.test_start} ~ {config.test_end}",
                "model_type": config.model_type,
                "benchmark_code": config.benchmark_code,
                "topk": config.topk,
                "n_drop": config.n_drop,
            },
            "dataset": {
                "train_shape": list(feature_result.train_shape),
                "valid_shape": list(feature_result.valid_shape),
                "test_shape": list(feature_result.test_shape),
            },
            "model": {
                "prediction_shape": list(model_result.prediction_shape),
                "model_artifact_path": model_result.model_artifact_path,
            },
            "backtest": {
                "report": backtest_output.report,
                "provenance": dict(backtest_output.provenance),
            },
            "risk_analysis": dict(backtest_output.risk_analysis),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False, default=str)

    @staticmethod
    def _print_summary(output: CanonicalBacktestOutput) -> None:
        print("\n" + "=" * 60)
        print("  V2 Pipeline Results")
        print("=" * 60)
        print(f"  Metric Status: {output.metric_status}")
        print(f"  Backtest Path: {output.official_backtest_path}")
        print(f"  Trading Days:  {output.report.get('total_days', 'N/A')}")
        print(f"  Period:        {output.report.get('start_date')} ~ {output.report.get('end_date')}")

        risk = output.risk_analysis
        for label in ("excess_return_without_cost", "excess_return_with_cost"):
            section = risk.get(label, {})
            risk_metrics = section.get("risk", {})
            if risk_metrics:
                print(f"\n  [{label}]")
                for key in ("annualized_return", "information_ratio", "max_drawdown"):
                    val = risk_metrics.get(key, "N/A")
                    if isinstance(val, float):
                        print(f"    {key}: {val:.4f}")
                    else:
                        print(f"    {key}: {val}")
        print("=" * 60)
