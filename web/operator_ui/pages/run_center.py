"""运行中心 — 数据更新手动补跑 + 今日出单的 UI 触发。

驾驶舱(ops_cockpit)的承诺是「只展示不代跑」,本页承担「代跑」:两个动作
都只是触发既有 CLI 的子进程,参数与驾驶舱印出的命令同源绑定。本页自身
绝不派生进程、绝不写任何数据文件——派生只发生在两个 audited runner
(``update_runner`` / ``recommend_runner``)里,各自被 logic 测试钉死 argv。

openspec 2026-08-16-ui-run-center。
"""

from __future__ import annotations

from collections.abc import MutableMapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

import streamlit as st

from web.operator_ui.bundle_health import resolve_default_provider_uri
from web.operator_ui.daily_signal_navigation import (
    DAILY_DECISION_REQUESTED_DATE_KEY,
    clear_run_center_published_date,
    remember_run_center_published_date,
    run_center_published_date,
)
from web.operator_ui.incumbent import (
    anchored_to_repo,
    resolve_incumbent,
    resolve_model_path,
    unusable_path_reason,
)
from web.operator_ui.page_header import render_page_header
from web.operator_ui.pages._ops_cockpit_helpers import (
    morning_command,
    provider_is_resolved,
    resolve_delisted_registry,
    resolve_name_source,
    serving_bundle_max_age_days,
)
from web.operator_ui.pages._run_center_helpers import (
    AWAIT_LAUNCH_WINDOW,
    await_window_expired,
)
from web.operator_ui.recommend_runner import run_daily_recommend
from web.operator_ui.update_progress import (
    AttributedProgress,
    last_fetch_progress_for_run,
)
from web.operator_ui.update_runner import (
    START_DATE,
    build_update_argv,
    calendar_gate_warning,
    cancel_update,
    cancelled_run_matches,
    default_log_path,
    evidence_binds_to_killed_run,
    gate_today,
    launch_daily_update,
    log_tail,
    log_window,
    observe_own_running_record,
    range_problem,
    settle_late_cancel,
)
from web.operator_ui.update_status import (
    RUNNING_FRESH,
    RUNNING_STALE,
    RUNNING_STALE_AFTER,
    classify_running,
    read_update_status,
    record_matches_provider,
    status_path_for_provider,
)

render_page_header(
    "运行中心",
    "数据更新手动补跑(后台)与今日出单(同步)——参数与驾驶舱同源绑定,"
    "本页只触发既有 CLI 的子进程,绝不改写任何数据。",
)

# 守望期间每这么久自动重读一次状态工件。守望 = running 记录新鲜,**或**
# 刚点过启动、子进程尚未写出记录的那段有界等待窗(见 _AWAIT_LAUNCH_KEY)。
# 别把它收窄回「仅 running」——那正是 codex #442 r1 修掉的 bug:点完启动
# 后页面永远停在启动前的状态,直到手点刷新,操作人会以为没启动成功。
# 两者都不满足时没有会变的东西。读一个小 JSON,成本可忽略。
_POLL_SECONDS = 30
_CN_TZ = timezone(timedelta(hours=8))

#: 等待窗的键。窗口长度与过期判据是**纯函数**,住在
#: ``_run_center_helpers``——那边不 import streamlit,所以没装 ui extra 的
#: logic 套件也能对它做行为测试(codex #442 r6)。
_AWAIT_LAUNCH_KEY = "run_center::awaiting_launch_since"
_AWAIT_LAUNCH_WINDOW = AWAIT_LAUNCH_WINDOW
_LAST_LAUNCH_KEY = "run_center::last_launch"
#: 本会话在飞的手动更新——**活进程句柄**在内存里（update_runner 的
#: launch 结果携带；本页零 spawn，守卫在档），受控取消的唯一通道（绝不
#: 按 pid 杀：pid 会被回收复用）。UI 重启即失去句柄，旧运行不可取消——
#: 如实降级。调度器的自动运行不是本 UI 子进程，天然不在射程。
_LIVE_RUN_KEY = "run_center::live_manual_run"
_CANCEL_ARM_KEY = "run_center::cancel_armed"
_LAST_CANCEL_KEY = "run_center::last_cancel"
#: 硬杀成功后的**持久**证据（不随 rerun 消失,codex #470 P1）:被取消那次
#: 运行的状态戳。running 记录的戳与它精确相等时,页面按「已取消」呈现并
#: 解锁启动闸;新运行写新戳,证据自动退役。
_CANCELLED_EVIDENCE_KEY = "run_center::cancelled_evidence"

# Streamlit's SessionStateProxy exposes the mapping operations used below, but
# its stubs do not inherit MutableMapping.  Keep the cast at the UI boundary;
# the navigation helpers remain Streamlit-free and testable with plain dicts.
_SESSION_STATE = cast(MutableMapping[str, object], st.session_state)


def _read_progress() -> AttributedProgress:
    """日志尾部最后一条 fetch 进度 + **它属不属于**最近一次运行。

    整页与片段**共用这一个读取器**——两处各写一份正是它们会分叉的方式
    (#442 r1/r3/r4 在这一页上连栽三次)。

    归属现在由**运行边界**判定,不再靠推断:写入侧每次运行都会落一行带日期的
    边界(2026-08-24-daily-update-run-ledger)。读到的窗口里找不到边界、或窗口
    没盖住整份日志时,如实说不知道——那正是边界落地之前的行为,不是退步。
    """
    text, complete = log_window(default_log_path(_provider_path))
    return last_fetch_progress_for_run(
        text, provider_dir=_provider_path, window_complete=complete)


def _status_signature(
    status: object, classification: str | None
) -> tuple[str, str, str, str]:
    """状态里「值得触发整页重绘」的部分。

    kind/started_at/finished_at **加上新鲜度分类**:前三项在一次运行
    跨过 6 小时陈旧线时逐字不变,只有分类会 fresh→stale 翻面。少了它,
    崩掉的运行会让闸门一直锁着、陈旧告警一直不出现,直到操作人手动
    整页刷新(codex #442 r1)。

    分类由**调用方传入**而非在此重算(codex #442 r4):基线那一侧必须用
    整页渲染时刻算出的那一个值,与闸门判断同源;片段那一侧才用当下时刻
    重算。两侧都在函数内部重算的话,跨线时刻会同时翻面而元组照样相等。
    """
    return (
        str(getattr(status, "kind", "")),
        str(getattr(status, "started_at", "")),
        str(getattr(status, "finished_at", "")),
        str(classification or ""),
    )

_provider = anchored_to_repo(resolve_default_provider_uri())
if not provider_is_resolved(_provider):
    st.error(
        "⚠ **未解析出 provider 路径**——`config.yaml` 缺失、无法解析,或没有 "
        "`provider_uri` 字段。请先修好 `config.yaml` 再回本页。"
    )
    st.stop()
