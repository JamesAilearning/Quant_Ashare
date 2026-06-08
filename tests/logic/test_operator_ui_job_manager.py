"""Unit tests for job lifecycle manager — subprocess boundaries and file outputs."""

from __future__ import annotations

import json
import subprocess
import sys as _sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_PROJECT_ROOT))


class JobManagerStartTests(unittest.TestCase):
    """subprocess.Popen is called with shell=False and correct args."""

    def test_default_job_and_result_roots_are_repo_anchored(self) -> None:
        from web.operator_ui.job_manager import JOB_ROOT, PROJECT_ROOT, RESULT_ROOT

        self.assertTrue(JOB_ROOT.is_absolute())
        self.assertTrue(RESULT_ROOT.is_absolute())
        self.assertEqual(JOB_ROOT, PROJECT_ROOT / "output" / "operator_ui" / "jobs")
        self.assertEqual(RESULT_ROOT, PROJECT_ROOT / "output" / "operator_ui" / "results")

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
                    self.assertIn("runner_stdout_path", data)
                    self.assertIn("runner_stderr_path", data)

    def test_start_captures_runner_logs_and_sets_pythonpath(self) -> None:
        config = {"provider_uri": "/data"}
        job_root = Path(tempfile.mkdtemp())
        result_root = Path(tempfile.mkdtemp())
        with patch("web.operator_ui.job_manager.JOB_ROOT", job_root):
            with patch("web.operator_ui.job_manager.RESULT_ROOT", result_root):
                with patch("subprocess.Popen") as mock_popen:
                    mock_proc = MagicMock()
                    mock_proc.pid = 99997
                    mock_popen.return_value = mock_proc
                    from web.operator_ui.job_manager import PROJECT_ROOT, JobManager

                    job_id = JobManager.start(config, "pipeline")

        job_dir = job_root / job_id
        self.assertTrue(job_dir.joinpath("runner_stdout.log").is_file())
        self.assertTrue(job_dir.joinpath("runner_stderr.log").is_file())
        kwargs = mock_popen.call_args.kwargs
        self.assertEqual(kwargs["cwd"], PROJECT_ROOT)
        self.assertIn(str(PROJECT_ROOT), kwargs["env"]["PYTHONPATH"])


