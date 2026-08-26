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
import os
import re
import signal
import subprocess
import sys
import time
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
# `[0-9]` 而不是 `\d`：Python 的 `\d` 收 Unicode 数字，`int()` 也收，于是粘进来
# 的全角 `２０２６０１０１` 能过形状检查、也能构造出 date；而随后的顺序比较是
# **按字典序**比原串，全角码位远在 ASCII 之上，一个数值上更早的结束日期会被
# 判成更晚，颠倒的区间就这样交给了子进程（codex P2）。
_DATE_SHAPE = re.compile(r"^[0-9]{8}$")


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


def range_problem(
    start_date: str, end_date: str, *, today: date | None = None,
) -> str | None:
    """两端一起看,而且比的是**生效值**,不是字面输入。

    留空不等于「没有这一端」:开始留空 = `START_DATE`(`build_update_argv`
    会替上),结束留空 = 运行日(编排器的 `end_date or run_date`)。只比字面值
    的话,这两种输入都会通过校验,然后子进程带着一个**颠倒的区间**跑起来——
    校验本来正是为了不让这种事变成一份失败的运行工件(codex P2 ×2):

      · 开始留空 + 结束 20170101 → 生效 20180101..20170101
      · 开始 20270101 + 结束留空 → 生效 20270101..(今天)
    """
    for value, label in ((start_date, "开始日期"), (end_date, "结束日期")):
        problem = date_input_problem(value, label=label)
        if problem is not None:
            return problem
    start = start_date.strip() or START_DATE
    end = end_date.strip() or (today or gate_today()).strftime("%Y%m%d")
    if start > end:
        # YYYYMMDD 定宽零填充,字典序就是时间序 —— 不需要解析成 date 再比。
        blank_start = "(留空,取缺省下限)" if not start_date.strip() else ""
        blank_end = "(留空,取运行日)" if not end_date.strip() else ""
        return (f"开始日期 {start}{blank_start} 晚于结束日期 {end}{blank_end}")
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


def gate_today() -> date:
    """日历闸眼里的「今天」。

    宿主本地日,**不是**东八区:编排器用的是 `date.today()`,而生产 CLI 没有
    `--now` 可以覆盖它。东八区是本仓库给**操作人可见时间戳**的约定,不是
    这个判定的时钟 —— 两者混用正是 #461 三条 P1 里的第一条。

    本机就在 +08:00,两个时钟数值恰好一致,带着这个 bug 一样全绿。所以守卫
    钉的是**取法**(页面必须调本函数),不是数值。
    """
    return date.today()


def _live_bundle_present(provider_dir: Path) -> bool:
    return (
        (provider_dir / "calendars" / "day.txt").exists()
        and (provider_dir / "instruments" / "all.txt").exists()
        and (provider_dir / "features").exists()
    )


def _effective_live_bundle_present(provider_dir: Path) -> bool:
    """**修复之后**是否会有可用的 live bundle。

    编排器在日历闸**之前**先跑 `check_and_repair`:live 目录不存在时,
    `.bak` + `.new` 会完成那次中断的切换(`.new` 变成 live),只有 `.bak` 时
    会从备份恢复 —— 两种情况修完都有 bundle,闸随后照样 no-op。只看修复
    **前**的状态就会在这个恢复序列上漏报,而那正是操作人最需要预警的时候
    (codex P2)。只有 `.new` 时它会被删掉(无法证明验证过),等于没有 bundle。

    `.new` / `.bak` 的命名同样是**重述**(本模块不许 import 编排器),连同
    修复语义一起由一条穷尽等价守卫钉住:它拿真的 `check_and_repair` 当
    oracle,在临时目录里把兄弟目录的在/不在组合全跑一遍。
    """
    if provider_dir.exists():
        return _live_bundle_present(provider_dir)
    backup = provider_dir.with_name(provider_dir.name + ".bak")
    staged = provider_dir.with_name(provider_dir.name + ".new")
    if not backup.exists():
        return False
    return _live_bundle_present(staged if staged.exists() else backup)


