"""「前往作业」跳转：把一次**仍在运行**的运行映射成作业页链接。

**结果页**在看一个还在跑的运行时，操作人下一步想去的地方是作业页——那里
才有活体状态（阶段进度、停止按钮、自动刷新）。本模块只回答一个可判定的
问题：*现在该不该给这个入口，给的话链接长什么样*。渲染留在页面。

**滚动验证页刻意不接这个入口**，而且不是「暂未接」——它在结构上看不到
运行中的作业：``JobManager.start`` 把 ``run_dir`` 初始化为 ``None``，
``job_runner.main`` **只在子进程成功之后**才写它，而那一页的 ``wf_jobs``
过滤掉没有 ``run_dir`` 的记录。三条合起来，一个正在跑的作业不在那一页的
任何一张表里，入口永不触发。首版接了那一页，评审指出后整体撤回；把
「两页共用」这个**已被否掉的前提**留在这里，等于邀请后来的人把那段不可达
的接线恢复回去（codex #473）。撤回的划界由一条钉住上述三条前提的测试守着
——任何一条变了，那条测试就该重新评估这个划界。

三条边界，每一条都对应本仓踩过的坑：

* **判据不自造**。「运行中」用的就是产出器写进作业记录的那个 ``status``
  词（``job_io._normalise_ui_job`` 归一之后的词汇，也是作业页状态下拉里
  的同一个值）。这里不做 strip、不做同义词扩展——多一步归一就是两套语
  义，同一个状态会在结果页判「运行中」而在这里判「不是」。
* **出发侧先过一遍作业页的守卫**。作业页把每个 URL 参数都送进
  ``_param_guard.sanitize``，不通过就**静默**落回默认值：``search`` 落回
  空串就成了「运行中的全部作业」，而操作人以为筛的是自己点开的这一次。
  所以凡是过不了那道校验的值，这里一律不画入口。
* **拿不到 id 就不画**。一个注定跳到空筛选的链接比没有入口更坏——它看起
  来像答案。

关于 handoff（``jobs.py`` 的一次性交接令牌）：本模块的链接**必须**带一个
新令牌。作业页的 ``_seed_session_from_url`` 只在「URL 值与上次消费的 URL
值不同」时才覆盖页面上的既有筛选控件；而本链接请求的 ``status=running``
恰恰是操作人上一次访问作业页时很可能已经留在 URL 里的值——那种情况下不
带令牌的链接会被**静默忽略**，跳过去看到的是他上次手选的筛选。令牌是不
带值的一次性导航标记，让这一次请求恰好压过陈旧控件状态一次。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from web.operator_ui._param_guard import sanitize as _sanitize_qp

#: 作业页在多页应用里的路径（``st.page_link`` / ``st.switch_page`` 的写法）。
JOBS_PAGE = "pages/jobs.py"

#: 作业页状态筛选里代表「运行中」的那个值。它同时是 ``_param_guard`` 白名单
#: 成员与作业页状态下拉的选项——两者由测试对着 ``jobs.py`` 源码真跑核对，
#: 而不是在这里手抄一份。
RUNNING_STATUS = "running"

#: 入口文案。放在模块里而不是页面里:这个动作将来若再有第二个来源页,
#: 两处必须叫同一个名字。当前只有结果页用它。
JUMP_LABEL = "前往作业 · 看它的运行中状态"


@dataclass(frozen=True)
class JobsJumpLink:
    """一个可以直接交给 ``st.page_link`` 的作业页链接。"""

    page: str
    label: str
    query_params: Mapping[str, str]


def running_run_jobs_link(
    *,
    run_id: object,
    status: object,
    handoff_token: str,
) -> JobsJumpLink | None:
    """运行中的运行 → 作业页链接；其余一律 ``None``（= 不画入口）。

    ``run_id`` / ``status`` 收 ``object``，因为调用方拿到的是作业记录字典
    里的原始值（``Mapping[str, Any].get``）；判定前不假设它们已经是字符串。

    ``handoff_token`` 由调用方铸造（``uuid4().hex``）。这里不铸造，是为了
    让本函数保持可判定、可复现——但令牌形状仍然要验：一个过不了作业页校
    验的令牌会被那边**静默丢弃**，链接于是退回「可能被陈旧筛选吞掉」的形
    态。那属于调用方的编码错误，所以直接抛，不是悄悄少带一个参数。
    """

    # 令牌先验，**在**判定之前：调用方的契约违约要在每一次渲染上都响，而不是
    # 潜伏到「恰好有个运行在跑」的那一天才炸——那正是本入口唯一有用的时刻。
    # ``not handoff_token`` 不能省：空串**等于**这里给的 default，只比
    # 「sanitize 后是否原样返回」的话，一个空令牌会静默通过。
    if not handoff_token or _sanitize_qp("handoff", handoff_token, default="") != handoff_token:
        raise ValueError(
            f"handoff_token={handoff_token!r} 过不了作业页的 handoff 校验："
            "它会被静默丢弃，链接将无法压过页面上的陈旧筛选。请传 uuid4().hex。"
        )
    if str(status or "").lower() != RUNNING_STATUS:
        return None
    run_id_text = str(run_id or "")
    if not run_id_text or _sanitize_qp("run_id", run_id_text, default="") != run_id_text:
        return None
    # run_id 的字符集是 search 白名单的子集，所以这一步正常永远通过；留着是
    # 因为两条白名单各自会演化，而它们一旦错开，错开的那一天就是链接静默变
    # 成空筛选的那一天。
    if _sanitize_qp("search", run_id_text, default="") != run_id_text:
        return None
    return JobsJumpLink(
        page=JOBS_PAGE,
        label=JUMP_LABEL,
        # ``search`` 而不是 ``run_id``：作业页没有 run_id 筛选，它的自由文本
        # 搜索匹配的正是 ``JobSummary.run_id``（job_io._apply_filters）。
        query_params={
            "status": RUNNING_STATUS,
            "search": run_id_text,
            "handoff": handoff_token,
        },
    )


__all__ = [
    "JOBS_PAGE",
    "JUMP_LABEL",
    "RUNNING_STATUS",
    "JobsJumpLink",
    "running_run_jobs_link",
]
