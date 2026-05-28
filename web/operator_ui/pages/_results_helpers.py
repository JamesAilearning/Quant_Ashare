"""Pure helpers for the results page (no Streamlit imports at module top).

Extracted from ``pages/results.py`` (UI review P1-1, phase 1). Everything
in this module is side-effect-free: no ``st.X`` calls, no
``unsafe_allow_html`` injection, no widget registration. The page module
imports these helpers and dispatches them inside its render functions,
which means:

* the helpers can be unit-tested without a Streamlit ScriptRunContext;
* changing how the page renders does not require re-reading hundreds of
  lines of pure logic (artifact reading, format, JSON depth-capping,
  path safety);
* a future phase 2 of the split can extract the remaining ``_render_*``
  helpers to a sibling module without touching anything in this file.

Public surface notes
--------------------

The names below are deliberately underscored — they are page-internal
helpers, not a public API. ``pages/results.py`` re-exports the ones
tests directly import (``_filter_json_by_query``, ``_FILTER_JSON_MAX_DEPTH``,
``_resolve_run_dir``, ``_log``, …) so existing test fixtures keep
working unchanged.
"""

from __future__ import annotations

import html
import logging
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from web.operator_ui import artifact_reader
from web.operator_ui._path_guard import guard_output_path, output_path
from web.operator_ui.artifact_reader import ArtifactReadIssue

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

MISSING = "N/A"
LOG_NAMES = ("stdout.log", "stderr.log", "runner_stdout.log", "runner_stderr.log")
PLOTLY_STRATEGY_COLOR = "royalblue"
PLOTLY_BENCHMARK_COLOR = "lightslategray"
PLOTLY_DRAWDOWN_COLOR = "firebrick"
PLOTLY_POSITIVE_COLOR = "seagreen"
PLOTLY_NEGATIVE_COLOR = "firebrick"
PLOTLY_NEUTRAL_COLOR = "white"


# ---------------------------------------------------------------------------
# Artifact reading wrappers — record issues, surface typed values
# ---------------------------------------------------------------------------


def _record_issue(
    issues: list[ArtifactReadIssue],
    result: artifact_reader.ArtifactReadResult,
) -> Any:
    if result.issue is not None:
        issues.append(result.issue)
    return result.value


def _read_json_artifact(
    path: Path | None,
    issues: list[ArtifactReadIssue],
    *,
    artifact_name: str | None = None,
) -> dict[str, Any]:
    value = _record_issue(
        issues,
        artifact_reader.read_json_artifact(path, artifact_name=artifact_name),
    )
    return value if isinstance(value, dict) else {}


def _read_parquet_artifact(
    path: Path | None,
    issues: list[ArtifactReadIssue],
    *,
    artifact_name: str | None = None,
) -> Any:
    return _record_issue(
        issues,
        artifact_reader.read_parquet_artifact(path, artifact_name=artifact_name),
    )


def _read_text_artifact(
    path: Path | None,
    issues: list[ArtifactReadIssue],
    *,
    artifact_name: str | None = None,
    tail_chars: int | None = None,
) -> str:
    value = _record_issue(
        issues,
        artifact_reader.read_text_artifact(
            path,
            artifact_name=artifact_name,
            tail_chars=tail_chars,
        ),
    )
    return str(value or "")


def _read_bytes_artifact(
    path: Path | None,
    issues: list[ArtifactReadIssue],
    *,
    artifact_name: str | None = None,
) -> bytes:
    value = _record_issue(
        issues,
        artifact_reader.read_bytes_artifact(path, artifact_name=artifact_name),
    )
    return value if isinstance(value, bytes) else b""


# ---------------------------------------------------------------------------
# Path helpers — job dir resolution, run dir safety guards
# ---------------------------------------------------------------------------


def _path_or_none(value: Any) -> Path | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return Path(text)


def _job_dir(job: Mapping[str, Any]) -> Path | None:
    config_path = _path_or_none(job.get("config_path"))
    if config_path is not None:
        return config_path.parent
    job_id = str(job.get("job_id") or "").strip()
    if not job_id:
        return None
    return output_path("operator_ui", "jobs", job_id)


