"""Job runner — launched by JobManager as a separate process.

Reads a config YAML, runs the real CLI via subprocess, and writes
job status transitions (running → success/failed) to job.json.
This decouples job lifecycle from Streamlit's rerun cycle.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from web.operator_ui.job_io import (
    write_job_json as _write_job_json,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ACTIVE_JOB_DIR: Path | None = None


def _runner_env() -> dict[str, str]:
    env = os.environ.copy()
    project_root = str(PROJECT_ROOT)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        project_root
        if not existing
        else project_root + os.pathsep + existing
    )
    return env


def _find_run_dir(output_dir: Path) -> str | None:
    # Walk-forward: output_dir IS the run dir (no runs/ subfolder)
    wf_report = output_dir / "walk_forward_report.json"
    if wf_report.is_file():
        return str(output_dir)
    # Tushare provider ingest: output_dir is the generated qlib provider.
    if (output_dir / "calendars").is_dir() or (output_dir / "features").is_dir():
        return str(output_dir)
    # Pipeline: run dir is under runs/ subfolder
    runs_dir = output_dir / "runs"
    if runs_dir.is_dir():
        entries = sorted(runs_dir.iterdir())
        for entry in entries:
            if entry.is_dir():
                return str(entry)
    return None


def _copy_exact_config_to_run_dir(config_path: Path, run_dir: str) -> None:
    target = Path(run_dir) / "config.yaml"
    try:
        target.write_bytes(config_path.read_bytes())
    except OSError:
        # The pipeline's normalized config.yaml remains available if this
        # best-effort exact-copy step fails. Do not flip a successful training
        # job to failed because a post-run UI convenience copy failed.
        return


def _handle_stop_signal(signum: int, _frame: Any) -> None:
    if _ACTIVE_JOB_DIR is not None:
        _write_job_json(
            _ACTIVE_JOB_DIR,
            {
                "status": "stopped",
                "ended_at": datetime.now(timezone.utc).isoformat(),
                "stop_signal": signum,
            },
        )
    raise SystemExit(128 + signum)


def _install_stop_signal_handler(job_dir: Path) -> None:
    global _ACTIVE_JOB_DIR
    _ACTIVE_JOB_DIR = job_dir
    signal.signal(signal.SIGTERM, _handle_stop_signal)


def _read_output_dir(config_path: Path) -> str | None:
    try:
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(loaded, dict):
        return None
    output_dir = loaded.get("output_dir")
    if output_dir is None:
        return None
    return str(output_dir)


def main(argv: list[str] | None = None) -> None:
    if argv is None:
        argv = sys.argv[1:]

    if len(argv) < 2:
        print("Usage: python -m web.operator_ui.job_runner <job_dir> <mode>", file=sys.stderr)
        sys.exit(2)

    job_dir = Path(argv[0])
    mode = argv[1]  # "pipeline" or "walk_forward"
    _install_stop_signal_handler(job_dir)

    config_path = job_dir / "config.yaml"
    if not config_path.is_file():
        _write_job_json(job_dir, {
            "status": "failed",
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "error": f"config.yaml not found at {config_path}",
        })
        sys.exit(1)

    stdout_path = job_dir / "stdout.log"
    stderr_path = job_dir / "stderr.log"

    cmd = [sys.executable]
    if mode == "pipeline":
        cmd.append("main.py")
    elif mode == "walk_forward":
        cmd.append("scripts/run_walk_forward.py")
    elif mode == "tushare_provider":
        cmd.append("scripts/ingest_tushare_qlib_provider.py")
    else:
        _write_job_json(job_dir, {
            "status": "failed",
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "error": f"Unknown mode: {mode!r}",
        })
        sys.exit(1)
    cmd.append(str(config_path))

    _write_job_json(job_dir, {"status": "running", "started_at": datetime.now(timezone.utc).isoformat()})

    with open(stdout_path, "w", encoding="utf-8") as out, open(stderr_path, "w", encoding="utf-8") as err:
        result = subprocess.run(
            cmd,
            stdout=out,
            stderr=err,
            cwd=PROJECT_ROOT,
            env=_runner_env(),
            shell=False,
        )

    ended = datetime.now(timezone.utc).isoformat()
    if result.returncode == 0:
        _write_job_json(job_dir, {"status": "success", "ended_at": ended})
    else:
        _write_job_json(job_dir, {"status": "failed", "ended_at": ended, "returncode": result.returncode})

    # Try to find the run directory
    output_dir = _read_output_dir(config_path)
    if output_dir:
        run_dir = _find_run_dir(Path(output_dir))
        if run_dir:
            if mode == "pipeline":
                _copy_exact_config_to_run_dir(config_path, run_dir)
            _write_job_json(job_dir, {"run_dir": run_dir})


if __name__ == "__main__":
    main()
