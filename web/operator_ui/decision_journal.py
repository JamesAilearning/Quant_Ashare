"""Append-only operator decision journal — web-layer owned (每日决策页).

The 今日推荐 page records adopt / reject / watch decisions (one line of JSON
per decision) so the operator's reasoning is reviewable later. Contract
(openspec ``add-daily-decision-page``, spec ``v2-daily-decision-page``):

* **Append-only.** Entries are never modified or deleted; a correction is a
  NEW appended entry, and :func:`read_journal` implements supersede semantics
  (latest ``decided_at`` wins per ``(trade_date, code)``).
* **Idempotent appends.** Each submitting form mints a uuid4 ``nonce`` that is
  PERSISTED in the line; :func:`append_decision` refuses a duplicate
  ``(trade_date, code, nonce)``, so a Streamlit rerun replaying one submission
  cannot double-append, while an intentional correction (fresh form → fresh
  nonce) is never suppressed.
* **Torn-line tolerant.** Every append builds the complete line (including the
  trailing newline) and issues a single ``write`` + ``flush`` + ``fsync`` on a
  binary append handle; the reader skips + counts malformed lines instead of
  crashing (a partial line from an interrupted process must not poison the
  journal).
* **Clean bytes.** UTF-8 without BOM; ``\\n`` line endings written in binary
  mode (never CRLF — the #321 lesson).
* **Boundary.** The journal lives OUTSIDE the repository's disposable
  ``output/`` tree (``QUANT_DECISION_JOURNAL_DIR``, default
  ``D:/stock/operator_journal``) because jobs cleanup / ``git clean`` treat
  ``output/`` as discardable while decisions are precious. It is operator
  state owned by ``web/``: **never an input to official metrics, backtests,
  training or promotion**, and no module under ``src/`` may reference it — a
  source-scan test enforces zero ``src/`` references.
* **Concurrency (documented boundary).** This is a single-operator console;
  appends are single small ``"ab"``-mode writes. Cross-process file locking is
  deliberately NOT implemented — two concurrent writers are out of scope, not
  silently supported.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import date as _date
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

JOURNAL_VERSION = 1
JOURNAL_FILENAME = "decision_journal.jsonl"
ENV_JOURNAL_DIR = "QUANT_DECISION_JOURNAL_DIR"
# Default root mirrors the D:/stock/<purpose> precedent for operator-owned,
# non-market-data assets (phase_b_artifacts); registered in
# docs/operations-env-vars.md — never a silent addition.
DEFAULT_JOURNAL_DIR = "D:/stock/operator_journal"
ACTIONS: tuple[str, ...] = ("adopt", "reject", "watch")

# Fixed +08:00, mirroring the repo convention (web/operator_ui/formatting.py
# ``_CN_TZ``). Asia/Shanghai has no DST, so the fixed offset is exact.
_CN_TZ = timezone(timedelta(hours=8))

_TRADE_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

# The repo's ``output/`` tree is DISPOSABLE (jobs cleanup / git clean); the
# journal must never live under it — see the boundary in the module docstring.
_REPO_ROOT = Path(__file__).resolve().parents[2]


class DecisionJournalError(ValueError):
    """Raised on an invalid entry or unusable journal input (fail-loud)."""


@dataclass(frozen=True)
class DecisionEntry:
    """One decision line. Field order == serialized key order (stable)."""

    journal_version: int
    trade_date: str   # copied from the artifact's as_of_date — NEVER the clock
    code: str
    action: str       # one of ACTIONS
    reason: str
    rank: int | None
    score: float | None
    model_id: str     # artifact meta model_pkl_sha256 (or an honest sentinel)
    decided_at: str   # tz-aware ISO8601, +08:00
    nonce: str        # uuid4 minted at form render; persisted for idempotency


@dataclass(frozen=True)
class JournalReadResult:
    """All valid entries (file order), the effective view, and bad-line count."""

    entries: tuple[DecisionEntry, ...]
    # (trade_date, code) -> the winning entry (latest decided_at; ties broken
    # by file position — later line wins, deterministically).
    effective: Mapping[tuple[str, str], DecisionEntry]
    malformed_count: int


def journal_path(journal_dir: str | Path | None = None) -> Path:
    """Resolve the journal file path: explicit dir > env var > default.

    Fails loud when the resolved directory sits under the repository's
    DISPOSABLE ``output/`` tree — decisions placed there would become
    eligible for jobs cleanup / ``git clean`` removal, contradicting the
    append-only-precious boundary (codex P2 on #330).
    """
    if journal_dir is not None:
        base = Path(journal_dir)
    else:
        base = Path(os.environ.get(ENV_JOURNAL_DIR, "").strip() or DEFAULT_JOURNAL_DIR)
    resolved = base.resolve()
    disposable = (_REPO_ROOT / "output").resolve()
    if resolved == disposable or resolved.is_relative_to(disposable):
        raise DecisionJournalError(
            f"decision journal dir {base} resolves under the repository's "
            f"disposable output/ tree ({disposable}) — cleanup tooling and "
            "git clean treat that tree as discardable, but decisions are "
            "append-only precious state. Point "
            f"{ENV_JOURNAL_DIR} outside the repository (default: "
            f"{DEFAULT_JOURNAL_DIR})."
        )
    return base / JOURNAL_FILENAME


def make_entry(
    *,
    trade_date: str,
    code: str,
    action: str,
    reason: str,
    rank: int | None,
    score: float | None,
    model_id: str,
    nonce: str,
    decided_at: str | None = None,
) -> DecisionEntry:
    """Validate inputs fail-loud and build an entry.

    ``decided_at`` is injectable for tests (value-injection, as elsewhere);
    the production default is now() in fixed +08:00. ``trade_date`` must be
    the artifact's ``as_of_date`` — callers must not pass a local "today".
    """
    # Strict YYYY-MM-DD, matching the artifact's as_of_date byte-for-byte.
    # (Python 3.11+ fromisoformat also accepts the compact "YYYYMMDD" form,
    # which would SPLIT the supersede key for the same trading day — so the
    # dashed shape is enforced explicitly before the parse.)
    if not _TRADE_DATE_RE.fullmatch(str(trade_date or "")):
        raise DecisionJournalError(
            f"trade_date must be YYYY-MM-DD (the artifact's as_of_date, "
            f"verbatim); got {trade_date!r}."
        )
    try:
        _date.fromisoformat(trade_date)
    except (TypeError, ValueError) as exc:
        raise DecisionJournalError(
            f"trade_date must be a real calendar date; got {trade_date!r}."
        ) from exc
    if not str(code or "").strip():
        raise DecisionJournalError("code must be a non-empty stock code.")
    if action not in ACTIONS:
        raise DecisionJournalError(
            f"action must be one of {ACTIONS}; got {action!r}."
        )
    if not str(reason or "").strip():
        raise DecisionJournalError(
            "reason must be non-empty — the journal exists to capture the "
            "operator's one-line rationale, not bare actions."
        )
    if not str(nonce or "").strip():
        raise DecisionJournalError(
            "nonce must be non-empty (the form-render uuid4; it is the "
            "idempotency key that distinguishes a rerun replay from a "
            "deliberate correction)."
        )
    if decided_at is None:
        decided_at = datetime.now(tz=_CN_TZ).isoformat()
    else:
        parsed = _parse_decided_at(decided_at)
        if parsed is None:
            raise DecisionJournalError(
                "decided_at must be a tz-aware ISO8601 timestamp; got "
                f"{decided_at!r}."
            )
    return DecisionEntry(
        journal_version=JOURNAL_VERSION,
        trade_date=trade_date,
        code=str(code).strip(),
        action=action,
        reason=str(reason).strip(),
        rank=rank,
        score=score,
        model_id=model_id,
        decided_at=decided_at,
        nonce=str(nonce).strip(),
    )


def append_decision(
    entry: DecisionEntry, *, journal_dir: str | Path | None = None,
) -> bool:
    """Append one entry; return False (no write) on a duplicate nonce.

    The duplicate check scans the existing file with the same tolerant parser
    as :func:`read_journal`, so a torn line cannot poison the dedupe. The
    write is the COMPLETE line (payload + ``\\n``) in ONE ``write`` call on a
    binary append handle, then ``flush`` + ``fsync`` — an interrupted process
    can lose the line, but can never leave half of it followed by a healthy
    next line being mis-joined.
    """
    path = journal_path(journal_dir)
    existing = read_journal(journal_dir=journal_dir)
    for prior in existing.entries:
        if (
            prior.trade_date == entry.trade_date
            and prior.code == entry.code
            and prior.nonce == entry.nonce
        ):
            return False
    line = json.dumps(asdict(entry), ensure_ascii=False).encode("utf-8") + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    # Quarantine a torn TAIL before appending: if a previous process died
    # mid-write and left a final line WITHOUT its newline, appending directly
    # would fuse the new entry onto the fragment — one combined malformed line,
    # and the operator's NEW decision would silently vanish from history and
    # the effective view (codex P1 on #330). A leading newline in the SAME
    # single write isolates the fragment as its own counted-malformed line and
    # lands the new entry on a clean line.
    if path.is_file() and path.stat().st_size > 0:
        with path.open("rb") as tail:
            tail.seek(-1, os.SEEK_END)
            if tail.read(1) != b"\n":
                line = b"\n" + line
    with path.open("ab") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())
    return True


def read_journal(*, journal_dir: str | Path | None = None) -> JournalReadResult:
    """Read the journal tolerantly: skip + count malformed lines, never crash.

    Supersede semantics: for each ``(trade_date, code)`` the entry with the
    latest ``decided_at`` wins; an exact timestamp tie is broken by file
    position (the later line wins), so corrections are always deterministic.
    """
    path = journal_path(journal_dir)
    if not path.is_file():
        return JournalReadResult(entries=(), effective={}, malformed_count=0)
    raw = path.read_bytes()
    entries: list[DecisionEntry] = []
    malformed = 0
    for line in raw.split(b"\n"):
        if not line.strip():
            continue
        parsed = _parse_line(line)
        if parsed is None:
            malformed += 1
        else:
            entries.append(parsed)
    effective: dict[tuple[str, str], tuple[datetime, int, DecisionEntry]] = {}
    for position, item in enumerate(entries):
        decided = _parse_decided_at(item.decided_at)
        if decided is None:  # defensive; _parse_line already validated
            malformed += 1
            continue
        key = (item.trade_date, item.code)
        incumbent = effective.get(key)
        if incumbent is None or (decided, position) >= (incumbent[0], incumbent[1]):
            effective[key] = (decided, position, item)
    return JournalReadResult(
        entries=tuple(entries),
        effective={key: value[2] for key, value in effective.items()},
        malformed_count=malformed,
    )


def _parse_decided_at(value: str) -> datetime | None:
    """A tz-AWARE ISO8601 timestamp, or None. Naive timestamps are rejected —
    comparing naive against aware raises, and a naive decided_at is exactly
    the clock/timezone confusion the contract forbids."""
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def _parse_line(line: bytes) -> DecisionEntry | None:
    """One journal line -> entry, or None when malformed (any reason)."""
    try:
        payload: Any = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    # Version pin on the RAW value, before any coercion: int(...) would
    # normalize 1.9 -> 1 and True -> 1 (bool is an int subclass, True == 1),
    # letting a non-v1 row through the version check (codex P2 on #330).
    raw_version = payload.get("journal_version")
    if (
        not isinstance(raw_version, int)
        or isinstance(raw_version, bool)
        or raw_version != JOURNAL_VERSION
    ):
        return None
    try:
        entry = DecisionEntry(
            journal_version=raw_version,
            trade_date=str(payload["trade_date"]),
            code=str(payload["code"]),
            action=str(payload["action"]),
            reason=str(payload["reason"]),
            rank=None if payload.get("rank") is None else int(payload["rank"]),
            score=(
                None if payload.get("score") is None else float(payload["score"])
            ),
            model_id=str(payload["model_id"]),
            decided_at=str(payload["decided_at"]),
            nonce=str(payload["nonce"]),
        )
    except (KeyError, TypeError, ValueError):
        return None
    if entry.action not in ACTIONS:
        return None
    if not entry.nonce.strip():
        return None
    if _parse_decided_at(entry.decided_at) is None:
        return None
    # Same strict YYYY-MM-DD shape the writer enforces (make_entry): a compact
    # "20260703" row (prior buggy build / manual edit) would otherwise enter
    # history/effective with malformed_count == 0 and SPLIT the supersede key
    # for that trading day (codex P2 on #330) — the reader is the boundary.
    if not _TRADE_DATE_RE.fullmatch(entry.trade_date):
        return None
    try:
        _date.fromisoformat(entry.trade_date)
    except ValueError:
        return None
    return entry
