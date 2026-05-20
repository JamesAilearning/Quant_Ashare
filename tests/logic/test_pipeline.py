"""Unit tests for Pipeline orchestrator."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.contracts.taxonomy_data_contract import TAXONOMY_MODE_STATIC, TAXONOMY_MODE_TRADE_DATE
from src.core.canonical_backtest_contract import CanonicalBacktestOutput
from src.core.pipeline import Pipeline, PipelineConfig, PipelineError, _sanitize_for_json
from src.core.pipeline_result_artifacts import (
    TRADES_NOT_PRODUCED_REASON,
    PipelineResultArtifactError,
    write_pipeline_result_artifacts,
)
from src.data.taxonomy_artifact_publisher import TaxonomyArtifactPublisher

_QLIB_DATA_DIR = Path(r"D:/qlib_data/my_cn_data")
_HAS_QLIB_DATA = _QLIB_DATA_DIR.exists() and (_QLIB_DATA_DIR / "calendars").exists()


class PipelineStructuralTests(unittest.TestCase):
    def test_config_defaults_are_valid(self) -> None:
        config = PipelineConfig(provider_uri="/tmp/fake")
        self.assertEqual(config.region, "cn")
        self.assertEqual(config.instruments, "csi300")
        self.assertEqual(config.model_type, "LGBModel")
        self.assertEqual(config.compute_device, "cpu")

    def test_make_run_dir_has_timestamp_uniq_and_fingerprint(self) -> None:
        config = PipelineConfig(provider_uri="/tmp/fake")
        root = Path("/tmp/any_root")
        run_dir = Pipeline._make_run_dir(root, config)
        # Must live under runs/ and follow:
        #   YYYYMMDD_HHMMSS_<microsec>_<uniq8hex>_<fingerprint12hex>
        self.assertEqual(run_dir.parent, root / "runs")
        name = run_dir.name
        parts = name.split("_")
        # 5 parts: date, time, microseconds, 8-hex uuid tag, 12-hex fingerprint
        self.assertEqual(len(parts), 5)
        self.assertEqual(len(parts[0]), 8)    # YYYYMMDD
        self.assertEqual(len(parts[1]), 6)    # HHMMSS
        self.assertEqual(len(parts[2]), 6)    # microseconds
        self.assertEqual(len(parts[3]), 8)    # uuid4 hex tag
        self.assertEqual(len(parts[4]), 12)   # sha256 prefix

    def test_make_run_dir_distinguishes_same_second_calls(self) -> None:
        # Two back-to-back calls with the same config must not collide.
        config = PipelineConfig(provider_uri="/tmp/fake")
        root = Path("/tmp/any_root")
        d1 = Pipeline._make_run_dir(root, config)
        d2 = Pipeline._make_run_dir(root, config)
        self.assertNotEqual(d1, d2)

    def test_make_run_dir_fingerprint_is_stable_for_same_config(self) -> None:
        config1 = PipelineConfig(provider_uri="/tmp/fake", topk=50)
        config2 = PipelineConfig(provider_uri="/tmp/fake", topk=50)
        # Fingerprint is the LAST split part; the new uuid tag (-2)
        # changes per call by design.
        fp1 = Pipeline._make_run_dir(Path("/tmp"), config1).name.split("_")[-1]
        fp2 = Pipeline._make_run_dir(Path("/tmp"), config2).name.split("_")[-1]
        self.assertEqual(fp1, fp2)

    def test_make_run_dir_fingerprint_changes_with_config(self) -> None:
        config1 = PipelineConfig(provider_uri="/tmp/fake", topk=50)
        config2 = PipelineConfig(provider_uri="/tmp/fake", topk=100)
        fp1 = Pipeline._make_run_dir(Path("/tmp"), config1).name.split("_")[-1]
        fp2 = Pipeline._make_run_dir(Path("/tmp"), config2).name.split("_")[-1]
        self.assertNotEqual(fp1, fp2)

    def test_make_run_dir_uuid_tag_differs_across_calls(self) -> None:
        """Two calls with identical config still produce distinct
        directory names because of the per-call uuid tag — that is what
        prevents collisions when the OS clock has microsecond
        granularity (Windows)."""
        config = PipelineConfig(provider_uri="/tmp/fake", topk=50)
        n1 = Pipeline._make_run_dir(Path("/tmp"), config).name
        n2 = Pipeline._make_run_dir(Path("/tmp"), config).name
        # Same fingerprint (last token); different uuid tag (second-to-last)
        self.assertEqual(n1.split("_")[-1], n2.split("_")[-1])
        self.assertNotEqual(n1.split("_")[-2], n2.split("_")[-2])


class PipelineResultArtifactTests(unittest.TestCase):
    @staticmethod
    def _backtest_output() -> CanonicalBacktestOutput:
        return CanonicalBacktestOutput(
            metric_status="official",
            official_backtest_path="qlib.backtest.backtest",
            return_series={
                "return": {"2025-01-02": 0.01, "2025-01-03": -0.005},
                "bench": {"2025-01-02": 0.002, "2025-01-03": 0.003},
                "cost": {"2025-01-02": 0.0001, "2025-01-03": 0.0002},
            },
            risk_analysis={
                "excess_return_with_cost": {
                    "annualized_return": 0.12,
                    "information_ratio": 1.25,
                    "max_drawdown": -0.04,
                },
                "excess_return_without_cost": {
                    "annualized_return": 0.13,
                    "max_drawdown": -0.035,
                },
            },
            report={"total_days": 2, "positions_days": 2},
            provenance={"config_fingerprint": "abc123"},
            positions={
                "2025-01-02": {"SH600000": 0.6, "SZ000001": 0.4},
                "2025-01-03": {"SH600000": 0.5},
            },
        )

    def test_write_pipeline_result_artifacts(self) -> None:
        import pandas as pd

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            paths = write_pipeline_result_artifacts(
                out,
                config=PipelineConfig(provider_uri="/tmp/fake"),
                backtest_output=self._backtest_output(),
                started_at="2026-05-20T00:00:00+00:00",
                report_path=str(out / "pipeline_report.json"),
            )

            for name in ("metadata", "metrics", "nav", "holdings", "trades", "config"):
                self.assertTrue(Path(paths[name]).exists(), f"missing {name}")

            metadata = json.loads((out / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["type"], "pipeline")
            self.assertEqual(metadata["trade_log_status"], TRADES_NOT_PRODUCED_REASON)

            metrics = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
            self.assertEqual(metrics["metric_status"], "official")
            self.assertEqual(
                metrics["performance"]["annual_excess_return_with_cost"],
                0.12,
            )
            self.assertEqual(metrics["trading"]["positions_days"], 2)

            nav = pd.read_parquet(out / "nav.parquet")
            self.assertEqual(
                set(["date", "strategy_return", "strategy_nav", "benchmark_return", "benchmark_nav", "cost"]),
                set(nav.columns),
            )
            self.assertEqual(len(nav), 2)

            holdings = pd.read_parquet(out / "holdings.parquet")
            self.assertEqual(list(holdings.columns), ["date", "stock", "weight", "rank"])
            self.assertEqual(len(holdings), 3)
            self.assertEqual(holdings.iloc[0]["rank"], 1)

            trades = pd.read_parquet(out / "trades.parquet")
            self.assertTrue(trades.empty)
            self.assertEqual(
                list(trades.columns),
                ["date", "stock", "side", "shares", "price", "amount", "cost"],
            )

    def test_nav_artifact_rejects_non_finite_returns(self) -> None:
        output = self._backtest_output()
        bad = CanonicalBacktestOutput(
            metric_status=output.metric_status,
            official_backtest_path=output.official_backtest_path,
            return_series={
                "return": {"2025-01-02": float("nan")},
                "bench": {},
                "cost": {},
            },
            risk_analysis=output.risk_analysis,
            report=output.report,
            provenance=output.provenance,
            positions=output.positions,
        )
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(PipelineResultArtifactError, "non-finite"):
                write_pipeline_result_artifacts(
                    Path(tmp),
                    config=PipelineConfig(provider_uri="/tmp/fake"),
                    backtest_output=bad,
                    started_at="2026-05-20T00:00:00+00:00",
                    report_path=str(Path(tmp) / "pipeline_report.json"),
                )


class PipelineConfigPostInitTests(unittest.TestCase):
    """``PipelineConfig.__post_init__`` catches obviously-wrong combinations
    at the boundary so the operator does not have to wait for downstream
    validators to surface them deep in a run.

    The validation is intentionally cheap and shape-only — date *format*,
    qlib bundle alignment, and other heavy semantic checks stay where
    they were.
    """

    def test_rejects_empty_provider_uri(self) -> None:
        with self.assertRaisesRegex(PipelineError, "provider_uri"):
            PipelineConfig(provider_uri="")

    def test_rejects_empty_benchmark_code(self) -> None:
        with self.assertRaisesRegex(PipelineError, "benchmark_code"):
            PipelineConfig(provider_uri="/tmp/fake", benchmark_code="")

    def test_rejects_unknown_compute_device(self) -> None:
        with self.assertRaisesRegex(PipelineError, "compute_device"):
            PipelineConfig(provider_uri="/tmp/fake", compute_device="cuda")

    def test_rejects_gpu_for_non_lgb_model(self) -> None:
        with self.assertRaisesRegex(PipelineError, "silently fall"):
            PipelineConfig(
                provider_uri="/tmp/fake",
                model_type="XGBModel",
                compute_device="gpu",
            )

    def test_rejects_transposed_train_window(self) -> None:
        with self.assertRaisesRegex(PipelineError, "train_start"):
            PipelineConfig(
                provider_uri="/tmp/fake",
                train_start="2024-12-31",
                train_end="2022-01-01",  # earlier than start
            )

    def test_rejects_transposed_test_window(self) -> None:
        with self.assertRaisesRegex(PipelineError, "test_start"):
            PipelineConfig(
                provider_uri="/tmp/fake",
                test_start="2025-12-31",
                test_end="2025-07-01",
            )

    def test_rejects_non_iso_date_at_boundary(self) -> None:
        with self.assertRaisesRegex(PipelineError, "YYYY-MM-DD"):
            PipelineConfig(provider_uri="/tmp/fake", train_start="2025-1-01")

    def test_rejects_non_positive_init_cash(self) -> None:
        with self.assertRaisesRegex(PipelineError, "init_cash"):
            PipelineConfig(provider_uri="/tmp/fake", init_cash=0)

    def test_rejects_zero_topk(self) -> None:
        with self.assertRaisesRegex(PipelineError, "topk"):
            PipelineConfig(provider_uri="/tmp/fake", topk=0)

    def test_rejects_n_drop_gte_topk(self) -> None:
        """``n_drop >= topk`` would empty the portfolio after the first
        rebalance under TopkDropoutStrategy. ``WalkForwardConfig`` already
        rejects this; ``PipelineConfig`` now does too — same boundary
        contract regardless of which engine builds the config."""
        with self.assertRaisesRegex(PipelineError, "n_drop"):
            PipelineConfig(provider_uri="/tmp/fake", topk=10, n_drop=10)
        with self.assertRaisesRegex(PipelineError, "n_drop"):
            PipelineConfig(provider_uri="/tmp/fake", topk=10, n_drop=15)

    def test_rejects_negative_n_drop(self) -> None:
        with self.assertRaisesRegex(PipelineError, "n_drop"):
            PipelineConfig(provider_uri="/tmp/fake", n_drop=-1)

    def test_rejects_bool_n_drop(self) -> None:
        """``True`` would silently behave as ``1`` (==/!= topk depending
        on topk), and ``False`` as ``0``. Both should be rejected at the
        type layer so a YAML typo doesn't slip through."""
        with self.assertRaisesRegex(PipelineError, "n_drop"):
            PipelineConfig(provider_uri="/tmp/fake", n_drop=True)
        with self.assertRaisesRegex(PipelineError, "n_drop"):
            PipelineConfig(provider_uri="/tmp/fake", n_drop=False)

    def test_rejects_negative_commission_rate(self) -> None:
        """Negative cost components silently inflate returns. Caught at
        config construction so the cheap fail beats running the full
        feature build + train before ``CanonicalExchangeCostModel``
        catches it downstream."""
        with self.assertRaisesRegex(PipelineError, "commission_rate"):
            PipelineConfig(provider_uri="/tmp/fake", commission_rate=-0.001)

    def test_rejects_negative_stamp_tax_bps(self) -> None:
        with self.assertRaisesRegex(PipelineError, "stamp_tax_bps"):
            PipelineConfig(provider_uri="/tmp/fake", stamp_tax_bps=-1.0)

    def test_rejects_negative_slippage_bps(self) -> None:
        with self.assertRaisesRegex(PipelineError, "slippage_bps"):
            PipelineConfig(provider_uri="/tmp/fake", slippage_bps=-5.0)

    def test_rejects_negative_min_cost(self) -> None:
        with self.assertRaisesRegex(PipelineError, "min_cost"):
            PipelineConfig(provider_uri="/tmp/fake", min_cost=-0.01)

    def test_rejects_invalid_limit_threshold_early(self) -> None:
        with self.assertRaisesRegex(PipelineError, "limit_threshold"):
            PipelineConfig(provider_uri="/tmp/fake", limit_threshold=0.0)
        with self.assertRaisesRegex(PipelineError, "limit_threshold"):
            PipelineConfig(provider_uri="/tmp/fake", limit_threshold=True)

    def test_rejects_bool_cost_field(self) -> None:
        """``True`` / ``False`` would silently be 1 / 0 — accept them as
        ints, so YAML ``commission_rate: false`` becomes 0 commission. Reject."""
        with self.assertRaisesRegex(PipelineError, "commission_rate"):
            PipelineConfig(provider_uri="/tmp/fake", commission_rate=True)

    def test_accepts_zero_lag_as_explicit_same_day_execution(self) -> None:
        cfg = PipelineConfig(provider_uri="/tmp/fake", signal_to_execution_lag=0)
        self.assertEqual(cfg.signal_to_execution_lag, 0)

    def test_rejects_negative_lag(self) -> None:
        with self.assertRaisesRegex(PipelineError, "signal_to_execution_lag"):
            PipelineConfig(provider_uri="/tmp/fake", signal_to_execution_lag=-1)

    def test_rejects_bool_lag(self) -> None:
        with self.assertRaisesRegex(PipelineError, "signal_to_execution_lag must be an int"):
            PipelineConfig(provider_uri="/tmp/fake", signal_to_execution_lag=True)
        with self.assertRaisesRegex(PipelineError, "signal_to_execution_lag must be an int"):
            PipelineConfig(provider_uri="/tmp/fake", signal_to_execution_lag=False)

    def test_default_config_is_valid(self) -> None:
        # Sanity: the defaults must construct successfully so downstream
        # tests / docs that build PipelineConfig(provider_uri=...) with
        # no overrides keep working.
        cfg = PipelineConfig(provider_uri="/tmp/fake")
        self.assertEqual(cfg.region, "cn")
        self.assertEqual(cfg.signal_to_execution_lag, 1)

    def test_lgb_regularisation_defaults_match_lightgbm(self) -> None:
        """The new ``lambda_l1`` / ``lambda_l2`` / ``min_data_in_leaf`` /
        ``feature_fraction`` / ``bagging_fraction`` / ``bagging_freq``
        fields default to LightGBM's own defaults so no behaviour
        change creeps in for callers that don't set them."""
        cfg = PipelineConfig(provider_uri="/tmp/fake")
        self.assertEqual(cfg.lambda_l1, 0.0)
        self.assertEqual(cfg.lambda_l2, 0.0)
        self.assertEqual(cfg.min_data_in_leaf, 20)
        self.assertEqual(cfg.feature_fraction, 1.0)
        self.assertEqual(cfg.bagging_fraction, 1.0)
        self.assertEqual(cfg.bagging_freq, 0)

    def test_rejects_partial_industry_taxonomy_config(self) -> None:
        with self.assertRaisesRegex(PipelineError, "industry_artifact_path"):
            PipelineConfig(
                provider_uri="/tmp/fake",
                industry_artifact_path="output/taxonomy/sw_l2.csv",
            )

    def test_rejects_unsupported_industry_temporal_mode(self) -> None:
        with self.assertRaisesRegex(PipelineError, "industry_temporal_mode"):
            PipelineConfig(
                provider_uri="/tmp/fake",
                industry_temporal_mode=TAXONOMY_MODE_TRADE_DATE,
            )