class JobManagerStopTests(unittest.TestCase):
    """stop() terminates the UI job on each supported platform."""

    def test_stop_runs_taskkill_with_pid(self) -> None:
        job_root = Path(tempfile.mkdtemp())
        job_dir = job_root / "test_job"
        job_dir.mkdir(parents=True)
        job_dir.joinpath("job.json").write_text(
            json.dumps({"job_id": "test_job", "status": "running", "pid": 12345}),
            encoding="utf-8",
        )
        with patch("web.operator_ui.job_manager.JOB_ROOT", job_root):
            with patch("web.operator_ui.job_manager.platform.system", return_value="Windows"):
                with patch("subprocess.run") as mock_run:
                    mock_run.return_value = subprocess.CompletedProcess(
                        args=["taskkill"], returncode=0
                    )
                    from web.operator_ui.job_manager import JobManager
                    JobManager.stop("test_job")
                    args = mock_run.call_args[0][0] if mock_run.call_args[0] else []
                    self.assertIn("taskkill", str(args).lower())
                    self.assertIn("/t", str(args).lower())
                    self.assertIn("12345", str(args))

    def test_stop_rejects_path_traversal_job_id(self) -> None:
        job_root = Path(tempfile.mkdtemp())

        with patch("web.operator_ui.job_manager.JOB_ROOT", job_root):
            from web.operator_ui.job_manager import JobManager, JobManagerError

            for bad_job_id in ("..\\outside", "foo\\bar", "foo/bar"):
                with self.subTest(bad_job_id=bad_job_id):
                    with self.assertRaises(JobManagerError):
                        JobManager.stop(bad_job_id)

    def test_stop_missing_job_does_not_create_job_dir(self) -> None:
        job_root = Path(tempfile.mkdtemp())

        with patch("web.operator_ui.job_manager.JOB_ROOT", job_root):
            from web.operator_ui.job_manager import JobManager, JobManagerError

            with self.assertRaises(JobManagerError):
                JobManager.stop("missing_job")

        self.assertFalse((job_root / "missing_job").exists())

    def test_stop_writes_stopped_status(self) -> None:
        job_root = Path(tempfile.mkdtemp())
        job_dir = job_root / "test_job2"
        job_dir.mkdir(parents=True)
        job_dir.joinpath("job.json").write_text(
            json.dumps({"job_id": "test_job2", "status": "running", "pid": 12346}),
            encoding="utf-8",
        )
        with patch("web.operator_ui.job_manager.JOB_ROOT", job_root):
            with patch("web.operator_ui.job_manager.platform.system", return_value="Windows"):
                with patch("subprocess.run") as mock_run:
                    mock_run.return_value = subprocess.CompletedProcess(
                        args=["taskkill"], returncode=0
                    )
                    from web.operator_ui.job_manager import JobManager
                    JobManager.stop("test_job2")
                    data = json.loads(job_dir.joinpath("job.json").read_text(encoding="utf-8"))
                    self.assertEqual(data["status"], "stopped")
                    self.assertIsNotNone(data["ended_at"])

    def test_stop_failure_does_not_write_stopped_status(self) -> None:
        job_root = Path(tempfile.mkdtemp())
        job_dir = job_root / "test_job3"
        job_dir.mkdir(parents=True)
        job_dir.joinpath("job.json").write_text(
            json.dumps({"job_id": "test_job3", "status": "running", "pid": 12347}),
            encoding="utf-8",
        )
        with patch("web.operator_ui.job_manager.JOB_ROOT", job_root):
            with patch("web.operator_ui.job_manager.platform.system", return_value="Windows"):
                with patch("subprocess.run") as mock_run:
                    mock_run.return_value = subprocess.CompletedProcess(
                        args=["taskkill"],
                        returncode=128,
                        stderr="Access is denied.",
                    )
                    from web.operator_ui.job_manager import JobManager, JobManagerError
                    with self.assertRaises(JobManagerError):
                        JobManager.stop("test_job3")
                    data = json.loads(job_dir.joinpath("job.json").read_text(encoding="utf-8"))
                    self.assertEqual(data["status"], "stop_failed")
                    self.assertEqual(data["stop_returncode"], 128)
                    self.assertIn("Access is denied", data["stop_error"])

    def test_stop_non_windows_signals_process_group_when_available(self) -> None:
        job_root = Path(tempfile.mkdtemp())
        job_dir = job_root / "test_job_posix"
        job_dir.mkdir(parents=True)
        job_dir.joinpath("job.json").write_text(
            json.dumps({
                "job_id": "test_job_posix",
                "status": "running",
                "pid": 12348,
                "process_group": "own_session",
            }),
            encoding="utf-8",
        )
        with patch("web.operator_ui.job_manager.JOB_ROOT", job_root):
            with patch("web.operator_ui.job_manager.platform.system", return_value="Linux"):
                with patch("web.operator_ui.job_manager.os.getpgid", return_value=54321, create=True) as mock_getpgid:
                    with patch("web.operator_ui.job_manager.os.killpg", create=True) as mock_killpg:
                        with patch("web.operator_ui.job_manager._wait_for_pid_exit", return_value=True):
                            from web.operator_ui.job_manager import JobManager, signal

                            JobManager.stop("test_job_posix")

        mock_getpgid.assert_called_once_with(12348)
        mock_killpg.assert_called_once_with(54321, signal.SIGTERM)
        data = json.loads(job_dir.joinpath("job.json").read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "stopped")

    def test_stop_non_windows_falls_back_to_pid_signal(self) -> None:
        job_root = Path(tempfile.mkdtemp())
        job_dir = job_root / "test_job_posix_pid"
        job_dir.mkdir(parents=True)
        job_dir.joinpath("job.json").write_text(
            json.dumps({"job_id": "test_job_posix_pid", "status": "running", "pid": 12349}),
            encoding="utf-8",
        )
        with patch("web.operator_ui.job_manager.JOB_ROOT", job_root):
            with patch("web.operator_ui.job_manager.platform.system", return_value="Linux"):
                with patch("web.operator_ui.job_manager.os.kill") as mock_kill:
                    with patch("web.operator_ui.job_manager._wait_for_pid_exit", return_value=True):
                        from web.operator_ui.job_manager import JobManager, signal

                        JobManager.stop("test_job_posix_pid")

        mock_kill.assert_called_once_with(12349, signal.SIGTERM)

    def test_stop_without_pid_does_not_write_stopped_status(self) -> None:
        job_root = Path(tempfile.mkdtemp())
        job_dir = job_root / "test_job4"
        job_dir.mkdir(parents=True)
        job_dir.joinpath("job.json").write_text(
            json.dumps({"job_id": "test_job4", "status": "running", "pid": None}),
            encoding="utf-8",
        )
        with patch("web.operator_ui.job_manager.JOB_ROOT", job_root):
            from web.operator_ui.job_manager import JobManager, JobManagerError
            with self.assertRaises(JobManagerError):
                JobManager.stop("test_job4")
            data = json.loads(job_dir.joinpath("job.json").read_text(encoding="utf-8"))
            self.assertEqual(data["status"], "stop_failed")
            self.assertIn("no pid", data["stop_error"])


