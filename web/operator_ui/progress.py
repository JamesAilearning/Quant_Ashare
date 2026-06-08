"""Informational progress estimates for operator UI jobs.

Progress is derived from UI job artifacts only.  This module intentionally does
not import qlib, Tushare, or core runtime engines.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

Progress = dict[str, Any]


def build_job_progress(job_dir: Path, job: Mapping[str, Any]) -> Progress:
    """Return an informational progress snapshot for a UI job."""

    status = str(job.get("status") or "unknown")
    mode = str(job.get("mode") or "unknown")
    config = _read_config(job_dir, job)

    if status == "success":
        return _progress(100, "已完成", _terminal_detail(job))
    if status == "stopped":
        return _progress(_estimate_percent(job_dir, mode, config), "已停止", _terminal_detail(job))
    if status == "failed":
        return _progress(_estimate_percent(job_dir, mode, config), "失败", _terminal_detail(job))
    if status == "stop_failed":
        return _progress(_estimate_percent(job_dir, mode, config), "停止失败", _terminal_detail(job))

    if mode == "pipeline":
        return _estimate_pipeline(job_dir, config, job)
    if mode == "walk_forward":
        return _estimate_walk_forward(job_dir, config, job)
    if status == "pending":
        return _progress(0, "等待中", "等待运行进程启动。")
    return _progress(5, "运行中", _log_detail(job_dir))


def _estimate_percent(job_dir: Path, mode: str, config: Mapping[str, Any]) -> int:
    if mode == "pipeline":
        return int(_estimate_pipeline(job_dir, config, {})["percent"])
    if mode == "walk_forward":
        return int(_estimate_walk_forward(job_dir, config, {})["percent"])
    return 0


def _estimate_pipeline(job_dir: Path, config: Mapping[str, Any], job: Mapping[str, Any]) -> Progress:
    percent = 5
    label = "正在启动流水线"
    detail = _log_detail(job_dir)

    if _has_logs(job_dir):
        percent = 20
        label = "流水线 CLI 运行中"

    run_dir = _run_dir_from_job(job) or _find_pipeline_run_dir(_optional_path(config.get("output_dir")))
    if run_dir and run_dir.is_dir():
        percent = max(percent, 35)
        label = "已创建流水线运行目录"
        detail = f"run_dir={run_dir}"
        if (run_dir / "model.pkl").is_file():
            percent = max(percent, 55)
            label = "已写入模型产物"
        # Smooth the model-training (55%) → report (92%) gap, which is
        # the longest wall-clock stretch and previously left the bar
        # frozen at 55% before a sudden jump to 95% (UI review P2-14).
        #
        # The smoothing keys on signals the pipeline ACTUALLY emits
        # during this window, in order:
        #   * ``positions.json`` — written by the backtest step
        #     (``pipeline.py`` ~L527), well before the report.
        #   * stdout/stderr phase log markers ("Running canonical
        #     backtest", "Running factor analysis", "Running
        #     performance attribution") — all logged before Step 8
        #     writes the report.
        # An earlier revision keyed on ``predictions.parquet`` /
        # ``metrics.json`` / ``nav.parquet``, but those are emitted by
        # ``write_pipeline_result_artifacts`` AFTER the report is
        # written, so they could never smooth a live run — the report
        # check below had already pushed to 92% (Codex follow-up on
        # PR #207).
        # positions.json (70) and the phase log markers are all
        # pre-report signals; apply each only while it is the
        # furthest-along signal so a later phase's label is never
        # downgraded back to "已写入回测持仓".
        if (run_dir / "positions.json").is_file() and percent < 70:
            percent = 70
            label = "已写入回测持仓"
        log_phase = _pipeline_log_phase(job_dir)
        if log_phase is not None and log_phase[0] > percent:
            percent, label = log_phase
        if (run_dir / "pipeline_report.json").is_file():
            percent = max(percent, 92)
            label = "已写入流水线报告"
        if _has_any_file(run_dir / "charts"):
            percent = max(percent, 95)
            label = "已写入图表"

    return _progress(percent, label, detail)


# Pipeline phase markers logged (via ``_logger`` / stdout) BEFORE
# ``pipeline_report.json`` is written. Mapped to monotonic percents so
# the operator sees the bar advance through the train→backtest→analysis
# window. Each marker is "Running X…" emitted at phase START, so the
# percent reflects "phase in progress", not "phase done".
_PIPELINE_LOG_PHASES: tuple[tuple[str, int, str], ...] = (
    ("Running canonical backtest", 65, "正在运行回测"),
    ("Running factor analysis", 80, "正在做因子分析"),
    ("Running performance attribution", 86, "正在做绩效归因"),
)


def _pipeline_log_phase(job_dir: Path) -> tuple[int, str] | None:
    """Return the furthest-along ``(percent, label)`` whose phase marker
    appears in the job's stdout/stderr logs, or ``None`` if no marker
    is present yet.

    Reads only the trailing 64 KiB of each log by seeking from EOF, so a
    multi-MB training log isn't loaded whole into memory on every
    progress poll (Codex follow-up on PR #207). Mirrors the bounded-tail
    read in ``job_io._extract_failure_detail``.
    """

    tail_bytes = 64 * 1024
    blob = ""
    for name in ("stdout.log", "stderr.log"):
        path = job_dir / name
        try:
            with path.open("rb") as handle:
                try:
                    handle.seek(-tail_bytes, 2)  # 2 = SEEK_END
                except OSError:
                    handle.seek(0)
                data = handle.read()
        except OSError:
            continue
        if data:
            blob += data.decode("utf-8", errors="replace")
    if not blob:
        return None
    best: tuple[int, str] | None = None
    for marker, phase_percent, phase_label in _PIPELINE_LOG_PHASES:
        if marker in blob and (best is None or phase_percent > best[0]):
            best = (phase_percent, phase_label)
    return best


def _estimate_walk_forward(job_dir: Path, config: Mapping[str, Any], job: Mapping[str, Any]) -> Progress:
    percent = 5
    label = "正在启动滚动验证"
    detail = _log_detail(job_dir)

    if _has_logs(job_dir):
        percent = 20
        label = "滚动验证 CLI 运行中"

    output_dir = _run_dir_from_job(job) or _optional_path(config.get("output_dir"))
    if output_dir and output_dir.is_dir():
        percent = max(percent, 30)
        label = "已创建滚动验证输出目录"
        fold_reports = list(output_dir.glob("fold_*_report.json"))
        if fold_reports:
            percent = max(percent, min(88, 35 + len(fold_reports) * 8))
            label = "已写入单折报告"
            detail = f"已检测到 {len(fold_reports)} 份单折报告"
        if (output_dir / "walk_forward_report.json").is_file():
            percent = max(percent, 95)
            label = "已写入滚动验证汇总报告"
            detail = f"run_dir={output_dir}"

    return _progress(percent, label, detail)


def _read_config(job_dir: Path, job: Mapping[str, Any]) -> dict[str, Any]:
    config_path = _optional_path(job.get("config_path")) or (job_dir / "config.yaml")
    if not config_path.is_file():
        return {}
    try:
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    if isinstance(loaded, dict):
        return loaded
    return {}


def _run_dir_from_job(job: Mapping[str, Any]) -> Path | None:
    return _optional_path(job.get("run_dir"))


def _find_pipeline_run_dir(output_dir: Path | None) -> Path | None:
    if output_dir is None:
        return None
    runs_dir = output_dir / "runs"
    if not runs_dir.is_dir():
        return None
    candidates = [path for path in runs_dir.iterdir() if path.is_dir()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _optional_path(value: Any) -> Path | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return Path(text)


def _has_logs(job_dir: Path) -> bool:
    return _has_nonempty_file(job_dir / "stdout.log") or _has_nonempty_file(job_dir / "stderr.log")


def _log_detail(job_dir: Path) -> str:
    parts: list[str] = []
    for name in ("stdout.log", "stderr.log", "runner_stdout.log", "runner_stderr.log"):
        path = job_dir / name
        if _has_nonempty_file(path):
            parts.append(f"{name}={path.stat().st_size} bytes")
    return ", ".join(parts) if parts else "等待作业日志生成。"


def _terminal_detail(job: Mapping[str, Any]) -> str:
    if job.get("error"):
        return str(job["error"])
    if job.get("stop_error"):
        return str(job["stop_error"])
    if job.get("run_dir"):
        return f"run_dir={job['run_dir']}"
    if job.get("ended_at"):
        return f"ended_at={job['ended_at']}"
    return ""


def _has_any_file(path: Path) -> bool:
    if not path.is_dir():
        return False
    try:
        for child in path.rglob("*"):
            if child.is_file():
                return True
    except OSError:
        return False
    return False


def _has_nonempty_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _progress(percent: int, label: str, detail: str = "") -> Progress:
    return {
        "percent": max(0, min(100, int(percent))),
        "label": label,
        "detail": detail,
    }
