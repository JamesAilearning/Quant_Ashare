"""Job lifecycle manager — create, start, stop, status, list UI-launched runs."""

from __future__ import annotations

import json
import os
import platform
import signal
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import yaml

JOB_ROOT = Path("output/operator_ui/jobs")
RESULT_ROOT = Path("output/operator_ui/results")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
JobMode = Literal["pipeline", "walk_forward", "tushare_provider"]


class JobManagerError(RuntimeError):
    """Raised when a UI job lifecycle transition cannot be completed."""


def _generate_job_id(mode: str) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    tag = uuid.uuid4().hex[:8]
    return f"{mode}_{ts}_{tag}"


def _write_config_yaml(config: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)


def _write_job_json(job_dir: Path, updates: dict[str, Any]) -> None:
    existing = _read_job_json(job_dir)
    existing.update(updates)
    tmp = job_dir / "job.json.tmp"
    tmp.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(job_dir / "job.json")


def _read_job_json(job_dir: Path) -> dict[str, Any]:
    path = job_dir / "job.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _runner_env() -> dict[str, str]:
    """Return an environment that can import repo-local ``src.*`` modules."""
    env = os.environ.copy()
    project_root = str(PROJECT_ROOT)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        project_root
        if not existing
        else project_root + os.pathsep + existing
    )
    return env


class JobManager:
    """Create, start, stop, and monitor UI-launched training runs."""

    @staticmethod
    def start(config: dict[str, Any], mode: JobMode) -> str:
        # Copy to avoid mutating the caller's dict
        config = dict(config)

        job_id = _generate_job_id(mode)
        job_dir = JOB_ROOT / job_id
        job_dir.mkdir(parents=True, exist_ok=False)

        # Force output paths under the UI result root so report_reader
        # and job history never have to chase machine-local defaults.
        result_dir = RESULT_ROOT / job_id
        if mode == "tushare_provider":
            provider_dir = result_dir / "qlib_provider"
            config["output_dir"] = str(provider_dir)
            config.setdefault("staging_dir", str(result_dir / "staging"))
            config.setdefault("manifest_path", str(result_dir / "manifest.json"))
            config.setdefault("validation_path", str(result_dir / "validation.json"))
            config.setdefault("comparison_path", str(result_dir / "comparison.json"))
        else:
            config["output_dir"] = str(result_dir)

        config_path = job_dir / "config.yaml"
        _write_config_yaml(config, config_path)

        now = datetime.now(timezone.utc).isoformat()
        _write_job_json(job_dir, {
            "job_id": job_id,
            "mode": mode,
            "status": "pending",
            "started_at": now,
            "ended_at": None,
            "config_path": str(config_path),
            "run_dir": None,
            "pid": None,
        })

        runner_cmd = [
            sys.executable, "-m", "web.operator_ui.job_runner",
            str(job_dir), mode,
        ]
        runner_stdout_path = job_dir / "runner_stdout.log"
        runner_stderr_path = job_dir / "runner_stderr.log"
        popen_kwargs: dict[str, Any] = {
            "stdout": None,
            "stderr": None,
            "cwd": PROJECT_ROOT,
            "shell": False,
            "env": _runner_env(),
        }
        if platform.system() != "Windows":
            popen_kwargs["start_new_session"] = True
        with open(runner_stdout_path, "w", encoding="utf-8") as runner_stdout:
            with open(runner_stderr_path, "w", encoding="utf-8") as runner_stderr:
                popen_kwargs["stdout"] = runner_stdout
                popen_kwargs["stderr"] = runner_stderr
                proc = subprocess.Popen(runner_cmd, **popen_kwargs)

        _write_job_json(job_dir, {
            "status": "running",
            "pid": proc.pid,
            "runner_stdout_path": str(runner_stdout_path),
            "runner_stderr_path": str(runner_stderr_path),
            "process_group": "own_session" if platform.system() != "Windows" else None,
        })

        return job_id

    @staticmethod
    def stop(job_id: str) -> None:
        job_dir = JOB_ROOT / job_id
        data = _read_job_json(job_dir)
        pid = data.get("pid")
        if not pid:
            message = f"Cannot stop job {job_id!r}: job.json has no pid."
            _write_job_json(job_dir, {
                "status": "stop_failed",
                "stop_error": message,
                "stop_failed_at": datetime.now(timezone.utc).isoformat(),
            })
            raise JobManagerError(message)

        if platform.system() == "Windows":
            result = subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                shell=False,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "").strip()
                if detail:
                    message = (
                        f"Failed to stop job {job_id!r} with pid {pid}: "
                        f"taskkill exited {result.returncode}: {detail}"
                    )
                else:
                    message = (
                        f"Failed to stop job {job_id!r} with pid {pid}: "
                        f"taskkill exited {result.returncode}."
                    )
                _write_job_json(job_dir, {
                    "status": "stop_failed",
                    "stop_error": message,
                    "stop_returncode": result.returncode,
                    "stop_failed_at": datetime.now(timezone.utc).isoformat(),
                })
                raise JobManagerError(message)
        else:
            try:
                if data.get("process_group") == "own_session":
                    os.killpg(os.getpgid(int(pid)), signal.SIGTERM)
                else:
                    os.kill(int(pid), signal.SIGTERM)
            except OSError as exc:
                message = (
                    f"Failed to stop job {job_id!r} with pid {pid}: "
                    f"{type(exc).__name__}: {exc}"
                )
                _write_job_json(job_dir, {
                    "status": "stop_failed",
                    "stop_error": message,
                    "stop_failed_at": datetime.now(timezone.utc).isoformat(),
                })
                raise JobManagerError(message) from exc

        _write_job_json(job_dir, {
            "status": "stopped",
            "ended_at": datetime.now(timezone.utc).isoformat(),
        })

    @staticmethod
    def status(job_id: str) -> dict[str, Any]:
        job_dir = JOB_ROOT / job_id
        data = _read_job_json(job_dir)
        if not data:
            return {"job_id": job_id, "status": "unknown"}
        # Trust job_runner's own status writes — do not signal the
        # process tree (os.kill is unsafe on Windows).  job_runner
        # writes success/failed + ended_at when the CLI exits.
        return data

    @staticmethod
    def list_jobs() -> list[dict[str, Any]]:
        if not JOB_ROOT.is_dir():
            return []
        results = []
        for job_dir in sorted(JOB_ROOT.iterdir(), reverse=True):
            if job_dir.is_dir():
                data = _read_job_json(job_dir)
                if data:
                    results.append(data)
        return results