class PipelineAttributionTaxonomyConfigTests(unittest.TestCase):
    @staticmethod
    def _publish_taxonomy(tmp: Path, taxonomy_name: str = "tushare_sw_l2") -> tuple[Path, Path]:
        artifact = tmp / "sw_l2.csv"
        manifest = tmp / "sw_l2.json"
        TaxonomyArtifactPublisher.publish(
            taxonomy_name=taxonomy_name,
            temporal_mode=TAXONOMY_MODE_STATIC,
            rows=[
                ("SH600000", "银行"),
                ("SZ000001", "银行"),
            ],
            artifact_path=str(artifact),
            manifest_path=str(manifest),
            snapshot_at="2025-07-01",
        )
        return artifact, manifest

    def test_valid_taxonomy_artifact_flows_into_attribution_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            artifact, manifest = self._publish_taxonomy(tmp)
            cfg = PipelineConfig(
                provider_uri="/tmp/fake",
                industry_artifact_path=str(artifact),
                industry_manifest_path=str(manifest),
                industry_taxonomy_id="tushare_sw_l2",
            )

            attr_cfg = Pipeline._build_attribution_config(cfg)

            self.assertEqual(attr_cfg.industry_taxonomy_id, "tushare_sw_l2")
            self.assertEqual(attr_cfg.industry_map_override["SH600000"], "银行")
            self.assertEqual(attr_cfg.industry_map_override["SZ000001"], "银行")

    def test_missing_taxonomy_artifact_fails_without_board_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            cfg = PipelineConfig(
                provider_uri="/tmp/fake",
                industry_artifact_path=str(tmp / "missing.csv"),
                industry_manifest_path=str(tmp / "missing.json"),
                industry_taxonomy_id="tushare_sw_l2",
            )

            with self.assertRaisesRegex(PipelineError, "missing_artifact_file"):
                Pipeline._build_attribution_config(cfg)

    def test_manifest_taxonomy_id_must_match_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            artifact, manifest = self._publish_taxonomy(tmp, taxonomy_name="actual")
            cfg = PipelineConfig(
                provider_uri="/tmp/fake",
                industry_artifact_path=str(artifact),
                industry_manifest_path=str(manifest),
                industry_taxonomy_id="expected",
            )

            with self.assertRaisesRegex(PipelineError, "taxonomy_name does not match"):
                Pipeline._build_attribution_config(cfg)