_provider_reason = unusable_path_reason(_provider)
if _provider_reason is not None:
    st.error(f"⚠ **provider 路径在本机不可用**:`{_provider}` — {_provider_reason}")
    st.stop()
_provider_path = Path(_provider)

# ---------------------------------------------------------------------------
# ① 数据更新
# ---------------------------------------------------------------------------
st.subheader("① 数据更新(手动补跑)")
st.caption(
    "自动通道 = 每晚 20:30 计划任务(`run_daily_update.bat`),本区只用于漏跑/"
    "失败后的手动补跑。完整一轮约 2 小时;启动后通过下方状态与日志观测。"
    "并发权威是 `daily_update` 自身的单飞锁——本页的「正在运行」判断只是"
    "预检,撞锁的那次会以 exit 17 落日志。"
)

try:
    _status_path = status_path_for_provider(_provider_path)
except ValueError as _exc:
    st.error(f"⚠ {_exc}")
    st.stop()
_status = read_update_status(_status_path)
_read_at = datetime.now(tz=_CN_TZ)
# 新鲜度**只在渲染时刻分类一次**,下面三处(闸门 / 展示 / 基线签名)全部复用
# 同一个值。分别各调一次 classify_running 的话,若记录恰在这几行之间跨过
# 6 小时线,闸门会认为「新鲜」而基线已是「陈旧」——此后片段每次读到的也
# 都是陈旧、恒等于基线,永不 rerun,闸门就永久锁死(codex #442 r4)。
_status_class = classify_running(_status)
_running_fresh = (
    _status.kind == "running"
    and record_matches_provider(_status, _provider_path)
    and _status_class == RUNNING_FRESH
)
# 句柄证据覆盖（codex #470 P1）:硬杀留下的 running 记录没有终态,若只在
# 取消后首个渲染更正,任何 rerun 都会退回「正在更新」并锁住启动按钮直到
# 六小时陈旧线。证据按状态戳精确相等绑定到**被取消的那一次**;戳变了
# （新运行/终态）即退役。
_cancel_evidence = st.session_state.get(_CANCELLED_EVIDENCE_KEY)
_cancelled_this_run = (
    isinstance(_cancel_evidence, dict)
    and _status.kind == "running"
    and cancelled_run_matches(
        _status.started_at, _cancel_evidence.get("started_at"))
)
if isinstance(_cancel_evidence, dict) and not _cancelled_this_run:
    # 状态已被接替——证据退役,绝不覆盖别人的运行。
    st.session_state.pop(_CANCELLED_EVIDENCE_KEY, None)
if _cancelled_this_run:
    _running_fresh = False
# 会话内在飞的手动更新（codex #470 第三轮 P2）：子进程写下 running 记录
# 之前有一段窗口（_AWAIT_LAUNCH_KEY 盖的那段），此时 _running_fresh 仍为
# False、启动按钮仍可点——再点一次会用第二个句柄**顶掉**第一个：第二个
# 子进程通常被单飞锁以 exit 17 拒绝、句柄随即退役，原来那次两小时的运行
# 就此失去唯一合法取消凭据。凭句柄本身把闸：在飞即禁再启。
_session_live = st.session_state.get(_LIVE_RUN_KEY)
_session_live_proc = (
    _session_live.get("process") if isinstance(_session_live, dict) else None)
# 退役/补结算必须在 watcher 片段注册**之前**（codex 第十三轮 P2）：片段
# 在每次整页执行时也内联运行,死句柄支路的 st.rerun 会在走到它之后的任何
# 代码之前中止本轮——补结算若在片段之后,下一轮又先撞片段,无限 rerun、
# 补结算永不执行、句柄永不退役（await 支路不循环,正因它的解除条件在片段
# 之前重算——同款结构,这里对齐）。
_live_run = _session_live
_live_proc = _session_live_proc


def _settle_late_pending(_live_run: Any, _live_proc: Any) -> None:
    """迟到死亡补结算（codex 第八轮 P2 引入）——kill 已发的未决取消,进程
    死后欠的完整收尾。收尾义务与当场确认死亡**完全同款**（codex 第九轮
    P2b）,走共享的补结算边界:结局标记 + 严格 swap 检查 + unknown 三态。
    单一实现、两处调用（顶部退役块 + confirm 分支的 already_finished 竞
    态拦截,第十四轮 P2）——分抄两份正是第九轮抓过的分叉。末尾整页重
    绘,**不返回**。"""
    _late_lp = (
        _live_run.get("log_path") if isinstance(_live_run, dict) else "")
    # 原失败尝试的标记状态从未决上下文带回（codex 第十轮 P2）：请求/
    # 失败标记当时没落盘的话,审计链缺口不因迟到结局标记写成而消失。
    # 缺键按 False 走（fail-closed:证不出审计完整就不声称）。
    _late_markers = bool(
        _live_run.get("cancel_pending_markers_written", False)
        if isinstance(_live_run, dict) else False)
    _late_outcome = settle_late_cancel(
        _live_proc, Path(_late_lp) if _late_lp else None,
        provider_dir=_provider_path,
        markers_written=_late_markers,
        launched_at=_live_run.get("launched_at")
        if isinstance(_live_run, dict) else None)
    # 证据落盘。身份=**生存期内观察到的精确戳候选**（codex 第十二轮
    # P2:第八轮的请求时刻上界拒真孤儿、第十一轮的观测时刻上界收回
    # 收 pid 的接替者——死亡观测可晚于真实死亡最长一个轮询周期,该
    # 空窗内 OS 可把 pid 回收给接替的调度器运行,戳与 pid 双双落窗。
    # 唯一两头都站得住的身份:趁进程可证活着时（confirm 当刻 + watcher
    # 每拍）观察它自己写的记录戳,补结算只收养**精确相等**的那条;
    # 没有候选=证不出身份,fail-closed 不收养,孤儿等陈旧线。）
    _own_stamp = (
        _live_run.get("cancel_pending_own_started_at")
        if isinstance(_live_run, dict) else None)
    _late_status = read_update_status(_status_path)
    _late_evidence = (
        _late_status.kind == "running"
        and record_matches_provider(_late_status, _provider_path)
        and _late_status.pid == _live_proc.pid
        and cancelled_run_matches(_late_status.started_at, _own_stamp))
    if _late_evidence:
        st.session_state[_CANCELLED_EVIDENCE_KEY] = {
            "started_at": _late_status.started_at or "",
        }
    # 结局按与当场取消同一套渲染呈报（成功文案 + swap/unknown/标记
    # 三警告都在 _LAST_CANCEL_KEY 的消费处）——迟到死亡不能只默默
    # 落证据,操作人得到的答复必须与当场死亡一致。
    st.session_state[_LAST_CANCEL_KEY] = {
        "kind": _late_outcome.kind,
        "graceful": _late_outcome.graceful,
        "returncode": _late_outcome.returncode,
        "error": _late_outcome.error,
        "swap_interrupted": _late_outcome.swap_interrupted,
        "markers_written": _late_outcome.markers_written,
        "swap_state_unknown": _late_outcome.swap_state_unknown,
        "terminal_recorded": _late_outcome.terminal_recorded,
        "evidence_stored": _late_evidence,
    }
    st.session_state.pop(_LIVE_RUN_KEY, None)
    st.session_state.pop(_CANCEL_ARM_KEY, None)
    # 当场重绘：上方状态横幅/闸门判断都算在补结算**之前**,不重绘会
    # 带着旧判断渲染到底。幂等出口:_LIVE_RUN_KEY 已 pop,重绘后不会
    # 再进本分支,不会循环。
    st.rerun()


