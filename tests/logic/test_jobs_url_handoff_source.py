"""Regression coverage for cross-page Jobs filter handoffs."""

from __future__ import annotations

import ast
from pathlib import Path


_JOBS_PAGE = Path("web/operator_ui/pages/jobs.py")


class _FakeStreamlit:
    def __init__(self, *, session_state: dict[str, str]) -> None:
        self.session_state = session_state


def _seed_handoff(
    *,
    session_state: dict[str, str],
    url_values: dict[str, str],
    keys: list[str],
) -> None:
    tree = ast.parse(_JOBS_PAGE.read_text(encoding="utf-8"))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_seed_session_from_url"
    )
    namespace = {
        "st": _FakeStreamlit(session_state=session_state),
        "_qp_read": url_values.__getitem__,
    }
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(_JOBS_PAGE), "exec"), namespace)
    namespace["_seed_session_from_url"](keys)


def test_jobs_status_handoff_replaces_stale_page_state_once() -> None:
    session_state = {
        "jobs_status": "running",
        "jobs_last_url_status": "running",
    }
    url_values = {"status": "failed"}

    _seed_handoff(session_state=session_state, url_values=url_values, keys=["status"])

    assert session_state["jobs_status"] == "failed"
    assert session_state["jobs_last_url_status"] == "failed"

    # The Jobs page has not mirrored the user's new choice to URL yet; this
    # rerun must not clobber that widget update back to the old handoff value.
    session_state["jobs_status"] = "partial"
    _seed_handoff(session_state=session_state, url_values=url_values, keys=["status"])
    assert session_state["jobs_status"] == "partial"