class AttributionReportSerializationTests(unittest.TestCase):
    """Pipeline JSON-report contract for attribution.

    ``print_report`` log lines have always carried the methodology /
    provenance caveats (Brinson method label, sector taxonomy, bench
    weighting, reconciliation residual). The JSON report did not — so
    any downstream consumer reading ``pipeline_report.json`` lost those
    caveats and could mistake a path-dependent residual or an equal-
    weight benchmark for an exact attribution. These tests pin the
    JSON contract so dashboards can rely on the fields being there.
    """

    @staticmethod
    def _build_result():
        from src.core.board_heuristic import (
            BOARD_HEURISTIC_TAXONOMY_ID,
            BOARD_SH_MAIN,
        )
        from src.core.performance_attribution import (
            ATTRIBUTION_METHOD_SINGLE_PERIOD,
            BENCH_WEIGHT_METHOD_EQUAL,
            AttributionResult,
            MonthlyReturn,
            SectorAttribution,
        )

        return AttributionResult(
            sector_attribution=(
                SectorAttribution(
                    sector=BOARD_SH_MAIN,
                    portfolio_weight=0.5,
                    benchmark_weight=0.4,
                    portfolio_return=0.10,
                    benchmark_return=0.08,
                    allocation_effect=0.001,
                    selection_effect=0.002,
                    interaction_effect=0.0005,
                    total_effect=0.0035,
                ),
            ),
            total_allocation_effect=0.001,
            total_selection_effect=0.002,
            total_interaction_effect=0.0005,
            monthly_returns=(
                MonthlyReturn(
                    year=2025, month=10,
                    portfolio_return=0.03, benchmark_return=0.01,
                    excess_return=0.02,
                ),
            ),
            total_portfolio_return=0.10,
            total_benchmark_return=0.05,
            total_excess_return=0.05,
            attribution_method=ATTRIBUTION_METHOD_SINGLE_PERIOD,
            sector_effects_sum=0.0035,
            reconciliation_residual=0.0465,
            sector_taxonomy=BOARD_HEURISTIC_TAXONOMY_ID,
            bench_weight_method=BENCH_WEIGHT_METHOD_EQUAL,
        )

    def test_methodology_fields_persisted_in_json(self) -> None:
        """All five methodology fields must appear in the JSON dict."""
        result = self._build_result()
        d = Pipeline._attribution_to_report_dict(result)
        for field in (
            "attribution_method",
            "sector_taxonomy",
            "bench_weight_method",
            "sector_effects_sum",
            "reconciliation_residual",
        ):
            self.assertIn(field, d, f"missing methodology field: {field}")

    def test_methodology_field_values_round_trip(self) -> None:
        """The values written to JSON must equal the values on the
        result — no rounding, no relabeling, no silent transforms."""
        result = self._build_result()
        d = Pipeline._attribution_to_report_dict(result)
        self.assertEqual(d["attribution_method"], result.attribution_method)
        self.assertEqual(d["sector_taxonomy"], result.sector_taxonomy)
        self.assertEqual(d["bench_weight_method"], result.bench_weight_method)
        self.assertAlmostEqual(d["sector_effects_sum"], result.sector_effects_sum)
        self.assertAlmostEqual(
            d["reconciliation_residual"], result.reconciliation_residual,
        )

    def test_dict_is_json_serializable(self) -> None:
        """The whole attribution dict must round-trip through json.dumps
        — protects against accidentally landing a numpy or pandas type
        in one of the methodology fields, which would break the report."""
        result = self._build_result()
        d = Pipeline._attribution_to_report_dict(result)
        encoded = json.dumps(d)
        decoded = json.loads(encoded)
        self.assertEqual(decoded["attribution_method"], result.attribution_method)
        self.assertEqual(decoded["sector_taxonomy"], result.sector_taxonomy)