if _live_proc is not None and _live_proc.poll() is not None:
    _pending_at = (
        _live_run.get("cancel_pending_at")
        if isinstance(_live_run, dict) else None)
    if _pending_at:
        # kill 已发的未决取消迟到死亡——完整收尾（内部整页重绘,不返回）。
        _settle_late_pending(_live_run, _live_proc)
    # 进程已结束——句柄退役，成败由状态工件/台账自述（自然完成,无未决
    # 取消;kill 导致的死亡在上面的补结算分支里已经 rerun 走人）。
    st.session_state.pop(_LIVE_RUN_KEY, None)
    st.session_state.pop(_CANCEL_ARM_KEY, None)
    _session_live = None
    _session_live_proc = None
_session_run_alive = (
    _session_live_proc is not None and _session_live_proc.poll() is None)
# 未决取消的句柄（codex 第九轮 P2a）：cancel_failed 之后 kill 已发、进程
# 还没死——它的迟到死亡不改状态签名也不推日志进度，watcher 只比那两样
# 的话补结算块要等操作人手动交互才被重新执行。把句柄交给 watcher 盯。
_pending_cancel_proc = (
    _session_live_proc
    if isinstance(_session_live, dict)
    and _session_live.get("cancel_pending_at")
    else None)

if _status.kind not in ("missing", "corrupt") and not record_matches_provider(
    _status, _provider_path
):
    st.error(
        "⚠ 该状态记录属于**另一个 provider**"
        f"(记录内:`{_status.provider_dir}`)——本页拒绝据此展示。"
    )
elif _status.kind == "missing":
    st.info(
        "尚无状态记录——新机、或生产 checkout 更新到含状态工件的版本"
        "(#434)后还没跑过。首跑后这里会出现记录。"
    )
elif _status.kind == "corrupt":
    st.error(f"⚠ 状态记录损坏(绝不用默认值顶替):{_status.error}")
elif _status.kind == "running":
    _cls = _status_class  # 同一次分类,勿重算(见上)
    if _cancelled_this_run:
        st.warning(
            f"⛔ 该 running 记录(始于 {_status.started_at})已被本会话"
            "**取消**——进程经句柄确认退出,不会再写终态;这不是仍在运行。"
            "单飞锁已随进程释放,可直接重新启动更新。"
        )
    elif _cls == RUNNING_FRESH:
        st.info(f"🔄 一次更新正在进行:始于 {_status.started_at}。")
    elif _cls == RUNNING_STALE:
        st.warning(
            f"⚠ 记录停在运行中且已超过 {RUNNING_STALE_AFTER}"
            f"(始于 {_status.started_at})——进程可能已被中断,也可能仍在"
            "异常缓慢地运行;本页无法区分,请查日志尾部。"
        )
    else:
        st.warning(
            "⚠ running 记录的起始时间无法核实——本页**无法确认**它是否"
            "仍在运行,请查日志尾部。"
        )
    # 「走到哪了」——信息本来就在日志里(fetch 每 200 支票打一条),只是埋在
    # 几百行里。抬到这里,让上面那句「正在运行」能接上下文。
    #
    # **只在 running 分支里读**:日志是追加的,一次运行结束后最后一条进度行
    # 仍留在文件尾。在非 running 时显示它,等于把**上一次**运行的进度当成
    # 当前进度——那是撒谎。陈旧/不可核实的 running 反而最该显示:昨晚那次
    # 断在 fetch 2400/5883,这一行正是唯一能说清「断在哪」的东西。
    _attributed = _read_progress()
    _progress = _attributed.progress
    _scope = (
        "分母是该端点该年的票数,不是整轮进度(fetch 只是六个阶段中的一个)。")
    if _progress is None:
        st.caption("⏳ 日志尾部没有 fetch 进度行(可能尚未进入 fetch 阶段)。")
    elif _attributed.attributed and (
            _attributed.boundary_stamp == (_status.started_at or "")):
        # 归属确定,且边界与**上面显示的那条状态记录**是同一次运行:写入侧在
        # 同一次运行里用同一个 started_at.isoformat() 写状态与边界,精确相等。
        st.caption(
            f"⏳ 本次运行的最后一条进度:{_progress.describe()}。"
            f"归属由运行边界确定(该次运行始于 "
            f"**{_attributed.boundary_stamp}**),不是推断出来的。{_scope}"
        )
    elif _attributed.attributed:
        # 边界与状态记录**对不上**:状态写入是 best-effort(写失败只记 ERROR,
        # 运行照常),于是日志与状态工件可以各自往前走——旧运行留下 running
        # 状态、新运行只落了边界。这条进度属于**边界那次**运行,把它说成
        # 上面显示的那次,又是把交错讲成确定(codex P2)。
        st.caption(
            f"⏳ 日志尾部最后一条进度:{_progress.describe()}。"
            f"日志边界显示有一次始于 **{_attributed.boundary_stamp}** 的运行,"
            f"而上面的状态记录写的是 **{_status.started_at or '?'}** —— 两者"
            "**对不上**(状态工件可能写失败或已陈旧)。这条进度属于边界那次"
            f"运行,不属于上面显示的状态记录。{_scope}"
        )
    else:
        # 不知道就说不知道,并说**真原因**:三种失败条件对操作人的下一步不同,
        # 一律说「没有边界」会在最常见的截断窗口上撒谎(codex P2)。
        _why = {
            "window_truncated": "日志长于读取窗口,窗口外可能还有别的运行边界",
            "foreign_boundary": "窗口里有别的 provider 的运行边界,行可能交错",
            "no_boundary": "读到的日志窗口里没有运行边界",
            "corrupt_boundary": "窗口里的运行边界戳读不出来(日志可能损坏)",
        }.get(_attributed.unattributed_reason, "归属条件未满足")
        st.caption(
            f"⏳ 日志尾部最后一条进度:{_progress.describe()}。"
            f"本次运行始于 **{_status.started_at or '?'}** —— {_why},"
            "因此**无法证明这条属于本次运行**(也可能是上一次留下的),"
            f"请对着两个时刻自行判断。{_scope}"
        )
