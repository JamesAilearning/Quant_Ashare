"""Byte-bound evidence for the single approved suspension-history incident.

This is a final-publication exception, never an API, partition or generic
retention bypass. Old rows are evidence only and are never merged into a reply.
"""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd
import pyarrow.parquet as pq

from src.contracts.suspension_quarantine import (
    APPROVED_KEYS,
    POLICY_ID,
    SuspensionQuarantine,
)
from src.data._atomic_io import atomic_write_parquet
from src.data.tushare import aggregate_response as aggregate
from src.data.tushare.aggregate_response import AggregateResponseError
from src.data.tushare.quarantine_transaction import (
    QuarantineTransactionError,
    begin_quarantine_publication,
    pending_quarantine_exists,
)

EVIDENCE_DIRECTORY = "_suspension_quarantine"
_DATES = frozenset(key[1] for key in APPROVED_KEYS)
_AFFECTED_DAYS = frozenset((key[0], key[1]) for key in APPROVED_KEYS)


def validate_quarantine_query(start_date: str, end_date: str) -> None:
    """Do not accept a request that cannot observe the entire known incident."""
    for value in (start_date, end_date):
        if (not isinstance(value, str) or len(value) != 8
                or not value.isascii() or not value.isdigit()):
            raise AggregateResponseError("suspension quarantine requires real YYYYMMDD bounds")
        try:
            datetime.strptime(value, "%Y%m%d")
        except ValueError as exc:
            raise AggregateResponseError("suspension quarantine requires real YYYYMMDD bounds") from exc
    if start_date > min(_DATES) or end_date < max(_DATES):
        raise AggregateResponseError("suspension quarantine query must cover all eight approved keys")


