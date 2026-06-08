"""Job runner — launched by JobManager as a separate process.

Reads a config YAML, runs the real CLI via subprocess, and writes
job status transitions (running → success/failed) to job.json.
This decouples job lifecycle from Streamlit's rerun cycle.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from src.core.logger import get_logger
from web.operator_ui.job_io import (
    write_job_json as _write_job_json,
)

_logger = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ACTIVE_JOB_DIR: Path | None = None

# Reference to the currently-running training subprocess so the SIGTERM
# handler can ``terminate()`` it before this runner exits. Without this,
# ``subprocess.run`` blocks while holding no caller-visible reference;
# raising ``SystemExit`` from the signal handler would let the runner
# exit but leave the training child orphaned (Linux re-parents to init,
# Windows just continues). bug.md P1-4.
_ACTIVE_CHILD_PROCESS: subprocess.Popen[bytes] | None = None
_CHILD_TERMINATE_TIMEOUT_S = 5.0


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
    # A bundle-shaped output_dir (has calendars/ or features/) is its own run dir.
    if (output_dir / "calendars").is_dir() or (output_dir / "features").is_dir():
        return str(output_dir)
    # Pipeline: run dir is under runs/ subfolder
    runs_dir = output_dir / "runs"
    if runs_dir.is_dir():
        entries = [entry for entry in runs_dir.iterdir() if entry.is_dir()]
        if entries:
            return str(max(entries, key=lambda path: path.stat().st_mtime))
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


def _copy_pipeline_logs_to_run_dir(
    *,
    stdout_path: Path,
    stderr_path: Path,
    run_dir: str,
) -> None:
    logs_dir = Path(run_dir) / "logs"
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
        target = logs_dir / "pipeline.log"
        with target.open("w", encoding="utf-8") as out:
            if stdout_path.is_file():
                out.write("### stdout.log\n")
                out.write(stdout_path.read_text(encoding="utf-8", errors="replace"))
                out.write("\n")
            if stderr_path.is_file():
                out.write("### stderr.log\n")
                out.write(stderr_path.read_text(encoding="utf-8", errors="replace"))
                out.write("\n")
        for source_name in ("stdout.log", "stderr.log"):
            source = stdout_path if source_name == "stdout.log" else stderr_path
            if source.is_file():
                shutil.copy2(source, logs_dir / source_name)
    except OSError:
        return


def _terminate_active_child() -> None:
    """Stop the training subprocess so it doesn't outlive this runner.

    Best-effort: try graceful ``terminate()`` first, fall back to
    ``kill()`` after a short timeout. Swallow any exception — the
    SIGTERM handler must not crash, since a crash here would skip the
    job.json ``stopped`` write that downstream UI relies on.
    """
    proc = _ACTIVE_CHILD_PROCESS
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        try:
            proc.wait(timeout=_CHILD_TERMINATE_TIMEOUT_S)
            return
        except subprocess.TimeoutExpired:
            pass
        proc.kill()
        try:
            proc.wait(timeout=_CHILD_TERMINATE_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return
    except Exception as exc:
        # OSError from already-dead PID, AttributeError on platform
        # quirks, etc. — never let the handler crash. But DO emit a
        # WARNING with the exception class before swallowing: the
        # silent fallthrough previously left an orphaned child while
        # the UI showed the job as "stopped", and the operator had
        # no log trail to investigate. With the WARNING, the same
        # contract holds (handler must not crash → still returns)
        # but the orphan condition is at least observable. Audit
        # P1-16.
        _logger.warning(
            "_terminate_active_child failed for pid=%r (%s: %s); "
            "the child process may be orphaned — verify via OS "
            "process listing if the UI reports the job as 'stopped' "
            "while compute remains active.",
            getattr(proc, "pid", None),
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        return


def _handle_stop_signal(signum: int, _frame: Any) -> None:
    # Kill the training subprocess BEFORE writing job.json — if we
    # wrote ``stopped`` first and the child then crashed during
    # terminate (e.g., flushing a partial model pickle), the UI would
    # see a "stopped" job with corrupt half-written artifacts. Killing
    # first means: by the time we write ``stopped``, the child is
    # guaranteed gone and no further writes are happening.
    _terminate_active_child()
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
    else:
        _write_job_json(job_dir, {
            "status": "failed",
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "error": f"Unknown mode: {mode!r}",
        })
        sys.exit(1)
    cmd.append(str(config_path))

    _write_job_json(job_dir, {"status": "running", "started_at": datetime.now(timezone.utc).isoformat()})

    # ``subprocess.Popen`` instead of ``subprocess.run``: gives us a
    # process handle that the SIGTERM handler can ``terminate()``
    # before this runner exits, so the training child doesn't
    # become an orphan. (bug.md P1-4.)
    global _ACTIVE_CHILD_PROCESS
    with open(stdout_path, "w", encoding="utf-8") as out, open(stderr_path, "w", encoding="utf-8") as err:
        _ACTIVE_CHILD_PROCESS = subprocess.Popen(
            cmd,
            stdout=out,
            stderr=err,
            cwd=PROJECT_ROOT,
            env=_runner_env(),
            shell=False,
        )
        try:
            returncode = _ACTIVE_CHILD_PROCESS.wait()
        finally:
            # Drop the global ref once wait() returns (the child has
            # exited on its own, or was terminated by the handler).
            # Holding a stale ref past this point would have the handler
            # try to terminate a dead PID on a subsequent signal.
            _ACTIVE_CHILD_PROCESS = None

    ended = datetime.now(timezone.utc).isoformat()
    succeeded = returncode == 0
    if succeeded:
        _write_job_json(job_dir, {"status": "success", "ended_at": ended})
    else:
        _write_job_json(job_dir, {"status": "failed", "ended_at": ended, "returncode": returncode})

    # Try to find the run directory only after successful CLI completion.
    # Failed runs can leave old directories under a reused output_dir; binding
    # those to this job would pollute historical artifacts and hide the failure.
    output_dir = _read_output_dir(config_path)
    if succeeded and output_dir:
        run_dir = _find_run_dir(Path(output_dir))
        if run_dir:
            if mode == "pipeline":
                _copy_exact_config_to_run_dir(config_path, run_dir)
                _copy_pipeline_logs_to_run_dir(
                    stdout_path=stdout_path,
                    stderr_path=stderr_path,
                    run_dir=run_dir,
                )
            _write_job_json(job_dir, {"run_dir": run_dir})


if __name__ == "__main__":
    main()
