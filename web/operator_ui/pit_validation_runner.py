"""PIT 校验子进程运行器 — 数据检视页只读语义的进程隔离实现。

Runs the 06 PIT validation CLI (``scripts/data_pipeline/06_validate_pit_data.py``)
in a SUBPROCESS instead of inside the UI process, and parses the structured
report the CLI writes via ``--report-json``. Two reasons:

1. qlib is a per-process singleton: once the UI process initializes it for
   one provider, validating a DIFFERENT provider_uri in-process hard-fails
   with a controlled ``QlibRuntimeInitError`` ("restart the UI"). A subprocess
   gets a fresh interpreter, so any bundle path can be validated any number
   of times from one UI session.
2. The inspector page stays free of validator / qlib imports — it only
   renders the parsed report dicts returned here.

Boundary: this module never writes into the inspected bundle. The CLI's
report JSON lands in a TemporaryDirectory deleted when the run returns; the
validator itself opens the bundle read-only. A malformed report is surfaced
loudly (kind="corrupt_report"), never defaulted.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
VALIDATOR_SCRIPT = (
    PROJECT_ROOT / "scripts" / "data_pipeline" / "06_validate_pit_data.py"
)

# The in-process page copy warned "可能需要数十秒"; a subprocess pays qlib init
# on top, and a full-registry boundary sweep scales with bundle size. 15 min
# is generous headroom that still prevents a hung validator from pinning the
# UI session forever.
DEFAULT_TIMEOUT_S = 900

_STDERR_TAIL_CHARS = 4000


@dataclass(frozen=True)
class PITRunResult:
    """Outcome of one subprocess validation run. ``kind`` drives the page:

    * ``ok`` — the CLI ran to completion AND its structured report parsed.
      ``exit_code`` is the report's own verdict (0 pass / 1 warnings /
      2 failures). NOTE: a process exit code of 2 WITH a parseable report is
      still ``ok`` here — it means "validation found failures", which is a
      RESULT to render, not a runner error.
    * ``run_failed`` — the CLI died before producing a report (setup error,
      e.g. unreadable registry) or the validator script itself is missing;
      ``error`` carries the stderr tail.
    * ``corrupt_report`` — the CLI finished but the report file is missing /
      unparseable / shape-invalid. Fail-loud; never a silent default.
    * ``timeout`` — the run exceeded ``timeout_s`` and was killed.
    * ``launch_failed`` — the Python interpreter could not be started at all.
    """

    kind: str
    exit_code: int | None = None
    checks: tuple[dict[str, Any], ...] = ()
    error: str = ""
    elapsed_s: float = 0.0


def _report_shape_errors(payload: Any) -> list[str]:
    """Shape-validate the parsed report. Returns a list of violations (empty =
    valid). Mirrors ``PITValidationReport.to_dict``; the validator and this
    reader deliberately do not import each other (web/ stays free of qlib), so
    the shape is pinned by a logic test against a real CLI run instead."""
    if not isinstance(payload, dict):
        return [f"顶层不是 JSON object（got {type(payload).__name__}）"]
    errors: list[str] = []
    exit_code = payload.get("exit_code")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        errors.append(f"exit_code 不是 int（got {exit_code!r}）")
    checks = payload.get("checks")
    if not isinstance(checks, list):
        errors.append(f"checks 不是 list（got {type(checks).__name__}）")
        return errors
    for i, c in enumerate(checks):
        if not isinstance(c, dict):
            errors.append(f"checks[{i}] 不是 object（got {type(c).__name__}）")
            continue
        for key in ("name", "code"):
            if not isinstance(c.get(key), str):
                errors.append(f"checks[{i}].{key} 不是 str（got {c.get(key)!r}）")
        if not isinstance(c.get("passed"), bool):
            errors.append(f"checks[{i}].passed 不是 bool（got {c.get('passed')!r}）")
        for key in ("warnings", "errors"):
            if not isinstance(c.get(key), list):
                errors.append(
                    f"checks[{i}].{key} 不是 list（got {type(c.get(key)).__name__}）"
                )
    return errors


def _tail(text: str) -> str:
    return text.strip()[-_STDERR_TAIL_CHARS:]


def run_pit_validation(
    provider_dir: Path,
    registry_path: Path,
    *,
    python: str | None = None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> PITRunResult:
    """Run the 06 PIT validator in a subprocess and parse its report.

    ``python`` defaults to ``sys.executable`` — the interpreter running the
    UI, which in production is the pinned canonical venv; an explicit override
    keeps the runner testable and lets an operator point at another env.
    """
    if not VALIDATOR_SCRIPT.exists():
        return PITRunResult(
            kind="run_failed",
            error=f"校验脚本不在预期路径（仓库布局变了？）：{VALIDATOR_SCRIPT}",
        )
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="pit_validate_") as tmp:
        report_path = Path(tmp) / "pit_report.json"
        cmd = [
            python or sys.executable,
            str(VALIDATOR_SCRIPT),
            "--provider-dir",
            str(provider_dir),
            "--delisted-registry",
            str(registry_path),
            "--report-json",
            str(report_path),
        ]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_s,
                cwd=str(PROJECT_ROOT),
            )
        except subprocess.TimeoutExpired:
            return PITRunResult(
                kind="timeout",
                error=f"超过 {timeout_s}s 上限，子进程已终止；bundle 未被触碰。",
                elapsed_s=time.monotonic() - started,
            )
        except OSError as exc:
            return PITRunResult(
                kind="launch_failed",
                error=f"无法启动解释器 {cmd[0]!r}：{exc}",
                elapsed_s=time.monotonic() - started,
            )
        elapsed = time.monotonic() - started
        if report_path.exists():
            try:
                payload: Any = json.loads(
                    report_path.read_text(encoding="utf-8")
                )
            except (OSError, ValueError) as exc:
                return PITRunResult(
                    kind="corrupt_report",
                    error=f"报告不是合法 JSON（{type(exc).__name__}: {exc}）",
                    elapsed_s=elapsed,
                )
            shape_errors = _report_shape_errors(payload)
            if shape_errors:
                return PITRunResult(
                    kind="corrupt_report",
                    error="报告形状违约：" + "；".join(shape_errors),
                    elapsed_s=elapsed,
                )
            return PITRunResult(
                kind="ok",
                exit_code=payload["exit_code"],
                checks=tuple(payload["checks"]),
                elapsed_s=elapsed,
            )
        # No report: the CLI only skips writing when validation SETUP failed
        # (PITValidatorError → exit 2 before validate()). Anything else with a
        # missing report is a contract breach — say so, loudly.
        if proc.returncode != 0:
            return PITRunResult(
                kind="run_failed",
                error=(
                    f"校验进程退出码 {proc.returncode} 且未产出报告。"
                    f"stderr 尾部：\n{_tail(proc.stderr) or '（空）'}"
                ),
                elapsed_s=elapsed,
            )
        return PITRunResult(
            kind="corrupt_report",
            error="校验进程退出码 0 但报告文件不存在 — 06 CLI 契约被违反。",
            elapsed_s=elapsed,
        )