def calendar_gate_warning(
    provider_dir: Path, *, today: date, end_date: str = '',
) -> str | None:
    """点下去会不会**什么都不做**,或 None。

    交易日历闸的 no-op 是三个条件的**合取**:非交易日 且 没传结束日期 且
    存在可用的 live bundle。只复现头一个,就会在「没有 live bundle,闸会放行
    去 bootstrap」的情况下说错话。

    第三个条件看的是 `check_and_repair` **修复之后**的状态 —— 那一步在闸
    之前跑,能把 `.bak`/`.new` 变回一个 live bundle。

    这是**预警**不是拦截:no-op 无害,操作人可能就是要它。
    """
    if end_date.strip():
        return None
    if not _is_non_trading_day(today):
        return None
    if not _effective_live_bundle_present(provider_dir):
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
    #: 活句柄——受控取消的**唯一**通道（2026-08-26-manual-update-
    #: controlled-cancel）。取消绑定 Popen 对象而非 pid：pid 会被系统回收
    #: 复用，按 pid 杀可能命中无关进程；句柄只活在本 UI 会话的内存里，
    #: UI 重启后旧运行不可取消（如实降级，不是缺陷）。frozen dataclass
    #: 携带可变句柄仅作传递，不参与相等性比较。
    process: subprocess.Popen[bytes] | None = None


@dataclass(frozen=True)
class UpdateCancel:
    """Outcome of one controlled-cancel attempt. ``kind`` drives the page:

    * ``already_finished`` — the process had already exited before the
      cancel ran (误点后其实已跑完/已失败). Nothing was killed; the status
      artifact / ledger carry whatever the run itself wrote.
    * ``cancelled`` — the process is confirmed gone. ``graceful`` says
      whether it exited on the polite signal (POSIX SIGINT → the
      orchestrator's BaseException path writes a terminal record) or had
      to be terminated (Windows; the status artifact then stays
      ``running`` — see the honesty note below).
    * ``cancel_failed`` — the process survived every attempt within the
      grace window; ``error`` says what was tried.

    诚实说明（Windows）:硬杀不给子进程写终态的机会,状态工件停在
    ``running``——本函数**绝不**替编排器伪造终态(写者纪律:状态工件只有
    编排器写)。页面凭本会话的句柄证据(``poll()`` 已退出)如实呈现:
    在线 bundle 未受影响(只有校验通过的构建才会原子切换)、单飞锁已随
    进程消亡自动释放、遗留 ``.new`` 由下次运行的 Stage-0 启动修复清理。
    """

    kind: str
    graceful: bool = False
    returncode: int | None = None
    error: str = ""


#: 礼貌信号后的等待窗。POSIX 下 SIGINT → KeyboardInterrupt → 编排器的
#: BaseException 终录路径（记录后再死）——给它写两个 JSON 的时间。
_CANCEL_GRACE_SECONDS = 10.0


def _append_cancel_marker(log_path: Path | None, text: str) -> None:
    """取消动作的带日期日志标记——复用 launch 的标记惯例，失败不致命。"""
    if log_path is None:
        return
    line = (
        f"[run_center] {datetime.now(tz=_CN_TZ).isoformat(timespec='seconds')}"
        f" {text}\n"
    )
    with contextlib.suppress(OSError):
        with open(log_path, "ab") as fh:
            fh.write(line.encode("utf-8"))
            fh.flush()


