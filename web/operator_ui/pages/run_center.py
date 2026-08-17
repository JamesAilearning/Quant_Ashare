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
from web.operator_ui.recommend_runner import run_daily_recommend
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

# 运行中的更新每这么久自动重读一次状态工件(仅 running 期间轮询;
# 非运行态没有会变的东西)。读一个小 JSON,成本可忽略。
_POLL_SECONDS = 30
_CN_TZ = timezone(timedelta(hours=8))


def _status_signature(status: object) -> tuple[str, str, str]:
    """状态里「值得触发整页重绘」的部分。

    只取三样:kind/started_at/finished_at。运行中反复重读得到的是同一个
    签名,只有真正的状态跃迁(running→finished、或换了一次运行)才会变。
    """
    return (
        str(getattr(status, "kind", "")),
        str(getattr(status, "started_at", "")),
        str(getattr(status, "finished_at", "")),
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
_running_fresh = (
    _status.kind == "running"
    and record_matches_provider(_status, _provider_path)
    and classify_running(_status) == RUNNING_FRESH
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
    _cls = classify_running(_status)
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

# 「我刚点的刷新到底生效了没」——状态没变时整页重绘长得一模一样,读取
# 时刻是唯一能证明重读发生过的痕迹(#440 后续:操作人反馈按钮像坏的)。
_poll_note = (
    f";更新进行中,每 {_POLL_SECONDS} 秒自动重读,完成时整页刷新"
    if _running_fresh
    else ""
)
st.caption(
    f"上次读取:{_read_at:%H:%M:%S}(点任意按钮或刷新页面都会重读{_poll_note})"
)

if _running_fresh:
    @st.fragment(run_every=_POLL_SECONDS)
    def _watch_update_completion() -> None:
        """轮询状态工件,状态跃迁时把整页拉起来重绘。

        只在 fragment 内重读,不渲染任何东西:运行中反复读到同一签名 =
        静默继续。一旦 running→finished(或换了一次运行),整页 rerun,
        下方出单按钮的闸门(依赖主脚本作用域的 ``_running_fresh``)才能
        跟着解锁——只刷新片段会让两处显示自相矛盾。
        """
        if _status_signature(read_update_status(_status_path)) != _status_signature(
            _status
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
    if _launch.kind == "launched":
        st.success(
            f"已在后台启动(pid {_launch.pid}),日志:`{_launch.log_path}`。"
            "**启动≠成功**——成败以上方状态(点「刷新状态」)与日志为准。"
        )
    else:
        st.error(f"未启动({_launch.kind}):{_launch.error}")

with st.expander("日志尾部(只读)"):
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
