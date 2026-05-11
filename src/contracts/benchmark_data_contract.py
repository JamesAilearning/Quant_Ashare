"""Benchmark data-contract foundation for V2 (contract-only, no runtime selection semantics)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from src.contracts import _shared_validators as _sv

BENCHMARK_CONTRACT_NAME = "v2-benchmark-data-contract"
BENCHMARK_SOURCE_OF_TRUTH = "explicit_artifact_with_manifest"
BENCHMARK_ALLOWED_SOURCES = (BENCHMARK_SOURCE_OF_TRUTH,)

BENCHMARK_REQUIRED_METADATA_FIELDS = (
    "benchmark_code",
    "source_name",
    "source_uri",
    "snapshot_at",
    "schema_version",
)
BENCHMARK_REQUIRED_DATA_COLUMNS = ("date", "close")

BENCHMARK_OPERATOR_STATUS_FIELDS = (
    "contract_name",
    "contract_health",
    "benchmark_code",
    "source_of_truth",
    "artifact_path",
    "manifest_path",
    "artifact_present",
    "manifest_present",
    "metadata_fields_present",
    "metadata_fields_missing",
    "snapshot_start",
    "snapshot_end",
    "rows",
    "columns_present",
    "stale_days",
    "coverage_ratio",
    "warnings",
    "errors",
    "governance_note",
    "selection_semantics_in_scope",
)

ISSUE_MISSING_ARTIFACT = "missing_artifact_file"
ISSUE_MISSING_MANIFEST = "missing_manifest_file"
ISSUE_SCHEMA_MISMATCH = "schema_mismatch"
ISSUE_STALE_DATA = "stale_data"
ISSUE_INCOMPLETE_COVERAGE = "incomplete_coverage"
ISSUE_TEMPORAL_ISSUE = "temporal_issue"

GOVERNANCE_NOTE = (
    "Informational contract health only; runtime benchmark-selection semantics remain out of scope."
)


class BenchmarkDataContractError(ValueError):
    """Raised when benchmark contract boundaries are violated."""


@dataclass(frozen=True)
class BenchmarkArtifactProfile:
    """Normalized benchmark artifact snapshot used by contract validation."""

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
    has_future_data: bool = False
    has_future_known_metadata: bool = False
    has_snapshot_at_mismatch: bool = False


@dataclass(frozen=True)
class BenchmarkContractInput:
    """Input boundary for benchmark contract validation."""

    benchmark_code: str
    source_of_truth: str = BENCHMARK_SOURCE_OF_TRUTH
    profile: BenchmarkArtifactProfile = field(
        default_factory=lambda: BenchmarkArtifactProfile(
            artifact_path=None,
            manifest_path=None,
            artifact_present=False,
            manifest_present=False,
            metadata={},
            rows=None,
        )
    )
    allow_implicit_source_fallback: bool = False
    runtime_selection_controls: Mapping[str, Any] = field(default_factory=dict)
    stale_days_warn_threshold: int = 5
    min_coverage_ratio: float = 0.95
    reference_date: str | None = None


@dataclass(frozen=True)
class BenchmarkContractStatus:
    """Operator-facing benchmark contract status payload (informational by default)."""

    contract_name: str
    contract_health: str
    benchmark_code: str
    source_of_truth: str
    artifact_path: str | None
    manifest_path: str | None
    artifact_present: bool
    manifest_present: bool
    metadata_fields_present: tuple[str, ...]
    metadata_fields_missing: tuple[str, ...]
    snapshot_start: str | None
    snapshot_end: str | None
    rows: int | None
    columns_present: tuple[str, ...]
    stale_days: int | None
    coverage_ratio: float | None
    warnings: tuple[str, ...]
    errors: tuple[str, ...]
    governance_note: str = GOVERNANCE_NOTE
    selection_semantics_in_scope: bool = False


class BenchmarkDataContract:
    """Benchmark data-contract validator with explicit governance boundaries."""

    @staticmethod
    def list_source_of_truth_options() -> tuple[str, ...]:
        """Return allowed explicit source-of-truth options."""
        return BENCHMARK_ALLOWED_SOURCES

    @staticmethod
    def required_metadata_fields() -> tuple[str, ...]:
        """Return required provenance metadata fields."""
        return BENCHMARK_REQUIRED_METADATA_FIELDS

    @staticmethod
    def operator_status_fields() -> tuple[str, ...]:
        """Return required operator-facing status schema fields."""
        return BENCHMARK_OPERATOR_STATUS_FIELDS

    @staticmethod
    def _as_date(value: str | None) -> date | None:
        """Thin wrapper kept for backward compatibility with validate_input_boundary."""
        return _sv.parse_iso_date(value, error_cls=BenchmarkDataContractError)

    @classmethod
    def validate_input_boundary(cls, request: BenchmarkContractInput) -> None:
        if not str(request.benchmark_code or "").strip():
            raise BenchmarkDataContractError("benchmark_code is required.")

        if request.source_of_truth not in BENCHMARK_ALLOWED_SOURCES:
            raise BenchmarkDataContractError(
                f"Unsupported source_of_truth '{request.source_of_truth}'. "
                f"Allowed: {BENCHMARK_ALLOWED_SOURCES}."
            )

        if request.allow_implicit_source_fallback:
            raise BenchmarkDataContractError(
                "Implicit benchmark-source fallback is forbidden by benchmark contract."
            )

        if request.runtime_selection_controls:
            raise BenchmarkDataContractError(
                "runtime_selection_controls are out of scope for benchmark data contract validation."
            )

        if request.min_coverage_ratio <= 0 or request.min_coverage_ratio > 1:
            raise BenchmarkDataContractError("min_coverage_ratio must be in (0, 1].")

        if request.stale_days_warn_threshold < 0:
            raise BenchmarkDataContractError("stale_days_warn_threshold must be >= 0.")

        cls._as_date(request.reference_date)

    @classmethod
    def validate_and_build_status(cls, request: BenchmarkContractInput) -> BenchmarkContractStatus:
        """Validate benchmark contract boundaries and emit operator-facing status payload."""
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
            BENCHMARK_REQUIRED_METADATA_FIELDS,
            schema_mismatch_code=ISSUE_SCHEMA_MISMATCH,
        )
        errors.extend(metadata_errors)

        columns_present = _sv.normalize_columns(profile.columns_present)
        errors.extend(
            _sv.check_required_columns(
                columns_present,
                BENCHMARK_REQUIRED_DATA_COLUMNS,
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

        errors.extend(
            _sv.check_temporal_basic(
                snapshot_end=profile.snapshot_end,
                reference_date=request.reference_date,
                has_future_data_flags=(
                    profile.has_future_data,
                    profile.has_future_known_metadata,
                ),
                temporal_code=ISSUE_TEMPORAL_ISSUE,
                error_cls=BenchmarkDataContractError,
            )
        )
        errors.extend(
            _sv.check_snapshot_at_mismatch(profile, temporal_code=ISSUE_TEMPORAL_ISSUE)
        )

        unique_errors = _sv.dedupe(errors)
        unique_warnings = _sv.dedupe(warnings)
        health = _sv.aggregate_health(unique_errors, unique_warnings)

        return BenchmarkContractStatus(
            contract_name=BENCHMARK_CONTRACT_NAME,
            contract_health=health,
            benchmark_code=request.benchmark_code,
            source_of_truth=request.source_of_truth,
            artifact_path=profile.artifact_path,
            manifest_path=profile.manifest_path,
            artifact_present=profile.artifact_present,
            manifest_present=profile.manifest_present,
            metadata_fields_present=present_fields,
            metadata_fields_missing=missing_fields,
            snapshot_start=profile.snapshot_start,
            snapshot_end=profile.snapshot_end,
            rows=profile.rows,
            columns_present=columns_present,
            stale_days=profile.stale_days,
            coverage_ratio=profile.coverage_ratio,
            warnings=unique_warnings,
            errors=unique_errors,
        )
