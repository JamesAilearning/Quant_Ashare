"""Informational progress estimates for operator UI jobs.

Progress is derived from UI job artifacts only.  This module intentionally does
not import qlib, Tushare, or core runtime engines.
"""

from __future__ import annotations

import json
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

    if mode == "tushare_provider":
        return _estimate_tushare_provider(job_dir, config)
    if mode == "pipeline":
        return _estimate_pipeline(job_dir, config, job)
    if mode == "walk_forward":
        return _estimate_walk_forward(job_dir, config, job)
    if status == "pending":
        return _progress(0, "等待中", "等待运行进程启动。")
    return _progress(5, "运行中", _log_detail(job_dir))


def _estimate_percent(job_dir: Path, mode: str, config: Mapping[str, Any]) -> int:
    if mode == "tushare_provider":
        return int(_estimate_tushare_provider(job_dir, config)["percent"])
    if mode == "pipeline":
        return int(_estimate_pipeline(job_dir, config, {})["percent"])
    if mode == "walk_forward":
        return int(_estimate_walk_forward(job_dir, config, {})["percent"])
    return 0


def _estimate_tushare_provider(job_dir: Path, config: Mapping[str, Any]) -> Progress:
    percent = 5
    label = "正在启动 Tushare 拉取"
    detail = _log_detail(job_dir)

    if _has_logs(job_dir):
        percent = 15
        label = "Tushare CLI 运行中"

    staging_dir = _optional_path(config.get("staging_dir"))
    if staging_dir and _has_any_file(staging_dir):
        percent = max(percent, 30)
        label = "已下载 Tushare 暂存数据"
        detail = f"staging_dir={staging_dir}"

    output_dir = _optional_path(config.get("output_dir"))
    if output_dir and output_dir.is_dir():
        percent = max(percent, 40)
        label = "正在准备 qlib 数据源"
        detail = f"output_dir={output_dir}"
        if (output_dir / "calendars").is_dir():
            percent = max(percent, 50)
            label = "已写入 qlib 日历"
        features_dir = output_dir / "features"
        if features_dir.is_dir():
            feature_files = _count_files(features_dir, cap=5000)
            percent = max(percent, min(90, 55 + feature_files // 150))
            label = "正在写入 qlib 特征文件"
            suffix = "+" if feature_files >= 5000 else ""
            detail = f"已检测到 {feature_files}{suffix} 个特征文件"

    manifest_path = _optional_path(config.get("manifest_path"))
    validation_path = _optional_path(config.get("validation_path"))
    if (manifest_path and manifest_path.is_file()) or (validation_path and validation_path.is_file()):
        percent = max(percent, 95)
        label = "已生成数据源校验产物"
        detail = _validation_detail(manifest_path, validation_path) or detail

    return _progress(percent, label, detail)


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
        if (run_dir / "positions.json").is_file():
            percent = max(percent, 70)
            label = "已写入回测持仓"
        if (run_dir / "pipeline_report.json").is_file():
            percent = max(percent, 92)
            label = "已写入流水线报告"
        if _has_any_file(run_dir / "charts"):
            percent = max(percent, 95)
            label = "已写入图表"

    return _progress(percent, label, detail)


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


def _validation_detail(manifest_path: Path | None, validation_path: Path | None) -> str:
    validation = _read_json(validation_path)
    if validation:
        health = validation.get("health")
        rows = validation.get("row_count")
        instruments = validation.get("instrument_count")
        return f"validation_health={health}, rows={rows}, instruments={instruments}"
    manifest = _read_json(manifest_path)
    if manifest:
        health = manifest.get("validation_health")
        rows = manifest.get("row_count")
        instruments = manifest.get("instrument_count")
        return f"validation_health={health}, rows={rows}, instruments={instruments}"
    return ""


def _read_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if isinstance(loaded, dict):
        return loaded
    return {}


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


def _count_files(path: Path, *, cap: int) -> int:
    count = 0
    if not path.is_dir():
        return count
    try:
        for child in path.rglob("*"):
            if child.is_file():
                count += 1
                if count >= cap:
                    return count
    except OSError:
        return count
    return count


def _progress(percent: int, label: str, detail: str = "") -> Progress:
    return {
        "percent": max(0, min(100, int(percent))),
        "label": label,
        "detail": detail,
    }
