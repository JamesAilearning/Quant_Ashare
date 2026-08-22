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
import re
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
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
#
# 这是手动通道的**缺省**下限,不再是唯一可能的值:操作人可以显式改写
# (见 `build_update_argv`)。缺省不变正是「本通道镜像调度器」那条不变式
# 仍然成立的证据 —— tests 那边有一条 FULL-LIST 相等的守卫钉着它。
START_DATE = "20180101"

TOKEN_ENV_VAR = "TUSHARE_TOKEN"

_LOG_TAIL_CHARS = 4000

# Operator-facing timestamps use fixed +08:00, mirroring the repo
# convention (``web/operator_ui/formatting.py``). Asia/Shanghai has no
# DST, so the fixed offset is exact.
_CN_TZ = timezone(timedelta(hours=8))


# --------------------------------------------------------------- 抓取范围
#
# 为什么手动通道需要能改范围:2026-08-17/20/21 连续三晚失败,原因是一次
# 收盘前的更宽范围抓取把 fetch manifest 撑到了 20151001 并记下未解决的洞,
# 此后每一次 `--start-date 20180101` 都被 manifest 的范围守卫拒绝——它拒绝
# 缩窄合并,因为更窄的范围不会重试范围外的洞。01 当时给出的修法就是「按完整
# 范围重跑」,而这个页面**做不到**:范围是写死的。操作人只能去命令行。
#
# 同理,周末想补跑必须能传结束日期(见 `calendar_gate_warning`)。
_DATE_SHAPE = re.compile(r"^\d{8}$")


def date_input_problem(value: str, *, label: str) -> str | None:
    """这一端的日期为什么不可用,或 None。空串 = 未指定,合法。

    编排器与 01 对日期格式**零校验**:畸形值会一路流进 01 的 argv,直到
    tushare 那头才炸——而那已经是一次约两小时运行的中途。所以在按钮之前拦。
    """
    text = value.strip()
    if not text:
        return None
    if not _DATE_SHAPE.match(text):
        return f"{label} 必须是 8 位 YYYYMMDD(收到 {text!r})"
    try:
        date(int(text[:4]), int(text[4:6]), int(text[6:]))
    except ValueError:
        # 20260231 / 20261301 都是 8 位数字,但都不是日期。
        return f"{label} 不是一个真实日期(收到 {text!r})"
    return None


def range_problem(start_date: str, end_date: str) -> str | None:
    """两端一起看:每一端各自合法,不代表这个区间合法。"""
    for value, label in ((start_date, "开始日期"), (end_date, "结束日期")):
        problem = date_input_problem(value, label=label)
        if problem is not None:
            return problem
    start, end = start_date.strip(), end_date.strip()
    if start and end and start > end:
        # YYYYMMDD 定宽零填充,字典序就是时间序 —— 不需要解析成 date 再比。
        return f"开始日期 {start} 晚于结束日期 {end}"
    return None


# 下面两条是 `src.data_pipeline.daily_update` 的判据,在这里**重述**:本模块与
# 编排器的唯一耦合是 CLI 进程边界,不许 import 它(模块 docstring 与
# tests/logic/test_update_runner.py 的边界守卫都钉着这一条)。
#
# 重述必然有漂移风险 —— 这个仓库刚为「同一件事写两处」付过学费。所以测试
# 那边有一条**穷尽等价**守卫:导入真判据,逐日比对整整一年,任何一天不一致
# 就红。这里不许「改进」它们(比如加上节假日):UI 要预警的是日历闸**会不会**
# no-op,不是「今天是不是节假日」;判得比闸更宽,就是在预警一件不会发生的事。
def _is_non_trading_day(day: date) -> bool:
    return day.weekday() >= 5


def _live_bundle_present(provider_dir: Path) -> bool:
    return (
        (provider_dir / "calendars" / "day.txt").exists()
        and (provider_dir / "instruments" / "all.txt").exists()
        and (provider_dir / "features").exists()
    )


