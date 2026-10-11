"""Byte-bound evidence for one explicitly selected suspension incident.

This is a final-publication exception, never an API, partition or generic
retention bypass. Old rows are evidence only and are never merged into a reply.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import sys
from collections import OrderedDict
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd
import pyarrow.parquet as pq

from src.contracts.suspension_quarantine import (
    APPROVED_KEYS,
    COMBINED_POLICY_ID,
    MAX_HISTORY_WITNESSES,
    MAX_QUARANTINED_SECURITIES,
    POLICY_ID,
    SECURITY_POLICY_ID,
    SecuritySuspensionQuarantine,
    SuspensionQuarantine,
    incident_for_policy,
)
from src.data._atomic_io import atomic_write_parquet
from src.data.tushare import aggregate_response as aggregate
from src.data.tushare.aggregate_response import AggregateResponseError
from src.data.tushare.fetch_manifest import read_manifest_for_quarantine_refresh
from src.data.tushare.quarantine_lineage import (
    preserve_lineage_record,
    read_security_lineage,
    verify_security_lineage,
)
from src.data.tushare.quarantine_transaction import (
    QuarantineTransactionError,
    begin_quarantine_publication,
    pending_quarantine_exists,
)

EVIDENCE_DIRECTORY = "_suspension_quarantine"
MAX_LINEAGE_RAW_BYTES = 256 * 1024 * 1024
MAX_LINEAGE_DECODED_BYTES = 16 * 1024 * 1024 * 1024


def validate_quarantine_query(start_date: str, end_date: str, *, policy: str = POLICY_ID) -> None:
    """Do not accept a request that cannot observe the entire known incident."""
    for value in (start_date, end_date):
        if (not isinstance(value, str) or len(value) != 8
                or not value.isascii() or not value.isdigit()):
            raise AggregateResponseError("suspension quarantine requires real YYYYMMDD bounds")
        try:
            datetime.strptime(value, "%Y%m%d")
        except ValueError as exc:
            raise AggregateResponseError("suspension quarantine requires real YYYYMMDD bounds") from exc
    if policy == SECURITY_POLICY_ID:
        if start_date > end_date:
            raise AggregateResponseError("security suspension quarantine refuses reversed query bounds")
        return
    dates = incident_for_policy(policy).dates
    if start_date > min(dates) or end_date < max(dates):
        scope = "all eight approved keys" if policy == POLICY_ID else "the approved conflict date"
        raise AggregateResponseError(f"suspension quarantine query must cover {scope}")


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


def _read_frame(
    path: Path, expected: str | None = None, *, witness_budget: dict[str, int] | None = None,
    lineage_budget: dict[str, int] | None = None,
) -> tuple[pd.DataFrame, str]:
    """Bound compressed bytes, footer, row count, decoded bytes and final frame."""
    try:
        if lineage_budget is not None:
            _check_path(path)
            _consume_lineage_budget(lineage_budget, "compressed", path.stat().st_size)
        if witness_budget is not None:
            _check_path(path)
            _consume_witness_budget(witness_budget, "compressed", path.stat().st_size)
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
            if witness_budget is not None:
                _consume_witness_budget(witness_budget, "footer", decoded_size)
            if lineage_budget is not None:
                _consume_lineage_budget(lineage_budget, "footer", decoded_size)
            frame = parquet.read(use_threads=False).to_pandas(use_threads=False)
        if witness_budget is not None:
            _consume_witness_budget(witness_budget, "decoded", int(frame.memory_usage(index=True, deep=True).sum()))
        if lineage_budget is not None:
            _consume_lineage_budget(lineage_budget, "decoded", int(frame.memory_usage(index=True, deep=True).sum()))
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


def _validate_history(
    candidate: pd.DataFrame, reference: pd.DataFrame, retained: pd.DataFrame, *, policy: str = POLICY_ID,
) -> tuple[str, ...]:
    """Allow only the selected incident; all other history remains lossless."""
    incident = incident_for_policy(policy)
    approved = incident.reference_keys
    affected_days = incident.affected_days
    incident_keys = approved | incident.conflict_keys
    for label, frame in (("candidate", candidate), ("reference", reference), ("retained", retained)):
        aggregate.validate_aggregate_frame(frame, "suspend_d", label=f"quarantine {label}")
        affected = {key for key in _keys(frame) if (key[0], key[1]) in affected_days}
        if policy == COMBINED_POLICY_ID:
            # This separately approved combination is exact, not two composable
            # waivers: eight omissions AND the recorded R+S pair must coexist.
            observed = (approved - APPROVED_KEYS) | incident.conflict_keys
            allowed: tuple[frozenset[tuple[str, str, str | None, str]], ...] = ((approved,) if label == "reference" else
                       (observed,) if label == "candidate" else (approved, observed))
            if not any(affected == expected for expected in allowed):
                raise AggregateResponseError(f"suspension quarantine {label} has unapproved combined payload")
        elif incident.conflict_keys:
            # The exact original R+S pair remains whole audit evidence;
            # only S is permitted as the newly observed candidate payload.
            allowed = ((approved, incident_keys) if label == "reference" else
                       (incident.conflict_keys,) if label == "candidate" else
                       (approved, incident.conflict_keys, incident_keys))
            if not any(affected == expected for expected in allowed):
                raise AggregateResponseError(f"suspension quarantine {label} has unapproved conflict payload")
        elif not affected.issubset(approved):
            raise AggregateResponseError(f"suspension quarantine {label} has conflicting affected-date keys")
    if not approved.issubset(_keys(reference)):
        raise AggregateResponseError("suspension quarantine reference does not contain all eight approved keys")
    for label, frame in (("original reference", reference), ("retained file", retained)):
        # The unchanged generic guard still owns all keys outside this exact set.
        other = frame.loc[[key not in incident_keys for key in (
            tuple(None if pd.isna(value) else value for value in row)
            for row in frame.loc[:, list(aggregate.AGGREGATE_FIELDS["suspend_d"])].itertuples(index=False, name=None)
        )]]
        aggregate.require_retained_keys(candidate, other, "suspend_d", label=f"quarantine {label}")
        if label == "original reference":
            aggregate.require_retained_keys(retained, other, "suspend_d", label="quarantine retained lineage")
    return tuple(sorted(key[1] for key in approved - _keys(candidate)))


def _evidence_path(raw_dir: Path, digest: str) -> Path:
    return raw_dir / EVIDENCE_DIRECTORY / f"{digest}.parquet"


def _consume_witness_budget(budget: dict[str, int], kind: str, size: int) -> None:
    budget[kind] += size
    if budget[kind] > aggregate.MAX_CANDIDATE_BYTES:
        raise AggregateResponseError(f"security suspension quarantine cumulative witness {kind} budget exceeded")


def _consume_lineage_budget(budget: dict[str, int], kind: str, size: int) -> None:
    budget[kind] += size
    limit = MAX_LINEAGE_RAW_BYTES if kind == "compressed" else MAX_LINEAGE_DECODED_BYTES
    if budget[kind] > limit:
        raise AggregateResponseError(f"security suspension quarantine cumulative lineage {kind} budget exceeded")


def _read_security_witnesses(
    raw_dir: Path, hashes: tuple[str, ...], *, lineage_budget: dict[str, int] | None = None,
) -> dict[str, pd.DataFrame]:
    # A transition can inspect at most 32 previous witnesses plus the previous
    # retained snapshot before pruning. Persisted evidence still permits only 32.
    if len(hashes) > MAX_HISTORY_WITNESSES + 1:
        raise AggregateResponseError("security suspension quarantine witness count exceeded")
    budget = {"compressed": 0, "footer": 0, "decoded": 0}
    return {
        digest: _read_frame(
            _evidence_path(raw_dir, digest), digest, witness_budget=budget, lineage_budget=lineage_budget,
        )[0]
        for digest in hashes
    }


def _security_key_sets(
    candidate: pd.DataFrame, reference: pd.DataFrame, retained: pd.DataFrame,
    witnesses: dict[str, pd.DataFrame],
) -> tuple[set[tuple[str | None, ...]], set[tuple[str | None, ...]]]:
    """Bound the complete key comparison, not just each decoded input frame."""
    candidate_keys: set[tuple[str | None, ...]] = set()
    required_keys: set[tuple[str | None, ...]] = set()
    unique_rows = key_bytes = 0
    for frame, target in (
        (candidate, candidate_keys), (reference, required_keys), (retained, required_keys),
        *((frame, required_keys) for frame in witnesses.values()),
    ):
        for row in frame.loc[:, list(aggregate.AGGREGATE_FIELDS["suspend_d"])].itertuples(index=False, name=None):
            key = tuple(None if pd.isna(value) else value for value in row)
            if key in target:
                continue
            if key not in candidate_keys and key not in required_keys:
                unique_rows += 1
            target.add(key)
            key_bytes += sys.getsizeof(key) + sum(sys.getsizeof(value) for value in key)
            if (unique_rows > aggregate.MAX_CANDIDATE_ROWS
                    or key_bytes + sys.getsizeof(candidate_keys) + sys.getsizeof(required_keys)
                    > aggregate.MAX_CANDIDATE_BYTES):
                raise AggregateResponseError("security suspension quarantine combined key budget exceeded")
    return candidate_keys, required_keys


def _validate_security_history(
    candidate: pd.DataFrame, reference: pd.DataFrame, retained: pd.DataFrame,
    witnesses: dict[str, pd.DataFrame], *, start_date: str, end_date: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Derive attributable loss from actual frames, never a supplied allowlist."""
    for label, frame in (
        ("candidate", candidate), ("reference", reference), ("retained", retained),
        *((f"witness {digest}", frame) for digest, frame in witnesses.items()),
    ):
        aggregate.validate_aggregate_frame(
            frame, "suspend_d", label=f"security quarantine {label}",
            start_date=start_date, end_date=end_date,
        )
        if any(re.fullmatch(r"[0-9]{6}\.(?:SH|SZ|BJ)", code) is None for code in frame["ts_code"]):
            raise AggregateResponseError(f"security suspension quarantine {label} has invalid stock identity")
    if candidate.empty:
        raise AggregateResponseError("security suspension quarantine refuses an empty candidate")
    candidate_keys, required_keys = _security_key_sets(candidate, reference, retained, witnesses)
    missing = required_keys - candidate_keys
    # Additions, including a legal R+S pair or historical backfill, are not loss.
    codes = tuple(sorted({str(key[0]) for key in missing}))
    baseline_codes = {key[0] for key in required_keys}
    if len(codes) > MAX_QUARANTINED_SECURITIES or (codes and 2 * len(codes) >= len(baseline_codes)):
        raise AggregateResponseError("security suspension quarantine refuses systemic/count/minority loss")
    return tuple(sorted({str(key[1]) for key in missing})), codes


