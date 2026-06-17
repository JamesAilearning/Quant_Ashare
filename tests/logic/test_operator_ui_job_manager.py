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
                    # created_at is stamped at creation (audit G: jobs page sorts
                    # / date-filters on it; previously never written).
                    self.assertTrue(data.get("created_at"))

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

    def test_start_windows_uses_new_process_group(self) -> None:
        """Audit G2: on Windows the training subprocess must be spawned in its
        OWN process group (CREATE_NEW_PROCESS_GROUP) so a Ctrl+C / console-close
        on the Streamlit server does not propagate to and kill the running job.
        ``start_new_session`` (POSIX) must NOT be set."""
        config = {"provider_uri": "/data"}
        job_root = Path(tempfile.mkdtemp())
        result_root = Path(tempfile.mkdtemp())
        with patch("web.operator_ui.job_manager.JOB_ROOT", job_root):
            with patch("web.operator_ui.job_manager.RESULT_ROOT", result_root):
                with patch("web.operator_ui.job_manager.platform.system", return_value="Windows"):
                    with patch("subprocess.Popen") as mock_popen:
                        mock_proc = MagicMock()
                        mock_proc.pid = 4242
                        mock_popen.return_value = mock_proc
                        from web.operator_ui.job_manager import JobManager
                        job_id = JobManager.start(config, "pipeline")

        kwargs = mock_popen.call_args.kwargs
        self.assertIn("creationflags", kwargs)
        # Assert the LITERAL Win32 value (0x200), not getattr(subprocess, ...):
        # production now uses a hardcoded constant, so this is meaningful on the
        # ubuntu CI leg too (where subprocess.CREATE_NEW_PROCESS_GROUP is absent
        # and a getattr-based assertion would tautologically be 0 == 0, masking a
        # degraded flag). Also pin non-zero so a 0 (no-op) can't slip through.
        self.assertEqual(kwargs["creationflags"], 0x00000200)
        self.assertNotEqual(kwargs["creationflags"], 0)
        self.assertNotIn("start_new_session", kwargs)
        data = json.loads((job_root / job_id / "job.json").read_text(encoding="utf-8"))
        self.assertEqual(data["process_group"], "windows_new_group")

    def test_create_new_process_group_constant_matches_subprocess(self) -> None:
        """The hardcoded flag must equal the real subprocess constant on Windows
        so the two can't silently drift; skipped where the attr doesn't exist."""
        from web.operator_ui.job_manager import _CREATE_NEW_PROCESS_GROUP

        self.assertEqual(_CREATE_NEW_PROCESS_GROUP, 0x00000200)
        if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
            self.assertEqual(
                _CREATE_NEW_PROCESS_GROUP, subprocess.CREATE_NEW_PROCESS_GROUP
            )

    def test_start_posix_uses_start_new_session(self) -> None:
        config = {"provider_uri": "/data"}
        job_root = Path(tempfile.mkdtemp())
        result_root = Path(tempfile.mkdtemp())
        with patch("web.operator_ui.job_manager.JOB_ROOT", job_root):
            with patch("web.operator_ui.job_manager.RESULT_ROOT", result_root):
                with patch("web.operator_ui.job_manager.platform.system", return_value="Linux"):
                    with patch("subprocess.Popen") as mock_popen:
                        mock_proc = MagicMock()
                        mock_proc.pid = 4343
                        mock_popen.return_value = mock_proc
                        from web.operator_ui.job_manager import JobManager
                        job_id = JobManager.start(config, "pipeline")

        kwargs = mock_popen.call_args.kwargs
        self.assertTrue(kwargs.get("start_new_session"))
        self.assertNotIn("creationflags", kwargs)
        data = json.loads((job_root / job_id / "job.json").read_text(encoding="utf-8"))
        self.assertEqual(data["process_group"], "own_session")


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
                with patch("web.operator_ui.job_manager._pid_is_alive", return_value=True):
                    with patch("web.operator_ui.job_manager._wait_for_pid_exit", return_value=True):
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
                with patch("web.operator_ui.job_manager._pid_is_alive", return_value=True):
                    with patch("web.operator_ui.job_manager._wait_for_pid_exit", return_value=True):
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
                with patch("web.operator_ui.job_manager._pid_is_alive", return_value=True):
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
                with patch("web.operator_ui.job_manager._pid_is_alive", return_value=True):
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
                with patch("web.operator_ui.job_manager._pid_is_alive", return_value=True):
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

    def test_stop_refuses_terminal_status_without_signalling(self) -> None:
        """Audit G2: a job already in a terminal state must NOT be killed — its
        pid may have been recycled by the OS for an unrelated process."""
        job_root = Path(tempfile.mkdtemp())
        job_dir = job_root / "done_job"
        job_dir.mkdir(parents=True)
        job_dir.joinpath("job.json").write_text(
            json.dumps({"job_id": "done_job", "status": "success", "pid": 12345}),
            encoding="utf-8",
        )
        with patch("web.operator_ui.job_manager.JOB_ROOT", job_root):
            with patch("web.operator_ui.job_manager.platform.system", return_value="Windows"):
                with patch("subprocess.run") as mock_run:
                    with patch("web.operator_ui.job_manager.os.kill") as mock_kill:
                        from web.operator_ui.job_manager import JobManager, JobManagerError
                        with self.assertRaises(JobManagerError) as ctx:
                            JobManager.stop("done_job")
        self.assertIn("terminal", str(ctx.exception).lower())
        mock_run.assert_not_called()
        mock_kill.assert_not_called()
        # Status untouched — no spurious "stopped" overwrite.
        data = json.loads(job_dir.joinpath("job.json").read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "success")

    def test_stop_dead_pid_marks_failed_without_signalling(self) -> None:
        """A running job whose pid is already gone (crash/reboot) must be marked
        failed WITHOUT issuing any kill — the pid may belong to someone else."""
        job_root = Path(tempfile.mkdtemp())
        job_dir = job_root / "zombie_job"
        job_dir.mkdir(parents=True)
        job_dir.joinpath("job.json").write_text(
            json.dumps({"job_id": "zombie_job", "status": "running", "pid": 12345}),
            encoding="utf-8",
        )
        with patch("web.operator_ui.job_manager.JOB_ROOT", job_root):
            with patch("web.operator_ui.job_manager.platform.system", return_value="Windows"):
                with patch("web.operator_ui.job_manager._pid_is_alive", return_value=False):
                    with patch("subprocess.run") as mock_run:
                        with patch("web.operator_ui.job_manager.os.kill") as mock_kill:
                            from web.operator_ui.job_manager import JobManager
                            JobManager.stop("zombie_job")  # must not raise
        mock_run.assert_not_called()
        mock_kill.assert_not_called()
        data = json.loads(job_dir.joinpath("job.json").read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "failed")
        self.assertEqual(data["failure_reason"], "process_not_running_at_stop")
        self.assertIsNotNone(data["ended_at"])

    def test_stop_unknown_liveness_falls_through_to_kill(self) -> None:
        """When the liveness probe is INCONCLUSIVE (None — e.g. a tasklist
        hiccup), stop() must NOT take the no-kill 'mark failed' branch (that
        would orphan a live training job). It falls through to taskkill."""
        job_root = Path(tempfile.mkdtemp())
        job_dir = job_root / "unknown_job"
        job_dir.mkdir(parents=True)
        job_dir.joinpath("job.json").write_text(
            json.dumps({"job_id": "unknown_job", "status": "running", "pid": 555}),
            encoding="utf-8",
        )
        with patch("web.operator_ui.job_manager.JOB_ROOT", job_root):
            with patch("web.operator_ui.job_manager.platform.system", return_value="Windows"):
                with patch("web.operator_ui.job_manager._pid_is_alive", return_value=None):
                    with patch("web.operator_ui.job_manager._wait_for_pid_exit", return_value=True):
                        with patch("subprocess.run") as mock_run:
                            mock_run.return_value = subprocess.CompletedProcess(
                                args=["taskkill"], returncode=0
                            )
                            from web.operator_ui.job_manager import JobManager
                            JobManager.stop("unknown_job")
        mock_run.assert_called_once()
        self.assertIn("taskkill", str(mock_run.call_args).lower())
        data = json.loads(job_dir.joinpath("job.json").read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "stopped")

    def test_stop_unparseable_pid_marks_stop_failed(self) -> None:
        """A corrupt/hand-edited non-numeric pid must surface as a clean
        JobManagerError, not an unhandled ValueError."""
        job_root = Path(tempfile.mkdtemp())
        job_dir = job_root / "corrupt_pid_job"
        job_dir.mkdir(parents=True)
        job_dir.joinpath("job.json").write_text(
            json.dumps({"job_id": "corrupt_pid_job", "status": "running", "pid": "not-a-pid"}),
            encoding="utf-8",
        )
        with patch("web.operator_ui.job_manager.JOB_ROOT", job_root):
            from web.operator_ui.job_manager import JobManager, JobManagerError
            with self.assertRaises(JobManagerError):
                JobManager.stop("corrupt_pid_job")
        data = json.loads(job_dir.joinpath("job.json").read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "stop_failed")

    def test_stop_does_not_clobber_concurrently_finished_success(self) -> None:
        """Compare-and-set: if the runner wrote 'success' in the race window
        after stop()'s stale terminal-guard read, the final 'stopped' write must
        NOT overwrite it. Simulated by flipping the on-disk status to 'success'
        right before the final write (during _wait_for_pid_exit)."""
        job_root = Path(tempfile.mkdtemp())
        job_dir = job_root / "raced_job"
        job_dir.mkdir(parents=True)
        job_dir.joinpath("job.json").write_text(
            json.dumps({"job_id": "raced_job", "status": "running", "pid": 666}),
            encoding="utf-8",
        )

        def _runner_finishes(_pid: int) -> bool:
            # Stand in for job_runner writing its terminal status concurrently.
            from web.operator_ui.job_io import write_job_json
            write_job_json(job_dir, {"status": "success", "ended_at": "now"})
            return True

        with patch("web.operator_ui.job_manager.JOB_ROOT", job_root):
            with patch("web.operator_ui.job_manager.platform.system", return_value="Windows"):
                with patch("web.operator_ui.job_manager._pid_is_alive", return_value=True):
                    with patch("web.operator_ui.job_manager._wait_for_pid_exit", side_effect=_runner_finishes):
                        with patch("subprocess.run") as mock_run:
                            mock_run.return_value = subprocess.CompletedProcess(
                                args=["taskkill"], returncode=0
                            )
                            from web.operator_ui.job_manager import JobManager
                            JobManager.stop("raced_job")
        data = json.loads(job_dir.joinpath("job.json").read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "success")  # NOT clobbered to "stopped"

    def test_stop_taskkill_rc_nonzero_but_process_gone_marks_failed(self) -> None:
        """taskkill rc is ambiguous (missing AND protected pids can both be 128).
        On a nonzero rc, stop() re-probes: a confirmed-dead pid is recorded as a
        benign already-exited 'failed', not a scary 'stop_failed'."""
        job_root = Path(tempfile.mkdtemp())
        job_dir = job_root / "gone_job"
        job_dir.mkdir(parents=True)
        job_dir.joinpath("job.json").write_text(
            json.dumps({"job_id": "gone_job", "status": "running", "pid": 707}),
            encoding="utf-8",
        )
        with patch("web.operator_ui.job_manager.JOB_ROOT", job_root):
            with patch("web.operator_ui.job_manager.platform.system", return_value="Windows"):
                # pre-check None (inconclusive → fall through to taskkill),
                # re-probe after the failed kill → confirmed dead (False).
                with patch("web.operator_ui.job_manager._pid_is_alive", side_effect=[None, False]):
                    with patch("subprocess.run") as mock_run:
                        mock_run.return_value = subprocess.CompletedProcess(
                            args=["taskkill"], returncode=128, stderr="not found"
                        )
                        from web.operator_ui.job_manager import JobManager
                        JobManager.stop("gone_job")  # must NOT raise
        data = json.loads(job_dir.joinpath("job.json").read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "failed")
        self.assertEqual(data["failure_reason"], "process_not_running_at_stop")

    def test_stop_taskkill_failure_does_not_clobber_finished_success(self) -> None:
        """The taskkill-rc!=0 failure write is CAS-guarded: if the runner wrote
        'success' in the race window, stop_failed must NOT overwrite it (though
        the operator still gets a JobManagerError)."""
        job_root = Path(tempfile.mkdtemp())
        job_dir = job_root / "raced_fail_job"
        job_dir.mkdir(parents=True)
        job_dir.joinpath("job.json").write_text(
            json.dumps({"job_id": "raced_fail_job", "status": "running", "pid": 717}),
            encoding="utf-8",
        )

        def _runner_finishes_then_fail(*_a: object, **_k: object) -> object:
            from web.operator_ui.job_io import write_job_json
            write_job_json(job_dir, {"status": "success", "ended_at": "now"})
            return subprocess.CompletedProcess(args=["taskkill"], returncode=1, stderr="Access is denied")

        with patch("web.operator_ui.job_manager.JOB_ROOT", job_root):
            with patch("web.operator_ui.job_manager.platform.system", return_value="Windows"):
                with patch("web.operator_ui.job_manager._pid_is_alive", return_value=True):
                    with patch("subprocess.run", side_effect=_runner_finishes_then_fail):
                        from web.operator_ui.job_manager import JobManager, JobManagerError
                        with self.assertRaises(JobManagerError):
                            JobManager.stop("raced_fail_job")
        data = json.loads(job_dir.joinpath("job.json").read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "success")  # NOT clobbered to stop_failed

    def test_stop_posix_process_lookup_error_marks_failed_not_stop_failed(self) -> None:
        """POSIX: an os.kill ProcessLookupError means the process is already gone
        — a benign already-exited 'failed', not 'stop_failed' + raise."""
        job_root = Path(tempfile.mkdtemp())
        job_dir = job_root / "posix_gone"
        job_dir.mkdir(parents=True)
        job_dir.joinpath("job.json").write_text(
            json.dumps({"job_id": "posix_gone", "status": "running", "pid": 727}),
            encoding="utf-8",
        )
        with patch("web.operator_ui.job_manager.JOB_ROOT", job_root):
            with patch("web.operator_ui.job_manager.platform.system", return_value="Linux"):
                with patch("web.operator_ui.job_manager._pid_is_alive", return_value=True):
                    with patch("web.operator_ui.job_manager.os.kill", side_effect=ProcessLookupError):
                        from web.operator_ui.job_manager import JobManager
                        JobManager.stop("posix_gone")  # must NOT raise
        data = json.loads(job_dir.joinpath("job.json").read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "failed")
        self.assertEqual(data["failure_reason"], "process_not_running_at_stop")

    def test_stop_windows_taskkill_uses_timeout_and_safe_decode(self) -> None:
        """taskkill must be invoked with a bounded timeout and utf-8/replace
        decoding so a localized OEM-codepage output can't crash Stop."""
        job_root = Path(tempfile.mkdtemp())
        job_dir = job_root / "decode_job"
        job_dir.mkdir(parents=True)
        job_dir.joinpath("job.json").write_text(
            json.dumps({"job_id": "decode_job", "status": "running", "pid": 777}),
            encoding="utf-8",
        )
        with patch("web.operator_ui.job_manager.JOB_ROOT", job_root):
            with patch("web.operator_ui.job_manager.platform.system", return_value="Windows"):
                with patch("web.operator_ui.job_manager._pid_is_alive", return_value=True):
                    with patch("web.operator_ui.job_manager._wait_for_pid_exit", return_value=True):
                        with patch("subprocess.run") as mock_run:
                            mock_run.return_value = subprocess.CompletedProcess(
                                args=["taskkill"], returncode=0, stdout="ok"
                            )
                            from web.operator_ui.job_manager import JobManager
                            JobManager.stop("decode_job")
        kwargs = mock_run.call_args.kwargs
        self.assertEqual(kwargs.get("encoding"), "utf-8")
        self.assertEqual(kwargs.get("errors"), "replace")
        self.assertIsNotNone(kwargs.get("timeout"))
        self.assertFalse(kwargs.get("shell", False))

    def test_stop_windows_taskkill_timeout_marks_stop_failed(self) -> None:
        """A hung taskkill must not block forever — TimeoutExpired surfaces as
        a JobManagerError and a stop_failed stamp."""
        job_root = Path(tempfile.mkdtemp())
        job_dir = job_root / "hang_job"
        job_dir.mkdir(parents=True)
        job_dir.joinpath("job.json").write_text(
            json.dumps({"job_id": "hang_job", "status": "running", "pid": 888}),
            encoding="utf-8",
        )
        with patch("web.operator_ui.job_manager.JOB_ROOT", job_root):
            with patch("web.operator_ui.job_manager.platform.system", return_value="Windows"):
                with patch("web.operator_ui.job_manager._pid_is_alive", return_value=True):
                    with patch(
                        "subprocess.run",
                        side_effect=subprocess.TimeoutExpired(cmd="taskkill", timeout=30),
                    ):
                        from web.operator_ui.job_manager import JobManager, JobManagerError
                        with self.assertRaises(JobManagerError):
                            JobManager.stop("hang_job")
        data = json.loads(job_dir.joinpath("job.json").read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "stop_failed")
        self.assertIn("30s", data["stop_error"])


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

    def _make_job(self, job_root: Path, job_id: str, payload: dict) -> Path:
        job_dir = job_root / job_id
        job_dir.mkdir(parents=True)
        job_dir.joinpath("job.json").write_text(json.dumps(payload), encoding="utf-8")
        return job_dir

    def test_status_marks_running_job_with_dead_pid_as_failed(self) -> None:
        """Audit G2: a hard kill / OOM / reboot leaves job.json stuck at
        ``running`` with a dead pid forever. status() must reconcile that to
        ``failed`` so the UI stops showing a perpetual running job."""
        job_root = Path(tempfile.mkdtemp())
        job_dir = self._make_job(
            job_root, "zombie", {"job_id": "zombie", "status": "running", "pid": 31337}
        )
        with patch("web.operator_ui.job_manager.JOB_ROOT", job_root):
            with patch("web.operator_ui.job_manager._pid_is_alive", return_value=False):
                from web.operator_ui.job_manager import JobManager
                result = JobManager.status("zombie")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failure_reason"], "zombie_process_died_without_status")
        # Persisted, not just returned.
        on_disk = json.loads(job_dir.joinpath("job.json").read_text(encoding="utf-8"))
        self.assertEqual(on_disk["status"], "failed")

    def test_status_leaves_running_job_with_live_pid_untouched(self) -> None:
        job_root = Path(tempfile.mkdtemp())
        self._make_job(
            job_root, "live", {"job_id": "live", "status": "running", "pid": 31338}
        )
        with patch("web.operator_ui.job_manager.JOB_ROOT", job_root):
            with patch("web.operator_ui.job_manager._pid_is_alive", return_value=True):
                from web.operator_ui.job_manager import JobManager
                result = JobManager.status("live")
        self.assertEqual(result["status"], "running")

    def test_status_does_not_probe_pid_for_pending_or_terminal(self) -> None:
        """pending has no stable pid yet; terminal states are final. Neither
        should trigger a (potentially expensive) liveness probe."""
        job_root = Path(tempfile.mkdtemp())
        self._make_job(job_root, "pend", {"job_id": "pend", "status": "pending"})
        self._make_job(
            job_root, "ok", {"job_id": "ok", "status": "success", "pid": 31339}
        )
        with patch("web.operator_ui.job_manager.JOB_ROOT", job_root):
            with patch("web.operator_ui.job_manager._pid_is_alive") as mock_alive:
                from web.operator_ui.job_manager import JobManager
                self.assertEqual(JobManager.status("pend")["status"], "pending")
                self.assertEqual(JobManager.status("ok")["status"], "success")
        mock_alive.assert_not_called()

    def test_status_unknown_liveness_leaves_running(self) -> None:
        """An inconclusive probe (None) must NOT reconcile a running job to
        failed — a transient tasklist hiccup can't permanently mislabel it."""
        job_root = Path(tempfile.mkdtemp())
        self._make_job(
            job_root, "maybe", {"job_id": "maybe", "status": "running", "pid": 31340}
        )
        with patch("web.operator_ui.job_manager.JOB_ROOT", job_root):
            with patch("web.operator_ui.job_manager._pid_is_alive", return_value=None):
                from web.operator_ui.job_manager import JobManager
                result = JobManager.status("maybe")
        self.assertEqual(result["status"], "running")

    def test_status_malformed_pid_does_not_crash(self) -> None:
        """A corrupt non-numeric pid must not raise out of status()."""
        job_root = Path(tempfile.mkdtemp())
        self._make_job(
            job_root, "bad", {"job_id": "bad", "status": "running", "pid": "xyz"}
        )
        with patch("web.operator_ui.job_manager.JOB_ROOT", job_root):
            from web.operator_ui.job_manager import JobManager
            result = JobManager.status("bad")  # must not raise
        self.assertEqual(result["status"], "running")


