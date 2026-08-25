"""Pure classification helpers for the read-only Today Workbench."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from web.operator_ui.incumbent import IncumbentIdentity
from web.operator_ui.job_io import JobSummary
from web.operator_ui.pages._daily_decision_helpers import (
    SUPPORTED_DAILY_RECOMMENDATION_ARTIFACT_SCHEMA_VERSION,
    VERDICT_MATCHES_INCUMBENT,
    VERDICT_SINGLE_SHA_OK,
    artifact_entry_timing_is_valid,
    artifact_meta_status,
    hold_state,
    picks_table_rows,
    provenance_verdict,
)
from web.operator_ui.pages._ops_cockpit_helpers import RetrainWindow
from web.operator_ui.update_status import NO_REASON_MARK, UpdateRunStatus

_TRUSTED_PROVENANCE = frozenset({
    VERDICT_MATCHES_INCUMBENT,
    VERDICT_SINGLE_SHA_OK,
})
_ACTIVE_JOB_STATUSES = frozenset({"pending", "running"})
_ATTENTION_JOB_STATUSES = frozenset({
    "failed", "partial", "stop_failed", "stopped", "cancelled",
})



def failed_update_summary(status: UpdateRunStatus) -> str:
    """失败运行的一行说明：退出码含义、死在哪个阶段、**为什么**。

    失败卡片与今日待办队列都从这里取,不各写一份:它们此前是两段手写的同义
    字符串,而唯一能让操作人动手的正是「为什么」那一段——两处分头演化,迟早
    有一处漏掉它。

    `detail` 空时**明说记录里没有原因**,不留白:留白读起来像「没有更多可说」,
    而真相是「这次运行没把原因写下来」,两者对操作人的下一步完全不同。

    而 `detail` **非空也不等于有原因**:阶段一条 ERROR 都没记时,写入侧存的是
    退出码摘要本身(例如 `fetch failed hard (exit 1)`)并附上一个标记。把它当成
    原因渲染成「原因:fetch failed hard (exit 1)」,是把「只有退出码」伪装成一条
    解释——比不说更糟(codex #462)。所以这里认那个标记,只在真有捕获内容时才用
    「原因」二字。
    """
    reason = (status.detail or "").strip()
    head = f"{status.exit_meaning}；失败阶段：{status.failed_stage or '未记录'}。"
    if not reason:
        return head + "状态记录未写下原因；请在运行中心查看日志。"
    if reason.endswith(NO_REASON_MARK):
        return head + reason[: -len(NO_REASON_MARK)].strip() + \
            "。该阶段未在日志中留下原因；请在运行中心查看日志。"
    return head + f"原因：{reason}"


@dataclass(frozen=True)
class DailySignalSummary:
    """A display-ready, provenance-aware daily signal summary."""

    kind: str
    detail: str
    as_of_date: str | None = None
    entry_date: str | None = None
    next_rebalance_date: str | None = None


@dataclass(frozen=True)
class OperationalSummary:
    """The single operational state a workbench should surface first."""

    kind: str
    detail: str
    job: JobSummary | None = None


def summarise_daily_signal(
    artifact_date: str | None,
    payload: object | None,
    *,
    incumbent: IncumbentIdentity,
    current_model_sha: str | None,
    read_error: str | None = None,
) -> DailySignalSummary:
    """Classify one artifact without promoting an unverified signal.

    This is intentionally stricter than filename ordering: the latest file is
    meaningful only after its payload date, v2 shape, and current-incumbent
    provenance have all been confirmed by the shared daily-decision helpers.
    """

    if artifact_date is None:
        return DailySignalSummary("missing", "尚无日度信号工件。")
    if read_error:
        return DailySignalSummary("needs_verification", f"工件不可读：{read_error}")
    if not isinstance(payload, dict):
        return DailySignalSummary("needs_verification", "工件顶层不是 JSON object。")

    as_of_date = payload.get("as_of_date")
    if not isinstance(as_of_date, str) or as_of_date != artifact_date:
        return DailySignalSummary(
            "needs_verification",
            "文件名日期与工件 as_of_date 不一致，无法确认信号归属。",
        )
    entry_date = payload.get("entry_date")
    if not isinstance(entry_date, str) or not entry_date.strip():
        return DailySignalSummary(
            "needs_verification",
            "工件缺少有效 entry_date，无法确认信号时点。",
            as_of_date=as_of_date,
        )
    if not artifact_entry_timing_is_valid(payload):
        return DailySignalSummary(
            "needs_verification",
            "工件 entry_date 必须是晚于 as_of_date 的严格 ISO 日期，无法确认信号时点。",
            as_of_date=as_of_date,
            entry_date=entry_date,
        )

    schema_version = payload.get("artifact_schema_version")
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != SUPPORTED_DAILY_RECOMMENDATION_ARTIFACT_SCHEMA_VERSION
    ):
        return DailySignalSummary(
            "needs_verification",
            "工件 schema 版本缺失、格式错误或不受当前工作台支持。",
            as_of_date=as_of_date,
            entry_date=entry_date,
        )

    meta_status = artifact_meta_status(payload, current_model_sha)
    if meta_status.artifact_is_corrupt_v2:
        return DailySignalSummary(
            "needs_verification",
            "带版本标记的工件缺少有效 meta，工件可能损坏。",
            as_of_date=as_of_date,
            entry_date=entry_date,
        )
    verdict = provenance_verdict(incumbent, meta_status)
    if verdict not in _TRUSTED_PROVENANCE:
        return DailySignalSummary(
            "needs_verification",
            f"工件来源无法与现任模型确认（{verdict}）。",
            as_of_date=as_of_date,
            entry_date=entry_date,
        )

    try:
        # The detailed page treats a missing/non-list picks value, or a
        # non-object member, as a corrupt producer artifact. The workbench
        # must use that same boundary before presenting this file as current.
        picks_table_rows(payload)
    except ValueError as exc:
        return DailySignalSummary(
            "needs_verification",
            f"工件候选列表不合法：{exc}",
            as_of_date=as_of_date,
            entry_date=entry_date,
        )

    cadence = hold_state(payload)
    if cadence.malformed is not None:
        return DailySignalSummary(
            "needs_verification",
            cadence.malformed,
            as_of_date=as_of_date,
            entry_date=entry_date,
        )
    if cadence.is_hold:
        return DailySignalSummary(
            "hold",
            "非再平衡日的监控视图，不构成入场指令。",
            as_of_date=as_of_date,
            entry_date=entry_date,
            next_rebalance_date=cadence.next_rebalance_date,
        )
    if "rebalance_day" in payload:
        return DailySignalSummary(
            "rebalance",
            "再平衡日信号，仍须在详情页完成人工核对。",
            as_of_date=as_of_date,
            entry_date=entry_date,
        )
    return DailySignalSummary(
        "daily",
        "日频工件，仍须在详情页完成人工核对。",
        as_of_date=as_of_date,
        entry_date=entry_date,
    )


def summarise_operations(jobs: Iterable[JobSummary]) -> OperationalSummary:
    """Prioritise running work, then the latest exceptional terminal job."""

    rows = tuple(jobs)
    active = [job for job in rows if job.status in _ACTIVE_JOB_STATUSES]
    if active:
        job = max(active, key=_job_timestamp)
        detail = job.key_metric_value or (
            "等待启动" if job.status == "pending" else "运行中"
        )
        return OperationalSummary(job.status, f"{job.type}：{detail}", job)

    attention = [job for job in rows if job.status in _ATTENTION_JOB_STATUSES]
    if attention:
        job = max(attention, key=_job_timestamp)
        detail = job.error_message or job.key_metric_value or job.status
        return OperationalSummary("attention", f"{job.type}：{detail}", job)
    return OperationalSummary("idle", "没有正在运行或需要处理的作业。")


def _job_timestamp(job: JobSummary) -> tuple[str, str]:
    return (job.finished_at or job.started_at or job.created_at, job.run_id)


__all__ = [
    "DailySignalSummary",
    "OperationalSummary",
    "SUPPORTED_DAILY_RECOMMENDATION_ARTIFACT_SCHEMA_VERSION",
    "summarise_daily_signal",
    "summarise_operations",
]

def model_age_rows(window: RetrainWindow) -> list[tuple[str, str]]:
    """身份卡上的模型时效行——数据**照抄**生产运维页⑤的 `retrain_window`。

    P3 缺口（UI 评估已批序列③）：工作台身份卡只报形态不报年龄，「模型多旧」
    要跳页才知道。此处不自造任何判定：同一个推导函数、同一套字段，只做措辞
    （生产运维页与本页共用一份实现的既定纪律——#461 首版另写一份三个决策
    全错的教训在档）。`known=False` 时如实说推导不了，不留空。

    窗口行必须在**可见文案**里自报「推导」身份（codex P1）：仓库没有机器
    可读的「下次重训到期日」，这个窗口是从 serving 间距 pin 推导出来的——
    驾驶舱④的披露契约（cockpit 模块头「labelled as DERIVED」）跟着窗口走
    到每一处展示，docstring 不渲染、不算数。
    """
    if not getattr(window, "known", False):
        return [("模型时效", "无法推导（现任非可解析 ensemble）")]
    state_text = {
        "before": "未开",
        "open": "开放中",
        "closed": "已过（fit_end 须落窗内；点火按操作卡排期）",
    }.get(str(getattr(window, "state", "")), str(getattr(window, "state", "")))
    return [
        ("fit 至", str(window.newest_fit_end)),
        ("模型年龄", f"{window.days_since_newest} 天"),
        (
            "下一成员 fit_end 窗（推导）",
            f"{window.opens_on}~{window.closes_on}（{state_text}；由 serving "
            f"间距 pin [{window.spacing_min},{window.spacing_max}] 天推导，"
            "仓库无机器可读的重训到期锚）",
        ),
    ]