def _check_path(path: Path, *, directory: bool = False) -> None:
    """Evidence must remain local regular files, including on Windows junctions."""
    for part in (path, *path.parents):
        if not part.exists() and not part.is_symlink():
            continue
        info = part.lstat()
        if (stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
            raise AggregateResponseError(f"suspension quarantine refuses linked evidence: {part}")
    if directory:
        if not path.is_dir():
            raise AggregateResponseError(f"suspension quarantine evidence directory unavailable: {path}")
    elif not path.is_file():
        raise AggregateResponseError(f"suspension quarantine evidence file unavailable: {path}")


def _sha256(path: Path) -> str:
    _check_path(path)
    if path.stat().st_size > aggregate.MAX_CANDIDATE_BYTES:
        raise AggregateResponseError("suspension quarantine evidence file budget exceeded")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            if size > aggregate.MAX_CANDIDATE_BYTES:
                raise AggregateResponseError("suspension quarantine evidence file budget exceeded")
            digest.update(chunk)
    return digest.hexdigest()


def _read_frame(path: Path, expected: str | None = None) -> tuple[pd.DataFrame, str]:
    """Bound compressed bytes, footer, row count, decoded bytes and final frame."""
    try:
        before = _sha256(path)
        if expected is not None and before != expected:
            raise AggregateResponseError(f"suspension quarantine evidence SHA mismatch: {path.name}")
        # PyArrow releases differ in whether this constructor carries typing
        # metadata. Keep the dynamic library boundary explicit without a
        # version-dependent suppression of strict type-checking errors.
        parquet_file: Callable[..., Any] = pq.ParquetFile
        with parquet_file(
            path, thrift_string_size_limit=1024 * 1024, thrift_container_size_limit=100_000,
        ) as parquet:
            metadata = parquet.metadata
            if metadata.num_rows > aggregate.MAX_CANDIDATE_ROWS:
                raise AggregateResponseError("suspension quarantine evidence row budget exceeded")
            if (metadata.num_columns != len(aggregate.AGGREGATE_FIELDS["suspend_d"])
                    or set(parquet.schema_arrow.names) != set(aggregate.AGGREGATE_FIELDS["suspend_d"])):
                raise AggregateResponseError("suspension quarantine evidence schema mismatch")
            decoded_size = sum(metadata.row_group(i).total_byte_size for i in range(metadata.num_row_groups))
            if decoded_size > aggregate.MAX_CANDIDATE_BYTES:
                raise AggregateResponseError("suspension quarantine evidence decoded budget exceeded")
            frame = parquet.read(use_threads=False).to_pandas(use_threads=False)
        aggregate.validate_aggregate_frame(frame, "suspend_d", label="suspension quarantine evidence")
        if _sha256(path) != before:
            raise AggregateResponseError("suspension quarantine evidence changed while reading")
        return frame, before
    except AggregateResponseError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise AggregateResponseError(
            f"suspension quarantine evidence unreadable: {path.name} ({type(exc).__name__})"
        ) from exc


def _keys(frame: pd.DataFrame) -> set[tuple[str | None, ...]]:
    return {
        tuple(None if pd.isna(value) else value for value in row)
        for row in frame.loc[:, list(aggregate.AGGREGATE_FIELDS["suspend_d"])].itertuples(index=False, name=None)
    }


def _validate_history(candidate: pd.DataFrame, reference: pd.DataFrame, retained: pd.DataFrame) -> tuple[str, ...]:
    """Allow missing approved keys only; reject replacement payloads at those dates."""
    for label, frame in (("candidate", candidate), ("reference", reference), ("retained", retained)):
        aggregate.validate_aggregate_frame(frame, "suspend_d", label=f"quarantine {label}")
        if any((key[0], key[1]) in _AFFECTED_DAYS and key not in APPROVED_KEYS for key in _keys(frame)):
            raise AggregateResponseError(f"suspension quarantine {label} has conflicting affected-date keys")
    if not APPROVED_KEYS.issubset(_keys(reference)):
        raise AggregateResponseError("suspension quarantine reference does not contain all eight approved keys")
    for label, frame in (("original reference", reference), ("retained file", retained)):
        # The unchanged generic guard still owns all keys outside this exact set.
        other = frame.loc[[key not in APPROVED_KEYS for key in (
            tuple(None if pd.isna(value) else value for value in row)
            for row in frame.loc[:, list(aggregate.AGGREGATE_FIELDS["suspend_d"])].itertuples(index=False, name=None)
        )]]
        aggregate.require_retained_keys(candidate, other, "suspend_d", label=f"quarantine {label}")
        if label == "original reference":
            aggregate.require_retained_keys(retained, other, "suspend_d", label="quarantine retained lineage")
    return tuple(sorted(key[1] for key in APPROVED_KEYS - _keys(candidate)))


def _evidence_path(raw_dir: Path, digest: str) -> Path:
    return raw_dir / EVIDENCE_DIRECTORY / f"{digest}.parquet"


def verify_quarantine_evidence(raw_dir: Path, evidence: SuspensionQuarantine) -> None:
    """Recheck actual bytes and business keys independently at each consumer gate."""
    try:
        if not isinstance(evidence, SuspensionQuarantine):
            raise ValueError("suspension quarantine requires typed evidence")
        SuspensionQuarantine.from_dict(evidence.to_dict())
    except ValueError as exc:
        raise AggregateResponseError(str(exc)) from exc
    raw_dir = Path(raw_dir)
    reference, _ = _read_frame(_evidence_path(raw_dir, evidence.reference_sha256), evidence.reference_sha256)
    retained, _ = _read_frame(_evidence_path(raw_dir, evidence.retained_sha256), evidence.retained_sha256)
    candidate, _ = _read_frame(raw_dir / "suspend_d.parquet", evidence.candidate_sha256)
    aggregate.validate_aggregate_frame(
        candidate, "suspend_d", label="quarantine candidate",
        start_date=evidence.query_start_date, end_date=evidence.query_end_date,
    )
    if _validate_history(candidate, reference, retained) != evidence.missing_dates:
        raise AggregateResponseError("suspension quarantine missing dates disagree with actual bytes")


def _preserve(raw_dir: Path, source: Path, digest: str) -> None:
    directory = raw_dir / EVIDENCE_DIRECTORY
    if directory.exists() or directory.is_symlink():
        _check_path(directory, directory=True)
    else:
        _check_path(raw_dir, directory=True)
        directory.mkdir()
    target = _evidence_path(raw_dir, digest)
    if target.exists() or target.is_symlink():
        if _sha256(target) != digest:
            raise AggregateResponseError("suspension quarantine immutable evidence SHA mismatch")
        return
    # Exclusive creation never overwrites earlier evidence, even after a failed run.
    created = False
    try:
        with target.open("xb") as output:
            created = True
            size = 0
            with source.open("rb") as source_stream:
                for chunk in iter(lambda: source_stream.read(1024 * 1024), b""):
                    size += len(chunk)
                    if size > aggregate.MAX_CANDIDATE_BYTES:
                        raise AggregateResponseError("suspension quarantine retained copy budget exceeded")
                    output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if _sha256(target) != digest:
            raise AggregateResponseError("suspension quarantine retained source changed during preservation")
    except BaseException:
        if created:
            target.unlink(missing_ok=True)
        raise


def publish_suspension_candidate(
    raw_dir: Path, candidate: pd.DataFrame, *, prior: SuspensionQuarantine | None,
    start_date: str, end_date: str,
) -> SuspensionQuarantine | None:
    """Publish exact checked vendor rows, returning evidence only while incomplete."""
    validate_quarantine_query(start_date, end_date)
    aggregate.validate_aggregate_frame(candidate, "suspend_d", label="quarantine candidate",
                                       start_date=start_date, end_date=end_date)
    _check_path(raw_dir, directory=True)
    path = raw_dir / "suspend_d.parquet"
    if prior is not None:
        verify_quarantine_evidence(raw_dir, prior)
    retained, retained_hash = (_read_frame(path) if path.exists() or path.is_symlink() else (
        pd.DataFrame(columns=list(aggregate.AGGREGATE_FIELDS["suspend_d"])), None,
    ))
    reference_hash: str | None
    if prior is not None:
        reference, reference_hash = _read_frame(_evidence_path(raw_dir, prior.reference_sha256), prior.reference_sha256)
    else:
        reference, reference_hash = retained, retained_hash
    if prior is None and APPROVED_KEYS.issubset(_keys(candidate)):
        # No exception is needed for a complete first acquisition.
        aggregate.require_retained_keys(candidate, retained, "suspend_d", label="suspend_d: retained file")
        if any((key[0], key[1]) in _AFFECTED_DAYS and key not in APPROVED_KEYS for key in _keys(candidate)):
            raise AggregateResponseError("suspension quarantine candidate has conflicting affected-date keys")
        missing: tuple[str, ...] = ()
    else:
        missing = _validate_history(candidate, reference, retained)
    prepared = path.with_name(f".suspend_d.{uuid4().hex}.parquet")
    try:
        atomic_write_parquet(candidate, prepared)
        candidate_hash = _sha256(prepared)
        evidence = None
        if prior is not None and retained_hash is not None:
            _preserve(raw_dir, path, retained_hash)
        if missing:
            if reference_hash is None or retained_hash is None:
                raise AggregateResponseError("suspension quarantine requires original retained evidence")
            _preserve(raw_dir, path, retained_hash)
            evidence = SuspensionQuarantine(
                policy_id=POLICY_ID, missing_dates=missing,
                reference_sha256=reference_hash, retained_sha256=retained_hash,
                candidate_sha256=candidate_hash, query_start_date=start_date, query_end_date=end_date,
            )
        if retained_hash is not None and _sha256(path) != retained_hash:
            raise AggregateResponseError("suspension quarantine retained source changed before publication")
        if prior is not None:
            verify_quarantine_evidence(raw_dir, prior)
        if evidence is not None or prior is not None or pending_quarantine_exists(raw_dir):
            if retained_hash is None:
                raise AggregateResponseError("suspension quarantine transaction requires retained bytes")
            # Flush this owned candidate before committing its journal. Do not
            # strengthen the generic writer or claim filesystem-wide power-loss
            # atomicity: the journal handles process interruption/write failure.
            with prepared.open("rb+") as stream:
                os.fsync(stream.fileno())
            begin_quarantine_publication(
                raw_dir, retained_sha256=retained_hash, candidate_sha256=candidate_hash,
                quarantine_after=evidence, query_start_date=start_date, query_end_date=end_date,
            )
        prepared.replace(path)
        return evidence
    except (OSError, QuarantineTransactionError) as exc:
        raise AggregateResponseError("suspension quarantine evidence publication failed; preserve retained data") from exc
    finally:
        prepared.unlink(missing_ok=True)
