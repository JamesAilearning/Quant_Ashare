"""Unit tests for job lifecycle manager — subprocess boundaries and file outputs."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import sys as _sys

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_PROJECT_ROOT))


class JobManagerStartTests(unittest.TestCase):
    """subprocess.Popen is called with shell=False and correct args."""

    def test_start_pipeline_uses_correct_args(self) -> None:
        config = {"provider_uri": "/data", "instruments": "csi300"}
        with patch("web.operator_ui.job_manager.JOB_ROOT", Path(tempfile.mkdtemp())):
            with patch("web.operator_ui.job_manager.RESULT_ROOT", Path(tempfile.mkdtemp())):
                with patch("subprocess.Popen") as mock_popen:
                    mock_proc = MagicMock()
                    mock_proc.pid = 12345
                    mock_popen.return_value = mock_proc
                    from web.operator_ui.job_manager import JobManager
                    job_id = JobManager.start(config, "pipeline")
                    self.assertTrue(job_id.startswith("pipeline_"))
                    args_list = mock_popen.call_args[0][0] if mock_popen.call_args[0] else mock_popen.call_args.kwargs.get("args", [])
                    self.assertIn("shell=False", str(mock_popen.call_args))
                    self.assertIn("-m", args_list)
                    self.assertIn("web.operator_ui.job_runner", str(args_list))

    def test_start_walk_forward_uses_correct_args(self) -> None:
        config = {"provider_uri": "/data", "overall_start": "2022-01-01", "overall_end": "2025-12-31",
                   "train_months": 24, "valid_months": 3, "test_months": 3, "step_months": 3}
        with patch("web.operator_ui.job_manager.JOB_ROOT", Path(tempfile.mkdtemp())):
            with patch("web.operator_ui.job_manager.RESULT_ROOT", Path(tempfile.mkdtemp())):
                with patch("subprocess.Popen") as mock_popen:
                    mock_proc = MagicMock()
                    mock_proc.pid = 12346
                    mock_popen.return_value = mock_proc
                    from web.operator_ui.job_manager import JobManager
                    job_id = JobManager.start(config, "walk_forward")
                    self.assertTrue(job_id.startswith("walk_forward_"))
                    self.assertIn("shell=False", str(mock_popen.call_args))

    def test_start_writes_job_json(self) -> None:
        config = {"provider_uri": "/data"}
        job_root = Path(tempfile.mkdtemp())
        result_root = Path(tempfile.mkdtemp())
        with patch("web.operator_ui.job_manager.JOB_ROOT", job_root):
            with patch("web.operator_ui.job_manager.RESULT_ROOT", result_root):
                with patch("subprocess.Popen") as mock_popen:
                    mock_proc = MagicMock()
                    mock_proc.pid = 99999
                    mock_popen.return_value = mock_proc
                    from web.operator_ui.job_manager import JobManager
                    job_id = JobManager.start(config, "pipeline")
                    job_json = job_root / job_id / "job.json"
                    self.assertTrue(job_json.is_file())
                    data = json.loads(job_json.read_text(encoding="utf-8"))
                    self.assertEqual(data["job_id"], job_id)
                    self.assertEqual(data["mode"], "pipeline")
                    self.assertIn(data["status"], ("running", "pending"))
                    self.assertEqual(data["pid"], 99999)


class JobManagerStopTests(unittest.TestCase):
    """stop() runs taskkill with correct arguments."""

    def test_stop_runs_taskkill_with_pid(self) -> None:
        job_root = Path(tempfile.mkdtemp())
        job_dir = job_root / "test_job"
        job_dir.mkdir(parents=True)
        job_dir.joinpath("job.json").write_text(
            json.dumps({"job_id": "test_job", "status": "running", "pid": 12345}),
            encoding="utf-8",
        )
        with patch("web.operator_ui.job_manager.JOB_ROOT", job_root):
            with patch("subprocess.run") as mock_run:
                from web.operator_ui.job_manager import JobManager
                JobManager.stop("test_job")
                args = mock_run.call_args[0][0] if mock_run.call_args[0] else []
                self.assertIn("taskkill", str(args).lower())
                self.assertIn("/t", str(args).lower())
                self.assertIn("12345", str(args))

    def test_stop_writes_stopped_status(self) -> None:
        job_root = Path(tempfile.mkdtemp())
        job_dir = job_root / "test_job2"
        job_dir.mkdir(parents=True)
        job_dir.joinpath("job.json").write_text(
            json.dumps({"job_id": "test_job2", "status": "running", "pid": 12346}),
            encoding="utf-8",
        )
        with patch("web.operator_ui.job_manager.JOB_ROOT", job_root):
            with patch("subprocess.run"):
                from web.operator_ui.job_manager import JobManager
                JobManager.stop("test_job2")
                data = json.loads(job_dir.joinpath("job.json").read_text(encoding="utf-8"))
                self.assertEqual(data["status"], "stopped")
                self.assertIsNotNone(data["ended_at"])


if __name__ == "__main__":
    unittest.main()
