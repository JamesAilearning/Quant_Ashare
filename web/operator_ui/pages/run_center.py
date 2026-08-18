"""运行中心 — 数据更新手动补跑 + 今日出单的 UI 触发。

驾驶舱(ops_cockpit)的承诺是「只展示不代跑」,本页承担「代跑」:两个动作
都只是触发既有 CLI 的子进程,参数与驾驶舱印出的命令同源绑定。本页自身
绝不派生进程、绝不写任何数据文件——派生只发生在两个 audited runner
(``update_runner`` / ``recommend_runner``)里,各自被 logic 测试钉死 argv。

openspec 2026-08-16-ui-run-center。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import streamlit as st

from web.operator_ui.bundle_health import resolve_default_provider_uri
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
from web.operator_ui.update_progress import FetchProgress, progress_for_run
from web.operator_ui.update_runner import (
    START_DATE,
    default_log_path,
    launch_daily_update,
    log_tail,
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


def _read_progress() -> FetchProgress | None:
    """本次运行的最后一条 fetch 进度。整页与片段**共用这一个读取器**。

    两处各写一份正是它们会分叉的方式(#442 r1/r3/r4 在这一页上连栽三次)。
    """
    log_path = default_log_path(_provider_path)
    try:
        mtime = datetime.fromtimestamp(log_path.stat().st_mtime, tz=_CN_TZ)
    except OSError:
        mtime = None
    return progress_for_run(
        log_tail(log_path), log_mtime=mtime, started_at=_status.started_at
    )


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
    if _cls == RUNNING_FRESH:
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
    _progress = _read_progress()
    if _progress is not None:
        st.caption(
            f"⏳ 最后进度(fetch 阶段):{_progress.describe()} —— 分母是**该"
            "端点该年**的票数,不是整轮进度(fetch 只是六个阶段中的一个);"
            "时间戳见下方日志尾部。"
        )
    else:
        st.caption(
            "⏳ 还没有**本次运行**的 fetch 进度行(可能尚未进入 fetch 阶段,"
            "或日志尾部那条属于上一次运行——归属不了就不显示,绝不拿旧的顶)。"
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

_watching = _running_fresh or _awaiting_launch

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
# 签名(kind/started_at/分类都不动)。只比签名的话,页面会一直冻在「还没有
# 进度行」直到运行结束(codex #450 r1)。基线同样在整页渲染时刻定格——
# 两侧都在片段里重算,正是 #442 r2 证伪过的那个错法。
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
st.caption(
    f"启动参数(镜像调度器):`--provider-dir {_provider_path}` · "
    f"`--tushare-dir {_tushare_dir}` · "
    f"`--delisted-registry {_update_registry}` · "
    f"`--reference-cases tests/pit/reference_cases.yaml` · "
    f"`--start-date {START_DATE}`"
)

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
        disabled=_running_fresh,
        use_container_width=True,
    )
if _refresh_clicked:
    st.toast(f"已重读状态工件({_read_at:%H:%M:%S})")
if _launch_clicked:
    _launch = launch_daily_update(
        _provider_path, _tushare_dir, _update_registry
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
            "到「今日推荐」页查看;**每次必读打印的 entry_date**——它是"
            "已收盘会话,不是「明早买入指令」。"
        )
        if _result.published:
            st.caption("已发布工件:" + "、".join(_result.published))
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

# ---------------------------------------------------------------------------
# ③ 看板入口
# ---------------------------------------------------------------------------
st.subheader("③ 看板")
st.markdown(
    "- **生产运维**:五问一屏(现任 / 授权门 / 年检 / 重训窗 / 数据新鲜度)\n"
    "- **数据检视**:bundle 健康 + 上次数据更新 + PIT 校验\n"
    "- **今日推荐**:最新出单工件与 HOLD 披露(非再平衡日拦下单表单)"
)