def _security_transition(
    raw_dir: Path, candidate: pd.DataFrame, reference: pd.DataFrame, retained: pd.DataFrame,
    prior: SuspensionQuarantine | None, *, reference_hash: str | None, retained_hash: str | None,
    start_date: str, end_date: str,
    lineage_budget: dict[str, int] | None = None,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    pool: tuple[str, ...] = ()
    if prior is not None:
        if not isinstance(prior, SecuritySuspensionQuarantine):
            raise AggregateResponseError("security suspension quarantine requires matching typed prior evidence")
        pool = tuple(sorted(set((*prior.history_sha256, prior.retained_sha256)) - {reference_hash, retained_hash}))
    witnesses = _read_security_witnesses(raw_dir, pool, lineage_budget=lineage_budget)
    # Validate every inspected historical input before selecting required proofs.
    _validate_security_history(
        candidate, reference, retained, witnesses, start_date=start_date, end_date=end_date,
    )
    covered = _keys(reference) | _keys(retained) | _keys(candidate)
    history = tuple(digest for digest, frame in witnesses.items() if _keys(frame) - covered)
    if len(history) > MAX_HISTORY_WITNESSES:
        raise AggregateResponseError("security suspension quarantine witness count exceeded")
    # Only the evidence that will actually be persisted defines the minority
    # denominator; discarded historical files cannot inflate it.
    missing, codes = _validate_security_history(
        candidate, reference, retained, {digest: witnesses[digest] for digest in history},
        start_date=start_date, end_date=end_date,
    )
    return missing, codes, history


def _verify_security_chain_business(
    raw_dir: Path, evidence: SecuritySuspensionQuarantine,
    nodes: tuple[SecuritySuspensionQuarantine, ...],
) -> None:
    """Validate every actual ancestor transition without retaining an unbounded raw cache."""
    budget = {"compressed": 0, "footer": 0, "decoded": 0}
    reference, _ = _read_frame(_evidence_path(raw_dir, evidence.reference_sha256),
                               evidence.reference_sha256, lineage_budget=budget)
    # The live head always gets its own actual byte check, even if its hash also
    # appears in an earlier generation. Only immutable archive reads are cached.
    live, _ = _read_frame(raw_dir / "suspend_d.parquet", evidence.candidate_sha256, lineage_budget=budget)
    frames: OrderedDict[str, pd.DataFrame] = OrderedDict()

    def archive(digest: str) -> pd.DataFrame:
        path = _evidence_path(raw_dir, digest)
        _check_path(path)
        if digest == evidence.reference_sha256:
            return reference
        if digest not in frames:
            frames[digest] = _read_frame(path, digest, lineage_budget=budget)[0]
        frames.move_to_end(digest)
        frame = frames[digest]
        while len(frames) > 2:
            frames.popitem(last=False)
        return frame

    # Reuse a semantic verdict only for identical raw/pool hashes and a query
    # covering the already-validated bounds. Date-only/idempotent updates do not
    # repeatedly decode their entire ancestry. This is operation-local, never a
    # persisted or cross-request validation bypass.
    verdicts: dict[tuple[str, str, tuple[str, ...]],
                   tuple[str, str, tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = {}
    parent: SecuritySuspensionQuarantine | None = None
    for node in reversed(nodes):
        pool = tuple(sorted(set((*parent.history_sha256, parent.retained_sha256))
                            - {node.reference_sha256, node.retained_sha256})) if parent is not None else ()
        key = (node.retained_sha256, node.candidate_sha256, pool)
        cached = verdicts.get(key)
        if cached is not None and node.query_start_date <= cached[0] and node.query_end_date >= cached[1]:
            missing, codes, history = cached[2:]
        else:
            retained = archive(node.retained_sha256)
            candidate = live if node == evidence else archive(node.candidate_sha256)
            missing, codes, history = _security_transition(
                raw_dir, candidate, reference, retained, parent,
                reference_hash=node.reference_sha256, retained_hash=node.retained_sha256,
                start_date=node.query_start_date, end_date=node.query_end_date, lineage_budget=budget,
            )
            verdicts[key] = (node.query_start_date, node.query_end_date, missing, codes, history)
        if (missing != node.missing_dates or codes != node.quarantined_ts_codes or history != node.history_sha256):
            raise AggregateResponseError("security suspension quarantine ancestor lineage disagrees with actual transition")
        parent = node


def verify_quarantine_evidence(raw_dir: Path, evidence: SuspensionQuarantine) -> None:
    """Recheck actual bytes and business keys independently at each consumer gate."""
    try:
        if not isinstance(evidence, SuspensionQuarantine):
            raise ValueError("suspension quarantine requires typed evidence")
        SuspensionQuarantine.from_dict(evidence.to_dict())
    except ValueError as exc:
        raise AggregateResponseError(str(exc)) from exc
    raw_dir = Path(raw_dir)
    try:
        nodes = (read_security_lineage(raw_dir, evidence)
                 if isinstance(evidence, SecuritySuspensionQuarantine) else ())
    except (OSError, ValueError) as exc:
        raise AggregateResponseError(f"security suspension quarantine lineage is invalid: {exc}") from exc
    if isinstance(evidence, SecuritySuspensionQuarantine):
        _verify_security_chain_business(raw_dir, evidence, nodes)
        return
    reference, _ = _read_frame(_evidence_path(raw_dir, evidence.reference_sha256), evidence.reference_sha256)
    retained, _ = _read_frame(_evidence_path(raw_dir, evidence.retained_sha256), evidence.retained_sha256)
    candidate, _ = _read_frame(raw_dir / "suspend_d.parquet", evidence.candidate_sha256)
    aggregate.validate_aggregate_frame(
        candidate, "suspend_d", label="quarantine candidate",
        start_date=evidence.query_start_date, end_date=evidence.query_end_date,
    )
    missing = _validate_history(candidate, reference, retained, policy=evidence.policy_id)
    if missing != evidence.missing_dates:
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
    start_date: str, end_date: str, policy: str = POLICY_ID,
) -> SuspensionQuarantine | None:
    """Publish exact checked vendor rows, returning evidence only while incomplete."""
    incident = None if policy == SECURITY_POLICY_ID else incident_for_policy(policy)
    validate_quarantine_query(start_date, end_date, policy=policy)
    aggregate.validate_aggregate_frame(candidate, "suspend_d", label="quarantine candidate",
                                       start_date=start_date, end_date=end_date)
    _check_path(raw_dir, directory=True)
    path = raw_dir / "suspend_d.parquet"
    if prior is not None:
        if prior.policy_id != policy:
            raise AggregateResponseError("suspension quarantine requires matching prior policy")
        verify_quarantine_evidence(raw_dir, prior)
        if isinstance(prior, SecuritySuspensionQuarantine):
            manifest = read_manifest_for_quarantine_refresh(
                raw_dir / "fetch_manifest.json", policy=policy,
                start_date=start_date, end_date=end_date, enabled=True,
            )
            coverage = manifest.endpoints.get("suspend_d") if manifest is not None else None
            if (coverage is None or coverage.status != "holes" or len(coverage.holes) != 1
                    or coverage.holes[0].quarantine != prior):
                raise AggregateResponseError("security suspension quarantine requires its actual published manifest prior")
    retained, retained_hash = (_read_frame(path) if path.exists() or path.is_symlink() else (
        pd.DataFrame(columns=list(aggregate.AGGREGATE_FIELDS["suspend_d"])), None,
    ))
    reference_hash: str | None
    if prior is not None:
        reference, reference_hash = _read_frame(_evidence_path(raw_dir, prior.reference_sha256), prior.reference_sha256)
    else:
        reference, reference_hash = retained, retained_hash
    history: tuple[str, ...] = ()
    codes: tuple[str, ...] = ()
    if policy == SECURITY_POLICY_ID:
        missing, codes, history = _security_transition(
            raw_dir, candidate, reference, retained, prior,
            reference_hash=reference_hash, retained_hash=retained_hash,
            start_date=start_date, end_date=end_date,
        )
    elif (incident is not None and policy != COMBINED_POLICY_ID and prior is None
            and incident.reference_keys.issubset(_keys(candidate))):
        # No exception is needed for a complete first acquisition.
        aggregate.require_retained_keys(candidate, retained, "suspend_d", label="suspend_d: retained file")
        affected_days = incident.affected_days
        if any((key[0], key[1]) in affected_days and key not in incident.reference_keys
               for key in _keys(candidate)):
            raise AggregateResponseError("suspension quarantine candidate has conflicting affected-date keys")
        missing = ()
    else:
        missing = _validate_history(candidate, reference, retained, policy=policy)
    prepared = path.with_name(f".suspend_d.{uuid4().hex}.parquet")
    try:
        atomic_write_parquet(candidate, prepared)
        candidate_hash = _sha256(prepared)
        evidence: SuspensionQuarantine | None = None
        if prior is not None and retained_hash is not None:
            _preserve(raw_dir, path, retained_hash)
        if missing:
            if reference_hash is None or retained_hash is None:
                raise AggregateResponseError("suspension quarantine requires original retained evidence")
            _preserve(raw_dir, path, retained_hash)
            if policy == SECURITY_POLICY_ID:
                predecessor_hash = (preserve_lineage_record(raw_dir, prior)
                                    if isinstance(prior, SecuritySuspensionQuarantine) else None)
                evidence = SecuritySuspensionQuarantine(
                    policy_id=policy, missing_dates=missing,
                    reference_sha256=reference_hash, retained_sha256=retained_hash,
                    candidate_sha256=candidate_hash, query_start_date=start_date, query_end_date=end_date,
                    quarantined_ts_codes=codes, history_sha256=history,
                    predecessor_sha256=predecessor_hash,
                )
                preserve_lineage_record(raw_dir, evidence)
                verify_security_lineage(raw_dir, evidence)
            else:
                evidence = SuspensionQuarantine(
                    policy_id=policy, missing_dates=missing,
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
                quarantine_after=evidence, query_start_date=start_date, query_end_date=end_date, policy=policy,
            )
        prepared.replace(path)
        return evidence
    except (OSError, QuarantineTransactionError) as exc:
        raise AggregateResponseError("suspension quarantine evidence publication failed; preserve retained data") from exc
    finally:
        prepared.unlink(missing_ok=True)
