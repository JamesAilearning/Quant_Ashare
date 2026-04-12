"""Run-artifact data-contract foundation for V2 (contract-only, no runtime execution semantics)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Mapping, Optional

RUN_ARTIFACT_CONTRACT_NAME = "v2-run-artifact-contract"
RUN_ARTIFACT_SOURCE_OF_TRUTH = "explicit_run_artifact_with_manifest"
RUN_ARTIFACT_ALLOWED_SOURCES = (RUN_ARTIFACT_SOURCE_OF_TRUTH,)

RUN_ARTIFACT_REQUIRED_METADATA_FIELDS = (
    "run_id",
    "run_kind",
    "produced_at",
    "config_fingerprint",
    "code_ref",
    "input_contract_snapshots",
    "schema_version",
)

RUN_ARTIFACT_OPERATOR_STATUS_FIELDS = (
    "contract_name",
    "contract_health",
    "run_id",
    "source_of_truth",
    "artifact_path",
    "manifest_path",
    "artifact_present",
    "manifest_present",
    "metadata_fields_present",
    "metadata_fields_missing",
    "produced_at",
    "reference_date",
    "lineage_consistency_status",
    "warnings",
    "errors",
    "governance_note",
    "runtime_execution_semantics_in_scope",
)

ISSUE_MISSING_ARTIFACT = "missing_artifact_file"
ISSUE_MISSING_MANIFEST = "missing_manifest_file"
ISSUE_SCHEMA_MISMATCH = "schema_mismatch"
ISSUE_MISSING_REPRO_METADATA = "missing_reproducibility_metadata"
ISSUE_LINEAGE_INCONSISTENCY = "lineage_inconsistency"
ISSUE_TEMPORAL_PROVENANCE_ANOMALY = "temporal_provenance_anomaly"

GOVERNANCE_NOTE = (
    "Informational run-artifact contract health only; runtime execution semantics remain out of scope."
)


class RunArtifactContractError(ValueError):
    """Raised when run-artifact contract boundaries are violated."""


@dataclass(frozen=True)
class RunArtifactProfile:
    """Normalized run-artifact snapshot used by contract validation."""

    artifact_path: Optional[str]
    manifest_path: Optional[str]
    artifact_present: bool
    manifest_present: bool
    metadata: Mapping[str, Any]
    has_schema_mismatch: bool = False
    has_lineage_inconsistency: bool = False
    has_temporal_provenance_anomaly: bool = False


@dataclass(frozen=True)
class RunArtifactContractInput:
    """Input boundary for run-artifact contract validation."""

    run_id: str
    source_of_truth: str = RUN_ARTIFACT_SOURCE_OF_TRUTH
    profile: RunArtifactProfile = field(
        default_factory=lambda: RunArtifactProfile(
            artifact_path=None,
            manifest_path=None,
            artifact_present=False,
            manifest_present=False,
            metadata={},
        )
    )
    allow_implicit_source_fallback: bool = False
    runtime_execution_controls: Mapping[str, Any] = field(default_factory=dict)
    reference_date: Optional[str] = None


@dataclass(frozen=True)
class RunArtifactContractStatus:
    """Operator-facing run-artifact contract status payload (informational by default)."""

    contract_name: str
    contract_health: str
    run_id: str
    source_of_truth: str
    artifact_path: Optional[str]
    manifest_path: Optional[str]
    artifact_present: bool
    manifest_present: bool
    metadata_fields_present: tuple[str, ...]
    metadata_fields_missing: tuple[str, ...]
    produced_at: Optional[str]
    reference_date: Optional[str]
    lineage_consistency_status: str
    warnings: tuple[str, ...]
    errors: tuple[str, ...]
    governance_note: str = GOVERNANCE_NOTE
    runtime_execution_semantics_in_scope: bool = False


class RunArtifactContract:
    """Run-artifact contract validator with explicit governance boundaries."""

    @staticmethod
    def list_source_of_truth_options() -> tuple[str, ...]:
        """Return allowed explicit source-of-truth options."""
        return RUN_ARTIFACT_ALLOWED_SOURCES

    @staticmethod
    def required_metadata_fields() -> tuple[str, ...]:
        """Return required reproducibility metadata fields."""
        return RUN_ARTIFACT_REQUIRED_METADATA_FIELDS

    @staticmethod
    def operator_status_fields() -> tuple[str, ...]:
        """Return required operator-facing status schema fields."""
        return RUN_ARTIFACT_OPERATOR_STATUS_FIELDS

    @staticmethod
    def _as_date(value: Optional[str]) -> Optional[date]:
        if value is None:
            return None
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return date.fromisoformat(text)
        except ValueError as exc:
            raise RunArtifactContractError(f"Invalid ISO date value: '{text}'.") from exc

    @classmethod
    def validate_input_boundary(cls, request: RunArtifactContractInput) -> None:
        if not str(request.run_id or "").strip():
            raise RunArtifactContractError("run_id is required.")

        if request.source_of_truth not in RUN_ARTIFACT_ALLOWED_SOURCES:
            raise RunArtifactContractError(
                f"Unsupported source_of_truth '{request.source_of_truth}'. "
                f"Allowed: {RUN_ARTIFACT_ALLOWED_SOURCES}."
            )

        if request.allow_implicit_source_fallback:
            raise RunArtifactContractError(
                "Implicit run-artifact source fallback is forbidden by run-artifact contract."
            )

        if request.runtime_execution_controls:
            raise RunArtifactContractError(
                "runtime_execution_controls are out of scope for run-artifact contract validation."
            )

        cls._as_date(request.reference_date)

    @classmethod
    def validate_and_build_status(cls, request: RunArtifactContractInput) -> RunArtifactContractStatus:
        """Validate run-artifact contract boundaries and emit operator-facing status payload."""
        cls.validate_input_boundary(request)
        profile = request.profile

        errors: list[str] = []

        if not profile.artifact_present:
            errors.append(ISSUE_MISSING_ARTIFACT)
        if not profile.manifest_present:
            errors.append(ISSUE_MISSING_MANIFEST)

        metadata = profile.metadata or {}
        present_fields = tuple(
            key for key in RUN_ARTIFACT_REQUIRED_METADATA_FIELDS if str(metadata.get(key, "")).strip()
        )
        missing_fields = tuple(key for key in RUN_ARTIFACT_REQUIRED_METADATA_FIELDS if key not in present_fields)
        if missing_fields:
            errors.append(ISSUE_MISSING_REPRO_METADATA)

        if profile.has_schema_mismatch:
            errors.append(ISSUE_SCHEMA_MISMATCH)

        lineage_status = "consistent"
        if profile.has_lineage_inconsistency:
            errors.append(ISSUE_LINEAGE_INCONSISTENCY)
            lineage_status = "inconsistent"
        else:
            run_id_from_metadata = str(metadata.get("run_id", "")).strip()
            if run_id_from_metadata and run_id_from_metadata != request.run_id:
                errors.append(ISSUE_LINEAGE_INCONSISTENCY)
                lineage_status = "inconsistent"

        reference = cls._as_date(request.reference_date)
        produced_at = str(metadata.get("produced_at", "")).strip() or None
        produced_date = cls._as_date(produced_at) if produced_at else None
        if profile.has_temporal_provenance_anomaly:
            errors.append(ISSUE_TEMPORAL_PROVENANCE_ANOMALY)
        elif reference is not None and produced_date is not None and produced_date > reference:
            errors.append(ISSUE_TEMPORAL_PROVENANCE_ANOMALY)

        unique_errors = tuple(dict.fromkeys(errors))
        health = "error" if unique_errors else "ok"

        return RunArtifactContractStatus(
            contract_name=RUN_ARTIFACT_CONTRACT_NAME,
            contract_health=health,
            run_id=request.run_id,
            source_of_truth=request.source_of_truth,
            artifact_path=profile.artifact_path,
            manifest_path=profile.manifest_path,
            artifact_present=profile.artifact_present,
            manifest_present=profile.manifest_present,
            metadata_fields_present=present_fields,
            metadata_fields_missing=missing_fields,
            produced_at=produced_at,
            reference_date=request.reference_date,
            lineage_consistency_status=lineage_status,
            warnings=(),
            errors=unique_errors,
        )
