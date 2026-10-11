"""Bounded publication lineage, independent of raw Parquet/business validation.

Hashes bind persisted evidence continuity; they are not vendor signatures or
authentication against replacement of an entire dataset and its audit records.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any

from src.contracts.suspension_quarantine import SecuritySuspensionQuarantine, SuspensionQuarantine

MAX_LINEAGE_RECORDS = 4096
MAX_LINEAGE_RECORD_BYTES = 64 * 1024
MAX_LINEAGE_BYTES = 16 * 1024 * 1024


def lineage_path(raw_dir: Path, digest: str) -> Path:
    return raw_dir / "_suspension_quarantine" / "lineage" / f"{digest}.json"


def lineage_payload(evidence: SecuritySuspensionQuarantine) -> bytes:
    evidence.__post_init__()
    payload = json.dumps(evidence.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    if len(payload) > MAX_LINEAGE_RECORD_BYTES:
        raise ValueError("security quarantine lineage record byte budget exceeded")
    return payload


def _check_path(path: Path, *, directory: bool = False) -> None:
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("security quarantine lineage refuses linked/reparse evidence")
        if item != path and not stat.S_ISDIR(info.st_mode):
            raise ValueError("security quarantine lineage parent is not a directory")
    if not (path.is_dir() if directory else path.is_file()):
        raise ValueError("security quarantine lineage record/directory unavailable")


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("security quarantine lineage contains duplicate JSON keys")
        result[key] = value
    return result


def _read_record(raw_dir: Path, digest: str) -> tuple[SecuritySuspensionQuarantine, int]:
    path = lineage_path(raw_dir, digest)
    _check_path(path)
    before = path.stat()
    if before.st_size > MAX_LINEAGE_RECORD_BYTES:
        raise ValueError("security quarantine lineage record byte budget exceeded")
    with path.open("rb") as stream:
        payload = stream.read(MAX_LINEAGE_RECORD_BYTES + 1)
    after = path.stat()
    if (len(payload) > MAX_LINEAGE_RECORD_BYTES
            or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            or hashlib.sha256(payload).hexdigest() != digest):
        raise ValueError("security quarantine lineage record SHA/identity mismatch")
    _check_path(path)
    try:
        evidence = SuspensionQuarantine.from_dict(json.loads(payload.decode("utf-8"), object_pairs_hook=_object))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("security quarantine lineage record is unreadable") from exc
    if not isinstance(evidence, SecuritySuspensionQuarantine) or payload != lineage_payload(evidence):
        raise ValueError("security quarantine lineage requires canonical same-policy evidence")
    return evidence, len(payload)


def preserve_lineage_record(raw_dir: Path, evidence: SecuritySuspensionQuarantine) -> str:
    """Publisher-only exclusive archival; never discover or synthesize ancestors."""
    payload = lineage_payload(evidence)
    digest = hashlib.sha256(payload).hexdigest()
    path = lineage_path(raw_dir, digest)
    for directory in (raw_dir, path.parent.parent, path.parent):
        if not directory.exists() and directory != raw_dir:
            _check_path(directory.parent, directory=True)
            directory.mkdir()
        _check_path(directory, directory=True)
    if path.exists() or path.is_symlink():
        stored, _ = _read_record(raw_dir, digest)
        if stored != evidence:
            raise ValueError("security quarantine immutable lineage changed")
        return digest
    created = False
    try:
        with path.open("xb") as output:
            created = True
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        stored, _ = _read_record(raw_dir, digest)
        if stored != evidence:
            raise ValueError("security quarantine lineage changed during preservation")
    except BaseException:
        if created:
            path.unlink(missing_ok=True)
        raise
    return digest


def read_security_lineage(
    raw_dir: Path, evidence: SecuritySuspensionQuarantine,
) -> tuple[SecuritySuspensionQuarantine, ...]:
    """Follow only hash-bound records, checking every edge through the root.

    The acquisition gate additionally verifies every node's real raw transition;
    structurally correct synthetic ancestors alone are not publication proof.
"""
    digest = hashlib.sha256(lineage_payload(evidence)).hexdigest()
    seen: set[str] = set()
    total = 0
    child: SecuritySuspensionQuarantine | None = None
    nodes: list[SecuritySuspensionQuarantine] = []
    while True:
        if digest in seen or len(seen) >= MAX_LINEAGE_RECORDS:
            raise ValueError("security quarantine lineage cycle/record budget exceeded")
        seen.add(digest)
        node, size = _read_record(raw_dir, digest)
        nodes.append(node)
        total += size
        if total > MAX_LINEAGE_BYTES:
            raise ValueError("security quarantine lineage cumulative byte budget exceeded")
        if child is None:
            if node != evidence:
                raise ValueError("security quarantine current lineage disagrees with evidence")
        else:
            if (child.policy_id != node.policy_id or child.reference_sha256 != node.reference_sha256
                    or child.retained_sha256 != node.candidate_sha256
                    or not set(child.history_sha256).issubset((*node.history_sha256, node.retained_sha256))):
                raise ValueError("security quarantine predecessor lineage continuity mismatch")
        if node.predecessor_sha256 is None:
            if node.retained_sha256 != node.reference_sha256 or node.history_sha256:
                raise ValueError("security quarantine lineage root requires original retained and empty history")
            return tuple(nodes)
        child, digest = node, node.predecessor_sha256


def verify_security_lineage(
    raw_dir: Path, evidence: SecuritySuspensionQuarantine,
) -> SecuritySuspensionQuarantine | None:
    """Standard-library commit gate; raw business validation remains acquisition-owned."""
    nodes = read_security_lineage(raw_dir, evidence)
    return nodes[1] if len(nodes) > 1 else None
