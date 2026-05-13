"""Job runner — launched by JobManager as a separate process.

Reads a config YAML, runs the real CLI via subprocess, and writes
job status transitions (running → success/failed) to job.json.
This decouples job lifecycle from Streamlit's rerun cycle.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _find_run_dir(output_dir: Path) -> str | None:
    # Walk-forward: output_dir IS the run dir (no runs/ subfolder)
    wf_report = output_dir / "walk_forward_report.json"
    if wf_report.is_file():
        return str(output_dir)
    # Pipeline: run dir is under runs/ subfolder
    runs_dir = output_dir / "runs"
    if runs_dir.is_dir():
        entries = sorted(runs_dir.iterdir())
        for entry in entries:
            if entry.is_dir():
                return str(entry)
    return None


def _read_job_json(job_dir: Path) -> dict[str, Any]:
    path = job_dir / "job.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _write_job_json(job_dir: Path, updates: dict[str, Any]) -> None:
    data = _read_job_json(job_dir)
    data.update(updates)
    tmp = job_dir / "job.json.tmp"
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(job_dir / "job.json")


def main(argv: list[str] | None = None) -> None:
    if argv is None:
        argv = sys.argv[1:]

    if len(argv) < 2:
        print("Usage: python -m web.operator_ui.job_runner <job_dir> <mode>", file=sys.stderr)
        sys.exit(2)

    job_dir = Path(argv[0])
    mode = argv[1]  # "pipeline" or "walk_forward"

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
        result = subprocess.run(cmd, stdout=out, stderr=err, cwd=Path.cwd())

    ended = datetime.now(timezone.utc).isoformat()
    if result.returncode == 0:
        _write_job_json(job_dir, {"status": "success", "ended_at": ended})
    else:
        _write_job_json(job_dir, {"status": "failed", "ended_at": ended, "returncode": result.returncode})

    # Try to find the run directory
    data = _read_job_json(job_dir)
    config_raw = config_path.read_text(encoding="utf-8")
    output_dir = None
    for line in config_raw.splitlines():
        if line.strip().startswith("output_dir:"):
            output_dir = line.split(":", 1)[1].strip().strip('"').strip("'")
            break
    if output_dir:
        run_dir = _find_run_dir(Path(output_dir))
        if run_dir:
            data["run_dir"] = run_dir
            _write_job_json(job_dir, {"run_dir": run_dir})


if __name__ == "__main__":
    main()