elif _status.ok:
    st.success(
        f"🟢 上次更新成功(exit 0):run_date={_status.run_date},"
        f"{_status.started_at} → {_status.finished_at}。{_status.detail}"
    )
else:
    st.error(
        f"🔴 上次更新**失败**:exit {_status.exit_code}"
        f"({_status.exit_meaning}),失败阶段:**{_status.failed_stage}**"
        f" — {_status.detail}"
    )

# 刚点过启动、子进程还没写出 running 记录的那段窗口也要轮询——否则
# 启动后的页面会停在启动前的状态直到手点刷新(codex #442 r1)。有界:
# 超过窗口就停,启动失败时不会无限空转。
_awaiting_launch = False
_await_deadline: datetime | None = None
_awaiting_raw = st.session_state.get(_AWAIT_LAUNCH_KEY)
if _awaiting_raw:
    _awaiting_since: datetime | None
    try:
        _awaiting_since = datetime.fromisoformat(str(_awaiting_raw))
    except ValueError:
        _awaiting_since = None
    if _awaiting_since is not None and not await_window_expired(
        _awaiting_since, _read_at
    ):
        _awaiting_launch = True
        _await_deadline = _awaiting_since + _AWAIT_LAUNCH_WINDOW
if _running_fresh:
    # 只有**新鲜**的 running 记录才证明子进程已经写出了自己的记录:新运行
    # 的 started_at 就是刚才,分类必为 fresh。一条**陈旧**的旧记录不是
    # 「新运行已出现」——恢复性启动(带着一条陈旧 running 记录去补跑)时把
    # 标记按它清掉,会让 _watching 落回 False,新运行直到手动刷新才被看见
    # (codex #442 r2)。此后由 _running_fresh 自己驱动轮询,标记功成身退。
    st.session_state.pop(_AWAIT_LAUNCH_KEY, None)
    _awaiting_launch = False
elif not _awaiting_launch:
    st.session_state.pop(_AWAIT_LAUNCH_KEY, None)

# 未决取消也要看着：等它死的期间状态签名/进度可能全程不动（硬杀后两者
# 都冻结），没有 watcher 它的补结算永远等不来（codex 第九轮 P2a）。
_watching = (
    _running_fresh or _awaiting_launch or _pending_cancel_proc is not None)

# 「我刚点的刷新到底生效了没」——状态没变时整页重绘长得一模一样,读取
# 时刻是唯一能证明重读发生过的痕迹(#440 后续:操作人反馈按钮像坏的)。
_poll_note = (
    f";每 {_POLL_SECONDS} 秒自动重读,状态一变就整页刷新"
    if _watching
    else ""
)
st.caption(
    f"上次读取:{_read_at:%H:%M:%S}(点任意按钮或刷新页面都会重读{_poll_note})"
)

# 基线签名必须在**整页渲染时刻**算定并闭包捕获。在片段里对两侧各算
# 一次是行不通的:跨过 6 小时线时,片段用同一个 now 去分类新读到的记录
# 和旧的 `_status` 对象,两边同时变成 stale,元组照样相等、永不 rerun
# ——闸门继续锁着、陈旧告警仍然不出(codex #442 r2)。
_baseline_signature = _status_signature(_status, _status_class)
# 进度也要进重跑判据:片段计时只重跑片段,而追加的 fetch 行**不改变**状态
# 签名(kind/started_at/分类都不动)。只比签名的话,页面会一直冻在旧的那一行
# 直到运行结束(codex #450 r1)。基线同样在整页渲染时刻定格——两侧都在片段里
# 重算,正是 #442 r2 证伪过的那个错法。
_baseline_progress = _read_progress()

if _watching:
    @st.fragment(run_every=_POLL_SECONDS)
    def _watch_update_completion() -> None:
        """轮询状态工件,与整页渲染时的基线签名比对,变了就整页重绘。

        只在 fragment 内重读,不渲染任何东西:反复读到同一签名 = 静默
        继续。一旦偏离基线(running→finished、新鲜→陈旧、或换了一次
        运行),整页 rerun——下方出单按钮的闸门与陈旧告警都依赖主脚本
        作用域的判断,只刷新片段会让两处显示自相矛盾。
        """
        _latest = read_update_status(_status_path)
        if _status_signature(_latest, classify_running(_latest)) != _baseline_signature:
            st.rerun(scope="app")
            return
        if _read_progress() != _baseline_progress:
            # fetch 又推进了一格 —— 整页重绘,让上面那句进度跟上。
            st.rerun(scope="app")
            return
        # 未决取消的迟到死亡（codex 第九轮 P2a）:硬杀后状态签名与日志
        # 进度都可能不再变化——没有这一支,主脚本里的补结算块要等操作人
        # 手动交互才会重新执行,死进程的取消控件与孤儿 running 会一直
        # 挂着。句柄一死就把整页拉起来,让补结算当场跑。
        if _pending_cancel_proc is not None:
            if _pending_cancel_proc.poll() is not None:
                st.rerun(scope="app")
                return
            # 还活着:趁生存期**可证**时刷新它自己写的记录戳（codex 第
            # 十二轮 P2——迟到收养的身份候选,pid 在两次 poll 之间被子
            # 进程持续持有,不可能被回收;死后观测到的任何记录都可能是
            # 回收 pid 的接替者,不能再当身份）。fragment 每次整页渲染
            # 也执行一遍,交互驱动的刷新一并覆盖。
            _own = observe_own_running_record(
                _pending_cancel_proc, _provider_path)
            if _own and isinstance(_session_live, dict):
                _session_live["cancel_pending_own_started_at"] = _own
        # 等待窗到期同样要把整页拉起来。片段计时**只重跑片段**,主脚本不再
        # 执行,所以主脚本里算出的窗口判断在片段注册之后永远不会被重新求值。
        # 子进程若在写出 running 记录前就死掉(例如撞单飞锁秒退 exit 17),
        # 签名永不变化——没有这一支,五分钟的"有界"窗口形同虚设,会一直轮询
        # 下去(codex #442 r3)。
        if _await_deadline is not None and await_window_expired(
            _await_deadline - _AWAIT_LAUNCH_WINDOW, datetime.now(tz=_CN_TZ)
        ):
            st.rerun(scope="app")

    _watch_update_completion()

_tushare_dir = _provider_path.parent / "tushare_raw"
_update_registry = Path(anchored_to_repo(resolve_delisted_registry()))

