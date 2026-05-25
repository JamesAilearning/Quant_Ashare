"""Query-param schema validation for the operator UI.

All URL parameters that get mirrored into ``st.session_state`` or
``st.query_params`` should pass through :func:`sanitize` first.
Invalid values silently fall back to the supplied default (no error
shown — a broken URL just renders the default view), and rejections
are logged so unusual traffic is greppable.

The schema lives here, not scattered across page modules, so adding a
new URL-backed widget is "add a key + validator here, then call
``sanitize`` at the page boundary". Page modules never trust
``st.query_params`` raw output.

No external dependency — pure stdlib. Safe to import from anywhere.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import date

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Allow-list value sets for enum-typed params. The source of truth is the
# UI selectbox options + ``job_io.SORT_OPTIONS``; mirrored here so this
# module has no runtime dependency on the page or job_io modules (avoids
# import cycles and lets the validators be importable from tests).
# ---------------------------------------------------------------------------

_TYPE_ALLOWED = frozenset({"all", "pipeline", "walk_forward", "provider"})
_STATUS_ALLOWED = frozenset(
    {"all", "queued", "running", "completed", "failed", "cancelled"}
)
_SOURCE_ALLOWED = frozenset({"all", "ui", "cli"})
_SORT_BY_ALLOWED = frozenset(
    {"created_at", "duration", "status", "type", "run_id"}
)
_SORT_DIR_ALLOWED = frozenset({"asc", "desc"})
_AUTOREFRESH_ALLOWED = frozenset({"0", "1"})

# Free-text search: CJK + Latin alphanumeric + space + a few punctuation
# chars commonly used in run IDs / model names / error excerpts. Length
# capped at 200 chars so a pathological URL can't blow up memory in
# downstream filters.
#
# Whitespace whitelisted is literal ``space`` and ``\t`` only — NOT
# ``\s``. ``\s`` would have covered ``\n / \r / \v / \f``, letting log
# injection sneak in via ``?search=abc%0Adef``. Search inputs are
# semantically single-line, so this is a tightening, not a regression.
_SEARCH_RE = re.compile(r"^[\w \t\-_./:@一-鿿]{0,200}$")

# run_id is path-segment safe: alphanumeric + dash + underscore + dot.
# Capped at 200 chars (actual run IDs are ~30). Rejects path traversal
# (`..`, `/`, `\`) and shell metacharacters.
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_\-.]{1,200}$")

# Page: positive integer ≤ ~9-digits so int() can't blow up.
_PAGE_RE = re.compile(r"^[1-9]\d{0,9}$")


# ---------------------------------------------------------------------------
# Per-validator helpers
# ---------------------------------------------------------------------------


def _enum(allowed: frozenset[str]) -> Callable[[str], str | None]:
    def check(value: str) -> str | None:
        return value if value in allowed else None

    return check


def _iso_date(value: str) -> str | None:
    """Return ``value`` if it parses as ISO YYYY-MM-DD or is the
    explicit empty-string sentinel; else None."""
    if value == "":
        return ""
    try:
        date.fromisoformat(value)
    except ValueError:
        return None
    return value


def _regex(pattern: re.Pattern[str]) -> Callable[[str], str | None]:
    def check(value: str) -> str | None:
        # ``fullmatch`` rather than ``match``: Python's ``$`` anchor also
        # matches *before* a trailing newline, so ``pattern.match("abc\n")``
        # with ``^[A-Za-z0-9_\-.]+$`` would accept the embedded ``\n`` and
        # pass it down into ``st.switch_page`` / log records. ``fullmatch``
        # forces the pattern to consume the entire input with no implicit
        # newline tolerance, which is what the whitelist is supposed to
        # mean. (Codex P2 review on PR #146.)
        return value if pattern.fullmatch(value) else None

    return check


# ---------------------------------------------------------------------------
# Schema registry
# ---------------------------------------------------------------------------

_VALIDATORS: dict[str, Callable[[str], str | None]] = {
    "type": _enum(_TYPE_ALLOWED),
    "status": _enum(_STATUS_ALLOWED),
    "source": _enum(_SOURCE_ALLOWED),
    "search": _regex(_SEARCH_RE),
    "date_from": _iso_date,
    "date_to": _iso_date,
    "sort_by": _enum(_SORT_BY_ALLOWED),
    "sort_dir": _enum(_SORT_DIR_ALLOWED),
    "page": _regex(_PAGE_RE),
    "autorefresh": _enum(_AUTOREFRESH_ALLOWED),
    "run_id": _regex(_RUN_ID_RE),
}


def known_keys() -> frozenset[str]:
    """The set of URL keys this module knows how to validate.

    Page modules can iterate / display this for debugging or to drive a
    URL-sync layer; updating the registry above is the only place to
    add a key.
    """
    return frozenset(_VALIDATORS.keys())


def sanitize(key: str, raw: object, default: str = "") -> str:
    """Return ``raw`` if it passes the per-key validator, else
    ``default``.

    Streamlit's ``st.query_params.get`` may return a string OR a list
    of strings when the URL has duplicates (``?key=a&key=b``); we take
    the first element in that case. Non-string inputs fall back to the
    default.

    Unknown ``key`` (not in :data:`_VALIDATORS`) is rejected to the
    default with a warning — a typo in caller code surfaces as "URL
    value ignored" rather than silently passing through.
    """
    if isinstance(raw, list):
        raw = raw[0] if raw else default
    if not isinstance(raw, str):
        return default
    check = _VALIDATORS.get(key)
    if check is None:
        _log.warning(
            "no validator for query-param %r; falling back to default %r",
            key, default,
        )
        return default
    cleaned = check(raw)
    if cleaned is None:
        _log.warning(
            "rejected query-param %s=%r (failed schema); using default %r",
            key, raw, default,
        )
        return default
    return cleaned


__all__ = ["known_keys", "sanitize"]