def _read_config(
    job: Mapping[str, Any],
    issues: list[ArtifactReadIssue],
) -> tuple[dict[str, Any], Path | None, bytes]:
    config_path = _path_or_none(job.get("config_path"))
    if config_path is None:
        candidate = _job_dir(job)
        config_path = candidate / "config.yaml" if candidate is not None else None
    config_bytes = _read_bytes_artifact(config_path, issues, artifact_name="config.yaml")
    if not config_bytes:
        return {}, config_path, b""
    try:
        loaded = yaml.safe_load(config_bytes.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        issues.append(
            ArtifactReadIssue(
                artifact_name="config.yaml",
                path="" if config_path is None else str(config_path),
                error_type=type(exc).__name__,
                message=str(exc),
            )
        )
        return {}, config_path, config_bytes
    return loaded if isinstance(loaded, dict) else {}, config_path, config_bytes


def _resolve_run_dir(job: Mapping[str, Any], config: Mapping[str, Any]) -> Path | None:
    run_dir = _path_or_none(job.get("run_dir"))
    if run_dir is not None:
        # UI-launched jobs always go through ``JobManager.start`` which forces
        # ``run_dir`` under ``RESULT_ROOT``. CLI catalog entries
        # (``output/runs/_index.jsonl``) carry whatever path the CLI wrote
        # with no schema validation, so a crafted entry could point at
        # ``..\..\..\Windows\System32``. Surface the guard before any
        # ``iterdir`` / ``stat`` runs against a hostile path, mirroring the
        # protection already in place on every ``_read_*_artifact`` call.
        if not _is_safe_run_dir(run_dir):
            return None
        return run_dir
    job_status = str(job.get("status") or "").lower()
    if job_status not in {"success", "completed", "ok"}:
        return None
    output_dir = _path_or_none(config.get("output_dir"))
    if output_dir is None:
        return None
    # CLI-sourced ``output_dir`` is operator-controlled and reaches this
    # function unsanitised (the catalog is just a JSONL of whatever the
    # CLI wrote). Reject paths outside ``allowed_output_roots()`` before
    # we touch the filesystem with ``is_dir`` / ``iterdir``. Downstream
    # ``_read_*_artifact`` calls all guard their own reads, but
    # ``iterdir`` here would already act as a directory-existence probe
    # against arbitrary paths — defence in depth.
    if not _is_safe_run_dir(output_dir):
        return None
    if str(job.get("mode") or "") == "pipeline":
        runs_dir = output_dir / "runs"
        # Re-check the derived ``runs`` path: even when ``output_dir``
        # itself resolves under an allowed root, ``runs`` could be a
        # symlink pointing at an arbitrary directory (or a relative-
        # parent traversal escape). ``guard_output_path`` resolves the
        # final path before checking containment, so this catches a
        # crafted ``runs -> /tmp/outside`` symlink before ``is_dir``
        # follows it. (Codex P2 follow-up on PR #192.)
        if not _is_safe_run_dir(runs_dir):
            return None
        if runs_dir.is_dir():
            # Filter each candidate through ``_is_safe_run_dir`` BEFORE
            # any ``is_dir`` / ``stat`` call runs on it — both of those
            # follow symlinks and would otherwise leak directory /
            # existence information about an arbitrary attacker-
            # controlled target before the guard could block the
            # return. Equally important, this lets ``_resolve_run_dir``
            # surface a legitimate non-symlinked run even when a
            # newer-mtime symlinked sibling resolves outside roots —
            # the prior "guard the winner only" version returned None
            # whenever the newest entry happened to be hostile, hiding
            # valid runs. (Codex P2 round 3 on PR #192.)
            safe_dir_candidates = [
                entry
                for entry in runs_dir.iterdir()
                if _is_safe_run_dir(entry) and entry.is_dir()
            ]
            if safe_dir_candidates:
                return max(
                    safe_dir_candidates,
                    key=lambda path: path.stat().st_mtime,
                )
    return output_dir


def _is_safe_run_dir(candidate: Path) -> bool:
    """Return ``True`` if ``candidate`` resolves under the allowed output
    roots. Logs a warning and returns ``False`` otherwise so the caller
    can render an empty / not-found state instead of probing arbitrary
    filesystem paths.

    Centralised here (rather than inlined in ``_resolve_run_dir``) so the
    same guard can be applied to both the ``job.run_dir`` and the
    ``config.output_dir`` branches without duplicating the try/except
    + log message.
    """

    try:
        guard_output_path(candidate)
    except ValueError as exc:
        # WARN level so the audit trail captures the suspect path. The
        # operator-visible side is the empty-state ("artifacts unavailable");
        # the suspect path stays in the server log for forensics.
        _log.warning(
            "Refusing to resolve run_dir outside allowed output roots: %s (%s)",
            candidate,
            exc,
        )
        return False
    return True


# ---------------------------------------------------------------------------
# Format / safe-value helpers
# ---------------------------------------------------------------------------


def _nested(data: Mapping[str, Any], *path: str) -> Any:
    cur: Any = data
    for key in path:
        if not isinstance(cur, Mapping):
            return None
        cur = cur.get(key)
    return cur


def _first(data: Mapping[str, Any], paths: Sequence[Sequence[str]]) -> Any:
    for path in paths:
        value = _nested(data, *path)
        if value is not None:
            return value
    return None


def _finite_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _fmt_percent(value: Any, *, signed: bool = False) -> str:
    number = _finite_float(value)
    if number is None:
        return MISSING
    prefix = "+" if signed and number > 0 else ""
    return f"{prefix}{number * 100:.2f}%"


def _fmt_number(value: Any, *, digits: int = 2) -> str:
    number = _finite_float(value)
    if number is None:
        return MISSING
    return f"{number:.{digits}f}"


def _fmt_int(value: Any) -> str:
    number = _finite_float(value)
    if number is None:
        return MISSING
    return f"{int(number):,}"


def _fmt_text(value: Any) -> str:
    if value is None:
        return MISSING
    text = str(value).strip()
    return text if text else MISSING


def _fmt_duration(started_at: Any, ended_at: Any) -> str:
    if not started_at or not ended_at:
        return MISSING
    from datetime import datetime

    try:
        start = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(ended_at).replace("Z", "+00:00"))
    except ValueError:
        return MISSING
    seconds = max(0, int((end - start).total_seconds()))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _status_badge_variant(status: Any) -> str:
    """Map a raw job status to a design-system badge modifier name.

    Returns the variant suffix (``success`` / ``info`` / ``danger`` /
    ``warning`` / ``neutral``) so the caller can compose
    ``qv2-badge qv2-badge--{variant}``. The results page used to define
    its own ``.status-*`` selectors inline; UI review P1-11 removed
    them in favour of the shared DS variant ladder defined in
    ``static/theme.css``.
    """

    normalized = str(status or "unknown").lower()
    if normalized in {"success", "completed", "ok"}:
        return "success"
    if normalized == "running":
        return "info"
    if normalized in {"failed", "stop_failed"}:
        return "danger"
    if normalized in {"stopped", "cancelled", "canceled"}:
        return "warning"
    return "neutral"


