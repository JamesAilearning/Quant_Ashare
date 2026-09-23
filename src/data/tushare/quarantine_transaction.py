"""Crash-closed publication of the one approved suspension-history exception.

This module is intentionally standard-library + contracts only. It binds bytes
and metadata; the acquisition boundary still proves the actual business keys.
A pending journal is not a partial-data override. Ordinary readers must refuse
it until an explicit matching refresh recovers or finishes the transaction.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import stat
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.contracts.suspension_quarantine import (
    APPROVED_KEYS,
    POLICY_ID,
    QUARANTINE_REASON,
    SuspensionQuarantine,
    validate_policy,
)

PENDING_FILENAME = "_suspension_quarantine_pending.json"
JOURNAL_SCHEMA_VERSION = 1
MAX_JOURNAL_BYTES = 64 * 1024
_MAX_MANIFEST_BYTES = 16 * 1024 * 1024
_MAX_RAW_BYTES = 256 * 1024 * 1024
_MANIFEST_FILENAME = "fetch_manifest.json"
_RAW_FILENAME = "suspend_d.parquet"
_EVIDENCE_DIRECTORY = "_suspension_quarantine"
_FIELDS = frozenset({
    "schema_version", "policy_id", "retained_sha256", "candidate_sha256",
    "before_manifest_sha256", "after_manifest_sha256", "quarantine_after",
    "query_start_date", "query_end_date",
})
_logger = logging.getLogger(__name__)


class QuarantineTransactionError(ValueError):
    """A publication cannot be proved safe; preserve all evidence and refuse."""


def _check_path(path: Path, *, directory: bool | None = None) -> None:
    """Reject links/reparse points in any existing component, including parents."""
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            if item == path and directory is not None:
                raise
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise QuarantineTransactionError(f"pending quarantine path is a link/reparse point: {item}")
        if item != path and not stat.S_ISDIR(info.st_mode):
            raise QuarantineTransactionError(f"pending quarantine parent is not a directory: {item}")
        if item == path and directory is not None:
            valid = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
            if not valid:
                raise QuarantineTransactionError(f"pending quarantine path has the wrong file type: {item}")


def pending_quarantine_exists(raw_dir: Path) -> bool:
    """Inspect the marker without parsing it; malformed markers still block."""
    path = Path(raw_dir) / PENDING_FILENAME
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    # Only this incident's persisted marker authorizes the stricter path
    # boundary; an ordinary clean raw directory retains its legacy behavior.
    _check_path(path)
    return True


def assert_no_pending_quarantine(raw_dir: Path) -> None:
    """No default, missing-manifest, reset or broad-hole path may ignore pending."""
    if pending_quarantine_exists(raw_dir):
        raise QuarantineTransactionError(
            "pending suspension quarantine publication; preserve evidence and run "
            "an explicit matching non-dry suspend_d refresh to recover"
        )


def _digest(path: Path, *, limit: int, optional: bool = False) -> str | None:
    _check_path(path)
    try:
        _check_path(path, directory=False)
    except FileNotFoundError:
        if optional:
            return None  # fallback-ok: absence is the explicit pre-publication manifest state, not unreadable data.
        raise
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            if size > limit:
                raise QuarantineTransactionError(f"pending quarantine file exceeds byte budget: {path}")
            digest.update(chunk)
    _check_path(path, directory=False)
    return digest.hexdigest()


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise QuarantineTransactionError(f"duplicate pending quarantine JSON key: {key}")
        result[key] = value
    return result


def _read_json(path: Path, *, limit: int) -> dict[str, Any]:
    _check_path(path, directory=False)
    with path.open("rb") as stream:
        payload = stream.read(limit + 1)
    if len(payload) > limit:
        raise QuarantineTransactionError(f"pending quarantine JSON exceeds byte budget: {path}")
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_object)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise QuarantineTransactionError(f"unreadable pending quarantine JSON: {path}") from exc
    if not isinstance(value, dict):
        raise QuarantineTransactionError("pending quarantine JSON must be an object")
    return value


def _bounds(start: object, end: object) -> tuple[str, str]:
    for value in (start, end):
        if not isinstance(value, str) or re.fullmatch(r"[0-9]{8}", value) is None:
            raise QuarantineTransactionError("pending quarantine dates must be ASCII YYYYMMDD")
        try:
            datetime.strptime(value, "%Y%m%d")
        except ValueError as exc:
            raise QuarantineTransactionError("pending quarantine dates must be real dates") from exc
    assert isinstance(start, str) and isinstance(end, str)
    dates = {key[1] for key in APPROVED_KEYS}
    if start > min(dates) or end < max(dates):
        raise QuarantineTransactionError("pending quarantine range must cover all eight approved dates")
    return start, end


def _validate_journal(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _FIELDS:
        raise QuarantineTransactionError("pending quarantine journal must contain exactly its nine fields")
    if type(value["schema_version"]) is not int or value["schema_version"] != JOURNAL_SCHEMA_VERSION:
        raise QuarantineTransactionError("unsupported pending quarantine journal schema")
    if validate_policy(value["policy_id"]) != POLICY_ID:
        raise QuarantineTransactionError("pending quarantine journal requires the explicit policy")
    for field in ("retained_sha256", "candidate_sha256", "before_manifest_sha256", "after_manifest_sha256"):
        digest = value[field]
        if field in {"before_manifest_sha256", "after_manifest_sha256"} and digest is None:
            continue
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise QuarantineTransactionError(f"pending quarantine {field} must be lowercase SHA256")
    start, end = _bounds(value["query_start_date"], value["query_end_date"])
    if value["quarantine_after"] is not None:
        evidence = SuspensionQuarantine.from_dict(value["quarantine_after"])
        if (evidence.retained_sha256 != value["retained_sha256"]
                or evidence.candidate_sha256 != value["candidate_sha256"]
                or evidence.query_start_date != start or evidence.query_end_date != end):
            raise QuarantineTransactionError("pending quarantine evidence does not bind the publication")
    return value


def _read_pending(raw_dir: Path) -> dict[str, Any]:
    return _validate_journal(_read_json(raw_dir / PENDING_FILENAME, limit=MAX_JOURNAL_BYTES))


def _write_pending(raw_dir: Path, journal: dict[str, Any], *, create: bool) -> None:
    """Publish complete fsynced bytes; exclusive initial link cannot overwrite."""
    payload = json.dumps(_validate_journal(journal), sort_keys=True, ensure_ascii=False).encode("utf-8")
    if len(payload) > MAX_JOURNAL_BYTES:
        raise QuarantineTransactionError("pending quarantine journal exceeds byte budget")
    _check_path(raw_dir, directory=True)
    path = raw_dir / PENDING_FILENAME
    _check_path(path)
    temporary = raw_dir / f".quarantine-journal-{uuid4().hex}.tmp"
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if create:
            os.link(temporary, path)
        else:
            _check_path(path, directory=False)
            os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _snapshot(raw_dir: Path, digest: str) -> Path:
    return raw_dir / _EVIDENCE_DIRECTORY / f"{digest}.parquet"


def _require_digest(path: Path, expected: str, *, limit: int = _MAX_RAW_BYTES) -> None:
    if _digest(path, limit=limit) != expected:
        raise QuarantineTransactionError(f"pending quarantine SHA mismatch: {path}")


def begin_quarantine_publication(
    raw_dir: Path, *, retained_sha256: str, candidate_sha256: str,
    quarantine_after: SuspensionQuarantine | None,
    query_start_date: str, query_end_date: str,
) -> None:
    """Bind the transition BEFORE raw replacement; retained archive is required."""
    raw_dir = Path(raw_dir)
    assert_no_pending_quarantine(raw_dir)
    if quarantine_after is not None and not isinstance(quarantine_after, SuspensionQuarantine):
        raise QuarantineTransactionError("quarantine_after must be typed evidence or None")
    journal = _validate_journal({
        "schema_version": JOURNAL_SCHEMA_VERSION, "policy_id": POLICY_ID,
        "retained_sha256": retained_sha256, "candidate_sha256": candidate_sha256,
        "before_manifest_sha256": _digest(raw_dir / _MANIFEST_FILENAME, limit=_MAX_MANIFEST_BYTES, optional=True),
        "after_manifest_sha256": None,
        "quarantine_after": quarantine_after.to_dict() if quarantine_after is not None else None,
        "query_start_date": query_start_date, "query_end_date": query_end_date,
    })
    _require_digest(raw_dir / _RAW_FILENAME, retained_sha256)
    _require_digest(_snapshot(raw_dir, retained_sha256), retained_sha256)
    _write_pending(raw_dir, journal, create=True)


def _validate_manifest_target(manifest: Mapping[str, Any], journal: dict[str, Any]) -> None:
    endpoints = manifest.get("endpoints")
    coverage = endpoints.get("suspend_d") if isinstance(endpoints, dict) else None
    if not isinstance(coverage, dict):
        raise QuarantineTransactionError("pending quarantine commit requires suspend_d coverage")
    if (coverage.get("coverage_start_date") != journal["query_start_date"]
            or coverage.get("coverage_end_date") != journal["query_end_date"]
            or type(coverage.get("units_written")) is not int or coverage["units_written"] != 1):
        raise QuarantineTransactionError("pending quarantine commit does not attest its real write and exact range")
    holes = coverage.get("holes")
    evidence = journal["quarantine_after"]
    version = manifest.get("schema_version")
    if evidence is None:
        if type(version) is not int or version != 1 or coverage.get("status") != "complete" or holes != []:
            raise QuarantineTransactionError("pending full recovery requires complete suspension coverage without holes")
    elif (type(version) is not int or version != 2 or coverage.get("status") != "holes"
          or not isinstance(holes, list) or len(holes) != 1 or not isinstance(holes[0], dict)
          or holes[0].get("unit") != "file" or holes[0].get("reason_class") != QUARANTINE_REASON
          or holes[0].get("quarantine") != evidence):
        raise QuarantineTransactionError("pending quarantine commit must preserve the exact intended incident evidence")


def prepare_quarantine_manifest_commit(
    raw_dir: Path, manifest: Mapping[str, Any], payload: bytes,
) -> dict[str, Any] | None:
    """Validate final metadata and durably bind its exact bytes before writing."""
    raw_dir = Path(raw_dir)
    if not pending_quarantine_exists(raw_dir):
        return None
    journal = _read_pending(raw_dir)
    _validate_manifest_target(manifest, journal)
    _require_digest(raw_dir / _RAW_FILENAME, journal["candidate_sha256"])
    _require_digest(_snapshot(raw_dir, journal["retained_sha256"]), journal["retained_sha256"])
    after = hashlib.sha256(payload).hexdigest()
    current = _digest(raw_dir / _MANIFEST_FILENAME, limit=_MAX_MANIFEST_BYTES, optional=True)
    if current != journal["before_manifest_sha256"] and not (
        journal["after_manifest_sha256"] == current == after
    ):
        raise QuarantineTransactionError("pending quarantine prior manifest SHA changed")
    if len(payload) > _MAX_MANIFEST_BYTES:
        raise QuarantineTransactionError("pending quarantine manifest exceeds byte budget")
    journal = {**journal, "after_manifest_sha256": after}
    _write_pending(raw_dir, journal, create=False)
    return journal


def _remove_pending(raw_dir: Path, journal: dict[str, Any]) -> None:
    if _read_pending(raw_dir) != journal:
        raise QuarantineTransactionError("pending quarantine journal changed before completion")
    (raw_dir / PENDING_FILENAME).unlink()


def finish_quarantine_manifest_commit(raw_dir: Path, journal: dict[str, Any] | None) -> None:
    """A failed check or deletion deliberately leaves the blocking marker."""
    if journal is None:
        return
    raw_dir = Path(raw_dir)
    _require_digest(raw_dir / _RAW_FILENAME, journal["candidate_sha256"])
    after = journal["after_manifest_sha256"]
    if after is None:
        raise QuarantineTransactionError("pending quarantine manifest has no committed byte binding")
    _require_digest(raw_dir / _MANIFEST_FILENAME, after, limit=_MAX_MANIFEST_BYTES)
    _remove_pending(raw_dir, journal)


def _copy_fsynced(source: Path, target: Path, digest: str, *, exclusive: bool) -> None:
    _require_digest(source, digest)
    _check_path(target.parent, directory=True)
    _check_path(target)
    temporary = target.parent / f".quarantine-copy-{uuid4().hex}.tmp"
    try:
        with source.open("rb") as incoming, temporary.open("xb") as outgoing:
            size = 0
            for chunk in iter(lambda: incoming.read(1024 * 1024), b""):
                size += len(chunk)
                if size > _MAX_RAW_BYTES:
                    raise QuarantineTransactionError("pending quarantine copy exceeds byte budget")
                outgoing.write(chunk)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        _require_digest(temporary, digest)
        if exclusive:
            try:
                os.link(temporary, target)
            except FileExistsError:
                _require_digest(target, digest)
        else:
            os.replace(temporary, target)
        _require_digest(target, digest)
    finally:
        temporary.unlink(missing_ok=True)


def recover_quarantine_publication(
    raw_dir: Path, *, policy: str | None, start_date: str, end_date: str,
    enabled: bool,
) -> bool:
    """Explicit recovery is hash-bound and requires a subsequent real refresh.

    An uncommitted candidate is archived and rolled back, never silently declared
    current. After rollback, a second interruption remains the old/old case.
    """
    raw_dir = Path(raw_dir)
    if not pending_quarantine_exists(raw_dir):
        return False
    if enabled is not True or validate_policy(policy) != POLICY_ID:
        raise QuarantineTransactionError("pending quarantine requires an explicit matching non-dry suspend_d refresh")
    start, end = _bounds(start_date, end_date)
    journal = _read_pending(raw_dir)
    if start > journal["query_start_date"] or end < journal["query_end_date"]:
        raise QuarantineTransactionError("pending quarantine recovery cannot narrow the recorded query range")
    raw_hash = _digest(raw_dir / _RAW_FILENAME, limit=_MAX_RAW_BYTES)
    manifest_hash = _digest(raw_dir / _MANIFEST_FILENAME, limit=_MAX_MANIFEST_BYTES, optional=True)
    retained = journal["retained_sha256"]
    candidate = journal["candidate_sha256"]
    _require_digest(_snapshot(raw_dir, retained), retained)
    if (raw_hash == candidate and journal["after_manifest_sha256"] is not None
            and manifest_hash == journal["after_manifest_sha256"]):
        _validate_manifest_target(_read_json(raw_dir / _MANIFEST_FILENAME, limit=_MAX_MANIFEST_BYTES), journal)
        finish_quarantine_manifest_commit(raw_dir, journal)
        _logger.warning("Recovered committed suspension quarantine publication; cleared pending marker; forcing refresh.")
        return True
    if manifest_hash != journal["before_manifest_sha256"] or raw_hash not in {retained, candidate}:
        raise QuarantineTransactionError("pending quarantine recovery found unknown raw/manifest bytes; preserve evidence")
    if raw_hash != retained:
        _copy_fsynced(raw_dir / _RAW_FILENAME, _snapshot(raw_dir, candidate), candidate, exclusive=True)
        # Archiving can take time: never overwrite bytes that changed after the
        # initial state check, even though the saved candidate is still valid.
        _require_digest(raw_dir / _RAW_FILENAME, candidate)
        if (_digest(raw_dir / _MANIFEST_FILENAME, limit=_MAX_MANIFEST_BYTES, optional=True)
                != journal["before_manifest_sha256"] or _read_pending(raw_dir) != journal):
            raise QuarantineTransactionError("pending quarantine state changed before rollback; preserve evidence")
        _copy_fsynced(_snapshot(raw_dir, retained), raw_dir / _RAW_FILENAME, retained, exclusive=False)
    _require_digest(raw_dir / _RAW_FILENAME, retained)
    if _digest(raw_dir / _MANIFEST_FILENAME, limit=_MAX_MANIFEST_BYTES, optional=True) != journal["before_manifest_sha256"]:
        raise QuarantineTransactionError("pending quarantine manifest changed during rollback")
    _remove_pending(raw_dir, journal)
    _logger.warning("Recovered uncommitted suspension quarantine publication to retained bytes; forcing real refresh.")
    return True
