"""Pure human-review projection for one dated daily recommendation artifact."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

from web.operator_ui.decision_journal import ACTIONS, DecisionEntry, _parse_decided_at


@dataclass(frozen=True)
class CandidateReviewState:
    """The effective human-review record for one current candidate, if any."""

    code: str
    action: str | None
    reason_summary: str | None
    decided_at: str | None

    @property
    def reviewed(self) -> bool:
        return self.action is not None


@dataclass(frozen=True)
class DailyReviewProgress:
    """Read-only coverage of the current candidate set for one trade date."""

    trade_date: str
    candidates: tuple[CandidateReviewState, ...]
    candidate_count: int
    reviewed_count: int
    unreviewed_count: int
    adopt_count: int
    reject_count: int
    watch_count: int
    latest_reviewed_at: str | None


def _reason_summary(reason: str, *, limit: int = 80) -> str:
    """Collapse display whitespace without changing the full audit reason."""
    compact = " ".join(reason.split())
    return compact if len(compact) <= limit else f"{compact[:limit - 1]}…"


def validate_review_candidate_codes(
    trade_date: str, candidate_codes: Iterable[str],
) -> tuple[str, ...]:
    """Normalize exact journal keys or reject an ambiguous candidate set."""
    codes = tuple(str(code).strip() for code in candidate_codes)
    if not trade_date or any(not code for code in codes):
        raise ValueError("候选代码或交易日不完整，无法计算人工审阅进度。")
    if len(set(codes)) != len(codes):
        raise ValueError("候选代码重复，无法将 journal 记录唯一映射为审阅进度。")
    return codes


def summarise_daily_review_progress(
    trade_date: str,
    candidate_codes: Iterable[str],
    effective_decisions: Mapping[tuple[str, str], DecisionEntry],
) -> DailyReviewProgress:
    """Match exact current candidates to the journal's already-effective view.

    The decision-journal reader owns correction semantics.  This projection
    never scans history or substitutes date/name/rank matching for the exact
    ``(trade_date, code)`` key.
    """
    codes = validate_review_candidate_codes(trade_date, candidate_codes)

    states: list[CandidateReviewState] = []
    action_counts = {action: 0 for action in ACTIONS}
    reviewed_times: list[tuple[datetime, str]] = []
    for code in codes:
        entry = effective_decisions.get((trade_date, code))
        if entry is None:
            states.append(CandidateReviewState(code, None, None, None))
            continue
        if not isinstance(entry, DecisionEntry) or (
            entry.trade_date != trade_date
            or entry.code != code
            or entry.action not in ACTIONS
            or not entry.reason.strip()
            or not entry.decided_at
        ):
            raise ValueError("有效决策记录与当前候选键不一致，无法计算审阅进度。")
        decided_at = _parse_decided_at(entry.decided_at)
        if decided_at is None:
            raise ValueError("有效决策记录的审阅时间无效，无法计算审阅进度。")
        action_counts[entry.action] += 1
        reviewed_times.append((decided_at, entry.decided_at))
        states.append(CandidateReviewState(
            code=code,
            action=entry.action,
            reason_summary=_reason_summary(entry.reason),
            decided_at=entry.decided_at,
        ))

    reviewed_count = len(reviewed_times)
    return DailyReviewProgress(
        trade_date=trade_date,
        candidates=tuple(states),
        candidate_count=len(codes),
        reviewed_count=reviewed_count,
        unreviewed_count=len(codes) - reviewed_count,
        adopt_count=action_counts["adopt"],
        reject_count=action_counts["reject"],
        watch_count=action_counts["watch"],
        latest_reviewed_at=(
            max(reviewed_times, key=lambda item: item[0])[1]
            if reviewed_times
            else None
        ),
    )


__all__ = [
    "CandidateReviewState",
    "DailyReviewProgress",
    "summarise_daily_review_progress",
    "validate_review_candidate_codes",
]