def _safe_html(text: Any) -> str:
    return html.escape(str(text or ""))


def _metric_color(value: Any, *, negative_is_bad: bool = True) -> str:
    number = _finite_float(value)
    if number is None:
        return ""
    if number < 0 and negative_is_bad:
        return " qv2-negative"
    if number > 0:
        return " qv2-positive"
    return ""


# ---------------------------------------------------------------------------
# Chart / frame reading helpers (artifact-side; pure)
# ---------------------------------------------------------------------------


def _chart_by_token(charts: Mapping[str, Path], *tokens: str) -> tuple[str, Path] | None:
    lowered_tokens = tuple(token.lower() for token in tokens)
    for label, path in charts.items():
        normalized = label.lower().replace("-", "_").replace(" ", "_")
        if any(token in normalized for token in lowered_tokens):
            return label, path
    return None


def _read_positions(run_dir: Path | None, issues: list[ArtifactReadIssue]) -> dict[str, Any]:
    if run_dir is None:
        return {}
    return _read_json_artifact(run_dir / "positions.json", issues, artifact_name="positions.json")


def _read_metadata(run_dir: Path | None, issues: list[ArtifactReadIssue]) -> dict[str, Any]:
    if run_dir is None:
        return {}
    return _read_json_artifact(run_dir / "metadata.json", issues, artifact_name="metadata.json")


def _read_metrics(run_dir: Path | None, issues: list[ArtifactReadIssue]) -> dict[str, Any]:
    if run_dir is None:
        return {}
    return _read_json_artifact(run_dir / "metrics.json", issues, artifact_name="metrics.json")


def _read_holdings_frame(run_dir: Path | None, issues: list[ArtifactReadIssue]) -> Any:
    if run_dir is None:
        return None
    return _read_parquet_artifact(
        run_dir / "holdings.parquet",
        issues,
        artifact_name="holdings.parquet",
    )


def _read_trades_frame(run_dir: Path | None, issues: list[ArtifactReadIssue]) -> Any:
    if run_dir is None:
        return None
    return _read_parquet_artifact(run_dir / "trades.parquet", issues, artifact_name="trades.parquet")


def _read_nav_frame(run_dir: Path | None, issues: list[ArtifactReadIssue]) -> Any:
    if run_dir is None:
        return None
    return _read_parquet_artifact(run_dir / "nav.parquet", issues, artifact_name="nav.parquet")


# ---------------------------------------------------------------------------
# JSON filter — Raw JSON tab depth-capped substring search
# ---------------------------------------------------------------------------

# Bound recursion in ``_filter_json_by_query`` so an artifact with
# pathologically deep nesting (adversarial input or a downstream pipeline
# producing unexpectedly nested structures) can't blow CPython's stack or
# hang the Streamlit session. Real pipeline reports nest 4-6 levels deep
# in practice; 32 leaves plenty of headroom while staying well below
# the default recursion limit (1000) and bounding worst-case CPU.
_FILTER_JSON_MAX_DEPTH = 32


