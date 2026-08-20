from __future__ import annotations

from pathlib import Path

import pytest

from web.operator_ui.decision_journal import DecisionEntry
from web.operator_ui.pages._daily_review_progress_helpers import (
    summarise_daily_review_progress,
    validate_review_candidate_codes,
)

_DATE = "2026-08-19"


def _entry(
    code: str,
    action: str = "adopt",
    *,
    trade_date: str = _DATE,
    decided_at: str = "2026-08-19T09:00:00+08:00",
    reason: str = "符合当日人工审阅条件",
) -> DecisionEntry:
    return DecisionEntry(
        journal_version=1,
        trade_date=trade_date,
        code=code,
        action=action,
        reason=reason,
        rank=1,
        score=0.01,
        model_id="model-sha",
        decided_at=decided_at,
        nonce=f"nonce-{code}-{decided_at}",
    )


def test_no_journal_records_leave_all_current_candidates_unreviewed() -> None:
    progress = summarise_daily_review_progress(_DATE, ("SH600000", "SZ000001"), {})

    assert progress.candidate_count == 2
    assert progress.reviewed_count == 0
    assert progress.unreviewed_count == 2
    assert progress.latest_reviewed_at is None
    assert all(not state.reviewed for state in progress.candidates)


def test_partial_current_records_keep_action_counts_and_latest_time() -> None:
    progress = summarise_daily_review_progress(
        _DATE,
        ("SH600000", "SZ000001", "SZ000002"),
        {
            (_DATE, "SH600000"): _entry("SH600000", "adopt"),
            (_DATE, "SZ000001"): _entry(
                "SZ000001", "watch", decided_at="2026-08-19T10:00:00+08:00"
            ),
        },
    )

    assert (progress.reviewed_count, progress.unreviewed_count) == (2, 1)
    assert (progress.adopt_count, progress.reject_count, progress.watch_count) == (1, 0, 1)
    assert progress.latest_reviewed_at == "2026-08-19T10:00:00+08:00"
    assert [state.action for state in progress.candidates] == ["adopt", "watch", None]


def test_latest_reviewed_time_uses_chronology_not_iso_string_order() -> None:
    progress = summarise_daily_review_progress(
        _DATE,
        ("SH600000", "SZ000001"),
        {
            (_DATE, "SH600000"): _entry(
                "SH600000", decided_at="20260819T090000+08:00"
            ),
            (_DATE, "SZ000001"): _entry(
                "SZ000001", decided_at="2026-08-19T10:00:00+08:00"
            ),
        },
    )

    assert progress.latest_reviewed_at == "2026-08-19T10:00:00+08:00"


def test_all_current_candidates_are_counted_once_from_the_effective_view() -> None:
    progress = summarise_daily_review_progress(
        _DATE,
        ("SH600000", "SZ000001", "SZ000002"),
        {
            (_DATE, "SH600000"): _entry("SH600000", "adopt"),
            (_DATE, "SZ000001"): _entry("SZ000001", "reject"),
            (_DATE, "SZ000002"): _entry("SZ000002", "watch"),
        },
    )

    assert progress.reviewed_count == progress.candidate_count == 3
    assert progress.unreviewed_count == 0
    assert (progress.adopt_count, progress.reject_count, progress.watch_count) == (1, 1, 1)


def test_effective_entry_is_the_current_state_after_a_candidate_correction() -> None:
    corrected = _entry(
        "SH600000", "reject", decided_at="2026-08-19T11:00:00+08:00",
        reason="复核后不采纳",
    )
    progress = summarise_daily_review_progress(
        _DATE, ("SH600000",), {(_DATE, "SH600000"): corrected}
    )

    state = progress.candidates[0]
    assert state.action == "reject"
    assert state.reason_summary == "复核后不采纳"
    assert state.decided_at == corrected.decided_at


def test_other_dates_and_unknown_codes_never_increase_current_coverage() -> None:
    progress = summarise_daily_review_progress(
        _DATE,
        ("SH600000",),
        {
            ("2026-08-18", "SH600000"): _entry("SH600000", trade_date="2026-08-18"),
            (_DATE, "SZ000001"): _entry("SZ000001"),
        },
    )

    assert progress.reviewed_count == 0
    assert not progress.candidates[0].reviewed


def test_incomplete_or_duplicate_candidate_identifiers_are_refused() -> None:
    with pytest.raises(ValueError, match="不完整"):
        summarise_daily_review_progress(_DATE, ("SH600000", ""), {})
    with pytest.raises(ValueError, match="重复"):
        summarise_daily_review_progress(_DATE, ("SH600000", "SH600000"), {})
    assert validate_review_candidate_codes(_DATE, (" SH600000 ",)) == ("SH600000",)


def test_invalid_effective_mapping_value_is_refused_instead_of_counted() -> None:
    with pytest.raises(ValueError, match="不一致"):
        summarise_daily_review_progress(
            _DATE, ("SH600000",), {(_DATE, "SH600000"): object()}  # type: ignore[dict-item]
        )


def test_effective_record_without_required_reason_is_refused() -> None:
    blank_reason = _entry("SH600000", reason="  ")

    with pytest.raises(ValueError, match="不一致"):
        summarise_daily_review_progress(
            _DATE, ("SH600000",), {(_DATE, "SH600000"): blank_reason}
        )


def test_reason_summary_does_not_replace_the_full_journal_audit_reason() -> None:
    long_reason = "a" * 90
    progress = summarise_daily_review_progress(
        _DATE, ("SH600000",), {(_DATE, "SH600000"): _entry("SH600000", reason=long_reason)}
    )

    assert progress.candidates[0].reason_summary == ("a" * 79) + "…"


def test_review_projection_stays_out_of_runtime_and_execution_layers() -> None:
    root = Path(__file__).resolve().parents[2]
    offenders = [
        path
        for path in (root / "src").rglob("*.py")
        if "daily_review_progress" in path.read_text(encoding="utf-8")
    ]

    assert offenders == []