def calendar_gate_warning(
    provider_dir: Path, *, today: date, end_date: str = '',
) -> str | None:
    """点下去会不会**什么都不做**,或 None。

    交易日历闸的 no-op 是三个条件的**合取**:非交易日 且 没传结束日期 且
    存在可用的 live bundle。只复现头一个,就会在「没有 live bundle,闸会放行
    去 bootstrap」的情况下说错话。

    这是**预警**不是拦截:no-op 无害,操作人可能就是要它。
    """
    if end_date.strip():
        return None
    if not _is_non_trading_day(today):
        return None
    if not _live_bundle_present(provider_dir):
        # 闸此时**放行**,跑完整管线去 bootstrap 一个 bundle。
        return None
    weekday = "周六" if today.weekday() == 5 else "周日"
    return (
        f"{today.isoformat()} 是{weekday}。不填结束日期时,`daily_update` 的"
        "交易日历闸会直接 no-op 并 exit 0 —— 不抓取、不重建、不切换,而状态"
        "工件会记成一次**成功**。要在周末真正补跑,把结束日期填成你要抓到的"
        "那一天(这是日历闸自己文档化的旁路;结束日期缺省本就等于运行日,"
        "所以填今天 = 同样的抓取范围,只是不再 no-op)。"
    )


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
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[str]:
    """The argv the scheduler wrapper uses, as an argv list.

    List-form argv needs no shell quoting — paths with spaces arrive as
    single arguments by construction.

    不传范围时,产出的 argv 与调度器的**逐字相同** —— 「手动通道镜像调度器」
    那条不变式说的正是这件事,tests 那边一条 FULL-LIST 相等的守卫原样钉着它。
    偏离只能来自操作人的显式输入,而且页面显示的就是这里产出的 argv 本身
    (不是另抄一份措辞),所以偏离永远是看得见的,不是被夹带的。
    """
    argv = [
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
        start_date or START_DATE,
    ]
    if end_date:
        argv += ["--end-date", end_date]
    return argv


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
    start_date: str | None = None,
    end_date: str | None = None,
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
    # 范围校验在这里**再做一遍**:页面已经拦过一次,但这个模块才是被审计的
    # 那道边界(路径校验、token 预检都在这里),而它是可以被页面之外的调用方
    # 使用的。一个畸形日期换来的是两小时后在 tushare 那头炸。
    bad_range = range_problem(start_date or "", end_date or "")
    if bad_range is not None:
        return UpdateLaunch(
            kind="bad_range",
            error=f"抓取范围不可用:{bad_range}——拒绝把它交给一次约 2 小时的运行。",
        )
    child_env = utf8_child_env(env)
    if not child_env.get(TOKEN_ENV_VAR, "").strip():
        return UpdateLaunch(
            kind="no_token",
            error=(
                f"环境变量 {TOKEN_ENV_VAR} 缺失或为空——fetch 阶段会立刻"
                "失败。请在启动 UI 前设置(UI 启动器模板自带 HKCU 注册表"
                "回读;调度任务则靠以登录用户身份运行来继承它,"
                "见 docs/run-center-runbook.md)。"
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
        provider_dir, tushare_dir, registry_path, python=python,
        start_date=start_date, end_date=end_date,
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


#: 可能不是 UTF-8 的那些行所用的旧编码。**别把它当纯历史包袱**:本模块经
#: ``utf8_child_env`` 已钉死 UTF-8,但调度器 .bat 只有在它自己补过
#: ``PYTHONIOENCODING=utf-8`` 之后才钉住——按旧模板部署的计划任务**今晚仍会**
#: 写出 GBK 行。所以这条回退既服务历史行,也服务未打补丁部署的新行;在确认
#: 所有部署都打过补丁之前不得删除(codex #442 r6 自审扫描)。
_LEGACY_LOG_ENCODING = "gbk"


def _decode_log_line(raw: bytes) -> str:
    """一行日志。UTF-8 为准,**只在解码失败时**回退旧编码。

    规则刻意保守:UTF-8 解码成功就采信,不再猜「它看起来像不像乱码」。
    曾经试过按字符区段猜(出现西里尔/拉丁扩展就改判 GBK),但那会**损坏
    合法的新行**——provider 路径里一个 ``José`` 就被改写成 ``Jos茅``
    (codex #442 r5 实测)。源头钉死的地方,读侧再猜就是净损失。

    但源头**并非处处**钉死:本模块经 ``utf8_child_env`` 钉住了,调度器 .bat
    只有补过 ``PYTHONIOENCODING=utf-8`` 才钉住,按旧模板部署的计划任务今晚
    仍会写 GBK 行。所以这条回退不只服务历史行。

    代价说清楚:GBK 字节恰好也是合法 UTF-8 的那种行,例如
    ``'抓取'.encode('gbk')`` 解出 ``'ץȡ'``——无法靠读侧还原;页面对此明示,
    并指示先核对调度器 .bat 里那一行。解码失败的行(占绝大多数)仍能救回。
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return raw.decode(_LEGACY_LOG_ENCODING)
        except UnicodeDecodeError:
            return raw.decode("utf-8", errors="replace")


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
