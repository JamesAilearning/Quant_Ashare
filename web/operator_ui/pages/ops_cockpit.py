"""生产运维 — 只读驾驶舱（现任身份 / 门 / 认证 / 重训窗 / 数据新鲜度）。

Renders READ-ONLY facts about the running production stack and hands the
operator the commands to act on them. It never runs one: no job launch, no
training, no rotation, no inference, no writes of any kind. Every command on
this page is text to copy into a terminal, because the acts it describes —
replacing the live bundle, rewriting the production manifest — are the
operator's to authorize, not a page's to trigger on a click.

Boundary reminders (machine-enforced by tests/logic):
* Zero write-side APIs; zero job/training surfaces.
* A fact that cannot be established is shown as unknown WITH the reason —
  never as a default, a placeholder, or a stale previous answer.
* The retrain window is labelled as DERIVED from the serving spacing pin;
  this repository holds no machine-readable retrain due date.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from web.operator_ui.bundle_health import (
    resolve_default_provider_uri,
    summarise_bundle_health,
)
from web.operator_ui.formatting import cn_today
from web.operator_ui.incumbent import resolve_incumbent, resolve_model_path
from web.operator_ui.page_header import render_page_header
from web.operator_ui.pages._ops_cockpit_helpers import (
    OpsCommand,
    bundle_freshness,
    data_update_command,
    morning_command,
    read_gate_cards,
    resolve_delisted_registry,
    resolve_namechange_path,
    retrain_window,
    rotation_commands,
)
from web.operator_ui.recert_health import probe_recert_health

render_page_header(
    "生产运维",
    "只读检视生产栈的五项状态 + 给出对应的运维命令文本。"
    "本页不执行任何命令、不触发作业/训练/轮换、不写任何文件——命令请自行在终端执行。",
)


def _render_command(cmd: OpsCommand) -> None:
    """Show a command as copyable text. Irreversible ones say so first."""
    if cmd.irreversible:
        st.warning(f"⚠ **不可逆**:{cmd.title}")
    else:
        st.caption(cmd.title)
    st.code(cmd.command, language="bash")
    if cmd.note:
        st.caption(cmd.note)


# ---------------------------------------------------------------------------
# ① 现任身份 — 与今日推荐页共用同一个解析器(两页不可能给出不同答案)
# ---------------------------------------------------------------------------
st.subheader("① 现任生产模型")
_incumbent = resolve_incumbent()
# Resolved once, up front: section ④'s gate commands must name the SAME
# bundle section ⑤ reports on — two resolutions could disagree.
_provider = resolve_default_provider_uri()
if _incumbent.kind == "unresolvable":
    st.error(
        "⚠ 现任 manifest 无法解析(本页绝不退回单模型形态顶替):"
        f"`{_incumbent.manifest_path}` — {_incumbent.error}。"
        "在此之前,下方所有与现任相关的判断都无法成立。"
    )
elif _incumbent.kind == "single":
    st.info(
        "ℹ 现任为**单模型形态**(QUANT_ENSEMBLE_MANIFEST 显式设为 `none` 的 opt-out)。"
        "本页的 ensemble 门/轮换/重训窗对该形态不适用。"
    )
else:
    st.success(
        f"✅ ensemble — `{str(_incumbent.manifest_path)}`\n\n"
        f"manifest sha256 `{str(_incumbent.manifest_sha256 or '')[:16]}…`,"
        f"{len(_incumbent.members)} 名成员"
    )
    _cols = st.columns(len(_incumbent.members) or 1)
    for _col, _mem in zip(_cols, _incumbent.members, strict=False):
        with _col:
            st.caption("成员 fit 窗")
            st.markdown(f"**{_mem['fit_start']} ~ {_mem['fit_end']}**")
_render_command(morning_command(
    _incumbent, model_path=resolve_model_path()))

# ---------------------------------------------------------------------------
# ② 授权门工件 — 权威是入库 baseline 的摘要,页面只做转录
# ---------------------------------------------------------------------------
st.markdown("---")
st.subheader("② 授权门工件（3 份 member + 1 份 ensemble）")
st.caption(
    "规范层是**五道具名门**:member 作用域 2 道(trainer_integrity / ic_direction)、"
    "ensemble 作用域 3 道(degeneracy / constraint_dry_run / serving_veto);"
    "授权 2026-08-05 切换的是**四份工件**。下方按工件分卡,卡内展开其具名门。"
    "每份工件先用入库基线记录的 sha256 校验内容,校验不过即不显示其自称的结论。"
)
_cards, _gate_fatal = read_gate_cards()
if _gate_fatal is not None:
    st.error(f"⚠ 无法建立门的授权来源:{_gate_fatal}")
elif not _cards:
    st.error("⚠ 授权基线里没有可核对的门工件条目。")
else:
    for _card in _cards:
        if not _card.evidence_intact:
            st.error(
                f"⚠ **{_card.key} — 证据链断裂**:{_card.error}。"
                f"授权摘要 `{_card.authorized_sha256[:16]}…`"
                f"(基线记录路径 `{_card.authorized_path}`)。"
                "该工件自称的结论**不予显示**——无法与授权绑定的文件不是证据。"
            )
            continue
        _tight: list[str] = []
        for _g in _card.gates:
            _tight.extend(
                f"{_g.name}.{_m.name} = {_m.value:g}(上限 {_m.limit:g},"
                f"余量 {_m.margin:.4g})"
                for _m in _g.metrics if _m.is_tight
            )
        _head = f"**{_card.key}** — overall `{_card.overall}`"
        if _card.missing_gates:
            st.error(
                f"⚠ {_head}，但**缺门**:{'、'.join(_card.missing_gates)}。"
                "缺门的工件不构成通过。"
            )
        elif _tight:
            st.warning(f"⚠ {_head}(通过,但有贴边指标):{'；'.join(_tight)}")
        else:
            st.success(f"✅ {_head}")
        with st.expander(f"{_card.key} 逐门明细", expanded=False):
            st.caption(
                f"scope `{_card.scope}` · 读取自 `{_card.resolved_path}` · "
                f"内容摘要与基线授权一致(`{_card.authorized_sha256[:16]}…`)"
            )
            for _g in _card.gates:
                st.markdown(f"**{_g.name}** — `{_g.verdict}`")
                for _m in _g.metrics:
                    _cmp = "<" if _m.exclusive else "≤"
                    st.caption(
                        f"　{_m.name} = {_m.value:g} （判据 {_cmp} {_m.limit:g}，"
                        f"余量 {_m.margin:.4g}）"
                        + ("　← 贴边" if _m.is_tight else "")
                    )
                for _r in _g.reasons:
                    st.caption(f"　reason: {_r}")
                for _n in _g.notes:
                    st.caption(f"　note: {_n}")

# ---------------------------------------------------------------------------
# ③ 年度再认证与 15 个月有效期 — 真跑执行器自己的判定
# ---------------------------------------------------------------------------
st.markdown("---")
st.subheader("③ 再认证状态与有效期")
# No clock argument: the probe defaults to the executor's own UTC
# instant, so this page cannot disagree with the machine decision
# it transcribes (codex #431 r2).
_recert = probe_recert_health()
if not _recert.known:
    st.error(
        f"⚠ **无法判定**再认证状态:{_recert.reason}。"
        "本页不显示上一次的结果、也不默认为有效——认证状态必须来自机器校验。"
    )
elif _recert.is_frozen:
    st.error(
        f"⛔ 认证 `{_recert.verdict}` — **季度轮换已冻结**:{_recert.reason}"
    )
else:
    st.success(f"✅ 认证 `{_recert.verdict}` — {_recert.reason}")
if _recert.pinned_rev:
    st.caption(
        f"锚 = `{_recert.status_path}` 在主线的 tip commit committer 日期"
        f"（{_recert.status_tip_iso}），有效期 {_recert.validity_months} 个月。"
        f"本次读取所 pin 的主线 rev = `{_recert.pinned_rev[:12]}…`"
        "（正文与日期取自同一个 rev）。若本机 origin/main 未及时 fetch，"
        "这里显示的就是旧主线的状态——请以该 rev 自行判断新鲜度。"
    )

# ---------------------------------------------------------------------------
# ④ 下一名成员的可接受 fit_end 窗口 —— 推导,不是仓库里的到期日
# ---------------------------------------------------------------------------
st.markdown("---")
st.subheader("④ 季度重训窗口")
_window = retrain_window(_incumbent, cn_today())
st.caption(
    "本仓库**没有**「下次重训到期日」这样的机器可读锚（规格里只有散文「每季度末」）。"
    "下面的窗口是由 serving 校验器自己的成员间距硬 pin "
    f"`[{_window.spacing_min}, {_window.spacing_max}]` 天**推导**出来的——"
    "它就是轮换产出的新 manifest 将被真实校验的那条规则。"
)
if not _window.known:
    st.warning(f"⚠ 无法推导重训窗口:{_window.error}")
else:
    _w1, _w2, _w3 = st.columns(3)
    with _w1:
        st.caption("最新成员 fit_end")
        st.markdown(f"**{_window.newest_fit_end}**")
        st.caption(f"{_window.days_since_newest} 天前")
    with _w2:
        st.caption("下一成员 fit_end 可接受窗口（推导）")
        st.markdown(f"**{_window.opens_on} ~ {_window.closes_on}**")
    with _w3:
        st.caption("窗口状态")
        if _window.state == "closed":
            st.markdown(f"**已关闭 {_window.days_closed} 天**")
        elif _window.state == "before":
            st.markdown(f"**{_window.days_until_open} 天后开启**")
        else:
            st.markdown("**开放中**")
    if _window.refused_if_fit_today:
        st.error(
            f"⛔ 用**今天**的数据训一名新成员：与最新成员的 fit_end 间距为 "
            f"{_window.gap_if_fit_today} 天，超出 "
            f"`[{_window.spacing_min}, {_window.spacing_max}]`——"
            "`load_ensemble_manifest` 会拒绝，轮换产出的 manifest 加载不了。"
            f"新成员的 fit_end 必须落在 **{_window.opens_on} ~ {_window.closes_on}** 内"
            "（即刻意不使用最近这段数据）。"
        )
    else:
        st.success(
            f"✅ 用今天的数据训，间距 {_window.gap_if_fit_today} 天，落在硬 pin 内。"
        )

st.markdown("**季度重训操作卡**（前提：上方 ③ 认证有效；执行器会机器校验）")
for _cmd in rotation_commands(
    str(_incumbent.manifest_path) if _incumbent.is_ensemble else None,
    # Both gate scopes read these; retrain_gate.py has hardcoded defaults and
    # the gate artifact records neither, so an omitted flag can produce a PASS
    # on a different bundle than the one shown above (codex #431 r2).
    provider_uri=_provider,
    namechange_path=resolve_namechange_path(),
):
    _render_command(_cmd)

# ---------------------------------------------------------------------------
# ⑤ 数据 bundle 新鲜度 — 阈值取自出单侧配置,不另立一个
# ---------------------------------------------------------------------------
st.markdown("---")
st.subheader("⑤ 数据 bundle 新鲜度")
_summary: Any = summarise_bundle_health(_provider)
_fresh = bundle_freshness(
    # No clock argument: defaults to the RECOMMENDER's own (host-local)
    # today, so this refusal prediction cannot disagree with the machine
    # that actually refuses (codex #431 r3).
    tail_date=_summary.tail_date,
    provider_uri=_summary.provider_uri,
    message=_summary.message,
)
if not _fresh.known:
    st.error(f"⚠ 无法判定 bundle 新鲜度:{_fresh.message}")
else:
    _b1, _b2, _b3 = st.columns(3)
    with _b1:
        st.caption("bundle 尾部交易日")
        st.markdown(f"**{_fresh.tail_date}**")
    with _b2:
        st.caption("落后（自然日）")
        st.markdown(f"**{_fresh.days_behind} 天**")
    with _b3:
        st.caption(f"距出单拒绝阈值（{_fresh.max_age_days} 天）")
        st.markdown(f"**余 {_fresh.headroom_days} 天**")
    if _fresh.refuses_today:
        st.error(
            f"⛔ 落后 {_fresh.days_behind} 天 > 阈值 {_fresh.max_age_days} 天——"
            "今天跑晨跑出单会被 fail-loud 拒绝。请先更新数据。"
        )
    elif (_fresh.headroom_days or 0) <= 3:
        st.warning(
            f"⚠ 只剩 {_fresh.headroom_days} 天余量,再不更新就会触发出单拒绝。"
        )
    else:
        st.success(f"✅ 落后 {_fresh.days_behind} 天,在阈值内。")
st.caption(
    f"provider = `{_fresh.provider_uri}`。尾部日期取自 **provider 元数据的 "
    "coverage_end_date**（bundle_health 读取路径）；出单侧另有一条路径读 "
    "qlib 运行时 `calendar[-1]`——两者通常一致，但不是同一个读取器。"
    f"拒绝阈值 {_fresh.max_age_days} 天取自 RecommendationConfig,非本页字面量。"
)
_render_command(data_update_command(
    provider_uri=str(_fresh.provider_uri or _provider),
    delisted_registry=resolve_delisted_registry()))