class AttributionSectionStatusTests(unittest.TestCase):
    """``attribution`` block must always appear in the JSON report,
    with a machine-readable ``status`` and ``skipped_reason``.

    The four cases that used to look identical in JSON (no
    ``attribution`` key at all) are now distinguishable:
    - ``status="ok"`` — engine succeeded; full data block present.
    - ``status="skipped", skipped_reason="disabled_by_config"``
    - ``status="skipped", skipped_reason="no_positions_from_backtest"``
    - ``status="skipped", skipped_reason="engine_error: ..."``

    Dashboards and downstream consumers can now surface degraded runs
    instead of treating them as "data missing".
    """

    @staticmethod
    def _build_ok_result():
        from src.core.board_heuristic import BOARD_HEURISTIC_TAXONOMY_ID
        from src.core.performance_attribution import (
            ATTRIBUTION_METHOD_SINGLE_PERIOD,
            BENCH_WEIGHT_METHOD_EQUAL,
            AttributionResult,
        )

        return AttributionResult(
            sector_attribution=(),
            total_allocation_effect=0.0,
            total_selection_effect=0.0,
            total_interaction_effect=0.0,
            monthly_returns=(),
            total_portfolio_return=0.0,
            total_benchmark_return=0.0,
            total_excess_return=0.0,
            attribution_method=ATTRIBUTION_METHOD_SINGLE_PERIOD,
            sector_effects_sum=0.0,
            reconciliation_residual=0.0,
            sector_taxonomy=BOARD_HEURISTIC_TAXONOMY_ID,
            bench_weight_method=BENCH_WEIGHT_METHOD_EQUAL,
        )

    def test_ok_status_when_result_present(self) -> None:
        block = Pipeline._attribution_section(self._build_ok_result(), None)
        self.assertEqual(block["status"], "ok")
        self.assertIsNone(block["skipped_reason"])
        # And the methodology fields from the previous PR are still here.
        self.assertIn("attribution_method", block)
        self.assertIn("sector_taxonomy", block)
        self.assertIn("bench_weight_method", block)

    def test_skipped_disabled_by_config(self) -> None:
        block = Pipeline._attribution_section(None, "disabled_by_config")
        self.assertEqual(block["status"], "skipped")
        self.assertEqual(block["skipped_reason"], "disabled_by_config")

    def test_skipped_no_positions(self) -> None:
        block = Pipeline._attribution_section(None, "no_positions_from_backtest")
        self.assertEqual(block["status"], "skipped")
        self.assertEqual(block["skipped_reason"], "no_positions_from_backtest")

    def test_skipped_engine_error_records_class_and_message(self) -> None:
        reason = "engine_error: PerformanceAttributionError: all-non-positive"
        block = Pipeline._attribution_section(None, reason)
        self.assertEqual(block["status"], "skipped")
        self.assertEqual(block["skipped_reason"], reason)

    def test_skipped_with_no_reason_falls_back_to_unknown(self) -> None:
        """Programmer-error guard: passing skipped + None reason should
        still produce a report-readable string rather than ``null``."""
        block = Pipeline._attribution_section(None, None)
        self.assertEqual(block["status"], "skipped")
        self.assertEqual(block["skipped_reason"], "unknown_reason")


