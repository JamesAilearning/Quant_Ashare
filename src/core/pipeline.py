"""V2 Quantitative Trading Pipeline — orchestrates the full workflow.

init → features → model → signal → backtest → factor analysis → attribution → report

All steps are wired through V2's contract and governance system.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from src.core.logger import get_logger

_logger = get_logger(__name__)

from src.core.backtest_runner import BacktestRunner
from src.core.canonical_backtest_contract import (
    ADJUST_MODE_PRE,
    CanonicalAccountConfig,
    CanonicalBacktestInput,
    CanonicalBacktestOutput,
    CanonicalExchangeConfig,
    CanonicalExchangeCostModel,
)
from src.core.factor_analyzer import FactorAnalysisConfig, FactorAnalysisResult, FactorAnalyzer
from src.core.model_trainer import ModelTrainConfig, ModelTrainer, ModelTrainResult
from src.core.performance_attribution import (
    AttributionConfig,
    AttributionResult,
    PerformanceAttribution,
    PerformanceAttributionError,
)
from src.core.qlib_runtime import QlibRuntimeConfig, init_qlib_canonical, is_canonical_qlib_initialized
from src.core.signal_analyzer import SignalAnalysisConfig, SignalAnalysisResult, SignalAnalyzer
from src.core.visualizer import ResultVisualizer, VisualizerConfig
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
    # A-share price-move bound: 0.095 = main board ±10%,
    # 0.195 = ChiNext/STAR ±20%, 0.045 = ST ±5%. Must match the
    # dominant board of the universe; canonical contract bounds check.
    limit_threshold: float = 0.095

    # reproducibility — seed for numpy/python random/LGB/XGB/CatBoost
    seed: int = 42

    # factor analysis
    run_factor_analysis: bool = True
    factor_forward_period: int = 5
    factor_top_n: int = 20
    factor_max_decay_lag: int = 20

    # performance attribution
    run_attribution: bool = True

    # output
    output_dir: str = "output"


@dataclass(frozen=True)
class PipelineResult:
    """Pipeline execution result."""

    feature_result: FeatureDatasetResult
    model_result: ModelTrainResult
    signal_analysis: SignalAnalysisResult
    backtest_output: CanonicalBacktestOutput
    factor_analysis: FactorAnalysisResult | None
    attribution: AttributionResult | None
    report_path: str


class Pipeline:
    """Orchestrates the full V2 quantitative trading pipeline."""

    @classmethod
    def run(cls, config: PipelineConfig) -> PipelineResult:
        # Per-run output directory: output/runs/{timestamp}_{fingerprint}/
        # Prevents successive runs from silently overwriting each other.
        # The fingerprint is computed from the config so re-running with
        # identical settings is visible in the directory name.
        root_dir = Path(config.output_dir)
        output_dir = cls._make_run_dir(root_dir, config)
        # exist_ok=False: if our microsecond timestamp somehow still collides
        # (extreme race on very coarse clocks), fail loud rather than clobber
        # an earlier run's artifacts.
        output_dir.mkdir(parents=True, exist_ok=False)
        _logger.info("Run directory: %s", output_dir)

        # Step 1: Initialize qlib (or validate config matches existing init)
        _logger.info("Initializing qlib runtime...")
        requested_config = QlibRuntimeConfig(
            provider_uri=config.provider_uri,
            region=config.region,
        )
        # init_qlib_canonical is idempotent for same config, raises on mismatch
        init_qlib_canonical(requested_config)

        # Step 2: Build feature dataset
        _logger.info("Building feature dataset...")
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
        _logger.info(
            "  Train: %s, Valid: %s, Test: %s",
            feature_result.train_shape, feature_result.valid_shape, feature_result.test_shape,
        )

        # Step 3: Train model
        _logger.info("Training model...")
        model_artifact_path = str(output_dir / "model.pkl")
        model_result = ModelTrainer.train_and_predict(
            config=ModelTrainConfig(
                model_type=config.model_type,
                num_boost_round=config.num_boost_round,
                early_stopping_rounds=config.early_stopping_rounds,
                learning_rate=config.learning_rate,
                max_depth=config.max_depth,
                num_leaves=config.num_leaves,
                seed=config.seed,
            ),
            dataset=feature_result.dataset,
            model_artifact_path=model_artifact_path,
        )
        _logger.info("  Predictions: %s", model_result.prediction_shape)

        # Step 4: Signal quality analysis
        _logger.info("Analyzing signal quality...")
        signal_result = SignalAnalyzer.analyze(
            predictions=model_result.predictions,
            config=SignalAnalysisConfig(topk=config.topk),
        )
        SignalAnalyzer.print_report(signal_result)

        # Step 5: Run canonical backtest
        _logger.info("Running canonical backtest...")
        # predictions_ref is a provenance marker (where the model artifact lives),
        # not consumed by BacktestRunner — predictions are passed directly below.
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
                limit_threshold=config.limit_threshold,
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

        # Step 6: Factor analysis (optional)
        factor_result: FactorAnalysisResult | None = None
        if config.run_factor_analysis:
            _logger.info("Running factor analysis...")
            # Reuse the Alpha158 dataset already built in step 2 — otherwise
            # FactorAnalyzer would rebuild the (expensive) handler from zero.
            factor_result = FactorAnalyzer.analyze(
                FactorAnalysisConfig(
                    instruments=config.instruments,
                    feature_handler=config.feature_handler,
                    test_start=config.test_start,
                    test_end=config.test_end,
                    forward_period=config.factor_forward_period,
                    top_n_factors=config.factor_top_n,
                    max_decay_lag=config.factor_max_decay_lag,
                ),
                dataset=feature_result.dataset,
            )
            FactorAnalyzer.print_report(factor_result)

        # Step 7: Performance attribution (optional)
        attribution_result: AttributionResult | None = None
        if config.run_attribution:
            if not backtest_output.positions:
                # The previous implementation silently coerced ``positions`` to
                # ``None`` here, which flipped PerformanceAttribution into its
                # prediction-score fallback mode — a semantically-different
                # attribution under the same metric name. That violates the
                # repo's "no implicit fallback" rule (see backtest_runner
                # ``_positions_to_weight_map`` docstring for the full chain).
                # We now skip the step explicitly and log loudly.
                _logger.warning(
                    "Skipping performance attribution: backtest produced no "
                    "positions map (len=%d). Attribution is configured as "
                    "position-based — refusing to silently fall back to "
                    "prediction-score attribution. Check backtest_runner "
                    "logs for per-day position parse warnings.",
                    len(backtest_output.positions) if backtest_output.positions else 0,
                )
            else:
                _logger.info("Running performance attribution...")
                try:
                    attribution_result = PerformanceAttribution.analyze(
                        return_series=backtest_output.return_series,
                        predictions=model_result.predictions,
                        config=AttributionConfig(
                            start_date=config.test_start,
                            end_date=config.test_end,
                            # benchmark_code intentionally omitted —
                            # attribution uses return_series["bench"]
                            # from CanonicalBacktestOutput, which already
                            # embeds the correct benchmark data.
                        ),
                        positions=backtest_output.positions,
                    )
                    PerformanceAttribution.print_report(attribution_result)
                except PerformanceAttributionError as exc:
                    # Degenerate inputs (e.g. all-non-positive predictions,
                    # all-zero position weights) raise from the attribution
                    # engine by design — they would otherwise be silently
                    # masked by a uniform-weighting fallback. Downgrade to
                    # "skipped with loud WARNING" so the run can still
                    # finish (backtest + report are already valid) while
                    # making the degradation visible to callers.
                    attribution_result = None
                    _logger.warning(
                        "Performance attribution skipped — engine raised "
                        "%s: %s. Backtest and risk_analysis remain valid; "
                        "only the sector-attribution block is absent from "
                        "the report.",
                        type(exc).__name__, exc,
                    )

        # Step 7b: Persist positions artifact (authoritative portfolio weights)
        if backtest_output.positions:
            positions_path = output_dir / "positions.json"
            with open(positions_path, "w", encoding="utf-8") as f:
                json.dump(dict(backtest_output.positions), f, indent=2, default=str)
            _logger.info(
                "  Positions: %s (%d days)",
                positions_path, len(backtest_output.positions),
            )

        # Step 8: Write report
        report_path = str(output_dir / "pipeline_report.json")
        cls._write_report(
            report_path, config, feature_result, model_result,
            signal_result, backtest_output, factor_result, attribution_result,
        )
        _logger.info("  Report: %s", report_path)

        # Step 9: Print summary
        cls._print_summary(backtest_output)

        # Step 10: Generate charts
        _logger.info("Generating performance charts...")
        charts_dir = str(output_dir / "charts")
        ResultVisualizer.generate(
            return_series=backtest_output.return_series,
            config=VisualizerConfig(output_dir=charts_dir),
        )

        return PipelineResult(
            feature_result=feature_result,
            model_result=model_result,
            signal_analysis=signal_result,
            backtest_output=backtest_output,
            factor_analysis=factor_result,
            attribution=attribution_result,
            report_path=report_path,
        )

    @staticmethod
    def _make_run_dir(root_dir: Path, config: PipelineConfig) -> Path:
        """Return ``root_dir / runs / {timestamp}_{fingerprint}``.

        The fingerprint hashes the config dict so identical re-runs produce a
        stable suffix; the timestamp prefix (microsecond resolution) guarantees
        uniqueness under rapid-fire runs. Callers must create the directory
        with ``exist_ok=False`` so an unexpected collision surfaces as an
        error rather than silently overwriting a prior run's artifacts.
        """
        import hashlib
        import json
        from dataclasses import asdict

        # Microsecond resolution plus a nanosecond counter suffix prevents
        # collisions even when datetime.now() resolves identically across two
        # rapid calls on coarse OS clocks (common on Windows).
        import time as _time
        ns_tail = _time.perf_counter_ns() % 1_000_000  # 6-digit ns jitter
        timestamp = datetime.now().strftime(f"%Y%m%d_%H%M%S_%f") + f"{ns_tail:06d}"
        config_json = json.dumps(asdict(config), sort_keys=True, default=str)
        fingerprint = hashlib.sha256(config_json.encode()).hexdigest()[:12]
        return root_dir / "runs" / f"{timestamp}_{fingerprint}"

    @staticmethod
    def _write_report(
        path: str,
        config: PipelineConfig,
        feature_result: FeatureDatasetResult,
        model_result: ModelTrainResult,
        signal_result: SignalAnalysisResult,
        backtest_output: CanonicalBacktestOutput,
        factor_result: FactorAnalysisResult | None = None,
        attribution_result: AttributionResult | None = None,
    ) -> None:
        report: dict[str, Any] = {
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
            "signal_analysis": {
                "ic_summary": dict(signal_result.ic_summary),
                "ic_decay": list(signal_result.ic_decay),
                "turnover": dict(signal_result.turnover_stats),
            },
            "backtest": {
                "report": backtest_output.report,
                "provenance": dict(backtest_output.provenance),
            },
            "risk_analysis": dict(backtest_output.risk_analysis),
        }

        if factor_result is not None:
            report["factor_analysis"] = {
                "total_factors": factor_result.total_factors,
                "top_factors": [
                    {
                        "name": s.factor_name, "mean_ic": s.mean_ic,
                        "std_ic": s.std_ic, "ir": s.ir,
                        "ic_positive_ratio": s.ic_positive_ratio,
                    }
                    for s in factor_result.factor_ic_stats[:20]
                ],
                "ic_decay": dict(factor_result.ic_decay),
            }

        if attribution_result is not None:
            report["attribution"] = Pipeline._attribution_to_report_dict(attribution_result)

        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False, default=str)

    @staticmethod
    def _attribution_to_report_dict(attribution_result: AttributionResult) -> dict:
        """Serialize an :class:`AttributionResult` to the JSON-report dict.

        Extracted so the JSON contract (which methodology fields land in
        ``pipeline_report.json``) is unit-testable without a full E2E
        run. The methodology / provenance fields below were surfaced in
        ``PerformanceAttribution.print_report`` log lines but were
        previously missing from the JSON — JSON consumers (dashboards,
        downstream scripts) had no way to tell whether sector buckets were
        boards vs. industries, whether the benchmark was equal-weighted
        vs. cap-weighted, or whether the Brinson sum reconciles with the
        compounded excess return. Persist them so the caveats travel
        with the data.
        """
        return {
            "total_portfolio_return": attribution_result.total_portfolio_return,
            "total_benchmark_return": attribution_result.total_benchmark_return,
            "total_excess_return": attribution_result.total_excess_return,
            "allocation_effect": attribution_result.total_allocation_effect,
            "selection_effect": attribution_result.total_selection_effect,
            "interaction_effect": attribution_result.total_interaction_effect,
            "attribution_method": attribution_result.attribution_method,
            "sector_taxonomy": attribution_result.sector_taxonomy,
            "bench_weight_method": attribution_result.bench_weight_method,
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
            "monthly_returns": [
                {
                    "month": f"{m.year}-{m.month:02d}",
                    "portfolio": m.portfolio_return,
                    "benchmark": m.benchmark_return,
                    "excess": m.excess_return,
                }
                for m in attribution_result.monthly_returns
            ],
        }

    @staticmethod
    def _print_summary(output: CanonicalBacktestOutput) -> None:
        log = _logger.info
        log("=" * 60)
        log("  V2 Pipeline Results")
        log("=" * 60)
        log(f"  Metric Status: {output.metric_status}")
        log(f"  Backtest Path: {output.official_backtest_path}")
        log(f"  Trading Days:  {output.report.get('total_days', 'N/A')}")
        log(f"  Period:        {output.report.get('start_date')} ~ {output.report.get('end_date')}")

        risk = output.risk_analysis
        for label in ("excess_return_without_cost", "excess_return_with_cost"):
            section = risk.get(label, {})
            if section:
                log(f"  [{label}]")
                for key in ("annualized_return", "information_ratio", "max_drawdown"):
                    val = section.get(key, "N/A")
                    if isinstance(val, float):
                        log(f"    {key}: {val:.4f}")
                    else:
                        log(f"    {key}: {val}")
        log("=" * 60)