def cancel_update(
    process: subprocess.Popen[bytes],
    log_path: Path | None,
    *,
    grace_seconds: float = _CANCEL_GRACE_SECONDS,
) -> UpdateCancel:
    """受控取消一次**本会话启动的**手动更新。

    只接受活的 ``Popen`` 句柄——绝不按 pid 杀（pid 会被系统回收复用，
    按数字杀可能命中无关进程）；调度器的自动运行不是本 UI 的子进程、
    没有句柄，天然不在射程内。

    数据安全前提（这是"取消"敢存在的原因）:管线是 build-then-atomic-swap
    ——六阶段全程写 ``<provider>.new`` 旁路，只有校验通过后的最后一步才
    原子切换上线。切换前任意时点杀进程,在线 bundle 一字节不动,仍是最后
    一次成功更新的数据（无论自动还是手动）。

    平台差异是**实测**出来的,不是猜的:

    * POSIX:子进程 ``start_new_session=True`` 是会话组长——``killpg``
      SIGINT → KeyboardInterrupt → 编排器 BaseException 终录路径（状态
      工件+台账落终态,然后再死）。宽限窗内未退再 SIGKILL。
    * Windows:``CREATE_NO_WINDOW`` 的子进程有自己的隐形控制台,
      ``CTRL_BREAK_EVENT`` 从 UI 进程**送达不了**（GenerateConsoleCtrl-
      Event 要求同控制台;本仓实测:信号发送成功返回、子进程纹丝不动）;
      即使送达,Python 把 CTRL_BREAK 映射为 SIGBREAK 直接终止、不产生
      KeyboardInterrupt。所以 Windows 路径**如实是硬杀**（七个阶段全部
      in-process 跑在编排器单进程里——`_default_runners` 逐个
      `_load_script_main`——杀单进程即完备,无孤儿阶段）。
    """
    if process.poll() is not None:
        return UpdateCancel(
            kind="already_finished", returncode=process.returncode)
    _append_cancel_marker(log_path, "cancel requested: manual daily_update")
    graceful = False
    if sys.platform != "win32":
        with contextlib.suppress(OSError):
            os.killpg(process.pid, signal.SIGINT)
        deadline = time.monotonic() + grace_seconds
        while time.monotonic() < deadline:
            if process.poll() is not None:
                graceful = True
                break
            time.sleep(0.2)
        if not graceful:
            with contextlib.suppress(OSError):
                os.killpg(process.pid, signal.SIGKILL)
    if process.poll() is None:
        try:
            process.kill()
        except OSError as exc:
            _append_cancel_marker(
                log_path, f"cancel FAILED: kill raised {exc!r}")
            return UpdateCancel(
                kind="cancel_failed", error=f"终止进程失败:{exc}")
        try:
            process.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            _append_cancel_marker(
                log_path,
                "cancel FAILED: process survived kill within grace window")
            return UpdateCancel(
                kind="cancel_failed",
                error="进程在宽限窗内未退出;请用任务管理器核查后重试")
    _append_cancel_marker(
        log_path,
        "cancel outcome: process exited "
        f"(returncode={process.returncode}, graceful={graceful})")
    return UpdateCancel(
        kind="cancelled", graceful=graceful, returncode=process.returncode)


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
    # 两端先归一再用:`range_problem` 把纯空白视为「未指定」(合法),若这里不
    # 归一,`"  " or START_DATE` 会取到那两个空格(非空即真),argv 里就出现
    # `--start-date "  "` —— 一路流到 01 才炸。页面恰好先 strip 过,但启动器
    # 才是被审计的边界,它必须自己站得住(与范围校验在这里再做一遍同理)。
    start = (start_date or "").strip()
    end = (end_date or "").strip()
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
        start or START_DATE,
    ]
    if end:
        argv += ["--end-date", end]
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
    return UpdateLaunch(kind="launched", pid=proc.pid, log_path=log_path,
                        process=proc)


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


def log_window(
    log_path: Path, *, chars: int = _LOG_TAIL_CHARS,
) -> tuple[str, bool]:
    """Last ``chars`` characters of the log, plus whether that IS the whole log.

    第二个返回值是**归属判断的前提**：窗口没盖住整份日志时，「窗口里看不到
    别人的边界」证明不了别人不存在——更早起跑、仍在写的兄弟 provider 的边界
    可能正好在窗口之外（codex #465 P1）。读侧必须把「我看到的是不是全部」
    这一事实交出去，而不是让下游把截断当成完整。

    Missing log = ``("", True)``——空日志是完整地读完了的。
    """
    try:
        with open(log_path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            start = max(0, size - chars * 4)  # UTF-8 worst case
            fh.seek(start)
            data = fh.read()
    except FileNotFoundError:
        return "", True
    except OSError as exc:
        return f"(日志不可读:{exc})", False
    lines = data.split(b"\n")
    if start > 0 and len(lines) > 1:
        # 从中间起读,首行多半被切成半个字符序列——丢掉它,别拿半行去猜编码。
        lines = lines[1:]
    text = "\n".join(_decode_log_line(line) for line in lines)
    return text[-chars:], start == 0 and len(text) <= chars


def log_tail(log_path: Path, *, chars: int = _LOG_TAIL_CHARS) -> str:
    """Last ``chars`` characters of the log, decoded leniently.

    Missing log = empty string (a fresh machine has no log yet; that is
    a state to render, not an error).
    """
    return log_window(log_path, chars=chars)[0]
