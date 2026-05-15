"""Unit tests for operator UI job progress estimates."""

from __future__ import annotations

import json
import sys as _sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_PROJECT_ROOT))


class OperatorUiProgressTests(unittest.TestCase):
    def test_success_job_reports_completed_progress(self) -> None:
        from web.operator_ui.progress import build_job_progress

        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp)
            progress = build_job_progress(
                job_dir,
                {"status": "success", "mode": "pipeline", "run_dir": str(job_dir)},
            )

        self.assertEqual(progress["percent"], 100)
        self.assertEqual(progress["label"], "Completed")
        self.assertIn("run_dir=", progress["detail"])

    def test_tushare_progress_uses_provider_artifacts(self) -> None:
        from web.operator_ui.progress import build_job_progress

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job_dir = root / "job"
            job_dir.mkdir()
            output_dir = root / "qlib_provider"
            features_dir = output_dir / "features" / "SH600000"
            features_dir.mkdir(parents=True)
            (features_dir / "close.day.bin").write_bytes(b"data")
            staging_dir = root / "staging"
            staging_dir.mkdir()
            (staging_dir / "daily.csv").write_text("trade_date,ts_code\n", encoding="utf-8")
            validation_path = root / "validation.json"
            validation_path.write_text(
                json.dumps({"health": "ok", "row_count": 10, "instrument_count": 1}),
                encoding="utf-8",
            )
            config_path = job_dir / "config.yaml"
            config_path.write_text(
                f"output_dir: {output_dir}\n"
                f"staging_dir: {staging_dir}\n"
                f"validation_path: {validation_path}\n",
                encoding="utf-8",
            )

            progress = build_job_progress(
                job_dir,
                {
                    "status": "running",
                    "mode": "tushare_provider",
                    "config_path": str(config_path),
                },
            )

        self.assertGreaterEqual(progress["percent"], 95)
        self.assertEqual(progress["label"], "Generated provider validation artifacts")
        self.assertIn("validation_health=ok", progress["detail"])

    def test_pipeline_progress_detects_report_artifact(self) -> None:
        from web.operator_ui.progress import build_job_progress

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job_dir = root / "job"
            job_dir.mkdir()
            output_dir = root / "output"
            run_dir = output_dir / "runs" / "run_1"
            run_dir.mkdir(parents=True)
            (run_dir / "pipeline_report.json").write_text("{}", encoding="utf-8")
            config_path = job_dir / "config.yaml"
            config_path.write_text(f"output_dir: {output_dir}\n", encoding="utf-8")

            progress = build_job_progress(
                job_dir,
                {
                    "status": "running",
                    "mode": "pipeline",
                    "config_path": str(config_path),
                },
            )

        self.assertGreaterEqual(progress["percent"], 92)
        self.assertEqual(progress["label"], "Pipeline report written")

    def test_job_manager_status_attaches_progress(self) -> None:
        job_root = Path(tempfile.mkdtemp())
        job_dir = job_root / "test_job"
        job_dir.mkdir(parents=True)
        job_dir.joinpath("job.json").write_text(
            json.dumps({"job_id": "test_job", "status": "success", "mode": "pipeline"}),
            encoding="utf-8",
        )

        with patch("web.operator_ui.job_manager.JOB_ROOT", job_root):
            from web.operator_ui.job_manager import JobManager

            status = JobManager.status("test_job")

        self.assertEqual(status["progress"]["percent"], 100)
        self.assertEqual(status["progress"]["label"], "Completed")


if __name__ == "__main__":
    unittest.main()
