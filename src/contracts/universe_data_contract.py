"""Universe data-contract foundation for V2 (contract-only, no runtime selection semantics)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Mapping, Optional

UNIVERSE_CONTRACT_NAME = "v2-universe-data-contract"
UNIVERSE_SOURCE_OF_TRUTH = "explicit_universe_artifact_with_manifest"
UNIVERSE_ALLOWED_SOURCES = (UNIVERSE_SOURCE_OF_TRUTH,)

UNIVERSE_MODE_STATIC = "static"
UNIVERSE_MODE_TRADE_DATE = "trade_date"
UNIVERSE_MODE_RANGE = "range"
UNIVERSE_SUPPORTED_TEMPORAL_MODES = (
    UNIVERSE_MODE_STATIC,
    UNIVERSE_MODE_TRADE_DATE,
    UNIVERSE_MODE_RANGE,
)

UNIVERSE_REQUIRED_METADATA_FIELDS = (
    "universe_name",
    "source_name",
    "source_uri",
    "snapshot_at",
    "schema_version",
    "temporal_mode",
)

UNIVERSE_REQUIRED_BASE_COLUMNS = ("instrument", "in_universe")
UNIVERSE_REQUIRED_MODE_COLUMNS = {
    UNIVERSE_MODE_STATIC: (),
    UNIVERSE_MODE_TRADE_DATE: ("trade_date",),
    UNIVERSE_MODE_RANGE: ("effective_start", "effective_end"),
}

UNIVERSE_OPERATOR_STATUS_FIELDS = (
    "contract_name",
    "contract_health",
    "universe_name",
    "source_of_truth",
    "artifact_path",
    "manifest_path",
    "artifact_present",
    "manifest_present",
    "temporal_mode",
    "metadata_fields_present",
    "metadata_fields_missing",
    "snapshot_start",
    "snapshot_end",
    "rows",
    "columns_present",
    "stale_days",
    "coverage_ratio",
    "membership_consistency_status",
    "warnings",
    "errors",
    "governance_note",
    "runtime_selection_semantics_in_scope",
)

ISSUE_MISSING_ARTIFACT = "missing_artifact_file"
ISSUE_MISSING_MANIFEST = "missing_manifest_file"
ISSUE_SCHEMA_MISMATCH = "schema_mismatch"
ISSUE_STALE_DATA = "stale_data"
ISSUE_INCOMPLETE_COVERAGE = "incomplete_coverage"
ISSUE_INCONSISTENT_MEMBERSHIP = "inconsistent_membership"
ISSUE_TEMPORAL_LEAKAGE = "temporal_leakage"

GOVERNANCE_NOTE = (
    "Informational universe contract health only; runtime universe-selection semantics remain out of scope."
)


class UniverseDataContractError(ValueError):
    """Raised when universe contract boundaries are violated."""


@dataclass(frozen=True)
class UniverseArtifactProfile:
    """Normalized universe artifact snapshot used by contract validation."""

    artifact_path: Optional[str]
    manifest_path: Optional[str]
    artifact_present: bool
    manifest_present: bool
    metadata: Mapping[str, Any]
    rows: Optional[int]
    columns_present: tuple[str, ...] = ()
    snapshot_start: Optional[str] = None
    snapshot_end: Optional[str] = None
    stale_days: Optional[int] = None
    coverage_ratio: Optional[float] = None
    has_inconsistent_membership: bool = False
    has_future_effective_data: bool = False
    has_future_known_metadata: bool = False


@dataclass(frozen=True)
class UniverseContractInput:
    """Input boundary for universe contract validation."""

    universe_name: str
    source_of_truth: str = UNIVERSE_SOURCE_OF_TRUTH
    temporal_mode: str = UNIVERSE_MODE_STATIC
    profile: UniverseArtifactProfile = field(
        default_factory=lambda: UniverseArtifactProfile(
            artifact_path=None,
            manifest_path=None,
            artifact_present=False,
            manifest_present=False,
            metadata={},
            rows=None,
        )
    )
    allow_implicit_source_fallback: bool = False
    runtime_universe_controls: Mapping[str, Any] = field(default_factory=dict)
    stale_days_warn_threshold: int = 5
    min_coverage_ratio: float = 0.95
    reference_date: Optional[str] = None


@dataclass(frozen=True)
class UniverseContractStatus:
    """Operator-facing universe contract status payload (informational by default)."""

    contract_name: str
    contract_health: str
    universe_name: str
    source_of_truth: str
    artifact_path: Optional[str]
    manifest_path: Optional[str]
    artifact_present: bool
    manifest_present: bool
    temporal_mode: str
    metadata_fields_present: tuple[str, ...]
    metadata_fields_missing: tuple[str, ...]
    snapshot_start: Optional[str]
    snapshot_end: Optional[str]
    rows: Optional[int]
    columns_present: tuple[str, ...]
    stale_days: Optional[int]
    coverage_ratio: Optional[float]
    membership_consistency_status: str
    warnings: tuple[str, ...]
    errors: tuple[str, ...]
    governance_note: str = GOVERNANCE_NOTE
    runtime_selection_semantics_in_scope: bool = False


class UniverseDataContract:
    """Universe data-contract validator with explicit governance boundaries."""

    @staticmethod
    def list_source_of_truth_options() -> tuple[str, ...]:
        """Return allowed explicit source-of-truth options."""
        return UNIVERSE_ALLOWED_SOURCES

    @staticmethod
    def supported_temporal_modes() -> tuple[str, ...]:
        """Return supported temporal-validity modes."""
        return UNIVERSE_SUPPORTED_TEMPORAL_MODES

    @staticmethod
    def required_metadata_fields() -> tuple[str, ...]:
        """Return required provenance metadata fields."""
        return UNIVERSE_REQUIRED_METADATA_FIELDS

    @staticmethod
    def operator_status_fields() -> tuple[str, ...]:
        """Return required operator-facing status schema fields."""
        return UNIVERSE_OPERATOR_STATUS_FIELDS

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
            raise UniverseDataContractError(f"Invalid ISO date value: '{text}'.") from exc

    @classmethod
    def validate_input_boundary(cls, request: UniverseContractInput) -> None:
        if not str(request.universe_name or "").strip():
            raise UniverseDataContractError("universe_name is required.")

        if request.source_of_truth not in UNIVERSE_ALLOWED_SOURCES:
            raise UniverseDataContractError(
                f"Unsupported source_of_truth '{request.source_of_truth}'. "
                f"Allowed: {UNIVERSE_ALLOWED_SOURCES}."
            )

        if request.temporal_mode not in UNIVERSE_SUPPORTED_TEMPORAL_MODES:
            raise UniverseDataContractError(
                f"Unsupported temporal_mode '{request.temporal_mode}'. "
                f"Allowed: {UNIVERSE_SUPPORTED_TEMPORAL_MODES}."
            )

        if request.allow_implicit_source_fallback:
            raise UniverseDataContractError(
                "Implicit universe-source fallback is forbidden by universe data contract."
            )

        if request.runtime_universe_controls:
            raise UniverseDataContractError(
                "runtime_universe_controls are out of scope for universe data contract validation."
            )

        if request.min_coverage_ratio <= 0 or request.min_coverage_ratio > 1:
            raise UniverseDataContractError("min_coverage_ratio must be in (0, 1].")

        if request.stale_days_warn_threshold < 0:
            raise UniverseDataContractError("stale_days_warn_threshold must be >= 0.")

        cls._as_date(request.reference_date)

    @classmethod
    def validate_and_build_status(cls, request: UniverseContractInput) -> UniverseContractStatus:
        """Validate universe contract boundaries and emit operator-facing status payload."""
        cls.validate_input_boundary(request)
        profile = request.profile

        warnings: list[str] = []
        errors: list[str] = []

        if not profile.artifact_present:
            errors.append(ISSUE_MISSING_ARTIFACT)
        if not profile.manifest_present:
            errors.append(ISSUE_MISSING_MANIFEST)

        metadata = profile.metadata or {}
        present_fields = tuple(
            key for key in UNIVERSE_REQUIRED_METADATA_FIELDS if str(metadata.get(key, "")).strip()
        )
        missing_fields = tuple(key for key in UNIVERSE_REQUIRED_METADATA_FIELDS if key not in present_fields)
        if missing_fields:
            errors.append(ISSUE_SCHEMA_MISMATCH)

        mode_from_metadata = str(metadata.get("temporal_mode", "")).strip().lower()
        if mode_from_metadata and mode_from_metadata != request.temporal_mode:
            errors.append(ISSUE_SCHEMA_MISMATCH)

        normalized_columns = tuple(str(column).strip().lower() for column in profile.columns_present if str(column).strip())
        required_columns = UNIVERSE_REQUIRED_BASE_COLUMNS + UNIVERSE_REQUIRED_MODE_COLUMNS[request.temporal_mode]
        missing_columns = [col for col in required_columns if col not in normalized_columns]
        if missing_columns:
            errors.append(ISSUE_SCHEMA_MISMATCH)

        if profile.stale_days is not None and profile.stale_days > request.stale_days_warn_threshold:
            warnings.append(ISSUE_STALE_DATA)

        if profile.coverage_ratio is not None and profile.coverage_ratio < request.min_coverage_ratio:
            warnings.append(ISSUE_INCOMPLETE_COVERAGE)

        if profile.has_inconsistent_membership:
            errors.append(ISSUE_INCONSISTENT_MEMBERSHIP)
            membership_status = "inconsistent"
        else:
            membership_status = "consistent"

        reference = cls._as_date(request.reference_date)
        end_date = cls._as_date(profile.snapshot_end)
        if profile.has_future_effective_data or profile.has_future_known_metadata:
            errors.append(ISSUE_TEMPORAL_LEAKAGE)
        elif reference is not None and end_date is not None and end_date > reference:
            errors.append(ISSUE_TEMPORAL_LEAKAGE)

        unique_warnings = tuple(dict.fromkeys(warnings))
        unique_errors = tuple(dict.fromkeys(errors))
        if unique_errors:
            health = "error"
        elif unique_warnings:
            health = "warning"
        else:
            health = "ok"

        return UniverseContractStatus(
            contract_name=UNIVERSE_CONTRACT_NAME,
            contract_health=health,
            universe_name=request.universe_name,
            source_of_truth=request.source_of_truth,
            artifact_path=profile.artifact_path,
            manifest_path=profile.manifest_path,
            artifact_present=profile.artifact_present,
            manifest_present=profile.manifest_present,
            temporal_mode=request.temporal_mode,
            metadata_fields_present=present_fields,
            metadata_fields_missing=missing_fields,
            snapshot_start=profile.snapshot_start,
            snapshot_end=profile.snapshot_end,
            rows=profile.rows,
            columns_present=normalized_columns,
            stale_days=profile.stale_days,
            coverage_ratio=profile.coverage_ratio,
            membership_consistency_status=membership_status,
            warnings=unique_warnings,
            errors=unique_errors,
        )
