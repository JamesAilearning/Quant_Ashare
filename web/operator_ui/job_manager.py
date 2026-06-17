"""Job lifecycle manager — create, start, stop, status, list UI-launched runs."""

from __future__ import annotations

import csv
import os
import platform
import shutil
import signal
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import yaml

from web.operator_ui.job_io import (
    read_job_json as _read_job_json,
)
from web.operator_ui.job_io import (
    write_job_json as _write_job_json,
)
from web.operator_ui.progress import build_job_progress

PROJECT_ROOT = Path(__file__).resolve().parents[2]
JOB_ROOT = PROJECT_ROOT / "output" / "operator_ui" / "jobs"
RESULT_ROOT = PROJECT_ROOT / "output" / "operator_ui" / "results"
JobMode = Literal["pipeline", "walk_forward"]

# Statuses the JOB RUNNER writes when a run genuinely ends. stop() refuses to
# signal a pid for a job in one of these (the pid may have been recycled by the
# OS), and the compare-and-set writes never overwrite them. NB: "stop_failed" is
# deliberately NOT here — a failed stop attempt does not mean the job ended.
_RUNNER_TERMINAL_STATUSES = frozenset({"success", "failed", "stopped"})

# Statuses from which stop() may still transition a job, used as the
# compare-and-set guard for its terminal writes. Includes "stop_failed" so an
# operator can RETRY after a transient taskkill timeout / access-denied (the
# process is likely still alive and still the managed job). Excludes the
# runner-terminal states above so a concurrently-finished run is never clobbered.
_STOP_WRITABLE_STATUSES = ("running", "pending", "stop_failed")

# Win32 CREATE_NEW_PROCESS_GROUP (0x00000200). Hardcoded rather than referenced
# as ``subprocess.CREATE_NEW_PROCESS_GROUP`` because that attribute exists only
# on Windows builds of CPython — referencing it would raise AttributeError on a
# non-Windows host that reaches the Windows branch (only happens in tests that
# patch platform.system; in production the branch is Windows-only). The value is
# a stable, documented Win32 constant. ``test_create_new_process_group_constant``
# cross-checks it against subprocess.CREATE_NEW_PROCESS_GROUP on Windows so the
# two cannot silently drift.
_CREATE_NEW_PROCESS_GROUP = 0x00000200


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