# 抓取范围:折叠起来,因为**缺省就是调度器的那一组**,改它是一次刻意的偏离。
# 需要能改的两个真实场景:
#   · fetch manifest 已被一次更宽的抓取撑大并记了洞,此后按缺省下限跑会被
#     范围守卫拒绝(2026-08-17/20/21 连续三晚),01 给的修法是「按完整范围重跑」;
#   · 周末要补跑,必须传结束日期才能绕过交易日历闸(见下方预警)。
with st.expander("抓取范围(缺省 = 调度器的那一组)"):
    st.caption(
        "开始日期是**重扫下限**不是全量重抓 —— 管线自己会剪掉已入库的日子。"
        "结束日期留空 = 抓到运行日当天(与调度器一致)。"
    )
    _range_start, _range_end = st.columns(2)
    with _range_start:
        _start_input = st.text_input(
            "开始日期 YYYYMMDD", value=START_DATE,
            key="run_center::start_date")
    with _range_end:
        _end_input = st.text_input(
            "结束日期 YYYYMMDD(留空 = 运行日)", value="",
            key="run_center::end_date")

# 传 `today`：结束日期留空时生效值就是运行日，只比字面输入会放过一个
# 颠倒的区间（codex P2）。时钟与日历闸同源，见 `gate_today`。
_range_error = range_problem(_start_input, _end_input, today=gate_today())
if _range_error is not None:
    st.error(f"⚠ 抓取范围不可用:{_range_error}")

# 预览的是 `build_update_argv` **产出的那个 argv 本身**,不是另抄一份措辞:
# 手抄的预览与真正执行的参数会分头漂移,而这里恰恰是「显示的必须就是要跑的」。
_preview_argv = build_update_argv(
    _provider_path, _tushare_dir, _update_registry,
    start_date=_start_input.strip() or None,
    end_date=_end_input.strip() or None,
)
st.caption(
    "启动参数(缺省即镜像调度器):"
    + " · ".join(f"`{part}`" for part in _preview_argv[2:])
)

_gate_warning = calendar_gate_warning(
    _provider_path,
    # 宿主本地日,不是东八区 —— 见 `gate_today` 的 docstring(#461 的第一条 P1)。
    today=gate_today(),
    end_date=_end_input,
)
if _gate_warning is not None:
    st.warning(f"⚠ {_gate_warning}")

_col_refresh, _col_launch = st.columns(2)
with _col_refresh:
    # 点击本身就重跑脚本、重读工件;下方 toast 与上方「上次读取」时刻是
    # 给操作人的确证——没有它们,一次成功的刷新和一个坏按钮长得一样。
    _refresh_clicked = st.button(
        "🔄 刷新状态",
        key="run_center::refresh_status",
        use_container_width=True,
    )
with _col_launch:
    _launch_clicked = st.button(
        "🚀 后台启动数据更新",
        key="run_center::launch_update",
        type="primary",
        # 范围不合法就不让点:畸形日期要到两小时后才在 tushare 那头炸。
        # 日历闸预警**不**禁用按钮 —— no-op 无害,操作人可能就是要它。
        disabled=(_running_fresh or _session_run_alive
                  or _range_error is not None),
        use_container_width=True,
    )
if _refresh_clicked:
    st.toast(f"已重读状态工件({_read_at:%H:%M:%S})")
if _launch_clicked and _session_run_alive:
    # 竞态兜底（按钮 disabled 之外的第二道）：本会话已有在飞运行时拒绝
    # 再启——顶掉句柄=原运行失去取消凭据。
    st.error(
        "本会话已有一次手动更新在飞——先取消它或等它结束，再启动新的。"
    )
elif _launch_clicked:
    # 证据时间绑定的**下界**必须在派生调用之前采样：子进程可能在派生
    # 返回后、我们记时之前就写下 running 记录——晚采的下界会把真被杀的
    # 运行判成「launch 之前的旧记录」拒绑，孤儿照样锁页六小时
    # （codex 第六轮 P1；本页零派生，动作全在 audited runner 里）。
    _pre_launch_at = datetime.now(tz=_CN_TZ).isoformat()
    _launch = launch_daily_update(
        _provider_path, _tushare_dir, _update_registry,
        start_date=_start_input.strip() or None,
        end_date=_end_input.strip() or None,
        # 已取消孤儿的精确戳——launch 内部的状态闸凭它放行**那一条**记录
        # （codex 第二轮 P1:按钮解锁了、闸还挡着=假解锁）。
        cancelled_started_at=(
            (_cancel_evidence or {}).get("started_at")
            if _cancelled_this_run else None),
    )
    # 结果暂存 + 整页 rerun:守望者的注册发生在本行**之上**,所以本次
    # 脚本运行里设的等待标记要等下一轮才生效。立刻 rerun 让它当场生效,
    # 否则启动后的页面不轮询(codex #442 r1)。
    st.session_state[_LAST_LAUNCH_KEY] = {
        "kind": _launch.kind,
        "pid": _launch.pid,
        "log_path": str(_launch.log_path) if _launch.log_path else "",
        "error": _launch.error,
    }
    if _launch.kind == "launched":
        st.session_state[_AWAIT_LAUNCH_KEY] = datetime.now(
            tz=_CN_TZ
        ).isoformat()
        # 活句柄入会话——受控取消的唯一凭据（frozen dataclass 里的
        # process 字段；见 update_runner.UpdateLaunch）。
        st.session_state[_LIVE_RUN_KEY] = {
            "process": _launch.process,
            "log_path": str(_launch.log_path) if _launch.log_path else "",
            # 证据时间绑定的下界（codex 第五/六轮 P1）：**spawn 之前**
            # 采样——子进程写 running 永远晚于它。
            "launched_at": _pre_launch_at,
        }
    st.rerun()

_last_launch = st.session_state.pop(_LAST_LAUNCH_KEY, None)
if isinstance(_last_launch, dict):
    if _last_launch.get("kind") == "launched":
        st.success(
            f"已在后台启动(pid {_last_launch.get('pid')}),日志:"
            f"`{_last_launch.get('log_path')}`。**启动≠成功**——成败以上方"
            "状态与日志为准(本页已开始自动重读)。"
        )
    else:
        st.error(
            f"未启动({_last_launch.get('kind')}):{_last_launch.get('error')}"
        )

# --- 受控取消（2026-08-26-manual-update-controlled-cancel） ---
# 只对**本会话启动且仍在飞**的手动更新可见；两步确认。数据安全前提：管线
# 是 build-then-atomic-swap——切换前任意时点终止，在线 bundle 一字节不动，
# 仍是最后一次成功更新的数据（无论自动还是手动）。
_live_run = st.session_state.get(_LIVE_RUN_KEY)
_live_proc = (
    _live_run.get("process") if isinstance(_live_run, dict) else None)
