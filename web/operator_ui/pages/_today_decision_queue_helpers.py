"""Pure, read-only queue projection for the Today Workbench."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from web.operator_ui.job_io import JobSummary
from web.operator_ui.pages._today_workbench_helpers import DailySignalSummary

_PRIORITY = {
    "blocker": 0,
    "attention": 1,
    "in_progress": 2,
    "review": 3,
    "information": 4,
}
_ATTENTION_STATUSES = frozenset(
    {"failed", "partial", "stop_failed", "stopped", "cancelled"}
)
_ACTIVE_STATUSES = frozenset({"queued", "pending", "running"})
_PAGE_BY_DESTINATION = {
    "daily_decision": "pages/daily_decision.py",
    "jobs": "pages/jobs.py",
    "run_center": "pages/run_center.py",
    "data_inspect": "pages/data_inspect.py",
    "ops_cockpit": "pages/ops_cockpit.py",
}


@dataclass(frozen=True)
class TodayQueueItem:
    """A navigation-only task derived from an already-read source."""

    kind: str
    source_key: str
    title: str
    detail: str
    source_time: str
    destination: str
    context: str | None = None


@dataclass(frozen=True)
class ReviewProgress:
    """Effective human-review coverage for one dated recommendation artifact."""

    artifact_date: str
    candidate_count: int
    reviewed_count: int
    unreviewed_count: int


def review_progress(
    artifact_date: str,
    candidate_codes: Iterable[str],
    effective_decisions: Mapping[tuple[str, str], object],
) -> ReviewProgress:
    """Measure only existing effective decisions for an exact artifact date."""
    codes = tuple(str(code).strip() for code in candidate_codes)
    if not artifact_date or any(not code for code in codes):
        raise ValueError("候选或工件日期不完整，无法计算人工审阅进度。")
    if len(set(codes)) != len(codes):
        raise ValueError("候选代码重复，无法将 journal 的按代码记录映射为审阅进度。")
    reviewed = sum((artifact_date, code) in effective_decisions for code in codes)
    return ReviewProgress(
        artifact_date=artifact_date,
        candidate_count=len(codes),
        reviewed_count=reviewed,
        unreviewed_count=len(codes) - reviewed,
    )


def _item(
    kind: str,
    source_key: str,
    title: str,
    detail: str,
    *,
    source_time: str = "",
    destination: str,
    context: str | None = None,
) -> TodayQueueItem:
    if kind not in _PRIORITY:
        raise ValueError(f"Unknown queue kind {kind!r}.")
    return TodayQueueItem(
        kind=kind,
        source_key=source_key,
        title=title,
        detail=detail,
        source_time=source_time,
        destination=destination,
        context=context,
    )


def queue_page_link(item: TodayQueueItem) -> tuple[str, Mapping[str, str] | None]:
    """Return the one read-only page link allowed for a queue item."""
    try:
        page = _PAGE_BY_DESTINATION[item.destination]
    except KeyError as exc:
        raise ValueError(f"Unsupported queue destination: {item.destination!r}") from exc
    if not item.context:
        return page, None
    if item.destination == "daily_decision":
        return page, {"as_of": item.context}
    if item.destination == "jobs":
        return page, {"status": item.context}
    return page, None


def build_today_decision_queue(
    *,
    provider_problem: str | None,
    bundle_status: str | None,
    bundle_detail: str,
    update_kind: str | None,
    update_detail: str,
    update_time: str,
    update_matches_provider: bool | None,
    update_running_class: str | None,
    signal: DailySignalSummary,
    jobs: Iterable[JobSummary],
    jobs_error: str | None,
    review: ReviewProgress | None,
    review_error: str | None,
    incumbent_kind: str,
    incumbent_detail: str,
) -> tuple[TodayQueueItem, ...]:
    """Project all visible evidence into a stably ordered task queue.

    Inputs are intentionally values, not readers: callers must perform all
    filesystem access at their existing boundary before calling this pure
    transformation.  Only identical source keys de-duplicate; two failed jobs
    are always two queue items.
    """
    items: list[TodayQueueItem] = []
    if provider_problem:
        items.append(_item(
            "blocker", "provider:configuration", "数据包需要核验", provider_problem,
            destination="data_inspect",
        ))
    elif bundle_status in {"error", "unconfigured"}:
        items.append(_item(
            "blocker", "bundle:health", "数据包不可用于人工决策", bundle_detail,
            destination="data_inspect",
        ))
    elif bundle_status == "warning":
        items.append(_item(
            "attention", "bundle:health", "数据包存在需要关注的告警", bundle_detail,
            destination="data_inspect",
        ))

    if update_matches_provider is False:
        items.append(_item(
            "blocker", "update:provider-mismatch", "数据更新来源不匹配", update_detail,
            source_time=update_time, destination="run_center",
        ))
    elif update_kind == "corrupt":
        items.append(_item(
            "blocker", "update:corrupt", "数据更新状态需要核验", update_detail,
            source_time=update_time, destination="run_center",
        ))
    elif update_kind == "running":
        if update_running_class == "fresh":
            items.append(_item(
                "in_progress", "update:running", "数据更新正在进行", update_detail,
                source_time=update_time, destination="run_center",
            ))
        else:
            items.append(_item(
                "attention", "update:running", "数据更新状态需要核验", update_detail,
                source_time=update_time, destination="run_center",
            ))
    elif update_kind == "finished" and update_detail:
        # The caller passes failed-only detail for terminal failures; a
        # successful historical update belongs in the existing summary card.
        items.append(_item(
            "attention", "update:failed", "数据更新失败", update_detail,
            source_time=update_time, destination="run_center",
        ))

    if signal.kind == "missing":
        items.append(_item(
            "blocker", "signal:missing", "缺少日度信号工件", signal.detail,
            destination="run_center",
        ))
    elif signal.kind == "needs_verification":
        items.append(_item(
            "blocker", "signal:verification", "日度信号需要核验", signal.detail,
            source_time=signal.as_of_date or "", destination="daily_decision",
            context=signal.as_of_date,
        ))

    if jobs_error:
        items.append(_item(
            "blocker", "jobs:verification", "作业目录需要核验", jobs_error,
            destination="jobs",
        ))
    else:
        for job in jobs:
            timestamp = job.finished_at or job.started_at or job.created_at
            if job.status in _ATTENTION_STATUSES:
                detail = job.error_message or job.key_metric_value or job.status
                # ``cancelled`` is a legacy catalog value that the Jobs page
                # deliberately does not expose as a selectable URL filter.
                # Do not attach a context that its validator would silently
                # discard; the unfiltered list is the honest destination.
                status_context = None if job.status == "cancelled" else job.status
                items.append(_item(
                    "attention", f"job:{job.run_id}", f"作业需要关注：{job.type}", detail,
                    source_time=timestamp, destination="jobs", context=status_context,
                ))
            elif job.status in _ACTIVE_STATUSES:
                detail = job.key_metric_value or job.status
                items.append(_item(
                    "in_progress", f"job:{job.run_id}", f"作业进行中：{job.type}", detail,
                    source_time=timestamp, destination="jobs", context=job.status,
                ))

    if review_error:
        items.append(_item(
            "blocker", "review:verification", "人工审阅进度需要核验", review_error,
            source_time=signal.as_of_date or "", destination="daily_decision",
            context=signal.as_of_date,
        ))
    elif review is not None and review.unreviewed_count:
        items.append(_item(
            "review", f"review:{review.artifact_date}", "可完成人工审阅",
            f"{review.unreviewed_count}/{review.candidate_count} 个候选尚无有效人工记录。",
            source_time=review.artifact_date, destination="daily_decision",
            context=review.artifact_date,
        ))
    elif (
        signal.kind in {"daily", "rebalance"}
        and review is not None
        and review.candidate_count
    ):
        items.append(_item(
            "information", f"review:{review.artifact_date}", "当日候选已完成记录核对",
            f"{review.reviewed_count}/{review.candidate_count} 个候选已有有效人工记录；不代表交易已执行。",
            source_time=review.artifact_date, destination="daily_decision",
            context=review.artifact_date,
        ))

    items.append(_item(
        "information", "serving:identity", "服务身份仅供核对", incumbent_detail,
        destination="ops_cockpit", context=incumbent_kind,
    ))
    unique: dict[str, TodayQueueItem] = {}
    for item in items:
        current = unique.get(item.source_key)
        if current is None or item.source_time >= current.source_time:
            unique[item.source_key] = item
    by_key = sorted(unique.values(), key=lambda item: item.source_key)
    by_time = sorted(by_key, key=lambda item: item.source_time, reverse=True)
    return tuple(sorted(by_time, key=lambda item: _PRIORITY[item.kind]))


def queue_counts(items: Iterable[TodayQueueItem]) -> Mapping[str, int]:
    """Return explicit counts for every queue class, including zeroes."""
    counts = {kind: 0 for kind in _PRIORITY}
    for item in items:
        counts[item.kind] += 1
    return counts


__all__ = [
    "ReviewProgress",
    "TodayQueueItem",
    "build_today_decision_queue",
    "queue_page_link",
    "queue_counts",
    "review_progress",
]
