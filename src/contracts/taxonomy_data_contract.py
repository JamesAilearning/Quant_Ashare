"""Taxonomy data-contract foundation for V2 (contract-only, no industry runtime semantics)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from src.contracts import _shared_validators as _sv

TAXONOMY_CONTRACT_NAME = "v2-taxonomy-data-contract"
TAXONOMY_SOURCE_OF_TRUTH = "explicit_taxonomy_artifact_with_manifest"
TAXONOMY_ALLOWED_SOURCES = (TAXONOMY_SOURCE_OF_TRUTH,)

TAXONOMY_MODE_STATIC = "static"
TAXONOMY_MODE_TRADE_DATE = "trade_date"
TAXONOMY_MODE_RANGE = "range"
TAXONOMY_SUPPORTED_TEMPORAL_MODES = (
    TAXONOMY_MODE_STATIC,
    TAXONOMY_MODE_TRADE_DATE,
    TAXONOMY_MODE_RANGE,
)

TAXONOMY_REQUIRED_METADATA_FIELDS = (
    "taxonomy_name",
    "source_name",
    "source_uri",
    "snapshot_at",
    "schema_version",
    "temporal_mode",
)

TAXONOMY_REQUIRED_BASE_COLUMNS = ("instrument", "industry_code")
TAXONOMY_REQUIRED_MODE_COLUMNS = {
    TAXONOMY_MODE_STATIC: (),
    TAXONOMY_MODE_TRADE_DATE: ("trade_date",),
    TAXONOMY_MODE_RANGE: ("effective_start", "effective_end"),
}

TAXONOMY_OPERATOR_STATUS_FIELDS = (
    "contract_name",
    "contract_health",
    "taxonomy_name",
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
    "mapping_consistency_status",
    "warnings",
    "errors",
    "governance_note",
    "industry_runtime_semantics_in_scope",
)

ISSUE_MISSING_ARTIFACT = "missing_artifact_file"
ISSUE_MISSING_MANIFEST = "missing_manifest_file"
ISSUE_SCHEMA_MISMATCH = "schema_mismatch"
ISSUE_STALE_DATA = "stale_data"
ISSUE_INCOMPLETE_COVERAGE = "incomplete_coverage"
ISSUE_INCONSISTENT_MAPPINGS = "inconsistent_mappings"
ISSUE_TEMPORAL_LEAKAGE = "temporal_leakage"

GOVERNANCE_NOTE = (
    "Informational taxonomy contract health only; industry-aware runtime semantics remain out of scope."
)


class TaxonomyDataContractError(ValueError):
    """Raised when taxonomy contract boundaries are violated."""


@dataclass(frozen=True)
class TaxonomyArtifactProfile:
    """Normalized taxonomy artifact snapshot used by contract validation."""

    artifact_path: str | None
    manifest_path: str | None
    artifact_present: bool
    manifest_present: bool
    metadata: Mapping[str, Any]
    rows: int | None
    columns_present: tuple[str, ...] = ()
    snapshot_start: str | None = None
    snapshot_end: str | None = None
    stale_days: int | None = None
    coverage_ratio: float | None = None
    has_inconsistent_mappings: bool = False
    has_future_effective_data: bool = False
    has_future_known_metadata: bool = False
    has_snapshot_at_mismatch: bool = False


@dataclass(frozen=True)
class TaxonomyContractInput:
    """Input boundary for taxonomy contract validation."""

    taxonomy_name: str
    source_of_truth: str = TAXONOMY_SOURCE_OF_TRUTH
    temporal_mode: str = TAXONOMY_MODE_STATIC
    profile: TaxonomyArtifactProfile = field(
        default_factory=lambda: TaxonomyArtifactProfile(
            artifact_path=None,
            manifest_path=None,
            artifact_present=False,
            manifest_present=False,
            metadata={},
            rows=None,
        )
    )
    allow_implicit_source_fallback: bool = False
    runtime_industry_controls: Mapping[str, Any] = field(default_factory=dict)
    stale_days_warn_threshold: int = 5
    min_coverage_ratio: float = 0.95
    reference_date: str | None = None


@dataclass(frozen=True)
class TaxonomyContractStatus:
    """Operator-facing taxonomy contract status payload (informational by default)."""

    contract_name: str
    contract_health: str
    taxonomy_name: str
    source_of_truth: str
    artifact_path: str | None
    manifest_path: str | None
    artifact_present: bool
    manifest_present: bool
    temporal_mode: str
    metadata_fields_present: tuple[str, ...]
    metadata_fields_missing: tuple[str, ...]
    snapshot_start: str | None
    snapshot_end: str | None
    rows: int | None
    columns_present: tuple[str, ...]
    stale_days: int | None
    coverage_ratio: float | None
    mapping_consistency_status: str
    warnings: tuple[str, ...]
    errors: tuple[str, ...]
    governance_note: str = GOVERNANCE_NOTE
    industry_runtime_semantics_in_scope: bool = False


class TaxonomyDataContract:
    """Taxonomy data-contract validator with explicit governance boundaries."""

    @staticmethod
    def list_source_of_truth_options() -> tuple[str, ...]:
        """Return allowed explicit source-of-truth options."""
        return TAXONOMY_ALLOWED_SOURCES

    @staticmethod
    def supported_temporal_modes() -> tuple[str, ...]:
        """Return supported temporal-validity modes."""
        return TAXONOMY_SUPPORTED_TEMPORAL_MODES

    @staticmethod
    def required_metadata_fields() -> tuple[str, ...]:
        """Return required provenance metadata fields."""
        return TAXONOMY_REQUIRED_METADATA_FIELDS

    @staticmethod
    def operator_status_fields() -> tuple[str, ...]:
        """Return required operator-facing status schema fields."""
        return TAXONOMY_OPERATOR_STATUS_FIELDS

    @staticmethod
    def _as_date(value: str | None) -> date | None:
        """Thin wrapper kept for backward compatibility with validate_input_boundary."""
        return _sv.parse_iso_date(value, error_cls=TaxonomyDataContractError)

    @classmethod
    def validate_input_boundary(cls, request: TaxonomyContractInput) -> None:
        if not str(request.taxonomy_name or "").strip():
            raise TaxonomyDataContractError("taxonomy_name is required.")

        if request.source_of_truth not in TAXONOMY_ALLOWED_SOURCES:
            raise TaxonomyDataContractError(
                f"Unsupported source_of_truth '{request.source_of_truth}'. "
                f"Allowed: {TAXONOMY_ALLOWED_SOURCES}."
            )

        if request.temporal_mode not in TAXONOMY_SUPPORTED_TEMPORAL_MODES:
            raise TaxonomyDataContractError(
                f"Unsupported temporal_mode '{request.temporal_mode}'. "
                f"Allowed: {TAXONOMY_SUPPORTED_TEMPORAL_MODES}."
            )

        if request.allow_implicit_source_fallback:
            raise TaxonomyDataContractError(
                "Implicit taxonomy-source fallback is forbidden by taxonomy data contract."
            )

        if request.runtime_industry_controls:
            raise TaxonomyDataContractError(
                "runtime_industry_controls are out of scope for taxonomy data contract validation."
            )

        if request.min_coverage_ratio <= 0 or request.min_coverage_ratio > 1:
            raise TaxonomyDataContractError("min_coverage_ratio must be in (0, 1].")

        if request.stale_days_warn_threshold < 0:
            raise TaxonomyDataContractError("stale_days_warn_threshold must be >= 0.")

        cls._as_date(request.reference_date)

    @classmethod
    def validate_and_build_status(cls, request: TaxonomyContractInput) -> TaxonomyContractStatus:
        """Validate taxonomy contract boundaries and emit operator-facing status payload."""
        cls.validate_input_boundary(request)
        profile = request.profile

        errors: list[str] = []
        warnings: list[str] = []

        errors.extend(
            _sv.check_presence(
                profile,
                missing_artifact_code=ISSUE_MISSING_ARTIFACT,
                missing_manifest_code=ISSUE_MISSING_MANIFEST,
            )
        )

        present_fields, missing_fields, metadata_errors = _sv.check_metadata_fields(
            profile,
            TAXONOMY_REQUIRED_METADATA_FIELDS,
            schema_mismatch_code=ISSUE_SCHEMA_MISMATCH,
        )
        errors.extend(metadata_errors)

        # Taxonomy-specific: declared temporal_mode must match metadata
        # (not shared because it reads Input.temporal_mode).
        metadata = profile.metadata or {}
        mode_from_metadata = str(metadata.get("temporal_mode", "")).strip().lower()
        if mode_from_metadata and mode_from_metadata != request.temporal_mode:
            errors.append(ISSUE_SCHEMA_MISMATCH)

        normalized_columns = _sv.normalize_columns(profile.columns_present)
        required_columns = TAXONOMY_REQUIRED_BASE_COLUMNS + TAXONOMY_REQUIRED_MODE_COLUMNS[request.temporal_mode]
        errors.extend(
            _sv.check_required_columns(
                normalized_columns,
                required_columns,
                schema_mismatch_code=ISSUE_SCHEMA_MISMATCH,
            )
        )

        warnings.extend(
            _sv.check_staleness(profile, request.stale_days_warn_threshold, stale_code=ISSUE_STALE_DATA)
        )
        warnings.extend(
            _sv.check_coverage(
                profile,
                request.min_coverage_ratio,
                incomplete_coverage_code=ISSUE_INCOMPLETE_COVERAGE,
            )
        )

        # Taxonomy-specific: mapping-consistency check.
        if profile.has_inconsistent_mappings:
            errors.append(ISSUE_INCONSISTENT_MAPPINGS)
            mapping_consistency_status = "inconsistent"
        else:
            mapping_consistency_status = "consistent"

        errors.extend(
            _sv.check_temporal_basic(
                snapshot_end=profile.snapshot_end,
                reference_date=request.reference_date,
                has_future_data_flags=(
                    profile.has_future_effective_data,
                    profile.has_future_known_metadata,
                ),
                temporal_code=ISSUE_TEMPORAL_LEAKAGE,
                error_cls=TaxonomyDataContractError,
            )
        )
        errors.extend(
            _sv.check_snapshot_at_mismatch(profile, temporal_code=ISSUE_TEMPORAL_LEAKAGE)
        )

        unique_errors = _sv.dedupe(errors)
        unique_warnings = _sv.dedupe(warnings)
        health = _sv.aggregate_health(unique_errors, unique_warnings)

        return TaxonomyContractStatus(
            contract_name=TAXONOMY_CONTRACT_NAME,
            contract_health=health,
            taxonomy_name=request.taxonomy_name,
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
            mapping_consistency_status=mapping_consistency_status,
            warnings=unique_warnings,
            errors=unique_errors,
        )