class JobManagerListJobsTests(unittest.TestCase):
    """list_jobs() drives 4 UI pages — it must reconcile zombies AND survive a
    single corrupt job without blanking the whole list."""

    def _make_job(self, job_root: Path, job_id: str, payload: dict) -> None:
        job_dir = job_root / job_id
        job_dir.mkdir(parents=True)
        job_dir.joinpath("job.json").write_text(json.dumps(payload), encoding="utf-8")

    def test_list_jobs_reconciles_dead_running_job(self) -> None:
        job_root = Path(tempfile.mkdtemp())
        self._make_job(
            job_root, "z", {"job_id": "z", "status": "running", "pid": 41001}
        )
        with patch("web.operator_ui.job_manager.JOB_ROOT", job_root):
            with patch("web.operator_ui.job_manager._pid_is_alive", return_value=False):
                from web.operator_ui.job_manager import JobManager
                rows = JobManager.list_jobs()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "failed")

    def test_list_jobs_tolerates_malformed_pid(self) -> None:
        """One corrupt job.json must not crash the list (P2: ValueError from
        int(pid) used to propagate and hide ALL jobs)."""
        job_root = Path(tempfile.mkdtemp())
        self._make_job(job_root, "ok", {"job_id": "ok", "status": "success"})
        self._make_job(
            job_root, "bad", {"job_id": "bad", "status": "running", "pid": "nope"}
        )
        with patch("web.operator_ui.job_manager.JOB_ROOT", job_root):
            from web.operator_ui.job_manager import JobManager
            rows = JobManager.list_jobs()  # must not raise
        statuses = {r["job_id"]: r["status"] for r in rows}
        self.assertEqual(statuses.get("ok"), "success")
        self.assertEqual(statuses.get("bad"), "running")  # left as-is, not crashed


