"""Pure helpers for dated daily-recommendation artifacts and UI routing."""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, MutableMapping
from datetime import date
from pathlib import Path

DAILY_DECISION_DATE_KEY = "dd_date"
DAILY_DECISION_REQUESTED_DATE_KEY = "daily_decision::requested_date"
RUN_CENTER_PUBLISHED_DATE_KEY = "run_center::published_recommendation_date"

_ARTIFACT_RE = re.compile(r"daily_recommendation_(\d{4}-\d{2}-\d{2})\.json")


def recommendation_artifact_date(value: str | Path) -> str | None:
    """Return the ISO date from one canonical recommendation JSON filename."""

    # Runner outputs are normally bare filenames. Normalize both path
    # separators so a persisted value remains safe to parse across platforms.
    filename = str(value).replace("\\", "/").rsplit("/", 1)[-1]
    match = _ARTIFACT_RE.fullmatch(filename)
    if match is None:
        return None
    candidate = match.group(1)
    try:
        date.fromisoformat(candidate)
    except ValueError:
        return None
    return candidate


def published_recommendation_date(published: Iterable[str]) -> str | None:
    """Return one unambiguous dated recommendation artifact, or ``None``.

    The runner publishes several files for a successful invocation. Only the
    canonical JSON artifact names a date suitable for selecting the detailed
    review page; stdout timestamps and output-directory ordering are never a
    substitute.
    """

    dates = [
        artifact_date
        for value in published
        if (artifact_date := recommendation_artifact_date(value)) is not None
    ]
    return dates[0] if len(dates) == 1 else None


def remember_run_center_published_date(
    session_state: MutableMapping[str, object], published: Iterable[str]
) -> str | None:
    """Store only an unambiguous run result for a later Streamlit rerun.

    ``st.button`` triggers a fresh rerun.  Clearing the prior value before
    recording the current result ensures that a failed or ambiguous later run
    cannot offer a link to an older recommendation artifact.
    """

    session_state.pop(RUN_CENTER_PUBLISHED_DATE_KEY, None)
    artifact_date = published_recommendation_date(published)
    if artifact_date is not None:
        session_state[RUN_CENTER_PUBLISHED_DATE_KEY] = artifact_date
    return artifact_date


def run_center_published_date(
    session_state: MutableMapping[str, object],
) -> str | None:
    """Return the persisted review date, discarding malformed state."""

    value = session_state.get(RUN_CENTER_PUBLISHED_DATE_KEY)
    if not isinstance(value, str):
        session_state.pop(RUN_CENTER_PUBLISHED_DATE_KEY, None)
        return None
    try:
        date.fromisoformat(value)
    except ValueError:
        session_state.pop(RUN_CENTER_PUBLISHED_DATE_KEY, None)
        return None
    return value


def clear_run_center_published_date(
    session_state: MutableMapping[str, object],
) -> None:
    """Remove a consumed or superseded Run Center review action."""

    session_state.pop(RUN_CENTER_PUBLISHED_DATE_KEY, None)


def prepare_daily_decision_selection(
    session_state: MutableMapping[str, object],
    available_dates: Collection[str],
) -> str | None:
    """Consume one requested date and remove stale select-box state.

    The request is one-shot. A deleted or invalid artifact date is discarded
    before Streamlit builds the ``dd_date`` selectbox, which avoids preserving
    a selection the detailed page cannot render.
    """

    requested = session_state.pop(DAILY_DECISION_REQUESTED_DATE_KEY, None)
    selected = session_state.get(DAILY_DECISION_DATE_KEY)
    if selected not in available_dates:
        session_state.pop(DAILY_DECISION_DATE_KEY, None)
    if isinstance(requested, str) and requested in available_dates:
        session_state[DAILY_DECISION_DATE_KEY] = requested
        return requested
    return None


__all__ = [
    "DAILY_DECISION_DATE_KEY",
    "DAILY_DECISION_REQUESTED_DATE_KEY",
    "RUN_CENTER_PUBLISHED_DATE_KEY",
    "clear_run_center_published_date",
    "prepare_daily_decision_selection",
    "published_recommendation_date",
    "recommendation_artifact_date",
    "remember_run_center_published_date",
    "run_center_published_date",
]
