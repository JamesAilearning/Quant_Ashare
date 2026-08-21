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

_TRUSTED_PROVENANCE = frozenset({
    VERDICT_MATCHES_INCUMBENT,
    VERDICT_SINGLE_SHA_OK,
})
_ACTIVE_JOB_STATUSES = frozenset({"pending", "running"})
_ATTENTION_JOB_STATUSES = frozenset({
    "failed", "partial", "stop_failed", "stopped", "cancelled",
})

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
