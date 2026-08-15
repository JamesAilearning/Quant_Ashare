"""上次数据更新运行状态读取器（只读）。

Reads the run-status artifact written by the daily data-update orchestrator
(``src/data_pipeline/daily_update.py`` — named HERE, in the helper, so the
inspector PAGE source stays free of the orchestrator / swap-machinery names the
read-only governance scan forbids: tests/governance/test_data_inspect_readonly.py).

Boundary: this module only READS the artifact. It never writes, never triggers
anything, and the artifact itself is observability-only — never a canonical
input (openspec 2026-08-14-daily-update-run-status).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Mirrors src/data_pipeline/daily_update.py STATUS_FILENAME. Duplicated by
# design (web/ must not import the pipeline layer for a filename); the logic
# test pins the two to the same value.
STATUS_FILENAME = "daily_update_status.json"

# Mirrors src/data_pipeline/daily_update.py STATUS_SCHEMA_VERSION, same
# duplication reasoning; pinned by the same logic test. A record with any
# OTHER version is corrupt here — never interpreted with v1 semantics.
STATUS_SCHEMA_VERSION = 1

# Exit-code → one-line Chinese meaning, mirroring the runbook table in
# docs/runbook_daily_update_scheduling.md (the canonical exit-code reference).
EXIT_CODE_MEANINGS: dict[int, str] = {
    0: "成功（含周末日历门 no-op）",
    2: "配置/启动错误",
    10: "启动修复发现不可修复的 bundle 状态",
    11: "抓取硬失败（查 token / 网络）",
    12: "抓取有洞且未放行（或收盘前数据未发布）",
    13: "active-stocks 快照未刷新到运行日",
    14: "重建失败（02/05/03/04/07 之一）",
    15: "校验失败（06 未通过，bundle 未切换）",
    16: "原子切换失败（查磁盘/权限）",
    17: "另一次运行持有单飞锁（并发冲突）",
}


@dataclass(frozen=True)
class UpdateRunStatus:
    """The parsed artifact. ``kind`` drives the page's rendering branch:

    * ``missing``  — no artifact (fresh machine / pre-first-run): info state.
    * ``corrupt``  — present but unreadable / shape-invalid: prominent error,
      never a silent default. ``error`` carries the reason.
    * ``running``  — a run is in flight (state="running" record).
    * ``finished`` — terminal record; ``exit_code == 0`` means success
      (including the deliberate weekend no-op), anything else failed at
      ``failed_stage``.
    """

    kind: str
    path: Path
    state: str = ""
    run_date: str = ""
    started_at: str = ""
    finished_at: str = ""
    exit_code: int | None = None
    failed_stage: str | None = None
    detail: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.kind == "finished" and self.exit_code == 0

    @property
    def exit_meaning(self) -> str:
        if self.exit_code is None:
            return ""
        return EXIT_CODE_MEANINGS.get(self.exit_code, "未知退出码")


def status_path_for_provider(provider_dir: Path) -> Path:
    """The artifact lives as a SIBLING of the provider dir (it must survive
    the bundle's atomic swap, which only renames the provider dir itself)."""
    return provider_dir.parent / STATUS_FILENAME


def read_update_status(path: Path) -> UpdateRunStatus:
    """Read + shape-validate the artifact. Fail-loud on corruption; a missing
    file is the only non-error absence."""
    if not path.exists():
        return UpdateRunStatus(kind="missing", path=path)
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return UpdateRunStatus(
            kind="corrupt", path=path,
            error=f"不是合法 JSON（{type(exc).__name__}: {exc}）",
        )
    if not isinstance(payload, dict):
        return UpdateRunStatus(
            kind="corrupt", path=path,
            error=f"顶层不是 JSON object（got {type(payload).__name__}）",
        )
    # codex P2: validate the COMPLETE state-specific schema before believing
    # any of it — a truncated or future-version record must read as corrupt,
    # never as a green success. Version first: an unsupported schema_version
    # is never interpreted with v1 semantics.
    version = payload.get("schema_version")
    # `isinstance(True, int)` is True and `True == 1`, so a JSON `true` would
    # otherwise satisfy the version check and an otherwise-complete record
    # would render GREEN on a document nothing validated (codex #434 r3).
    # Type first, value second — the same order the state check below uses.
    if isinstance(version, bool) or version != STATUS_SCHEMA_VERSION:
        return UpdateRunStatus(
            kind="corrupt", path=path,
            error=f"schema_version 缺失或不受支持（got {version!r}，仅支持 "
                  f"{STATUS_SCHEMA_VERSION}）",
        )
    state = payload.get("state")
    if state not in ("running", "finished"):
        return UpdateRunStatus(
            kind="corrupt", path=path,
            error=f"state 缺失或非法（got {state!r}，期望 running/finished）",
        )
    required = ["run_date", "started_at"]
    if state == "finished":
        required.append("finished_at")
    missing = [
        k for k in required
        if not (isinstance(payload.get(k), str) and payload.get(k))
    ]
    if missing:
        return UpdateRunStatus(
            kind="corrupt", path=path,
            error=f"{state} 记录缺少非空字段：{', '.join(missing)}"
                  f"（截断的记录绝不按成功/运行中渲染）",
        )
    exit_code = payload.get("exit_code")
    if state == "finished" and (
        isinstance(exit_code, bool) or not isinstance(exit_code, int)
    ):
        return UpdateRunStatus(
            kind="corrupt", path=path,
            error=f"finished 记录的 exit_code 不是 int（got {exit_code!r}）",
        )
    if state == "finished":
        # codex P2 round 2: the writer ALWAYS emits failed_stage (null on
        # success) and detail — a truncated record that still carries the
        # timestamp fields must not slip through as a green success. Require
        # both keys, type-check them, and enforce the success/failure
        # invariant between exit_code and failed_stage.
        absent = [k for k in ("failed_stage", "detail") if k not in payload]
        if absent:
            return UpdateRunStatus(
                kind="corrupt", path=path,
                error=f"finished 记录缺少字段：{', '.join(absent)}"
                      f"（写侧恒定产出，缺即截断）",
            )
        failed_stage = payload["failed_stage"]
        if failed_stage is not None and not (
            isinstance(failed_stage, str) and failed_stage
        ):
            return UpdateRunStatus(
                kind="corrupt", path=path,
                error=f"failed_stage 非法（got {failed_stage!r}，期望 null 或"
                      f"非空阶段键）",
            )
        if not isinstance(payload["detail"], str):
            return UpdateRunStatus(
                kind="corrupt", path=path,
                error=f"detail 不是 str（got {type(payload['detail']).__name__}）",
            )
        if exit_code == 0 and failed_stage is not None:
            return UpdateRunStatus(
                kind="corrupt", path=path,
                error=f"exit_code=0 却带 failed_stage={failed_stage!r}——"
                      f"成功/失败不变式被破坏",
            )
        if exit_code != 0 and failed_stage is None:
            return UpdateRunStatus(
                kind="corrupt", path=path,
                error=f"exit_code={exit_code} 却缺 failed_stage——失败记录"
                      f"必须命名失败阶段",
            )
    return UpdateRunStatus(
        kind=state,
        path=path,
        state=state,
        run_date=str(payload.get("run_date") or ""),
        started_at=str(payload.get("started_at") or ""),
        finished_at=str(payload.get("finished_at") or ""),
        exit_code=exit_code if state == "finished" else None,
        failed_stage=(
            str(payload["failed_stage"])
            if payload.get("failed_stage") is not None else None
        ),
        detail=str(payload.get("detail") or ""),
    )