class SignalAnalysisSectionTests(unittest.TestCase):
    """``Pipeline._signal_analysis_section`` must coerce ``ic_summary``
    keys to ``str`` *up front*, not lean on ``json.dump``'s implicit
    int -> str.

    The legacy code wrote ``dict(signal_result.ic_summary)`` directly,
    which left keys as ints in memory; ``json.dump`` then stringified
    them on write. After ``json.load`` the round-tripped dict had
    str keys, so a single consumer reading both fresh-from-memory and
    reloaded-from-disk dicts would have to special-case both shapes.
    Aligning the writer to mirror walk_forward's explicit coercion
    closes that.
    """

    @staticmethod
    def _build_signal_result():
        from src.core.signal_analyzer import SignalAnalysisResult
        return SignalAnalysisResult(
            ic_summary={
                1: {"mean_ic": 0.012, "std_ic": 0.018, "ir": 0.667, "num_days": 60},
                5: {"mean_ic": 0.024, "std_ic": 0.020, "ir": 1.20, "num_days": 60},
            },
            ic_series={},
            ic_decay=[0.012, 0.020, 0.024, 0.018, 0.010],
            turnover_stats={"mean_turnover": 0.4, "std_turnover": 0.05},
        )

    def test_keys_are_strings_in_memory(self) -> None:
        section = Pipeline._signal_analysis_section(self._build_signal_result())
        keys = list(section["ic_summary"].keys())
        self.assertTrue(
            all(isinstance(k, str) for k in keys),
            f"ic_summary keys must be strings; got {keys!r}",
        )
        self.assertEqual(set(keys), {"1", "5"})

    def test_inner_stats_round_trip(self) -> None:
        section = Pipeline._signal_analysis_section(self._build_signal_result())
        self.assertAlmostEqual(section["ic_summary"]["1"]["mean_ic"], 0.012)
        self.assertAlmostEqual(section["ic_summary"]["5"]["ir"], 1.20)

    def test_ic_decay_and_turnover_passed_through(self) -> None:
        section = Pipeline._signal_analysis_section(self._build_signal_result())
        self.assertEqual(len(section["ic_decay"]), 5)
        self.assertEqual(section["turnover"]["mean_turnover"], 0.4)

    def test_strict_json_round_trip_preserves_str_keys(self) -> None:
        """End-to-end: dump with ``allow_nan=False`` (the writer's
        actual mode), reload, and assert the ic_summary keys are still
        strings — and equal to the in-memory keys. Without the explicit
        coercion these two dicts would differ (int vs str)."""
        section = Pipeline._signal_analysis_section(self._build_signal_result())
        encoded = json.dumps(section, allow_nan=False)
        decoded = json.loads(encoded)
        self.assertEqual(
            set(section["ic_summary"].keys()),
            set(decoded["ic_summary"].keys()),
            "in-memory and round-tripped key sets must match exactly",
        )
        # Belt + braces: both are ``str``.
        self.assertTrue(all(isinstance(k, str) for k in decoded["ic_summary"]))