class WaitForPidExitTests(unittest.TestCase):
    """_wait_for_pid_exit was rewired onto _pid_is_alive — cover the routing."""

    def test_returns_true_once_pid_dies(self) -> None:
        from web.operator_ui import job_manager

        with patch.object(job_manager, "_pid_is_alive", side_effect=[True, True, False]) as m:
            self.assertTrue(
                job_manager._wait_for_pid_exit(123, attempts=5, interval_seconds=0)
            )
        self.assertEqual(m.call_count, 3)

    def test_returns_true_on_unknown_probe(self) -> None:
        """A None (probe-failed) result must break the wait, not spin (each probe
        can cost up to the tasklist timeout)."""
        from web.operator_ui import job_manager

        with patch.object(job_manager, "_pid_is_alive", return_value=None) as m:
            self.assertTrue(
                job_manager._wait_for_pid_exit(123, attempts=5, interval_seconds=0)
            )
        self.assertEqual(m.call_count, 1)

    def test_returns_false_when_pid_stays_alive(self) -> None:
        from web.operator_ui import job_manager

        with patch.object(job_manager, "_pid_is_alive", return_value=True):
            self.assertFalse(
                job_manager._wait_for_pid_exit(123, attempts=3, interval_seconds=0)
            )


class PidIsAliveTests(unittest.TestCase):
    """The safe, never-signalling liveness probe (audit G2)."""

    def test_non_positive_pid_is_not_alive(self) -> None:
        from web.operator_ui.job_manager import _pid_is_alive

        self.assertFalse(_pid_is_alive(0))
        self.assertFalse(_pid_is_alive(-1))

    def test_posix_uses_signal_zero_probe(self) -> None:
        with patch("web.operator_ui.job_manager.platform.system", return_value="Linux"):
            with patch("web.operator_ui.job_manager.os.kill") as mock_kill:
                from web.operator_ui.job_manager import _pid_is_alive

                self.assertTrue(_pid_is_alive(4321))
        mock_kill.assert_called_once_with(4321, 0)

    def test_posix_dead_pid_returns_false(self) -> None:
        with patch("web.operator_ui.job_manager.platform.system", return_value="Linux"):
            with patch("web.operator_ui.job_manager.os.kill", side_effect=ProcessLookupError):
                from web.operator_ui.job_manager import _pid_is_alive

                self.assertFalse(_pid_is_alive(4321))

    def test_posix_permission_error_means_alive(self) -> None:
        with patch("web.operator_ui.job_manager.platform.system", return_value="Linux"):
            with patch("web.operator_ui.job_manager.os.kill", side_effect=PermissionError):
                from web.operator_ui.job_manager import _pid_is_alive

                self.assertTrue(_pid_is_alive(4321))

    def test_posix_other_oserror_is_unknown_not_dead(self) -> None:
        with patch("web.operator_ui.job_manager.platform.system", return_value="Linux"):
            with patch("web.operator_ui.job_manager.os.kill", side_effect=OSError("eintr")):
                from web.operator_ui.job_manager import _pid_is_alive

                self.assertIsNone(_pid_is_alive(4321))

    def test_windows_uses_tasklist_and_never_signals(self) -> None:
        """On Windows os.kill(pid, 0) would TerminateProcess the pid; the probe
        must shell out to tasklist instead and must NOT call os.kill."""
        csv = '"python.exe","12345","Console","1","123,456 K"\r\n'
        with patch("web.operator_ui.job_manager.platform.system", return_value="Windows"):
            with patch("web.operator_ui.job_manager.os.kill") as mock_kill:
                with patch("subprocess.run") as mock_run:
                    mock_run.return_value = subprocess.CompletedProcess(
                        args=["tasklist"], returncode=0, stdout=csv
                    )
                    from web.operator_ui.job_manager import _pid_is_alive

                    self.assertTrue(_pid_is_alive(12345))
        mock_kill.assert_not_called()
        self.assertIn("tasklist", str(mock_run.call_args).lower())

    def test_windows_no_match_returns_false(self) -> None:
        no_match = "INFO: No tasks are running which match the specified criteria.\r\n"
        with patch("web.operator_ui.job_manager.platform.system", return_value="Windows"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = subprocess.CompletedProcess(
                    args=["tasklist"], returncode=0, stdout=no_match
                )
                from web.operator_ui.job_manager import _pid_is_alive

                self.assertFalse(_pid_is_alive(12345))

    def test_windows_tasklist_failure_is_unknown_not_dead(self) -> None:
        """A probe failure must be UNKNOWN (None), never 'dead' — else stop()
        would skip the kill and reconcile would mislabel a live job."""
        with patch("web.operator_ui.job_manager.platform.system", return_value="Windows"):
            with patch("subprocess.run", side_effect=OSError("boom")):
                from web.operator_ui.job_manager import _pid_is_alive

                self.assertIsNone(_pid_is_alive(12345))

    def test_windows_tasklist_timeout_is_unknown_not_dead(self) -> None:
        with patch("web.operator_ui.job_manager.platform.system", return_value="Windows"):
            with patch(
                "subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="tasklist", timeout=15),
            ):
                from web.operator_ui.job_manager import _pid_is_alive

                self.assertIsNone(_pid_is_alive(12345))

    def test_windows_nonzero_returncode_is_unknown(self) -> None:
        """tasklist returns rc 0 for BOTH match and no-match; a nonzero rc is an
        abnormal error and must read as unknown, not dead."""
        with patch("web.operator_ui.job_manager.platform.system", return_value="Windows"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = subprocess.CompletedProcess(
                    args=["tasklist"], returncode=1, stdout="", stderr="error"
                )
                from web.operator_ui.job_manager import _pid_is_alive

                self.assertIsNone(_pid_is_alive(12345))

    def test_windows_pid_only_matches_pid_column_not_session(self) -> None:
        """CSV parsing must pin the match to the PID field (col 1), not a naive
        substring that could collide with e.g. the Session# column."""
        # A row for an UNRELATED process whose Session# is '7' must not match pid 7.
        csv_row = '"explorer.exe","9999","Console","7","123,456 K"\r\n'
        with patch("web.operator_ui.job_manager.platform.system", return_value="Windows"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = subprocess.CompletedProcess(
                    args=["tasklist"], returncode=0, stdout=csv_row
                )
                from web.operator_ui.job_manager import _pid_is_alive

                self.assertFalse(_pid_is_alive(7))
                self.assertTrue(_pid_is_alive(9999))


if __name__ == "__main__":
    unittest.main()
