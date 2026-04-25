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

from src.core.pipeline import Pipeline, PipelineConfig, PipelineError, _sanitize_for_json


_QLIB_DATA_DIR = Path(r"D:/qlib_data/my_cn_data")
_HAS_QLIB_DATA = _QLIB_DATA_DIR.exists() and (_QLIB_DATA_DIR / "calendars").exists()


class PipelineStructuralTests(unittest.TestCase):
    def test_config_defaults_are_valid(self) -> None:
        config = PipelineConfig(provider_uri="/tmp/fake")
        self.assertEqual(config.region, "cn")
        self.assertEqual(config.instruments, "csi300")
        self.assertEqual(config.model_type, "LGBModel")

    def test_make_run_dir_has_timestamp_and_fingerprint(self) -> None:
        config = PipelineConfig(provider_uri="/tmp/fake")
        root = Path("/tmp/any_root")
        run_dir = Pipeline._make_run_dir(root, config)
        # Must live under runs/ and follow: YYYYMMDD_HHMMSS_<microsec>_<12hex>
        self.assertEqual(run_dir.parent, root / "runs")
        name = run_dir.name
        parts = name.split("_")
        # Format: YYYYMMDD_HHMMSS_<microsec><ns_tail>_<12hex> → 4 parts
        self.assertEqual(len(parts), 4)
        self.assertEqual(len(parts[0]), 8)    # date
        self.assertEqual(len(parts[1]), 6)    # time
        self.assertEqual(len(parts[2]), 12)   # microseconds (6) + ns_tail (6)
        self.assertEqual(len(parts[3]), 12)   # sha256 prefix

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
        fp1 = Pipeline._make_run_dir(Path("/tmp"), config1).name.split("_")[-1]
        fp2 = Pipeline._make_run_dir(Path("/tmp"), config2).name.split("_")[-1]
        self.assertEqual(fp1, fp2)

    def test_make_run_dir_fingerprint_changes_with_config(self) -> None:
        config1 = PipelineConfig(provider_uri="/tmp/fake", topk=50)
        config2 = PipelineConfig(provider_uri="/tmp/fake", topk=100)
        fp1 = Pipeline._make_run_dir(Path("/tmp"), config1).name.split("_")[-1]
        fp2 = Pipeline._make_run_dir(Path("/tmp"), config2).name.split("_")[-1]
        self.assertNotEqual(fp1, fp2)


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
        from src.core.performance_attribution import (
            ATTRIBUTION_METHOD_SINGLE_PERIOD,
            BENCH_WEIGHT_METHOD_EQUAL,
            AttributionResult,
            MonthlyReturn,
            SectorAttribution,
        )
        from src.core.board_heuristic import (
            BOARD_HEURISTIC_TAXONOMY_ID,
            BOARD_SH_MAIN,
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
        from src.core.performance_attribution import (
            ATTRIBUTION_METHOD_SINGLE_PERIOD,
            BENCH_WEIGHT_METHOD_EQUAL,
            AttributionResult,
        )
        from src.core.board_heuristic import BOARD_HEURISTIC_TAXONOMY_ID

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