class JsonSanitizationTests(unittest.TestCase):
    """``_sanitize_for_json`` must replace non-finite floats with
    ``None`` so ``json.dumps`` produces standard JSON.

    Why this matters: SignalAnalyzer and FactorAnalyzer use NaN to
    encode "undefined IR" (zero or single-day std). Python's default
    ``json.dump`` emits the literal token ``NaN`` for those — non-
    standard JSON that browsers, ``jq``, and strict parsers reject.
    The sanitizer converts NaN → null so the report stays parseable.
    """

    def test_top_level_nan_replaced_with_none(self) -> None:
        out = _sanitize_for_json({"x": float("nan")})
        self.assertIsNone(out["x"])

    def test_inf_replaced_with_none(self) -> None:
        out = _sanitize_for_json({"a": float("inf"), "b": float("-inf")})
        self.assertIsNone(out["a"])
        self.assertIsNone(out["b"])

    def test_finite_floats_pass_through(self) -> None:
        out = _sanitize_for_json({"x": 1.5, "y": -2.0, "z": 0.0})
        self.assertEqual(out, {"x": 1.5, "y": -2.0, "z": 0.0})

    def test_nested_structures_walked(self) -> None:
        nested = {
            "a": [1.0, float("nan"), {"b": float("nan")}],
            "c": ({"d": 1.0, "e": float("nan")},),
        }
        out = _sanitize_for_json(nested)
        self.assertEqual(out["a"][0], 1.0)
        self.assertIsNone(out["a"][1])
        self.assertIsNone(out["a"][2]["b"])
        self.assertEqual(out["c"][0]["d"], 1.0)
        self.assertIsNone(out["c"][0]["e"])

    def test_strings_ints_bools_unchanged(self) -> None:
        out = _sanitize_for_json({"s": "hi", "n": 42, "b": True, "x": None})
        self.assertEqual(out, {"s": "hi", "n": 42, "b": True, "x": None})

    def test_sanitized_dict_round_trips_through_strict_json(self) -> None:
        """End-to-end: a NaN-laden report must produce parseable
        standard JSON after sanitization."""
        report = {
            "factor_analysis": {
                "top_factors": [
                    {"name": "FOO", "ir": float("nan"), "mean_ic": 0.05},
                    {"name": "BAR", "ir": 1.5, "mean_ic": 0.04},
                ],
            },
        }
        sanitized = _sanitize_for_json(report)
        encoded = json.dumps(sanitized, allow_nan=False)
        decoded = json.loads(encoded)
        self.assertIsNone(decoded["factor_analysis"]["top_factors"][0]["ir"])
        self.assertEqual(decoded["factor_analysis"]["top_factors"][1]["ir"], 1.5)