class JobManagerDeleteTests(unittest.TestCase):
    def test_delete_removes_non_running_job_dir(self) -> None:
        job_root = Path(tempfile.mkdtemp())
        job_dir = job_root / "finished_job"
        job_dir.mkdir(parents=True)
        job_dir.joinpath("job.json").write_text(
            json.dumps({"job_id": "finished_job", "status": "success", "pid": 12345}),
            encoding="utf-8",
        )

        with patch("web.operator_ui.job_manager.JOB_ROOT", job_root):
            from web.operator_ui.job_manager import JobManager

            JobManager.delete("finished_job")

        self.assertFalse(job_dir.exists())

    def test_delete_rejects_running_job(self) -> None:
        job_root = Path(tempfile.mkdtemp())
        job_dir = job_root / "running_job"
        job_dir.mkdir(parents=True)
        job_dir.joinpath("job.json").write_text(
            json.dumps({"job_id": "running_job", "status": "running", "pid": 12345}),
            encoding="utf-8",
        )

        with patch("web.operator_ui.job_manager.JOB_ROOT", job_root):
            from web.operator_ui.job_manager import JobManager, JobManagerError

            with self.assertRaises(JobManagerError):
                JobManager.delete("running_job")

        self.assertTrue(job_dir.is_dir())

    def test_delete_rejects_path_traversal_job_id(self) -> None:
        job_root = Path(tempfile.mkdtemp())

        with patch("web.operator_ui.job_manager.JOB_ROOT", job_root):
            from web.operator_ui.job_manager import JobManager, JobManagerError

            for bad_job_id in ("..\\outside", "foo\\bar", "foo/bar"):
                with self.subTest(bad_job_id=bad_job_id):
                    with self.assertRaises(JobManagerError):
                        JobManager.delete(bad_job_id)

    # ----------------------------------------------------------------
    # Regression for bug.md P1-3: ``delete()`` only cleaned the job
    # config dir under JOB_ROOT, never touched the corresponding
    # ``RESULT_ROOT / job_id`` which holds the model pickle + reports
    # (the actually-big artifacts). This left a disk-space leak for
    # every UI-deleted job.
    # ----------------------------------------------------------------

    def test_delete_also_removes_result_dir(self) -> None:
        job_root = Path(tempfile.mkdtemp())
        result_root = Path(tempfile.mkdtemp())
        job_dir = job_root / "finished_job"
        job_dir.mkdir(parents=True)
        job_dir.joinpath("job.json").write_text(
            json.dumps({"job_id": "finished_job", "status": "success"}),
            encoding="utf-8",
        )
        # Result dir with one fake artifact — must also be gone after delete.
        result_dir = result_root / "finished_job"
        result_dir.mkdir(parents=True)
        (result_dir / "model.pkl").write_bytes(b"\x80\x03N.")  # pickle stub

        with patch("web.operator_ui.job_manager.JOB_ROOT", job_root), \
             patch("web.operator_ui.job_manager.RESULT_ROOT", result_root):
            from web.operator_ui.job_manager import JobManager

            JobManager.delete("finished_job")

        self.assertFalse(job_dir.exists())
        self.assertFalse(
            result_dir.exists(),
            "result directory must be cleaned alongside the job dir — "
            "P1-3 regression",
        )

    def test_delete_tolerates_missing_result_dir(self) -> None:
        """A job that failed before producing any artifacts may have
        no result dir at all. ``delete()`` must not raise in that
        case — it should clean the job config and silently skip the
        absent result dir."""
        job_root = Path(tempfile.mkdtemp())
        result_root = Path(tempfile.mkdtemp())
        job_dir = job_root / "no_artifacts_job"
        job_dir.mkdir(parents=True)
        job_dir.joinpath("job.json").write_text(
            json.dumps({"job_id": "no_artifacts_job", "status": "failed"}),
            encoding="utf-8",
        )
        # Note: no result_root / "no_artifacts_job" — never created.

        with patch("web.operator_ui.job_manager.JOB_ROOT", job_root), \
             patch("web.operator_ui.job_manager.RESULT_ROOT", result_root):
            from web.operator_ui.job_manager import JobManager

            JobManager.delete("no_artifacts_job")  # must not raise

        self.assertFalse(job_dir.exists())


class JobManagerStatusTests(unittest.TestCase):
    def test_status_rejects_path_traversal_job_id(self) -> None:
        job_root = Path(tempfile.mkdtemp())

        with patch("web.operator_ui.job_manager.JOB_ROOT", job_root):
            from web.operator_ui.job_manager import JobManager, JobManagerError

            for bad_job_id in ("..\\outside", "foo\\bar", "foo/bar"):
                with self.subTest(bad_job_id=bad_job_id):
                    with self.assertRaises(JobManagerError):
                        JobManager.status(bad_job_id)


if __name__ == "__main__":
    unittest.main()
