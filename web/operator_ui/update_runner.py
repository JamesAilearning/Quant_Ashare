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
from uuid import uuid4

from scripts.child_env import utf8_child_env
from web.operator_ui.update_status import (
    RUNNING_FRESH,
    UpdateRunStatus,
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

#: 每次 launch 的一次性身份经它传给子进程,编排器写进自己的每条状态记录
#: （#470 第二十四轮:随记录落盘的身份没有观察窗——观察式候选只能覆盖到
#: 最后一次观察,「最后观察→死亡」尾窗数学上封不住;nonce 覆盖生存期内
#: 任意时刻写出的记录,且天然免疫 pid 复用）。变量名与
#: src/data_pipeline/daily_update.py 镜像（两模块互不 import,logic 测试
#: 钉两侧一致）。
LAUNCH_NONCE_ENV = "QUANT_DAILY_UPDATE_LAUNCH_NONCE"

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
    #: 本次 launch 的一次性身份（uuid4().hex）——已注入子进程环境,编排
    #: 器会把它写进每条状态记录;收养侧凭 nonce 相等认领记录,无观察窗
    #: （#470 第二十四轮）。仅 launched 时非 None。
    launch_nonce: str | None = None


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
    #: 取消恰好落在两段 rename 的切换窗内（canonical 目录暂缺,由下次更新
    #: 的启动修复复原——swap 的基线契约本就只承诺 crash-atomicity + 事后
    #: 修复）。True 时页面必须**响亮**指引立即重跑更新,不许再说「在线数据
    #: 不受影响」（codex #470 P1）。
    swap_interrupted: bool = False
    #: 取消的日志标记是否全部落盘。规格要求每次对活进程的取消都留下
    #: 请求+结局双标记——日志在启动后变得不可写（权限/磁盘满）时,静默
    #: 吞掉会让一次操作人动作不可审计（codex 第四轮 P2）:终止照常执行
    #: （主结局优先）,但缺失必须透出让页面响亮说。
    markers_written: bool = True
    #: 切换窗状态**核实不了**（检查自身抛 OSError:卷不可用/权限/IO 错）。
    #: 静默当健康会让 graceful 文案在未核实时声称「在线数据未受影响」
    #: （codex 第五轮 P2）——unknown 是第三态,不是 False。
    swap_state_unknown: bool = False
    #: 进程**确认死亡的当刻**（ISO,+08:00）。证据时间绑定的上界必须取在
    #: 这里而不是 cancel_update 返回之后——确认死亡后本函数还要写标记、
    #: 查文件系统,调度器可在那段里拿到已释放的锁并写下接替 running,晚
    #: 采的上界会把它也框进窗内（codex 第六轮 P1）。
    exited_at: str | None = None
    #: 终止信号是否**实际发出**。证据分级（第十七/二十轮）:killpg 成功
    #: 返回=内核受理投递,即为证明;Popen.kill() 正常返回**还不够**——它
    #: 内部先 poll,进程已终结时什么都不发静默返回,须复检仍活才算真送
    #: 达（复检已死走终态 oracle 裁决）。cancel_failed 的两种失败对
    #: 迟到死亡的语义相反（codex 第十轮 P2）:已发但宽限窗内没等到 → 之
    #: 后的死亡是取消导致的,可补结算;**所有**信号调用都抛了 → 进程没被
    #: 碰过,之后自然跑完就是自然完成,补结算成「已强制取消」是撒谎。
    #: 后备 kill() 恰与已发 SIGKILL 生效的退出竞态抛 OSError 时,组信号
    #: 的成功返回仍算已发——不许把那次死亡当自然完成。
    kill_issued: bool = False
    #: graceful 退出后,状态工件里**核实到**本次运行的终态记录（finished
    #: 且写者 pid == 被杀句柄 pid）。SIGINT 可以落在编排器还在 import/解析
    #: 配置/拿锁的阶段——终录路径尚未就位,及时退出证明不了终态写了
    #: （codex 第十轮 P2）;页面只有在这里为 True 时才许声称「编排器自己
    #: 写下了终态记录」。硬杀恒 False。
    terminal_recorded: bool = False
    #: 「终录先在 + kill 静默返回 + 死亡」格的**送达不可判定**标记（第三
    #: 十六轮 P2）:杀前快照只证明记录时序,不证明送达——TerminateProcess
    #: 真杀了活进程,与它在快照后、TerminateProcess 前自然退出（CPython
    #: 吞 access-denied 记 exit code 正常返回）,经 Popen API 无法区分。
    #: 二者对数据结果**等价**（终态已记录;台账追加不保证）,页面对此格
    #: 用专属措辞如实说「无法确定是否实际送达」,不在「取消未执行」与
    #: 「强制终止已执行」两个都可能撒谎的标签里二选一。
    terminal_race: bool = False
    #: cancel_failed 时,在**边界内、进程确证还活着**的时刻观察到的它自己
    #: 的 running 记录戳（codex 第二十三轮 P2:子进程可在页面取消前观察
    #: 之后才写出记录、又在本函数返回与页面事后观察之间死掉——两端观察
    #: 双双落空,真孤儿无候选被拒收养。宽限窗超时/复检仍活的失败路径是
    #: 最后一个可证生存期窗口,在这里补一次观察）。None=没观察到。
    own_running_stamp: str | None = None


#: 礼貌信号后的等待窗。POSIX 下 SIGINT → KeyboardInterrupt → 编排器的
#: BaseException 终录路径（记录后再死）——给它写两个 JSON 的时间。
_CANCEL_GRACE_SECONDS = 10.0


def _append_cancel_marker(log_path: Path | None, text: str) -> bool:
    """取消动作的带日期日志标记——复用 launch 的标记惯例。

    失败不打断取消本身（终止进程是主结局），但**必须回报**：静默吞掉会让
    一次操作人动作不可审计（codex 第四轮 P2）。返回是否落盘成功；
    ``log_path is None`` 视为「无处可写」= False，调用方自行决定是否在意。
    """
    if log_path is None:
        return False
    line = (
        f"[run_center] {datetime.now(tz=_CN_TZ).isoformat(timespec='seconds')}"
        f" {text}\n"
    )
    try:
        with open(log_path, "ab") as fh:
            fh.write(line.encode("utf-8"))
            fh.flush()
    except OSError:
        return False
    return True


def evidence_binds_to_killed_run(
    record_started_at: str | None,
    launched_at: str | None,
    killed_at: str | None,
    *,
    record_pid: int | None,
    killed_pid: int | None,
) -> bool:
    """终止后重读到的 running 记录是否**属于被杀的那次**运行。

    单飞锁随进程消亡即释放——调度器的自动运行可以在「杀死之后、重读之前」
    起跑并写下自己的 running 记录:按 provider+kind 收养它,会把**活着的**
    调度器运行标成已取消并解锁两个启动闸（codex 第五轮 P1）。

    判据是**进程身份 + 时间窗**的合取（codex 第九轮:光有时间窗不够——
    调度器可以在 launch 之后、UI 子进程拿锁之前起跑并夺锁,它的
    ``started_at`` 恰好落在窗内,被杀的 UI 子进程只是 exit-17 的输家;
    按时间收养会把**活着的**调度器运行标成已取消）:

    * 身份:记录里的写者 ``pid`` == 被杀句柄的 pid——产出器把
      ``os.getpid()`` 落进每条记录,而本启动器直接 spawn 编排器
      （无 shell 壳）,``Popen.pid`` 就是写者本身。旧记录没有 pid → None
      → 不绑定（fail-closed:证不出身份就不声称）。
    * 时间窗:``launch 时刻 ≤ started_at ≤ 杀死完成时刻``——身份之外
      仍保留,防 pid 复用（被杀 pid 被系统回收给新进程再写记录;窗内
      该 pid 一直被本会话子进程持有,复用者必然越界）。

    三个戳同主机同钟（UI 与编排器都取本机 now）,严格比较即可;任一解析
    不动=不绑定,绝不靠猜收养。pid 比较排除 bool（``True == 1``）。
    """
    if not (
        isinstance(record_pid, int) and not isinstance(record_pid, bool)
        and isinstance(killed_pid, int) and not isinstance(killed_pid, bool)
        and record_pid == killed_pid
    ):
        return False
    if not (record_started_at and launched_at and killed_at):
        return False
    try:
        record = datetime.fromisoformat(record_started_at)
        launched = datetime.fromisoformat(launched_at)
        killed = datetime.fromisoformat(killed_at)
    except ValueError:
        return False
    if record.tzinfo is None or launched.tzinfo is None or killed.tzinfo is None:
        return False
    return launched <= record <= killed


def terminal_record_confirms_the_run(
    provider_dir: Path | None,
    pid: int,
    *,
    launched_at: str | None,
    exited_at: str | None,
    launch_nonce: str | None = None,
) -> bool:
    """状态工件里是否有**本次运行自己写下的**终态记录。

    graceful 的「编排器自己写下了终态记录」不许从**及时退出**推断
    （codex 第十轮 P2）:SIGINT 可以落在编排器还在 import/解析配置/拿单飞
    锁的阶段——终录路径尚未就位,进程照样在宽限窗内体面退出,而工件此刻
    要么缺失、要么还是**上一次**运行的记录。

    身份在场一票裁决（codex 第二十七轮 P2,与覆盖谓词同语义）:本会话有
    launch nonce 时,子进程写下的终录必然带同一 nonce——核实=finished
    且 nonce 相等且 pid 相等,**不再看时间窗**（pid 复用 + 冻结/粗粒度时
    钟可让陈年工件同时满足 pid 与窗;nonce 唯一,陈年/接替工件拿不到）。
    记录带 nonce 而会话没有（或互异）同样拒。

    legacy 合取（双方都无 nonce,旧产出器在飞会话,第十一轮原判据）:

    * 身份:finished 且写者 pid == 被杀句柄 pid（第九轮判据）。
    * 时间窗:记录的 started_at/finished_at 都落在本次
      ``launch ≤ started ≤ finished ≤ exit`` 内——旧工件的 started_at
      必早于本次 launch,越界即拒。

    旧记录无 pid / 还是 running / 工件缺失损坏 / 任一戳缺失或解析不动或
    无时区,一律 False——证不出来就不声称。
    """
    if provider_dir is None or not (launched_at and exited_at):
        return False
    try:
        status = read_update_status(status_path_for_provider(provider_dir))
    except ValueError:
        # 文件系统根这类推导不出状态路径的 provider——证不出来。
        return False
    return terminal_status_confirms_the_run(
        status, pid, launched_at=launched_at, exited_at=exited_at,
        launch_nonce=launch_nonce)


def terminal_status_confirms_the_run(
    status: UpdateRunStatus,
    pid: int,
    *,
    launched_at: str | None,
    exited_at: str | None,
    launch_nonce: str | None = None,
) -> bool:
    """快照版终态核实——对**已捕获的**状态快照判定,绝不二次读取。

    页面收养/补结算已各自持有本帧读到的快照（codex 第三十一轮 P2:核实
    若自行重读,快照与重读之间调度器接替可改写共享工件——第一读见到本
    次终录、第二读见到接替,检出翻假,帧自相矛盾。同一帧的判定必须作用
    在同一次读取上）。判据与 ``terminal_record_confirms_the_run`` 完全
    一致,后者=读一次+委托本函数（供 cancel_update 内部这类没有现成
    快照的调用方用）。
    """
    if not (launched_at and exited_at):
        return False
    if status.kind != "finished" or status.pid != pid:
        return False
    if launch_nonce or status.launch_nonce:
        return record_bears_launch_nonce(status.launch_nonce, launch_nonce)
    try:
        started = datetime.fromisoformat(status.started_at)
        finished = datetime.fromisoformat(status.finished_at)
        launched = datetime.fromisoformat(launched_at)
        exited = datetime.fromisoformat(exited_at)
    except ValueError:
        return False
    if any(t.tzinfo is None for t in (started, finished, launched, exited)):
        return False
    return launched <= started <= finished <= exited


def observe_own_running_record(
    process: subprocess.Popen[bytes], provider_dir: Path,
) -> str | None:
    """在进程**可证活着**的时刻,观察它自己写下的 running 记录戳。

    迟到收养需要一个**不可能越过被杀进程真实生存期**的身份（codex 第十
    二轮 P2:观测时刻上界留了「真实死亡→观测」最长一个轮询周期的空窗,
    OS 可在其中把 pid 回收给接替的调度器运行——戳和 pid 双双落窗,活运
    行被标成已取消）。取法=活→读→活 三步:两次 ``poll()`` 都在世,期间
    这个 pid 被该子进程**持续持有**,不可能被回收——读到的「pid==句柄」
    记录必然是它自己写的。返回该记录的 ``started_at`` 作精确身份候选;
    观察不到（进程已死/记录不是它的/属别的 provider/推导不出状态路径）
    返回 None——收养侧对 None fail-closed:宁可孤儿等六小时陈旧线,不
    收养证不出身份的记录。
    """
    if process.poll() is not None:
        return None
    try:
        status = read_update_status(status_path_for_provider(provider_dir))
    except ValueError:
        return None
    if (status.kind == "running"
            and record_matches_provider(status, provider_dir)
            and status.pid == process.pid
            and process.poll() is None):
        return status.started_at or None
    return None


def record_bears_launch_nonce(
    record_nonce: str | None, launch_nonce: str | None,
) -> bool:
    """记录是否带着**本次 launch 的专属 nonce**。

    身份由子进程自己写进它的每条状态记录（launcher 生成、经环境变量传
    入）——覆盖生存期内**任意时刻**写出的记录（codex 第二十四轮 P2:
    观察式候选只能覆盖到最后一次观察,「最后观察→死亡」的尾窗在数学上
    无法用观察封闭;随记录本体落盘的身份没有观察窗）,且天然免疫 pid
    复用（nonce 每次 launch 唯一,回收 pid 的接替进程拿不到它）。双方都
    在场且相等才认;任一缺失=不认（fail-closed:调度器运行/旧产出器的
    记录没有 nonce,legacy 记录走既有的 pid+候选链）。
    """
    return bool(record_nonce) and record_nonce == launch_nonce


def evidence_covers_record(
    record_started_at: str | None,
    record_launch_nonce: str | None,
    evidence_started_at: str | None,
    evidence_launch_nonce: str | None,
) -> bool:
    """取消证据是否覆盖这条 ``running`` 记录——nonce 身份在场时一票裁决。

    任一侧带 nonce 时,覆盖成立的唯一方式是**双方带同一个** nonce:戳相等
    不再够格——粗粒度/冻结的系统时钟可以让接替记录与被取消记录同戳,而
    nonce 不同（或没有,如调度器运行）证明它是别人;反向同理,带 nonce 的
    记录必然出自新产出器,legacy（无 nonce）证据也认不了它（codex 第二十
    六轮 P2:``or`` 语义会把活着的同戳接替标成已取消并解锁双闸）。只有
    **双方都没有** nonce（旧产出器在飞会话的 legacy 对）才回退到戳的精确
    相等。三处消费（页首覆盖判定/证据退役/启动闸放行）共用本谓词,不许
    各抄一份会分叉的。
    """
    if evidence_launch_nonce or record_launch_nonce:
        return record_bears_launch_nonce(
            record_launch_nonce, evidence_launch_nonce)
    return cancelled_run_matches(record_started_at, evidence_started_at)


def evidence_retires(
    status_kind: str,
    status_started_at: str | None,
    status_launch_nonce: str | None,
    evidence_started_at: str | None,
    evidence_launch_nonce: str | None,
) -> bool:
    """取消证据是否应当退役——只认**确凿**接替（codex 第二十三轮 P2）。

    确凿接替只有两种:一条**两种身份都对不上**（戳不同且 nonce 不同）的
    合法 running 记录（新运行顶替了孤儿）,或一条 finished 终态记录（孤儿
    被终态改写）。missing/corrupt 是**读取失败**,不是接替证明——卷/权限
    瞬时失效时借它把证据永久清掉,访问恢复后同一条孤儿 running 复现,会
    被当活运行锁启动到六小时陈旧线。读不出来就保留证据:它只在匹配的
    running 记录出现时才生效,留着无害。

    nonce 覆盖优先于戳（codex 第二十五轮 P2）:补结算的死后读取不确凿时
    证据只带 nonce 不带戳——带着**本次 launch nonce** 的 running 记录是
    被杀孤儿本人,绝不因戳对不上被误判成接替者而退役。覆盖判定走
    ``evidence_covers_record``（第二十六轮:身份在场时一票裁决,同戳但
    nonce 不同/缺失的接替是**确凿**接替,必须退役）。
    """
    if status_kind == "finished":
        return True
    if status_kind == "running":
        return not evidence_covers_record(
            status_started_at, status_launch_nonce,
            evidence_started_at, evidence_launch_nonce)
    return False


def cancelled_run_matches(
    status_started_at: str | None, evidence_started_at: str | None,
) -> bool:
    """本会话的取消证据是否覆盖当前 ``running`` 状态记录。

    钉**精确相等**的状态戳身份（与边界归属同款纪律）:戳一致 = 就是被取消
    的那一次;任何新运行会写新的 ``started_at``,证据即刻失效——绝不覆盖
    别人的运行。任一侧为空都不覆盖:无法证明同一性就不声称（codex #470
    P1:硬杀留下的 running 记录若只在取消后首个渲染被更正,一次 rerun 就
    会退回「正在更新」并锁住启动按钮直到六小时陈旧线）。
    """
    return bool(status_started_at) and status_started_at == evidence_started_at


def cancel_update(
    process: subprocess.Popen[bytes],
    log_path: Path | None,
    *,
    provider_dir: Path | None = None,
    grace_seconds: float = _CANCEL_GRACE_SECONDS,
    launched_at: str | None = None,
    prior_markers_written: bool | None = None,
    launch_nonce: str | None = None,
) -> UpdateCancel:
    """受控取消一次**本会话启动的**手动更新。

    ``prior_markers_written``:**上一次**失败尝试的标记落盘结果（未决上
    下文携带;None=没有上一次）。跨重试聚合（codex 第十五轮 P2）:首次
    kill 超时且标记写失败,日志随后恢复可写、重试成功终止——只报本次的
    True 会把先前那次活取消的审计缺口静默洗掉;同理再次超时也不得用本次
    True 覆盖存量 False。聚合在本边界单点做,所有返回路径自动携带。

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
    markers_written = _append_cancel_marker(
        log_path, "cancel requested: manual daily_update"
    ) and (prior_markers_written is not False)
    graceful = False
    # POSIX 组信号的**成功返回**就是「已发出」的证明——内核已受理投递
    # （codex 第十七轮 P2:此前 suppress 后一律当证不出,后备 process.kill()
    # 恰与退出竞态抛 OSError 时硬编码 kill_issued=False——先前 SIGKILL 导
    # 致的迟到死亡被当自然完成,整套迟到收尾被跳过）。只有**所有**信号调
    # 用都抛了,才算没发出去。
    signal_issued = False
    if sys.platform != "win32":
        try:
            os.killpg(process.pid, signal.SIGINT)
        except OSError:
            pass
        else:
            signal_issued = True
        deadline = time.monotonic() + grace_seconds
        while time.monotonic() < deadline:
            if process.poll() is not None:
                graceful = True
                break
            time.sleep(0.2)
        if not graceful:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
            else:
                signal_issued = True
    if process.poll() is None:
        # 杀前终态快照（codex 第三十五轮 P2）：此刻进程还活着——若它的
        # 终态记录**已经**在盘上（终态已写、正在追加台账的收尾窗口）,
        # 随后 kill() 的静默返回就不再是「无事发生」的证明:预检活着 +
        # 杀后死亡 + 终录**早已在** = 杀点落在终录之后的收尾上（Windows
        # TerminateProcess 对活进程即执行）,按已发信号的确认死亡归类,
        # 页面 terminal_after_kill 链如实呈报「终录已写、台账可能被打
        # 断」;终录仅在杀后才出现,才是真自然完成（kill 内部 no-op）。
        pre_kill_terminal = (
            provider_dir is not None
            and terminal_record_confirms_the_run(
                provider_dir, process.pid,
                launched_at=launched_at,
                exited_at=datetime.now(tz=_CN_TZ).isoformat(),
                launch_nonce=launch_nonce))
        try:
            process.kill()
        except OSError as exc:
            # kill() 抛错的两种真相要**复检**分开（codex 第十九轮 P2）：
            # 进程恰在上面那次 poll 与 kill() 之间退出时,抛错只是句柄已
            # 终结——死亡已确认,立即返回 cancel_failed 会留下死句柄、把
            # 自然完成报成取消失败,还绕过下面的分类闸。复检仍活才是真
            # 失败;已死则落到分类闸（无信号=自然完成,有信号=取消导致
            # 的确认死亡）。
            if process.poll() is None:
                # 失败结局同样要审计——标记结果照聚合（codex 第五轮 P2:
                # 两个 cancel_failed 返回此前落在聚合之外,不可写日志让
                # 失败的取消静默无审计）。
                markers_written = _append_cancel_marker(
                    log_path, f"cancel FAILED: kill raised {exc!r}"
                ) and markers_written
                # kill() 自身抛了——但 POSIX 组信号若已成功发出,死亡仍
                # 可能是取消导致的（kill() 恰与 SIGKILL 生效的退出竞态
                # 抛错正是常见形态）;Windows 无前置信号,此处即「没碰过
                # 进程」,之后自然跑完就是自然完成,不许补结算成已强制
                # 取消（第十/十七轮）。
                return UpdateCancel(
                    kind="cancel_failed", error=f"终止进程失败:{exc}",
                    markers_written=markers_written,
                    kill_issued=signal_issued,
                    # 进程确证还活着——趁生存期在边界内补一次候选观察
                    # （第二十三轮 P2）。
                    own_running_stamp=(
                        observe_own_running_record(process, provider_dir)
                        if provider_dir is not None else None))
        else:
            # kill() **正常返回不是送达证据**（codex 第二十轮 P2）：
            # CPython 的 Popen.kill()→send_signal() 内部先 poll,进程恰在
            # 外层预检与它之间自然终结时,它什么都不发、静默返回（Windows
            # 的 terminate() 对已退出进程同样吞 PermissionError 返回）。
            # 复检裁决——kill() 返回后仍活=信号真发出去了（内部 poll 见
            # 到的是活进程,os.kill 已执行）;已死=歧义微秒窗,用状态工件
            # 当 oracle（复用第九/十一轮的 pid+时间窗判据）:本次运行自己
            # 的终态记录在=自然完成;不在（且此前无组信号）则按已发信号
            # 的确认死亡走——即便真相是无终录的自然猝死,孤儿收养的收尾
            # 对它同样是正确处置,且那要求崩溃恰落在本微秒窗内。至此
            # kill 调用的观察空间四分完备:{抛错,返回}×{仍活,已死} 各有
            # 显式归类,不再有靠假设的格子。
            if process.poll() is None:
                signal_issued = True
                try:
                    process.wait(timeout=grace_seconds)
                except subprocess.TimeoutExpired:
                    markers_written = _append_cancel_marker(
                        log_path,
                        "cancel FAILED: process survived kill within "
                        "grace window"
                    ) and markers_written
                    # kill 已发出（返回后复检仍活=真送达）,只是宽限窗内
                    # 没等到死亡——之后的死亡是取消导致的,迟到补结算
                    # 成立。TimeoutExpired 本身证明进程此刻还活着——这
                    # 是补结算候选的最后一个可证生存期窗口,在边界内观察
                    # 一次（第二十三轮 P2:记录可写在页面取消前观察之后,
                    # 而进程又死在本函数返回与页面事后观察之间——两端
                    # 双双落空时真孤儿无候选被拒收养）。
                    return UpdateCancel(
                        kind="cancel_failed",
                        error="进程在宽限窗内未退出;请用任务管理器核查后"
                              "重试",
                        markers_written=markers_written, kill_issued=True,
                        own_running_stamp=(
                            observe_own_running_record(process, provider_dir)
                            if provider_dir is not None else None))
            elif not signal_issued and pre_kill_terminal:
                # 「终录先在 + kill 静默返回 + 死亡」——**送达不可判定**
                # （第三十五/三十六轮）:杀前快照只证明记录时序,不证明送
                # 达。TerminateProcess 真杀了收尾中的活进程,与进程在快照
                # 后、TerminateProcess 前自然退出（CPython 吞 access-
                # denied 正常返回）,经 Popen API 无法区分;报「取消未执
                # 行」或「强制终止已执行」都可能撒谎。二者对数据结果等
                # 价:终态已记录,台账追加不保证——按 already_finished +
                # terminal_race 返回,页面用专属措辞如实说不可判定。
                markers_written = _append_cancel_marker(
                    log_path,
                    "cancel outcome: terminal record predates the kill; "
                    "terminate delivery is undecidable (natural-exit race) "
                    "— data outcome identical either way, ledger append "
                    "not guaranteed"
                ) and markers_written
                return UpdateCancel(
                    kind="already_finished", returncode=process.returncode,
                    markers_written=markers_written, kill_issued=False,
                    terminal_race=True,
                    # 死亡观测时刻——页面渲染帧复验该格身份时的时间界
                    # （nonce 会话不看窗;legacy 对观测时刻 ≥ 真实死亡
                    # 成立,第三十七轮）。
                    exited_at=datetime.now(tz=_CN_TZ).isoformat())
            elif (not signal_issued
                    and terminal_record_confirms_the_run(
                        provider_dir, process.pid,
                        launched_at=launched_at,
                        exited_at=datetime.now(tz=_CN_TZ).isoformat(),
                        # 第二十八轮 P2:UI 子进程的终录带 nonce,第二十七
                        # 轮起 nonce 一票裁决——不转发会把合法终录按
                        # 「nonce vs None」拒掉,自然完成反被报成强制取消。
                        launch_nonce=launch_nonce)):
                # 无任何信号在先 + 终录**仅在杀后**才出现——它在微秒窗
                # 内自然跑完了,kill() 什么都没发:真自然完成。
                markers_written = _append_cancel_marker(
                    log_path,
                    "cancel outcome: process had already finished with its "
                    "own terminal record before any signal was issued"
                ) and markers_written
                return UpdateCancel(
                    kind="already_finished", returncode=process.returncode,
                    markers_written=markers_written, kill_issued=False)
            else:
                # 已死 + （组信号在先,或无终录）——按已发信号的确认死亡
                # 归类,走取消收尾（孤儿收养/如实无孤儿两条呈报都由状态
                # 工件裁决,见页面收养链）。
                signal_issued = True
    if not signal_issued:
        # 分类闸（codex 第十八轮 P2）：初检时还活着,但**没有任何**信号
        # 成功送达它就死了——POSIX 是「初检后自然完成 + SIGINT 抛错」的
        # 竞态（此时宽限窗里见到的死亡会被误判 graceful）,Windows 是
        # 「初检到 kill() 之间死亡」的毫秒窗。这不是取消,是自然完成:
        # 报成 cancelled 会给它套上取消专属的 swap/审计收尾、graceful
        # 文案,而它的终态该由运行自己的状态工件与台账自述。请求标记已
        # 落,补一条结局标记把这次尝试如实收口。
        markers_written = _append_cancel_marker(
            log_path,
            "cancel outcome: process exited before any signal was issued "
            "(natural completion; nothing was cancelled)"
        ) and markers_written
        return UpdateCancel(
            kind="already_finished", returncode=process.returncode,
            markers_written=markers_written, kill_issued=False)
    return _confirmed_death_outcome(
        process, log_path, provider_dir,
        graceful=graceful, markers_written=markers_written, late=False,
        launched_at=launched_at, launch_nonce=launch_nonce)


def settle_late_cancel(
    process: subprocess.Popen[bytes],
    log_path: Path | None,
    *,
    provider_dir: Path | None = None,
    markers_written: bool = True,
    launched_at: str | None = None,
) -> UpdateCancel:
    """``cancel_failed`` 之后进程**迟到死亡**的补结算边界。

    kill 已发、宽限窗内没等到,进程在 ``cancel_update`` 返回之后才真死——
    这仍是取消导致的死亡,收尾义务与确认死亡路径**完全同款**（codex 第
    九轮 P2:此前补结算只重读状态、存证据,不做切换窗检查——迟到死亡同样
    可能恰好落在两段 rename 之间,不查就把「canonical 缺位需立即修复」
    静默标成一次干净的取消）。共享同一个收尾实现,不重抄一份会分叉的。

    只接受**已死**的进程:死亡是单调的（``poll()`` 非 None 后不会翻回）,
    活进程到这儿是调用方编程错误,fail-loud 而非静默装作结算过。

    ``markers_written``:**原失败尝试**的标记落盘结果（codex 第十轮 P2）。
    请求/失败标记当时没写进去,日志之后恢复可写、迟到结局标记写成了——
    审计链**仍然**缺了头两条,从乐观缺省重来会把这次结算谎报成审计完整。
    调用方从未决上下文里把它带回来,这里聚合而非重置。
    """
    if process.poll() is None:
        raise ValueError(
            "settle_late_cancel 只对已退出的进程补结算——活进程请走 "
            "cancel_update")
    return _confirmed_death_outcome(
        process, log_path, provider_dir,
        graceful=False, markers_written=markers_written, late=True,
        launched_at=launched_at, launch_nonce=None)


def _confirmed_death_outcome(
    process: subprocess.Popen[bytes],
    log_path: Path | None,
    provider_dir: Path | None,
    *,
    graceful: bool,
    markers_written: bool,
    late: bool,
    launched_at: str | None,
    launch_nonce: str | None,
) -> UpdateCancel:
    """确认死亡后的共同收尾:结局标记 + 切换窗检查 + 结果组装。

    ``cancel_update``（当场确认）与 ``settle_late_cancel``（迟到死亡）
    共用——两条路对「进程死了之后还欠什么」的义务一字不差,分抄两份
    只会分叉（codex 第九轮 P2 正是补结算那份漏了切换窗检查）。

    ``exited_at``:当场路径 = 确认死亡的当刻（codex 第六轮 P1:晚采会把
    接替者框进窗）,是当场收养的时间上界;迟到路径 = 补结算入口的观测
    时刻,**仅作呈报**,不当收养界——观测可晚于真实死亡最长一个轮询周
    期,该空窗内 pid 可被回收给接替运行（codex 第十二轮 P2）。迟到收养
    的身份是 ``observe_own_running_record`` 在进程可证活着时观察到的
    精确戳候选（第八轮的请求时刻上界、第十一轮的观测时刻上界先后被证
    伪:前者拒真孤儿,后者收回收 pid 的接替者——生存期内观察是唯一
    两头都站得住的取法）。

    ``launched_at``:本会话 spawn 前采样的下界,供 graceful 终态核实的
    时间窗用;调用方没有它（None）时终态核实 fail-closed。
    """
    exited_at = datetime.now(tz=_CN_TZ).isoformat()
    markers_written = _append_cancel_marker(
        log_path,
        "cancel outcome: process exited "
        + ("late (after the grace window) " if late else "")
        + f"(returncode={process.returncode}, graceful={graceful})"
    ) and markers_written
    # 切换窗检测（codex #470 P1）:swap 是两段 rename——若取消恰好落在
    # 「live→.bak 之后、.new→live 之前」,canonical 目录此刻**不存在**,
    # 要到下次更新的启动修复才复原（swap 基线契约:crash-atomicity + 事后
    # 修复）。判据是 crash 态**签名**而非裸存在性:canonical 缺 **且**
    # `.bak` 在——首次 bootstrap 这类「本来就没有 live bundle」的运行,
    # canonical 在取消后同样不存在,但没有 .bak,不许被误诊成切换窗命中
    # （codex 第二轮 P2）。命名镜像 bundle_swap 的 `<provider>.bak`
    # （web/ 不 import 管线层;logic 测试钉两侧一致）。
    swap_interrupted = False
    swap_state_unknown = False
    if provider_dir is not None:
        def _present(path: Path) -> bool:
            # 严格 stat 探测（codex 第七轮 P2）：Path.exists() 把权限/IO
            # 类 OSError 吞成 False——「探测失败」会被读成「确证不在」，
            # 两次失败探测拼成一个健康的非切换态。只有 FileNotFoundError
            # 证明不在；其它 OSError 上抛给 unknown 分支。
            try:
                path.stat()
            except FileNotFoundError:
                return False
            return True
        try:
            swap_interrupted = (
                not _present(provider_dir)
                and _present(provider_dir.with_name(
                    provider_dir.name + ".bak")))
        except OSError:
            # 检查自身失败 ≠ 状态健康——unknown 是第三态,页面必须说
            # 「核实不了,请人工确认」而不是照常声称数据无恙
            # （codex 第五轮 P2）。
            swap_state_unknown = True
            markers_written = _append_cancel_marker(
                log_path,
                "cancel outcome: swap-state check FAILED (filesystem "
                "error) — verify the provider dir and .bak/.new manually"
            ) and markers_written
        if swap_interrupted:
            markers_written = _append_cancel_marker(
                log_path,
                "cancel landed inside the SWAP WINDOW: canonical provider "
                "dir is missing; run the update again — startup repair "
                "restores it") and markers_written
    # graceful 的终态声称要**核实**不要推断（codex 第十轮 P2）:进程死了,
    # 它的记录不会再变,此刻读是安全的。硬杀恒 False（不读——被强杀的进程
    # 写没写终态由页面的重读收养链自己回答）。身份+时间窗合取,见 helper
    # （codex 第十一轮 P2:纯 pid 会把复用同 pid 的陈年 finished 工件核实
    # 成本次终态）。
    terminal_recorded = graceful and terminal_record_confirms_the_run(
        provider_dir, process.pid,
        launched_at=launched_at, exited_at=exited_at,
        launch_nonce=launch_nonce)
    return UpdateCancel(
        kind="cancelled", graceful=graceful, returncode=process.returncode,
        swap_interrupted=swap_interrupted, markers_written=markers_written,
        swap_state_unknown=swap_state_unknown, exited_at=exited_at,
        kill_issued=True, terminal_recorded=terminal_recorded)


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


def _blocking_run_status(
    provider_dir: Path,
    *,
    cancelled_started_at: str | None = None,
    cancelled_launch_nonce: str | None = None,
) -> str | None:
    """Why a launch should be refused based on the status artifact, or None.

    Only a record that (a) belongs to THIS provider, (b) says running and
    (c) classifies as FRESH blocks the button — a stale or unverifiable
    running record may be a crashed run, and a foreign record proves
    nothing about this provider. Those cases fall through to the
    single-flight lock, which is the real arbiter.

    ``cancelled_started_at``:本会话硬杀留下的孤儿 running 记录的**精确**
    状态戳（codex #470 第二轮 P1:页面解锁了按钮,这道闸却仍按 fresh
    running 拒绝——尤其切换窗命中后被指引的「立即重跑」会一直
    already_running 到六小时线）。只放行**戳完全相等**的那一条;任何新
    运行写新戳,放行即刻失效。单飞锁仍是真仲裁——进程已死锁已释放。
    """
    status = read_update_status(status_path_for_provider(provider_dir))
    if status.kind != "running":
        return None
    if not record_matches_provider(status, provider_dir):
        return None
    if classify_running(status) != RUNNING_FRESH:
        return None
    # 放行判据=证据覆盖（第二十五/二十六轮）:与页首覆盖判定、证据退役
    # 共用同一谓词——nonce 身份在场时一票裁决（同戳但 nonce 不同/缺失
    # 的接替记录不放行:粗粒度时钟可造同戳）,戳只留给双方都无 nonce 的
    # legacy 对;nonce-only 证据（无戳）凭 nonce 放行,防「按钮解锁、闸
    # 仍拒绝」的假解锁（第二轮同款）。
    if evidence_covers_record(
            status.started_at, status.launch_nonce,
            cancelled_started_at, cancelled_launch_nonce):
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
    cancelled_started_at: str | None = None,
    cancelled_launch_nonce: str | None = None,
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
        blocking = _blocking_run_status(
            provider_dir, cancelled_started_at=cancelled_started_at,
            cancelled_launch_nonce=cancelled_launch_nonce)
    except ValueError as exc:
        # status_path_for_provider refuses a filesystem-root provider —
        # that same path would also be an unusable --provider-dir.
        return UpdateLaunch(kind="launch_failed", error=str(exc))
    if blocking is not None:
        return UpdateLaunch(kind="already_running", error=blocking)

    # 本次 launch 的一次性身份——经环境传给子进程,编排器写进每条状态
    # 记录;收养凭 nonce 认领,无观察窗（#470 第二十四轮）。在 spawn 之前
    # 生成并注入,保证子进程从第一条记录起就带着它。
    launch_nonce = uuid4().hex
    child_env[LAUNCH_NONCE_ENV] = launch_nonce

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
                        process=proc, launch_nonce=launch_nonce)


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
