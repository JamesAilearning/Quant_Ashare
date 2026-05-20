"""Read operator UI artifacts with explicit, displayable read issues."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from web.operator_ui._path_guard import guard_output_path


@dataclass(frozen=True)
class ArtifactReadIssue:
    """A non-missing artifact could not be read or decoded."""

    artifact_name: str
    path: str
    error_type: str
    message: str


@dataclass(frozen=True)
class ArtifactReadResult:
    """Artifact read value plus an optional read issue."""

    value: Any
    issue: ArtifactReadIssue | None = None


def _artifact_name(path: Path | None, artifact_name: str | None) -> str:
    if artifact_name:
        return artifact_name
    if path is None:
        return "<unknown>"
    return path.name


def _issue(path: Path | None, artifact_name: str | None, exc: BaseException) -> ArtifactReadIssue:
    return ArtifactReadIssue(
        artifact_name=_artifact_name(path, artifact_name),
        path="" if path is None else str(path),
        error_type=type(exc).__name__,
        message=str(exc),
    )


def _guard_read_path(path: Path | None, artifact_name: str | None) -> ArtifactReadIssue | None:
    if path is None:
        return None
    try:
        guard_output_path(path)
    except ValueError as exc:
        return _issue(path, artifact_name, exc)
    return None


def read_json_artifact(path: Path | None, *, artifact_name: str | None = None) -> ArtifactReadResult:
    """Read a JSON object artifact.

    Missing files are a normal empty state. Existing but unreadable or malformed
    files produce an issue so the UI can show the operator what went wrong.
    """

    guard_issue = _guard_read_path(path, artifact_name)
    if guard_issue is not None:
        return ArtifactReadResult({}, guard_issue)
    if path is None or not path.is_file():
        return ArtifactReadResult({})
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return ArtifactReadResult({}, _issue(path, artifact_name, exc))
    if not isinstance(loaded, dict):
        return ArtifactReadResult(
            {},
            ArtifactReadIssue(
                artifact_name=_artifact_name(path, artifact_name),
                path=str(path),
                error_type="InvalidArtifactShape",
                message="Expected a JSON object.",
            ),
        )
    return ArtifactReadResult(loaded)


def read_parquet_artifact(path: Path | None, *, artifact_name: str | None = None) -> ArtifactReadResult:
    guard_issue = _guard_read_path(path, artifact_name)
    if guard_issue is not None:
        return ArtifactReadResult(None, guard_issue)
    if path is None or not path.is_file():
        return ArtifactReadResult(None)
    try:
        import pandas as pd

        return ArtifactReadResult(pd.read_parquet(path))
    except Exception as exc:  # noqa: BLE001 - UI must surface backend/engine read errors.
        return ArtifactReadResult(None, _issue(path, artifact_name, exc))


def read_text_artifact(
    path: Path | None,
    *,
    artifact_name: str | None = None,
    tail_chars: int | None = None,
) -> ArtifactReadResult:
    guard_issue = _guard_read_path(path, artifact_name)
    if guard_issue is not None:
        return ArtifactReadResult("", guard_issue)
    if path is None or not path.is_file():
        return ArtifactReadResult("")
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return ArtifactReadResult("", _issue(path, artifact_name, exc))
    if tail_chars is not None and len(text) > tail_chars:
        text = text[-tail_chars:]
    return ArtifactReadResult(text)


def read_bytes_artifact(path: Path | None, *, artifact_name: str | None = None) -> ArtifactReadResult:
    guard_issue = _guard_read_path(path, artifact_name)
    if guard_issue is not None:
        return ArtifactReadResult(b"", guard_issue)
    if path is None or not path.is_file():
        return ArtifactReadResult(b"")
    try:
        return ArtifactReadResult(path.read_bytes())
    except OSError as exc:
        return ArtifactReadResult(b"", _issue(path, artifact_name, exc))
