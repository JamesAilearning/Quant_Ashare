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

from src.core.pipeline import Pipeline, PipelineConfig, PipelineError


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
        # Must live under runs/ and have two underscore-separated segments
        self.assertEqual(run_dir.parent, root / "runs")
        name = run_dir.name
        parts = name.split("_")
        # Format: YYYYMMDD_HHMMSS_<12hex>  →  3 parts
        self.assertEqual(len(parts), 3)
        self.assertEqual(len(parts[0]), 8)
        self.assertEqual(len(parts[1]), 6)
        self.assertEqual(len(parts[2]), 12)

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
