from __future__ import annotations

import pytest

from web.operator_ui.job_io import JobSummary
from web.operator_ui.pages._today_decision_queue_helpers import (
    build_today_decision_queue,
    queue_counts,
    queue_page_link,
    review_progress,
)
from web.operator_ui.pages._today_workbench_helpers import DailySignalSummary


def _job(run_id: str, status: str, *, finished_at: str = "") -> JobSummary:
    return JobSummary(
        run_id=run_id,
        type="pipeline",
        status=status,
        source="ui",
        finished_at=finished_at,
        error_message=f"{run_id} failed" if status == "failed" else "",
    )


def _queue(**overrides):
    values = {
        "provider_problem": None,
        "bundle_status": "ok",
        "bundle_detail": "bundle healthy",
        "update_kind": "missing",
        "update_detail": "",
        "update_time": "",
        "update_matches_provider": None,
        "update_running_class": None,
        "signal": DailySignalSummary("daily", "valid", as_of_date="2026-08-19"),
        "jobs": (),
        "jobs_error": None,
        "review": None,
        "review_error": None,
        "incumbent_kind": "single",
        "incumbent_detail": "serving identity only",
    }
    values.update(overrides)
    return build_today_decision_queue(**values)


def test_multiple_distinct_exceptions_are_not_hidden_by_higher_priority_signal_blocker() -> None:
    items = _queue(
        signal=DailySignalSummary("needs_verification", "picks invalid", as_of_date="2026-08-19"),
        jobs=(
            _job("failed-a", "failed", finished_at="2026-08-19T08:00:00+08:00"),
            _job("failed-b", "failed", finished_at="2026-08-19T09:00:00+08:00"),
        ),
    )

    assert [item.source_key for item in items] == [
        "signal:verification", "job:failed-b", "job:failed-a", "serving:identity",
    ]
    assert queue_counts(items) == {
        "blocker": 1, "attention": 2, "in_progress": 0, "review": 0, "information": 1,
    }


def test_same_source_key_deduplicates_but_distinct_jobs_remain_visible() -> None:
    items = _queue(jobs=(
        _job("same", "failed", finished_at="2026-08-19T10:00:00+08:00"),
        _job("same", "failed", finished_at="2026-08-19T11:00:00+08:00"),
        _job("other", "failed", finished_at="2026-08-19T09:00:00+08:00"),
    ))

    assert [item.source_key for item in items if item.source_key.startswith("job:")] == [
        "job:same", "job:other",
    ]


def test_valid_signal_with_unreviewed_candidates_creates_dated_review_navigation() -> None:
    progress = review_progress(
        "2026-08-19", ("SH600000", "SZ000001"), {("2026-08-19", "SH600000"): object()},
    )
    items = _queue(review=progress)

    review = next(item for item in items if item.kind == "review")
    assert review.context == "2026-08-19"
    assert review.destination == "daily_decision"
    assert "1/2" in review.detail
    assert queue_page_link(review) == (
        "pages/daily_decision.py", {"as_of": "2026-08-19"}
    )


def test_journal_or_candidate_shape_problem_blocks_fake_zero_review_count() -> None:
    items = _queue(review_error="决策日志读取失败")

    assert any(item.source_key == "review:verification" and item.kind == "blocker" for item in items)
    assert not any(item.kind == "review" for item in items)
    with pytest.raises(ValueError, match="重复"):
        review_progress("2026-08-19", ("SH600000", "SH600000"), {})


def test_malformed_job_catalog_blocks_a_partial_catalog_from_looking_healthy() -> None:
    items = _queue(jobs_error="作业目录含 1 行损坏的 CLI 索引记录。")

    assert any(
        item.source_key == "jobs:verification" and item.kind == "blocker"
        for item in items
    )


def test_stable_order_uses_newest_timestamp_within_same_queue_kind() -> None:
    items = _queue(jobs=(
        _job("old", "running", finished_at="2026-08-19T08:00:00+08:00"),
        _job("new", "running", finished_at="2026-08-19T09:00:00+08:00"),
    ))

    assert [item.source_key for item in items if item.kind == "in_progress"] == ["job:new", "job:old"]


def test_queue_normalises_aware_timestamps_before_ordering() -> None:
    items = _queue(
        update_kind="running",
        update_detail="update in progress",
        update_time="2026-08-19T09:00:00+08:00",
        update_running_class="fresh",
        jobs=(_job("later-utc", "running", finished_at="2026-08-19T02:00:00+00:00"),),
    )

    assert [item.source_key for item in items if item.kind == "in_progress"] == [
        "job:later-utc", "update:running",
    ]


def test_exception_link_keeps_its_real_filter_status() -> None:
    items = _queue(jobs=(_job("partial", "partial"),))

    job = next(item for item in items if item.source_key == "job:partial")
    assert queue_page_link(job) == ("pages/jobs.py", {"status": "partial"})


def test_provider_mismatch_remains_a_blocker_even_when_update_is_terminal() -> None:
    items = _queue(
        update_kind="finished",
        update_detail="状态工件属于另一个 provider。",
        update_matches_provider=False,
    )

    mismatch = next(item for item in items if item.source_key == "update:provider-mismatch")
    assert mismatch.kind == "blocker"
    assert mismatch.title == "数据更新来源不匹配"


def test_duplicate_source_key_keeps_its_newest_evidence_regardless_of_input_order() -> None:
    items = _queue(jobs=(
        _job("same", "failed", finished_at="2026-08-19T11:00:00+08:00"),
        _job("same", "failed", finished_at="2026-08-19T10:00:00+08:00"),
    ))

    job = next(item for item in items if item.source_key == "job:same")
    assert job.source_time == "2026-08-19T11:00:00+08:00"