if _live_proc is not None and _live_proc.poll() is not None:
    # 死亡发生在顶部退役/补结算块之后（本轮脚本运行途中）——此处不再做
    # 任何结算（codex 第十三轮 P2:退役/补结算已整体移到 watcher 片段注
    # 册之前,否则片段的死句柄 rerun 会在走到这里之前中止每一轮脚本,
    # 补结算永不执行）。本轮只收起取消控件;下一轮顶部块接手退役或补结
    # 算,未决死亡由片段的轮询保证在 30 秒内拉起那一轮。
    _live_proc = None
if _live_proc is not None:
    if not st.session_state.get(_CANCEL_ARM_KEY):
        if st.button(
            "⛔ 取消本会话启动的手动更新",
            key="run_center::cancel_request",
            use_container_width=True,
        ):
            st.session_state[_CANCEL_ARM_KEY] = True
            st.rerun()
    else:
        st.error(
            "确认取消这次手动更新？管线只有校验通过后才原子切换——除"
            "**切换瞬间的两段重命名窗口**外，取消不影响在线数据（仍是最"
            "后一次成功更新的那份）；若恰好落在切换窗内，本页会响亮提示"
            "并指引立即重跑更新（启动修复自动复原,swap 契约本就承诺"
            "crash-atomicity + 事后修复）。Windows 下为强制终止：状态工"
            "件会停在 running（本页据句柄证据持续如实标注，不伪造终态）；"
            "单飞锁随进程消亡自动释放；遗留半成品由下次运行的启动修复"
            "清理。"
        )
        _c1, _c2 = st.columns(2)
        with _c1:
            if st.button(
                "确认取消",
                key="run_center::cancel_confirm",
                type="primary",
                use_container_width=True,
            ):
                _lp = (_live_run or {}).get("log_path") or ""
                # 生存期候选的**取消前**观察（codex 第十七轮 P2）：取消
                # 调用可耗满宽限窗,kill 恰在它返回之后生效——确认后的那
                # 次观察会撞上死进程返回 None,已写出 running 的真孤儿因
                # 无候选被拒收养、锁页六小时。此刻进程还没被碰,观察必然
                # 落在生存期内;记录戳在写出后不变,前后观察若都成功必然
                # 同值。
                _own_before = observe_own_running_record(
                    _live_proc, _provider_path)
                # 请求时刻——迟到死亡补结算的**触发标记 + 审计戳**
                # （codex 第八轮 P2 引入）。不再当绑定上界用：子进程可在
                # 请求之后才写出自己的 running 记录,请求时刻上界会把真孤
                # 儿拒之窗外（codex 第十一轮 P2）——绑定上界改用补结算入
                # 口的死亡观测时刻。
                _cancel_requested_at = datetime.now(tz=_CN_TZ).isoformat()
                _outcome = cancel_update(
                    _live_proc, Path(_lp) if _lp else None,
                    provider_dir=_provider_path,
                    # graceful 终态核实的时间窗下界（codex 第十一轮
                    # P2：纯 pid 会把复用同 pid 的陈年 finished 工件核
                    # 实成本次终态）。
                    launched_at=(_live_run or {}).get("launched_at"),
                    # 上一次失败尝试的标记状态,跨重试聚合（codex 第十五
                    # 轮 P2）:先前的审计缺口不因本次重试写成而消失,存量
                    # False 也不被本次 True 覆盖——聚合在取消边界单点做。
                    prior_markers_written=(_live_run or {}).get(
                        "cancel_pending_markers_written"))
                if (_outcome.kind == "already_finished"
                        and isinstance(_live_run, dict)
                        and _live_run.get("cancel_pending_at")):
                    # 竞态拦截（codex 第十四轮 P2）：先前 cancel_failed
                    # 已发 kill、操作人重试,进程恰在「顶部结算检查之后、
                    # cancel_update 初检之前」死掉——no-op 语义只属于
                    # **没有未决取消**的自然结束;这次死亡是先前 kill 导
                    # 致的,按 already_finished 丢弃句柄会把迟到收尾（结
                    # 局标记/swap 诊断/孤儿证据）整个跳过:孤儿锁页六小
                    # 时、切换窗命中无人报。改走完整补结算（内部整页重
                    # 绘,不返回）。
                    _settle_late_pending(_live_run, _live_proc)
                st.session_state[_LAST_CANCEL_KEY] = {
                    "kind": _outcome.kind,
                    "graceful": _outcome.graceful,
                    "returncode": _outcome.returncode,
                    "error": _outcome.error,
                    "swap_interrupted": _outcome.swap_interrupted,
                    "markers_written": _outcome.markers_written,
                    "swap_state_unknown": _outcome.swap_state_unknown,
                    "terminal_recorded": _outcome.terminal_recorded,
                    "evidence_stored": False,
                }
                # 上界由取消边界在**确认死亡当刻**返回（codex 第六轮
                # P1）：cancel_update 死亡确认后还要写标记/查文件系统，
                # 页面此刻再采时间会把那段里起跑的调度器接替也框进窗。
                _killed_at = _outcome.exited_at
                if _outcome.kind in ("cancelled", "already_finished"):
                    # 只有确认终局才交出句柄——cancel_failed 时进程可能
                    # 还活着,句柄是唯一合法取消凭据,丢了就只剩任务管理
                    # 器（codex #470 P2）。
                    st.session_state.pop(_LIVE_RUN_KEY, None)
                elif (_outcome.kind == "cancel_failed"
                        and _outcome.kill_issued
                        and isinstance(_live_run, dict)):
                    # 取消未决上下文（codex 第八轮 P2）：kill 已发、宽限
                    # 窗内没等到——进程可能在返回之后才迟到死亡。退役块
                    # 凭这个标记补结算证据,否则迟到死亡被当自然完成、孤
                    # 儿 running 无证据锁页六小时。
                    # 只在 kill **实际发出**时才留（codex 第十轮 P2）：
                    # kill 调用自身抛了=进程没被碰过,之后自然跑完就是
                    # 自然完成,补结算成「已强制取消」是撒谎——那种失败
                    # 走普通退役,状态工件自述。
                    _live_run["cancel_pending_at"] = _cancel_requested_at
                    # 原失败尝试的标记落盘结果一并携带（codex 第十轮
                    # P2）：请求/失败标记当时没写进去,迟到结局标记写成
                    # 了,审计链仍缺头两条——补结算不得从乐观缺省重来。
                    _live_run["cancel_pending_markers_written"] = (
                        _outcome.markers_written)
                    # 趁进程此刻可能还活着,再观察一次它自己写的记录戳
                    # （codex 第十二轮 P2:候选只能在生存期内取——此后
                    # watcher 每 30 秒续刷）;进程已在取消调用尾声死掉时
                    # 用取消前那次观察兜底（第十七轮 P2:两次观察若都成
                    # 功必然同戳,后观察只为覆盖「请求后才写出记录」）。
                    _own_now = observe_own_running_record(
                        _live_proc, _provider_path) or _own_before
                    if _own_now:
                        _live_run["cancel_pending_own_started_at"] = _own_now
                elif (_outcome.kind == "cancel_failed"
                        and isinstance(_live_run, dict)
                        and _live_run.get("cancel_pending_at")):
                    # kill 调用没发出去的重试,但**先前的 kill 已在未决**
                    # （codex 第十六轮 P2）：本次的标记缺失同属这条未决
                    # 取消的审计链——聚合值（cancel_update 已按 prior 种
                    # 子聚合,False 不被洗回）写回,否则迟到结算从陈旧
                    # True 起步,报出完整审计链、抹掉本次缺失的请求/失败
                    # 标记。不动 cancel_pending_at:本次没发 kill,未决
                    # 身份仍属先前那次。
                    _live_run["cancel_pending_markers_written"] = (
                        _outcome.markers_written)
                if (_outcome.kind == "cancelled"
                        and not _outcome.terminal_recorded):
                    # 进程死了且**没有核实到**它写下终态——硬杀必然如此;
                    # graceful 也可能如此（SIGINT 落在终录路径就位之前,
                    # 若它已写 running 就同样留下孤儿——codex 第十轮
                    # P2）。两者同走孤儿收养:按状态戳存持久证据,跨
                    # rerun 更正呈现并解锁启动闸（codex P1）。
                    # 戳必须在进程终止后**重读**:子进程可能在页面顶部那次
                    # 读取之后才写下它的 running 记录,拿页首快照会存下前
                    # 一次运行的旧戳,下一轮精确匹配落空、证据被当场退役,
                    # 孤儿记录照样锁页六小时（codex 第二轮 P1）。只有重读
                    # 确认是本 provider 的 running 记录才落证据。
                    _fresh_status = read_update_status(_status_path)
                    # 身份+时间绑定（codex 第五/九轮）：单飞锁随进程消亡
                    # 即释放，调度器可在「杀死之后、重读之前」起跑并写下
                    # 自己的 running——按 provider+kind 收养会把活着的调
                    # 度器运行标成已取消并解锁启动闸。时间窗之外还必须
                    # 记录 pid == 被杀句柄 pid：launch 之后、子进程拿锁之
                    # 前起跑并夺锁的调度器,started_at 恰好落在窗内,光看
                    # 时间照样误收养（第九轮 P2c）。
                    if (_fresh_status.kind == "running"
                            and record_matches_provider(
                                _fresh_status, _provider_path)
                            and evidence_binds_to_killed_run(
                                _fresh_status.started_at,
                                (_live_run or {}).get("launched_at"),
                                _killed_at,
                                record_pid=_fresh_status.pid,
                                killed_pid=_live_proc.pid)):
                        st.session_state[_CANCELLED_EVIDENCE_KEY] = {
                            "started_at": _fresh_status.started_at or "",
                        }
                        _lc = st.session_state[_LAST_CANCEL_KEY]
                        _lc["evidence_stored"] = True
                st.session_state.pop(_CANCEL_ARM_KEY, None)
                st.rerun()
        with _c2:
            if st.button(
                "保留运行",
                key="run_center::cancel_abort",
                use_container_width=True,
            ):
                st.session_state.pop(_CANCEL_ARM_KEY, None)
                st.rerun()

