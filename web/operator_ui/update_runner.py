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

import contextlib
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
    * ``unusable_path`` — one of the three argv paths is not an
      absolute path (empty resolves to the CWD, a foreign-convention
      spelling lands elsewhere); nothing was started. The morning
      command's ``_arg`` boundary refuses the same class for pasteable
      text — a 2-hour detached run deserves no weaker a gate.
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
    for flag, path in (
        ("--provider-dir", provider_dir),
        ("--tushare-dir", tushare_dir),
        ("--delisted-registry", registry_path),
    ):
        # Path("") normalizes to "." and a foreign-convention spelling
        # ("/srv/…" on Windows, "D:x") is not absolute either — one
        # check covers the whole class this box can produce.
        if not path.is_absolute():
            return UpdateLaunch(
                kind="unusable_path",
                error=(
                    f"{flag} 不是绝对路径({str(path)!r})——拒绝把它交给"
                    "一次约 2 小时的后台运行(空串会被读成当前工作目录)。"
                ),
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
    # "launch attempt", not "launched": the marker is written BEFORE
    # Popen, and a failed spawn must not leave a line that misattributes
    # the scheduler's later output to a nonexistent UI run — the failure
    # path appends its own closing marker (codex #440 r6).
    marker = (
        f"[run_center] {datetime.now(tz=_CN_TZ).isoformat(timespec='seconds')}"
        " launch attempt: daily_update (detached; scheduler stays the"
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
        # Flush BEFORE spawning: the child appends through its inherited
        # descriptor, and an immediate exit-17 lock refusal would beat a
        # buffered marker into the file (codex #440 r1).
        log_fh.write(marker.encode("utf-8"))
        log_fh.flush()
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
        # Close out the attempt marker so nothing later (e.g. the
        # scheduler's own run) gets misattributed to this failed try.
        with contextlib.suppress(OSError):
            failure_line = (
                f"[run_center] launch FAILED before process creation:"
                f" {exc}\n"
            )
            log_fh.write(failure_line.encode("utf-8"))
            log_fh.flush()
        return UpdateLaunch(
            kind="launch_failed",
            error=f"无法启动解释器 {cmd[0]!r}:{exc}",
        )
    finally:
        # The child holds its own duplicated handle; ours must not leak
        # into the (long-lived) UI process.
        log_fh.close()
    return UpdateLaunch(kind="launched", pid=proc.pid, log_path=log_path)


#: 解码后若出现这些区段的字符,基本可以断定是 GBK 被当成 UTF-8 读了:
#: 本日志的内容只有 ASCII、CJK、中文标点,不会出现西里尔/希腊/亚美尼亚。
#: 例:``'抓取'.encode('gbk')`` 是合法 UTF-8,解出 ``'ץȡ'``(希伯来+拉丁扩展)。
_MOJIBAKE_RANGES: tuple[tuple[int, int], ...] = (
    (0x00C0, 0x024F),  # Latin-1 补充 / 拉丁扩展 A、B
    (0x0370, 0x03FF),  # 希腊
    (0x0400, 0x04FF),  # 西里尔
    (0x0530, 0x058F),  # 亚美尼亚
    (0x0590, 0x05FF),  # 希伯来
)


def _looks_like_mojibake(text: str) -> bool:
    """解出来的文本是否明显是「GBK 被当成 UTF-8」的产物。"""
    return any(
        any(lo <= ord(ch) <= hi for lo, hi in _MOJIBAKE_RANGES) for ch in text
    )


def _decode_log_line(raw: bytes) -> str:
    """一行日志,按它的写入者实际用的编码解码。

    这条共享日志历史上有两个写入者:本模块启动的运行钉了 UTF-8
    (``utf8_child_env``),而计划任务的 .bat 早先没钉,它的中文行落在
    控制台代码页(本机 cp936)。行永远不跨进程,所以逐行解码。

    **「UTF-8 解码成功」不等于「本来就是 UTF-8」**(codex #442 r4):
    部分 GBK 字节对恰好是合法 UTF-8——``'抓取'.encode('gbk')`` 解出
    ``'ץȡ'``,毫无替换符地静默出错,而「抓取」正是抓取阶段日志里的高频词。
    所以在 UTF-8 解码成功后还要看结果**像不像**乱码:出现西里尔/希腊/
    希伯来/拉丁扩展这些本日志绝不会有的区段,就改用 GBK 的结果。

    这仍是启发式,只用于**历史**行。根治在源头:调度器 .bat 现已钉死
    ``PYTHONIOENCODING=utf-8``,此后新写入的行一律 UTF-8。
    """
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return raw.decode("gbk")
        except UnicodeDecodeError:
            return raw.decode("utf-8", errors="replace")
    if _looks_like_mojibake(text):
        try:
            return raw.decode("gbk")
        except UnicodeDecodeError:
            return text
    return text


def log_tail(log_path: Path, *, chars: int = _LOG_TAIL_CHARS) -> str:
    """Last ``chars`` characters of the log, decoded leniently.

    Missing log = empty string (a fresh machine has no log yet; that is
    a state to render, not an error).
    """
    try:
        with open(log_path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            start = max(0, size - chars * 4)  # UTF-8 worst case
            fh.seek(start)
            data = fh.read()
    except FileNotFoundError:
        return ""
    except OSError as exc:
        return f"(日志不可读:{exc})"
    lines = data.split(b"\n")
    if start > 0 and len(lines) > 1:
        # 从中间起读,首行多半被切成半个字符序列——丢掉它,别拿半行去猜编码。
        lines = lines[1:]
    return "\n".join(_decode_log_line(line) for line in lines)[-chars:]