def _truncate_for_st_json(obj: Any, _depth: int = 0) -> Any:
    """Recursively trim ``obj`` so no branch reaches deeper than
    :data:`_FILTER_JSON_MAX_DEPTH` levels from this call's root. At the
    cap, the deep subtree is replaced with ``None`` so the result stays
    JSON-serialisable AND so Streamlit's ``st.json`` cannot itself
    recurse over a pathologically deep structure.

    Used by :func:`_filter_json_by_query` on the matched-key branch
    (where the matched value would otherwise pass through unchanged
    and bypass the recursion cap — see Codex P2 follow-up on PR #192).
    """

    if _depth >= _FILTER_JSON_MAX_DEPTH:
        return None
    if isinstance(obj, dict):
        return {k: _truncate_for_st_json(v, _depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_truncate_for_st_json(v, _depth + 1) for v in obj]
    return obj


def _filter_json_by_query(obj: Any, query: str, *, _depth: int = 0) -> Any:
    """Recursive substring filter for nested JSON.

    Returns a subtree containing only branches where some key, string
    value, or numeric value contains ``query`` (case-insensitive).
    Returns ``None`` when nothing matches; the caller treats ``None`` /
    empty result as "no hits".

    Designed to keep the Raw JSON tab usable on large pipeline reports
    by letting the operator narrow to a key like ``sharpe`` or
    ``drawdown`` without scrolling through hundreds of lines.

    Recursion is bounded at :data:`_FILTER_JSON_MAX_DEPTH`: at the cap
    we return the subtree unchanged rather than continuing to recurse.
    Returning the subtree (vs. ``None``) preserves the operator's
    ability to see *something* below the cap; truncation is preferable
    to silently hiding data that may have matched deeper down.
    """

    q = query.strip().lower()
    if not q:
        return obj

    if _depth >= _FILTER_JSON_MAX_DEPTH:
        # Stop recursing. Return None so the upstream
        # ``if filtered not in (None, {}, [])`` prune drops this
        # branch from the filtered tree.
        #
        # An earlier revision of this guard returned the subtree
        # unchanged, but Codex P2 on PR #192 flagged two problems
        # with that choice: (a) it misrepresented branches deeper
        # than the cap as "matched" even when the query never
        # matched below the cap, surfacing arbitrary deep blobs as
        # fake hits in the Raw JSON tab; and (b) it handed the
        # pathologically-deep subtree to ``st.json`` for rendering,
        # so Streamlit's serialiser would re-recurse over exactly
        # the adversarial structure this cap is meant to protect
        # against. Returning None keeps the contract honest (only
        # branches with a real match survive) AND keeps the deep
        # subtree out of any downstream recursive code path.
        return None

    if isinstance(obj, dict):
        kept: dict[str, Any] = {}
        for key, value in obj.items():
            if q in str(key).lower():
                # Matched-key branch: previously passed ``value`` through
                # unchanged, so an artifact like
                # ``{"needle": <100-deep-tree>}`` would still hand the
                # whole deep subtree to ``st.json`` and reproduce the
                # same Streamlit serializer recursion the cap was meant
                # to prevent (Codex P2 follow-up on PR #192). Truncate
                # the matched value to the same depth budget, sharing
                # the running ``_depth`` so the budget is global to the
                # filter call, not reset per matched key.
                kept[key] = _truncate_for_st_json(value, _depth + 1)
                continue
            filtered = _filter_json_by_query(value, query, _depth=_depth + 1)
            if filtered not in (None, {}, []):
                kept[key] = filtered
        return kept or None

    if isinstance(obj, list):
        out = []
        for entry in obj:
            filtered = _filter_json_by_query(entry, query, _depth=_depth + 1)
            if filtered not in (None, {}, []):
                out.append(filtered)
        return out or None

    # Scalar leaves.
    if obj is None:
        return None
    return obj if q in str(obj).lower() else None


# ---------------------------------------------------------------------------
# Job-list helpers (pure)
# ---------------------------------------------------------------------------


def _job_label(job: Mapping[str, Any]) -> str:
    job_id = str(job.get("job_id") or "?")
    mode = str(job.get("mode") or "?")
    status = str(job.get("status") or "?")
    return f"{job_id} ({mode}, {status})"


def _default_job_id(jobs: Sequence[Mapping[str, Any]]) -> str:
    for job in jobs:
        if str(job.get("status") or "").lower() in {"success", "completed"}:
            return str(job.get("job_id") or "")
    return str(jobs[0].get("job_id") or "") if jobs else ""
