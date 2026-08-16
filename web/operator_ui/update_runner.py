"""数据更新 detached 启动器 — 运行中心页的手动补跑通道。

Launches ``scripts/daily_update.py`` as a DETACHED subprocess whose argv
mirrors the scheduler wrapper (``run_daily_update.bat``), appending its
output to the SAME log stream the scheduler writes. A full update run
takes ~2 hours (2026-08-14 measured), so the launcher never waits: the
page observes progress through the #434 status artifact
(``web.operator_ui.update_status``) and the log tail.

Boundaries (openspec 2026-08-16-ui-run-center):

* The ONLY coupling with the orchestrator is the CLI process boundary —
  this module never imports ``src.data_pipeline.*``.
* Concurrency authority is ``daily_update``'s own single-flight lock
  (a losing concurrent run exits 17 into the log). The pre-launch
  "already running" check here is ADVISORY UX, derived from the status
  artifact only; this module never touches the lock file.
* ``launched`` means "a process was created", never "the update
  succeeded" — success/failure lives in the status artifact and the log.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.child_env import utf8_child_env
from web.operator_ui.update_status import (
    RUNNING_FRESH,
    classify_running,
    read_update_status,
    record_matches_provider,
    status_path_for_provider,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
UPDATE_SCRIPT = PROJECT_ROOT / "scripts" / "daily_update.py"
REFERENCE_CASES = PROJECT_ROOT / "tests" / "pit" / "reference_cases.yaml"

# Mirrors the scheduler wrapper: the pipeline's own repair logic prunes
# already-ingested days, so a fixed start date is a re-scan floor, not a
# full refetch.
START_DATE = "20180101"

TOKEN_ENV_VAR = "TUSHARE_TOKEN"

_LOG_TAIL_CHARS = 4000

# Operator-facing timestamps use fixed +08:00, mirroring the repo
# convention (``web/operator_ui/formatting.py``). Asia/Shanghai has no
# DST, so the fixed offset is exact.
_CN_TZ = timezone(timedelta(hours=8))


@dataclass(frozen=True)
class UpdateLaunch:
    """Outcome of one launch attempt. ``kind`` drives the page:

    * ``launched`` — a detached process was created; ``pid`` and
      ``log_path`` are set. NOT a success claim.
    * ``no_token`` — the child would inherit no usable ``TUSHARE_TOKEN``;
      nothing was started (the fetch stage would die ~immediately, so
      refuse where the operator can read why).
    * ``already_running`` — the status artifact for THIS provider says a
      run is in flight and fresh. Advisory duplicate-click guard; the
      single-flight lock stays the authority.
    * ``script_missing`` — repo layout drifted; nothing was started.
    * ``launch_failed`` — log dir/file or interpreter could not be set
      up (OSError); ``error`` carries the reason.
    """

    kind: str
    pid: int | None = None
    log_path: Path | None = None
    error: str = ""


def default_log_path(provider_dir: Path) -> Path:
    """The scheduler's log stream: ``<provider parent>/logs/daily_update.log``.

    Derived, not hardcoded, so the same convention holds wherever the
    bundle lives (the production wrapper logs to
    ``D:/qlib_data/logs/daily_update.log`` next to the bundle).
    """
    resolved = provider_dir.resolve()
    return resolved.parent / "logs" / "daily_update.log"


def build_update_argv(
    provider_dir: Path,
    tushare_dir: Path,
    registry_path: Path,
    *,
    python: str | None = None,
) -> list[str]:
    """The exact argv the scheduler wrapper uses, as an argv list.

    List-form argv needs no shell quoting — paths with spaces arrive as
    single arguments by construction.
    """
    return [
        python or sys.executable,
        str(UPDATE_SCRIPT),
        "--tushare-dir",
        str(tushare_dir),
        "--provider-dir",
        str(provider_dir),
        "--delisted-registry",
        str(registry_path),
        "--reference-cases",
        str(REFERENCE_CASES),
        "--start-date",
        START_DATE,
    ]


def _blocking_run_status(provider_dir: Path) -> str | None:
    """Why a launch should be refused based on the status artifact, or None.

    Only a record that (a) belongs to THIS provider, (b) says running and
    (c) classifies as FRESH blocks the button — a stale or unverifiable
    running record may be a crashed run, and a foreign record proves
    nothing about this provider. Those cases fall through to the
    single-flight lock, which is the real arbiter.
    """
    status = read_update_status(status_path_for_provider(provider_dir))
    if status.kind != "running":
        return None
    if not record_matches_provider(status, provider_dir):
        return None
    if classify_running(status) != RUNNING_FRESH:
        return None
    return (
        f"状态工件显示一次更新正在进行(始于 {status.started_at})。"
        "并发运行会被 daily_update 的单飞锁以 exit 17 拒绝;"
        "若确认那次运行已死,等它按 reader 语义变为陈旧(>6h)后再试。"
    )


def launch_daily_update(
    provider_dir: Path,
    tushare_dir: Path,
    registry_path: Path,
    *,
    python: str | None = None,
    env: Mapping[str, str] | None = None,
) -> UpdateLaunch:
    """Launch one detached update run mirroring the scheduler.

    ``env`` defaults to the current process environment; the child always
    receives it through ``utf8_child_env`` (stdout encoder pinned to
    UTF-8 so the shared log never mixes encodings). The token pre-check
    reads the SAME mapping the child will inherit.
    """
    if not UPDATE_SCRIPT.exists():
        return UpdateLaunch(
            kind="script_missing",
            error=f"更新脚本不在预期路径(仓库布局变了?):{UPDATE_SCRIPT}",
        )
    child_env = utf8_child_env(env)
    if not child_env.get(TOKEN_ENV_VAR, "").strip():
        return UpdateLaunch(
            kind="no_token",
            error=(
                f"环境变量 {TOKEN_ENV_VAR} 缺失或为空——fetch 阶段会立刻"
                "失败。请在启动 UI 前设置(调度器的 .bat 从 HKCU 注册表"
                "回读,UI 启动器模板同款,见 docs/run-center-runbook.md)。"
            ),
        )
    try:
        blocking = _blocking_run_status(provider_dir)
    except ValueError as exc:
        # status_path_for_provider refuses a filesystem-root provider —
        # that same path would also be an unusable --provider-dir.
        return UpdateLaunch(kind="launch_failed", error=str(exc))
    if blocking is not None:
        return UpdateLaunch(kind="already_running", error=blocking)

    log_path = default_log_path(provider_dir)
    cmd = build_update_argv(
        provider_dir, tushare_dir, registry_path, python=python
    )
    marker = (
        f"[run_center] {datetime.now(tz=_CN_TZ).isoformat(timespec='seconds')}"
        " manual launch of daily_update (detached; scheduler stays the"
        " automatic channel)\n"
    )
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_fh = open(log_path, "ab")  # noqa: SIM115 — handed to Popen
    except OSError as exc:
        return UpdateLaunch(
            kind="launch_failed",
            error=f"无法打开日志 {log_path}(磁盘/权限?):{exc}",
        )
    try:
        # The shared log's own lines carry only HH:MM:SS — the dated
        # marker is what lets an operator attribute the next block.
        log_fh.write(marker.encode("utf-8"))
        if sys.platform == "win32":
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                cwd=str(PROJECT_ROOT),
                env=child_env,
                creationflags=(
                    subprocess.CREATE_NEW_PROCESS_GROUP
                    | subprocess.CREATE_NO_WINDOW
                ),
            )
        else:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                cwd=str(PROJECT_ROOT),
                env=child_env,
                start_new_session=True,
            )
    except OSError as exc:
        return UpdateLaunch(
            kind="launch_failed",
            error=f"无法启动解释器 {cmd[0]!r}:{exc}",
        )
    finally:
        # The child holds its own duplicated handle; ours must not leak
        # into the (long-lived) UI process.
        log_fh.close()
    return UpdateLaunch(kind="launched", pid=proc.pid, log_path=log_path)


def log_tail(log_path: Path, *, chars: int = _LOG_TAIL_CHARS) -> str:
    """Last ``chars`` characters of the log, decoded leniently.

    Missing log = empty string (a fresh machine has no log yet; that is
    a state to render, not an error).
    """
    try:
        with open(log_path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - chars * 4))  # UTF-8 worst case
            data = fh.read()
    except FileNotFoundError:
        return ""
    except OSError as exc:
        return f"(日志不可读:{exc})"
    return data.decode("utf-8", errors="replace")[-chars:]