def _with_progress(job_dir: Path, data: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(data)
    enriched["progress"] = build_job_progress(job_dir, enriched)
    return enriched


def _reconcile_zombie(job_dir: Path, data: dict[str, Any]) -> dict[str, Any]:
    """Mark a ``running`` job whose pid is CONFIRMED gone as ``failed`` (zombie).

    Only ``running`` is reconciled: ``pending`` has no stable pid yet, and the
    terminal states are already final. The flip is conservative and atomic:
    - only a CONFIRMED-dead probe (``is False``) reconciles; an unknown probe
      (None — tasklist hiccup) leaves the job running so a transient failure
      can't permanently mislabel a healthy run.
    - the write is a compare-and-set guarded on ``status == running``, so it can
      never clobber a ``success``/``failed`` that job_runner wrote in the window
      while we were probing the pid.
    Best-effort: a read-only status() call must never raise.
    """
    if data.get("status") != "running":
        return data
    pid = _coerce_pid(data.get("pid"))
    if pid is None:
        return data
    if _pid_is_alive(pid) is not False:  # alive (True) or unknown (None)
        return data
    try:
        _write_job_json(job_dir, {
            "status": "failed",
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "failure_reason": "zombie_process_died_without_status",
        }, only_if_status=("running",))
    except OSError:
        return data
    return _read_job_json(job_dir) or data


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


def _resolve_child_dir(root: Path, child_name: str) -> Path:
    name = str(child_name or "").strip()
    if not name or "/" in name or "\\" in name or Path(name).name != name:
        raise JobManagerError(f"Invalid UI job id: {child_name!r}.")
    resolved_root = root.resolve()
    resolved_path = (root / name).resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise JobManagerError(
            f"Refusing to access path outside UI job root: {resolved_path}"
        ) from exc
    return resolved_path


def _coerce_pid(pid: Any) -> int | None:
    """Parse a job.json pid into an int, or None if missing/corrupt.

    A hand-edited or corrupted job.json can carry a non-numeric pid; an
    unguarded ``int(pid)`` there would raise and (via list_jobs) blank the whole
    Jobs page. Callers treat None as "no usable pid".
    """
    try:
        return int(pid)
    except (TypeError, ValueError):
        return None


def _pid_is_alive(pid: int) -> bool | None:
    """SAFE liveness probe. Returns True=alive, False=confirmed dead, None=unknown.

    NEVER signals the process: ``os.kill(pid, 0)`` on Windows is NOT a liveness
    probe — CPython routes any non-CTRL signal to ``TerminateProcess``, so signal
    0 would KILL the pid (and a reused pid kills an unrelated process). POSIX uses
    signal 0 (the real no-op probe); Windows shells out to ``tasklist`` (audit G2).

    The three-valued return is load-bearing: a probe FAILURE (tasklist timeout,
    OSError, transient access error) must NOT be read as "dead" — that would make
    stop() skip the kill on a live job and make the zombie reconcile mislabel a
    healthy run. Callers treat None as "still running / do not reconcile".
    """
    if pid <= 0:
        return False
    if platform.system() == "Windows":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                shell=False, capture_output=True,
                encoding="utf-8", errors="replace", timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            return None  # probe failed — unknown, NOT dead
        if result.returncode != 0:
            return None  # abnormal tasklist exit (rc==0 for both match/no-match)
        # tasklist /NH /FO CSV emits the pid as column index 1, e.g.
        # "python.exe","12345","Console","1","14,660 K". Parse the CSV and pin
        # the comparison to that field (a naive substring match could collide
        # with the Session# column). The localized "No tasks" no-match line
        # parses as a single-column row and is correctly rejected.
        for row in csv.reader((result.stdout or "").splitlines()):
            if len(row) > 1 and row[1].strip() == str(pid):
                return True
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # alive but owned by another user
    except OSError:
        return None  # unknown — do not treat as dead
    return True


def _wait_for_pid_exit(pid: int, *, attempts: int = 10, interval_seconds: float = 0.1) -> bool:
    import time

    for _ in range(attempts):
        # Stop waiting as soon as the pid is not confirmed-alive. A None
        # (probe-failed) result also breaks the loop rather than spinning —
        # otherwise a repeatedly-timing-out tasklist could block for attempts ×
        # 15s. The return value is advisory; callers use this only as a grace
        # delay after issuing a kill.
        if _pid_is_alive(pid) is not True:
            return True
        time.sleep(interval_seconds)
    return False


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
        config["output_dir"] = str(result_dir)

        config_path = job_dir / "config.yaml"
        _write_config_yaml(config, config_path)

        now = datetime.now(timezone.utc).isoformat()
        _write_job_json(job_dir, {
            "job_id": job_id,
            "mode": mode,
            "status": "pending",
            # created_at is stamped ONCE at creation and never overwritten — the
            # jobs page sorts (default) and date-filters on it, so without it
            # running jobs sank to the bottom and vanished under any date filter
            # (audit G; created_at was previously never written). started_at is
            # kept for back-compat with existing readers.
            "created_at": now,
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
        if platform.system() == "Windows":
            # Put the training subprocess in its OWN process group so a Ctrl+C on
            # the Streamlit server's console does NOT propagate to and kill the
            # running job (audit G2). NB: this only isolates CTRL_C_EVENT —
            # closing the console window / logoff / shutdown still terminate the
            # child (those events ignore process groups); full survival would
            # need DETACHED_PROCESS, out of scope here.
            popen_kwargs["creationflags"] = _CREATE_NEW_PROCESS_GROUP
        else:
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
            "process_group": "own_session" if platform.system() != "Windows" else "windows_new_group",
        })

        return job_id

    @staticmethod
    def stop(job_id: str) -> None:
        job_dir = _resolve_child_dir(JOB_ROOT, job_id)
        if not job_dir.is_dir():
            raise JobManagerError(
                f"Cannot stop job {job_id!r}: job directory not found."
            )
        data = _read_job_json(job_dir)
        status = data.get("status")
        # Refuse to kill a job the runner reported as ENDED: its pid may have been
        # recycled by the OS for an unrelated process, and taskkill /T would take
        # down that process tree (audit G2). "stop_failed" is intentionally NOT
        # refused — a prior stop attempt that timed out / was denied leaves the
        # job likely still alive, so the operator must be able to retry.
        if status in _RUNNER_TERMINAL_STATUSES:
            raise JobManagerError(
                f"Cannot stop job {job_id!r}: already in terminal state {status!r}."
            )
        pid = data.get("pid")
        if not pid:
            message = f"Cannot stop job {job_id!r}: job.json has no pid."
            _write_job_json(job_dir, {
                "status": "stop_failed",
                "stop_error": message,
                "stop_failed_at": datetime.now(timezone.utc).isoformat(),
            })
            raise JobManagerError(message)
        pid_int = _coerce_pid(pid)
        if pid_int is None:
            message = f"Cannot stop job {job_id!r}: job.json pid is not an integer: {pid!r}."
            _write_job_json(job_dir, {
                "status": "stop_failed",
                "stop_error": message,
                "stop_failed_at": datetime.now(timezone.utc).isoformat(),
            })
            raise JobManagerError(message)

        # CONFIRMED dead pid: the runner exited (crash / hard-kill / reboot)
        # without recording a terminal status. Do NOT issue a kill — the pid may
        # now belong to an unrelated process. Mark failed via compare-and-set so
        # we don't clobber a status the runner wrote concurrently. An UNKNOWN
        # probe (None — tasklist hiccup) falls through to taskkill rather than
        # skipping the kill on a possibly-live job; if the process turns out to
        # be already gone, the failure paths below re-probe and record it as a
        # benign already-exited rather than a stop failure.
        if _pid_is_alive(pid_int) is False:
            _write_job_json(job_dir, {
                "status": "failed",
                "ended_at": datetime.now(timezone.utc).isoformat(),
                "failure_reason": "process_not_running_at_stop",
            }, only_if_status=_STOP_WRITABLE_STATUSES)
            return

        if platform.system() == "Windows":
            try:
                result = subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    shell=False,
                    capture_output=True,
                    # taskkill prints in the console OEM code page (e.g. cp936 on
                    # zh-CN Windows); decoding as utf-8 with errors="replace"
                    # avoids a UnicodeDecodeError crashing Stop. timeout bounds a
                    # taskkill that hangs (e.g. an unkillable/elevated process).
                    encoding="utf-8",
                    errors="replace",
                    timeout=30,
                )
            except subprocess.TimeoutExpired as exc:
                message = (
                    f"Failed to stop job {job_id!r} with pid {pid}: "
                    "taskkill did not return within 30s."
                )
                _write_job_json(job_dir, {
                    "status": "stop_failed",
                    "stop_error": message,
                    "stop_failed_at": datetime.now(timezone.utc).isoformat(),
                }, only_if_status=_STOP_WRITABLE_STATUSES)
                raise JobManagerError(message) from exc
            if result.returncode != 0:
                # taskkill failed. Its exit code is ambiguous (a missing pid AND
                # a protected live pid can both return 128 on Windows), so re-probe
                # to tell "already gone" (benign — we fell through here from an
                # inconclusive probe that raced the runner exiting) from a real
                # failure (e.g. access denied, still running).
                if _pid_is_alive(pid_int) is False:
                    _write_job_json(job_dir, {
                        "status": "failed",
                        "ended_at": datetime.now(timezone.utc).isoformat(),
                        "failure_reason": "process_not_running_at_stop",
                    }, only_if_status=_STOP_WRITABLE_STATUSES)
                    return
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
                }, only_if_status=_STOP_WRITABLE_STATUSES)
                raise JobManagerError(message)
        else:
            try:
                if data.get("process_group") == "own_session":
                    # ``os.killpg`` / ``os.getpgid`` are POSIX-only and
                    # not present on Windows; mypy flags them as
                    # unbound on the Windows-side analysis. The branch
                    # only runs after ``platform.system() != "Windows"``
                    # — see ``test_stop_non_windows_*`` for behavioural
                    # coverage. (``sys.platform`` would narrow but the
                    # tests patch ``platform.system``.) The
                    # ``unused-ignore`` code makes the suppression
                    # safe across mypy versions whose os stubs
                    # disagree on whether these attrs exist.
                    os.killpg(os.getpgid(pid_int), signal.SIGTERM)  # type: ignore[attr-defined,unused-ignore]
                else:
                    os.kill(pid_int, signal.SIGTERM)
            except ProcessLookupError:
                # Process already gone — benign (we reached the kill from an
                # inconclusive/alive probe that raced the runner exiting). Record
                # as already-exited, not a stop failure.
                _write_job_json(job_dir, {
                    "status": "failed",
                    "ended_at": datetime.now(timezone.utc).isoformat(),
                    "failure_reason": "process_not_running_at_stop",
                }, only_if_status=_STOP_WRITABLE_STATUSES)
                return
            except OSError as exc:
                message = (
                    f"Failed to stop job {job_id!r} with pid {pid}: "
                    f"{type(exc).__name__}: {exc}"
                )
                _write_job_json(job_dir, {
                    "status": "stop_failed",
                    "stop_error": message,
                    "stop_failed_at": datetime.now(timezone.utc).isoformat(),
                }, only_if_status=_STOP_WRITABLE_STATUSES)
                raise JobManagerError(message) from exc
        _wait_for_pid_exit(pid_int)

        # Compare-and-set: only record "stopped" if the job is still active. If
        # the runner finished (success) or wrote its own "stopped" in the race
        # window after our terminal-state guard, leave that result intact.
        _write_job_json(job_dir, {
            "status": "stopped",
            "ended_at": datetime.now(timezone.utc).isoformat(),
        }, only_if_status=_STOP_WRITABLE_STATUSES)

    @staticmethod
    def delete(job_id: str) -> None:
        job_dir = _resolve_child_dir(JOB_ROOT, job_id)
        if not job_dir.is_dir():
            raise JobManagerError(f"Cannot delete job {job_id!r}: job directory not found.")
        data = _read_job_json(job_dir)
        if data.get("status") == "running":
            raise JobManagerError(
                f"Cannot delete running job {job_id!r}; stop it before deleting."
            )
        shutil.rmtree(job_dir)
        # Also clean ``RESULT_ROOT / job_id`` — the job-config directory
        # under ``JOB_ROOT`` is small, but the result directory carries
        # the trained model pickles, predictions, and reports
        # (megabytes to gigabytes per run). Leaving it behind after
        # ``delete()`` was a disk-space leak for any operator who used
        # the UI to clean up failed experiments. ``_resolve_child_dir``
        # re-validates the job_id against ``RESULT_ROOT`` so this can't
        # be coaxed into traversal even though we already passed the
        # JOB_ROOT check (defense-in-depth: the two roots could in
        # principle have different layouts).
        result_dir = _resolve_child_dir(RESULT_ROOT, job_id)
        if result_dir.is_dir():
            shutil.rmtree(result_dir)

    @staticmethod
    def status(job_id: str) -> dict[str, Any]:
        job_dir = _resolve_child_dir(JOB_ROOT, job_id)
        if not job_dir.is_dir():
            return {"job_id": job_id, "status": "unknown"}
        data = _read_job_json(job_dir)
        if not data:
            return {"job_id": job_id, "status": "unknown"}
        # job_runner writes success/failed + ended_at when the CLI exits, so we
        # trust those. But a hard kill / OOM / reboot leaves job.json stuck at
        # "running" with a dead pid forever (a zombie). Detect that and mark it
        # failed so the UI stops showing a perpetual "running" (audit G2). We
        # never SIGNAL the process — _pid_is_alive is a safe probe.
        data = _reconcile_zombie(job_dir, data)
        return _with_progress(job_dir, data)

    @staticmethod
    def list_jobs() -> list[dict[str, Any]]:
        if not JOB_ROOT.is_dir():
            return []
        results = []
        for job_dir in sorted(JOB_ROOT.iterdir(), reverse=True):
            if job_dir.is_dir():
                data = _read_job_json(job_dir)
                if data:
                    # Reconcile zombies here too so the list view doesn't show a
                    # crashed job as "running" until its detail is opened. Cost is
                    # bounded: _reconcile_zombie only probes a pid when status is
                    # "running" (typically 0-1 jobs), not for every listed job.
                    data = _reconcile_zombie(job_dir, data)
                    results.append(_with_progress(job_dir, data))
        return results