_last_cancel = st.session_state.pop(_LAST_CANCEL_KEY, None)
if isinstance(_last_cancel, dict):
    if _last_cancel.get("kind") == "already_finished":
        st.info(
            "取消未执行：该运行在取消前已自行结束"
            f"（returncode={_last_cancel.get('returncode')}）——成败以上方"
            "状态与台账为准。"
        )
    elif _last_cancel.get("kind") == "cancelled":
        # 「编排器自己写下了终态记录」只许在**核实到**时说（finished 且
        # 写者 pid 属本次运行——codex 第十轮 P2）：SIGINT 可落在编排器
        # 还在 import/解析配置/拿锁的阶段,终录路径尚未就位,及时退出证明
        # 不了终态写了。没核实到的 graceful 与硬杀同走孤儿收养呈报,措辞
        # 如实区分。
        _mode = ("礼貌信号生效、但未核实到编排器写下终态记录"
                 if _last_cancel.get("graceful") else "强制终止")
        if (_last_cancel.get("graceful")
                and _last_cancel.get("terminal_recorded")):
            st.success(
                "已取消（礼貌信号生效）：编排器自己写下了终态记录（已核"
                "实：finished 且写者 pid 属本次运行），状态与台账如实"
                "可查。"
                + ("" if (_last_cancel.get("swap_interrupted")
                          or _last_cancel.get("swap_state_unknown"))
                   else "在线数据未受影响。")
            )
        elif _last_cancel.get("evidence_stored"):
            st.success(
                f"已取消（{_mode}，returncode="
                f"{_last_cancel.get('returncode')}）。"
                "**状态工件仍标 running**——该进程没有写下终态记录，"
                "本页将持续按「已取消」如实标注（跨刷新有效），启动按钮"
                "已解锁；单飞锁已自动释放，下次更新照常。"
            )
        else:
            # 进程在写下自己的 running 记录之前就被终止（或记录已是别次
            # 运行的）——没有孤儿要更正，也没有证据可存；上面那套「将持续
            # 标注/已解锁」的话在这种情形下会与下一次 rerun 矛盾
            # （codex 第四轮 P2）。按状态工件如实展示即可。
            st.success(
                f"已取消（{_mode}，returncode="
                f"{_last_cancel.get('returncode')}）。状态工件此刻没有该"
                "运行的 running 记录（进程在写下记录前即被终止）——无需"
                "更正标注，页面按状态工件如实展示；单飞锁已自动释放，"
                "下次更新照常。"
            )
    else:
        st.error(
            f"取消失败：{_last_cancel.get('error')}"
        )
    # 以下警告在**共同作用域**：cancel_failed 同样可能标记写失败——嵌在
    # cancelled 分支里会让失败结局的审计缺失永远渲染不到（codex 第七轮
    # P2）。swap 两条只在 cancelled 才可能为真，放共同层无害。
    if _last_cancel.get("swap_state_unknown"):
        st.warning(
            "⚠ 取消后**无法核实**数据目录状态（检查自身失败：卷不可"
            "用/权限/IO 错）——本页不声称在线数据无恙；请人工确认 "
            "provider 目录与 .bak/.new 的现状，必要时立即重跑更新。"
        )
    if (_last_cancel.get("kind") in ("cancelled", "cancel_failed",
                                     "already_finished")
            and not _last_cancel.get("markers_written", True)):
        st.warning(
            "⚠ 取消动作的**日志标记写入失败**（权限/磁盘满？）——这次"
            "操作在共享日志里没有审计线索；请检查 "
            "`logs/daily_update.log` 的可写性。"
        )
    if _last_cancel.get("swap_interrupted"):
        st.error(
            "⚠ 本次取消**恰好落在切换窗内**：canonical 数据目录此刻"
            "缺位（swap 契约的 crash 态）。请**立即重新启动一次更新**"
            "——启动修复会自动复原（.bak/.new 均在）；在此之前出单侧"
            "会拒绝读取，这是 fail-loud 而非数据丢失。"
        )