from tests.e2e_guard import skip_unless_e2e


@skip_unless_e2e
@unittest.skipUnless(_HAS_QLIB_DATA, "qlib data bundle not available")
class PipelineE2ETests(unittest.TestCase):
    """End-to-end pipeline test. Runs the full workflow."""

    def test_full_pipeline_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = PipelineConfig(
                provider_uri=str(_QLIB_DATA_DIR),
                region="cn",
                instruments="csi300",
                feature_handler="Alpha158",
                train_start="2024-01-01",
                train_end="2025-06-30",
                valid_start="2025-07-01",
                valid_end="2025-09-30",
                test_start="2025-10-01",
                test_end="2025-12-31",
                model_type="LGBModel",
                num_boost_round=20,
                early_stopping_rounds=5,
                benchmark_code="SH600000",
                topk=30,
                n_drop=3,
                output_dir=tmp,
            )
            result = Pipeline.run(config)

            self.assertEqual(result.backtest_output.metric_status, "official")
            self.assertGreater(result.feature_result.train_shape[0], 0)
            self.assertGreater(result.model_result.prediction_shape[0], 0)

            # Check report was written
            report_path = Path(result.report_path)
            self.assertTrue(report_path.exists())
            with report_path.open() as f:
                report = json.load(f)
            self.assertEqual(report["metric_status"], "official")
            self.assertIn("risk_analysis", report)


if __name__ == "__main__":
    unittest.main()
