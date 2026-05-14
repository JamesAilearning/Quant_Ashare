"""Job lifecycle manager — create, start, stop, status, list UI-launched runs."""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import yaml

JOB_ROOT = Path("output/operator_ui/jobs")
RESULT_ROOT = Path("output/operator_ui/results")


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


class JobManager:
    """Create, start, stop, and monitor UI-launched training runs."""

    @staticmethod
    def start(config: dict[str, Any], mode: Literal["pipeline", "walk_forward"]) -> str:
        # Copy to avoid mutating the caller's dict
        config = dict(config)

        job_id = _generate_job_id(mode)
        job_dir = JOB_ROOT / job_id
        job_dir.mkdir(parents=True, exist_ok=False)

        # Force output_dir so report_reader can find results
        config["output_dir"] = str(RESULT_ROOT / job_id)

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
        proc = subprocess.Popen(
            runner_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=Path.cwd(),
            shell=False,
        )

        _write_job_json(job_dir, {
            "status": "running",
            "pid": proc.pid,
        })

        return job_id

    @staticmethod
    def stop(job_id: str) -> None:
        job_dir = JOB_ROOT / job_id
        data = _read_job_json(job_dir)
        pid = data.get("pid")
        if pid:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                shell=False,
                capture_output=True,
            )
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