with st.expander("日志尾部(只读)"):
    st.caption(
        "本页启动的运行已钉 UTF-8;调度器 `.bat` 只有**打过** "
        "`set \"PYTHONIOENCODING=utf-8\"` 补丁之后写的行才是 UTF-8。"
        "**见到乱码请先核对自己的 `run_daily_update.bat` 有没有这行**——"
        "按旧模板建的调度任务没有,要手工补(见调度 runbook);补上后新写入的"
        "行一律 UTF-8。此前的历史行里,字节恰好既是合法 GBK 又是合法 UTF-8 "
        "的那类读侧无法还原,只能从日志文件本身追溯。"
    )
    _log_text = log_tail(default_log_path(_provider_path))
    if _log_text:
        st.code(_log_text)
    else:
        st.caption("(暂无日志——本机还没跑过数据更新)")

# ---------------------------------------------------------------------------
# ② 今日出单
# ---------------------------------------------------------------------------
st.subheader("② 今日出单")

_incumbent = resolve_incumbent()
_registry_str = resolve_delisted_registry()
_name_source = resolve_name_source()
_bundle_age = serving_bundle_max_age_days()
_cmd = morning_command(
    _incumbent,
    model_path=anchored_to_repo(resolve_model_path()),
    provider_uri=_provider,
    delisted_registry=_registry_str,
    name_source=_name_source,
    bundle_max_age_days=_bundle_age,
)
st.caption(
    "权威命令文本(与驾驶舱同源;终端复制路径保持可用)。按钮执行用的就是"
    "同一组解析器取值。"
)
st.code(_cmd.command, language="bash")
if _cmd.note:
    st.caption(_cmd.note)

_manifest = _incumbent.manifest_path or ""
_runnable = (
    _incumbent.is_ensemble
    and bool(_manifest)
    and _cmd.command.startswith("python ")
    # 换库的两段 rename 不与读者并发——更新进行中不提供出单按钮,
    # 读者真空瞬间的出单会撞到暂时不存在的 live 路径(codex #440 r1)。
    and not _running_fresh
)
if not _runnable:
    if _running_fresh:
        st.warning(
            "一次数据更新正在进行——bundle 换库(两段 rename)不与读者"
            "并发,更新结束前本页不提供出单按钮。等上方状态变为"
            " finished 后再跑。"
        )
    elif not _incumbent.is_ensemble:
        st.info(
            f"本页按钮只支持 ensemble 生产形态(现任形态:{_incumbent.kind})。"
            "单模型/不可解析现任请按命令文本在终端处理,或先修好 manifest。"
        )
    else:
        st.warning(
            "现任是 ensemble,但某个参数路径无法安全渲染成命令——按钮已"
            "收起。真实原因见上方命令框下的说明;修好那条路径再回本页。"
        )
elif st.button(
    "📝 跑今日出单(同步,分钟级)",
    key="run_center::run_recommend",
    type="primary",
):
    # A new invocation must never retain a direct-review action from an older
    # successful run. The current result repopulates it only after one dated
    # recommendation artifact is published.
    clear_run_center_published_date(_SESSION_STATE)
    with st.spinner("正在子进程中运行 daily_recommend …"):
        _result = run_daily_recommend(
            ensemble_manifest=_manifest,
            provider_uri=_provider,
            delisted_registry=_registry_str,
            name_source=_name_source,
            bundle_max_age_days=_bundle_age,
        )
    if _result.kind == "ok":
        st.success(
            f"出单完成(exit 0,{_result.elapsed_s:.0f}s)。清单与 HOLD 披露"
            "到「日度信号与人工决策」页查看;**每次必读打印的 entry_date**——它是"
            "已收盘会话,不是「明早买入指令」。"
        )
        if _result.published:
            st.caption("已发布工件:" + "、".join(_result.published))
        remember_run_center_published_date(_SESSION_STATE, _result.published)
        if _result.stdout_tail:
            st.code(_result.stdout_tail)
    elif _result.kind == "blocked_by_update":
        st.warning(
            f"⏳ 出单被更新单飞锁挡下(权威判定,状态展示只是参考):"
            f"{_result.error}"
        )
    elif _result.kind == "failed":
        st.error(
            f"出单被拒/失败(exit {_result.exit_code})。本 CLI 一律 "
            "fail-loud——原因如下,修好数据再试,不存在静默错单。"
        )
        # 拒绝原因经本仓 logger 落 STDOUT(StreamHandler(sys.stdout),
        # propagate=False);stderr 多为 import 期环境噪音——顺序不能反。
        st.code(_result.stdout_tail or _result.stderr_tail or "(无输出)")
        if _result.stdout_tail and _result.stderr_tail:
            with st.expander("stderr 尾部(import 期环境噪音可能混入)"):
                st.code(_result.stderr_tail)
    else:
        st.error(f"无法运行({_result.kind}):{_result.error}")

# This stays outside the one-shot ``run_recommend`` branch. Clicking the
# action is a second Streamlit rerun, so the persisted date is what makes the
# exact successful artifact available to the callback on that second click.
_published_date = run_center_published_date(_SESSION_STATE)
if _published_date is not None and st.button(
    "查看本次日度信号",
    key="run_center::view_published_daily_signal",
):
    _SESSION_STATE[DAILY_DECISION_REQUESTED_DATE_KEY] = _published_date
    clear_run_center_published_date(_SESSION_STATE)
    st.switch_page("pages/daily_decision.py")

# ---------------------------------------------------------------------------
# ③ 看板入口
# ---------------------------------------------------------------------------
st.subheader("③ 看板")
st.markdown(
    "- **生产运维**:五问一屏(现任 / 授权门 / 年检 / 重训窗 / 数据新鲜度)\n"
    "- **数据检视**:bundle 健康 + 上次数据更新 + PIT 校验\n"
    "- **日度信号与人工决策**:最新出单工件与 HOLD 披露(非再平衡日拦下单表单)"
)
