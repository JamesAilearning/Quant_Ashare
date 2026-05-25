"""Tests for the operator UI's URL query-param schema validator.

The guard whitelists per-key formats so a hostile URL like
``?run_id=../../etc/passwd&search=<img onerror=...>`` can't inject
arbitrary strings into ``st.session_state`` or downstream filters.
Invalid values silently fall back to the supplied default.
"""

from __future__ import annotations

import pytest

from web.operator_ui._param_guard import known_keys, sanitize


# ---------------------------------------------------------------------------
# Schema registry surface
# ---------------------------------------------------------------------------


def test_known_keys_includes_jobs_page_defaults():
    """Every key the jobs page mirrors into URL must have a validator."""
    jobs_page_keys = {
        "type", "status", "source", "search",
        "date_from", "date_to",
        "sort_by", "sort_dir",
        "page", "autorefresh",
    }
    assert jobs_page_keys.issubset(known_keys()), (
        f"missing validators for: {jobs_page_keys - known_keys()}"
    )


def test_known_keys_includes_run_id():
    """The detail pages (results / walk_forward) read run_id from URL."""
    assert "run_id" in known_keys()


# ---------------------------------------------------------------------------
# Enum-typed keys
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "good"),
    [
        ("type", "all"),
        ("type", "pipeline"),
        ("type", "walk_forward"),
        ("status", "running"),
        ("status", "failed"),
        ("source", "ui"),
        ("source", "cli"),
        ("sort_by", "created_at"),
        ("sort_by", "duration"),
        ("sort_dir", "asc"),
        ("sort_dir", "desc"),
        ("autorefresh", "0"),
        ("autorefresh", "1"),
    ],
)
def test_enum_accepts_listed_value(key, good):
    assert sanitize(key, good, default="all") == good


@pytest.mark.parametrize(
    ("key", "bad"),
    [
        ("type", "delete"),  # not in allowed set
        ("type", "all'; DROP TABLE"),
        ("status", "PENDING"),  # case-sensitive
        ("status", ""),
        ("source", "external"),
        ("sort_by", "name"),  # not a known sort column
        ("sort_dir", "ascending"),  # full word rejected
        ("autorefresh", "true"),
        ("autorefresh", "2"),
    ],
)
def test_enum_rejects_unlisted_value(key, bad):
    assert sanitize(key, bad, default="sentinel") == "sentinel"


# ---------------------------------------------------------------------------
# ISO date keys
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("good", ["2024-01-01", "2026-12-31", ""])
def test_iso_date_accepts_iso_or_empty(good):
    assert sanitize("date_from", good, default="default") == good


@pytest.mark.parametrize(
    "bad",
    [
        "2024/01/01",
        "01-01-2024",
        "yesterday",
        "2024-13-01",  # month 13
        "2024-01-32",  # day 32
        "<script>",
    ],
)
def test_iso_date_rejects_other_formats(bad):
    assert sanitize("date_from", bad, default="fallback") == "fallback"


# ---------------------------------------------------------------------------
# Free-text search
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "good",
    [
        "model_v2",
        "lgb-2024-fold5",
        "alpha158 baseline",
        "用户搜索",  # CJK
        "run.20260101.abc",
        "",  # empty allowed
    ],
)
def test_search_accepts_safe_text(good):
    assert sanitize("search", good, default="x") == good


@pytest.mark.parametrize(
    "bad",
    [
        "<img onerror=alert(1)>",
        "'; DROP TABLE jobs;--",
        "abc\x00def",
        "x" * 201,  # over 200-char cap
    ],
)
def test_search_rejects_unsafe_text(bad):
    assert sanitize("search", bad, default="safe") == "safe"


# ---------------------------------------------------------------------------
# run_id — most security-sensitive
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "good",
    [
        "run-20260101-abc123",
        "pipeline.fold5.v2",
        "ABC_123",
        "single",
    ],
)
def test_run_id_accepts_path_safe_strings(good):
    assert sanitize("run_id", good, default="") == good


@pytest.mark.parametrize(
    "bad",
    [
        "../../etc/passwd",
        "..\\..\\windows",
        "/absolute/path",
        "C:\\Users",
        "run id with spaces",  # spaces not allowed in path-segment
        "run;id",  # shell metacharacter
        "run id$(rm)",
        "run<script>",
        "",  # empty rejected (length 1+)
        "x" * 201,  # over cap
    ],
)
def test_run_id_rejects_traversal_and_shell_meta(bad):
    assert sanitize("run_id", bad, default="default") == "default"


# ---------------------------------------------------------------------------
# Page numbers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("good", ["1", "42", "100"])
def test_page_accepts_positive_int(good):
    assert sanitize("page", good, default="1") == good


@pytest.mark.parametrize(
    "bad",
    [
        "0",  # 0 not positive
        "-1",
        "1.5",
        "abc",
        "01",  # leading zero
        "1" * 11,  # > 10 digits
    ],
)
def test_page_rejects_non_positive_int(bad):
    assert sanitize("page", bad, default="1") == "1"


# ---------------------------------------------------------------------------
# Hostile input shapes
# ---------------------------------------------------------------------------


def test_sanitize_unknown_key_returns_default():
    """A key not in the registry must NOT pass through — catches typos
    in caller code as 'value ignored' rather than silent passthrough."""
    assert sanitize("not_a_real_key", "anything", default="fallback") == "fallback"


def test_sanitize_handles_list_input_first_element():
    """``st.query_params.get`` may return a list when the URL has
    duplicate keys (``?type=a&type=b``); sanitize takes the first."""
    assert sanitize("type", ["pipeline", "walk_forward"], default="all") == "pipeline"


def test_sanitize_handles_empty_list_input():
    assert sanitize("type", [], default="all") == "all"


def test_sanitize_handles_non_string_input():
    """An int, dict, or None must not crash — just return the default."""
    assert sanitize("type", 42, default="all") == "all"
    assert sanitize("type", None, default="all") == "all"
    assert sanitize("type", {"k": "v"}, default="all") == "all"
