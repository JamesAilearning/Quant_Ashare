"""Operator status/workflow contract foundation for V2 (contract-only, no runtime semantics)."""

from __future__ import annotations

from dataclasses import dataclass, field

STATUS_OK = "ok"
STATUS_WARNING = "warning"
STATUS_ERROR = "error"
STATUS_NOT_READY = "not_ready"
OPERATOR_STATUS_CATEGORIES = (
    STATUS_OK,
    STATUS_WARNING,
    STATUS_ERROR,
    STATUS_NOT_READY,
)

BOUNDARY_CANONICAL_RUNTIME = "canonical_runtime_boundary"
BOUNDARY_DATA_CONTRACT = "data_contract_boundary"
BOUNDARY_RUNTIME_PLACEHOLDER = "runtime_placeholder_boundary"
OPERATOR_BOUNDARY_TYPES = (
    BOUNDARY_CANONICAL_RUNTIME,
    BOUNDARY_DATA_CONTRACT,
    BOUNDARY_RUNTIME_PLACEHOLDER,
)
REQUIRED_WORKFLOW_BOUNDARIES = OPERATOR_BOUNDARY_TYPES

GOVERNANCE_UNSPECIFIED = "unspecified"
GOVERNANCE_CANONICAL = "canonical"
GOVERNANCE_EXPERIMENTAL = "experimental"
GOVERNANCE_RESEARCH = "research"
GOVERNANCE_LABELS = (
    GOVERNANCE_UNSPECIFIED,
    GOVERNANCE_CANONICAL,
    GOVERNANCE_EXPERIMENTAL,
    GOVERNANCE_RESEARCH,
)

OPERATOR_STATUS_SUMMARY_FIELDS = (
    "component_id",
    "boundary_type",
    "status_category",
    "summary",
    "warnings",
    "errors",
    "is_placeholder",
    "governance_label",
    "informational_note",
    "governance_meaning_from_status",
)

INFORMATIONAL_BOUNDARY_NOTE = (
    "Informational status only; governance meaning is defined separately and must not be inferred from status."
)


class OperatorStatusWorkflowContractError(ValueError):
    """Raised when operator status/workflow contract boundaries are violated."""


@dataclass(frozen=True)
class OperatorStatusEntry:
    """Single operator-visible status entry for one component boundary."""

    component_id: str
    boundary_type: str
    status_category: str
    summary: str
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    is_placeholder: bool = False
    governance_label: str = GOVERNANCE_UNSPECIFIED
    informational_note: str = INFORMATIONAL_BOUNDARY_NOTE
    governance_meaning_from_status: bool = False


@dataclass(frozen=True)
class OperatorWorkflowStatusInput:
    """Input boundary for operator workflow status snapshot validation."""

    entries: tuple[OperatorStatusEntry, ...] = field(default_factory=tuple)
    allow_implicit_status_fallback: bool = False


@dataclass(frozen=True)
class OperatorWorkflowStatusSnapshot:
    """Validated operator-facing status snapshot across required workflow boundaries."""

    overall_status_category: str
    entries: tuple[OperatorStatusEntry, ...]
    represented_boundary_types: tuple[str, ...]
    required_boundary_types: tuple[str, ...]
    missing_boundary_types: tuple[str, ...]


class OperatorStatusWorkflowContract:
    """Operator-facing status/workflow contract validator with explicit governance boundaries."""

    @staticmethod
    def status_categories() -> tuple[str, ...]:
        """Return supported operator status categories."""
        return OPERATOR_STATUS_CATEGORIES

    @staticmethod
    def boundary_types() -> tuple[str, ...]:
        """Return supported operator boundary categories."""
        return OPERATOR_BOUNDARY_TYPES

    @staticmethod
    def required_boundaries() -> tuple[str, ...]:
        """Return required cross-domain boundary coverage for workflow status."""
        return REQUIRED_WORKFLOW_BOUNDARIES

    @staticmethod
    def summary_fields() -> tuple[str, ...]:
        """Return required operator status summary fields."""
        return OPERATOR_STATUS_SUMMARY_FIELDS

    @staticmethod
    def _validate_entry(entry: OperatorStatusEntry) -> None:
        if not str(entry.component_id or "").strip():
            raise OperatorStatusWorkflowContractError("component_id is required for operator status entry.")
        if entry.boundary_type not in OPERATOR_BOUNDARY_TYPES:
            raise OperatorStatusWorkflowContractError(
                f"Unsupported boundary_type '{entry.boundary_type}'. Allowed: {OPERATOR_BOUNDARY_TYPES}."
            )
        if entry.status_category not in OPERATOR_STATUS_CATEGORIES:
            raise OperatorStatusWorkflowContractError(
                f"Unsupported status_category '{entry.status_category}'. Allowed: {OPERATOR_STATUS_CATEGORIES}."
            )
        if not str(entry.summary or "").strip():
            raise OperatorStatusWorkflowContractError("summary is required for operator status entry.")
        if entry.governance_label not in GOVERNANCE_LABELS:
            raise OperatorStatusWorkflowContractError(
                f"Unsupported governance_label '{entry.governance_label}'. Allowed: {GOVERNANCE_LABELS}."
            )

        if entry.governance_meaning_from_status:
            raise OperatorStatusWorkflowContractError(
                "governance_meaning_from_status must remain False; informational status cannot redefine governance."
            )

        if entry.is_placeholder and entry.status_category != STATUS_NOT_READY:
            raise OperatorStatusWorkflowContractError(
                "Placeholder status entries must use status_category='not_ready'."
            )

        if entry.status_category == STATUS_ERROR and not entry.errors:
            raise OperatorStatusWorkflowContractError(
                "status_category='error' requires at least one error message."
            )
        if entry.status_category == STATUS_WARNING and not entry.warnings:
            raise OperatorStatusWorkflowContractError(
                "status_category='warning' requires at least one warning message."
            )
        if entry.status_category == STATUS_OK and (entry.warnings or entry.errors):
            raise OperatorStatusWorkflowContractError(
                "status_category='ok' cannot include warnings or errors."
            )
        if entry.status_category == STATUS_NOT_READY and not entry.is_placeholder:
            raise OperatorStatusWorkflowContractError(
                "status_category='not_ready' must be represented as placeholder."
            )

        if INFORMATIONAL_BOUNDARY_NOTE not in str(entry.informational_note or ""):
            raise OperatorStatusWorkflowContractError(
                "informational_note must explicitly preserve informational-vs-governance separation."
            )

    @classmethod
    def validate_input_boundary(cls, request: OperatorWorkflowStatusInput) -> None:
        if request.allow_implicit_status_fallback:
            raise OperatorStatusWorkflowContractError(
                "Implicit status fallback is forbidden; missing boundary states must be explicit."
            )

        if not request.entries:
            raise OperatorStatusWorkflowContractError(
                "At least one operator status entry is required."
            )

        for entry in request.entries:
            cls._validate_entry(entry)

        represented = tuple(dict.fromkeys(entry.boundary_type for entry in request.entries))
        missing = tuple(boundary for boundary in REQUIRED_WORKFLOW_BOUNDARIES if boundary not in represented)
        if missing:
            missing_text = ", ".join(missing)
            raise OperatorStatusWorkflowContractError(
                "Missing required boundary status checkpoints. "
                f"These must be explicitly represented with not_ready placeholders: {missing_text}."
            )

    @classmethod
    def build_snapshot(cls, request: OperatorWorkflowStatusInput) -> OperatorWorkflowStatusSnapshot:
        """Validate operator workflow status and build normalized snapshot."""
        cls.validate_input_boundary(request)

        represented = tuple(dict.fromkeys(entry.boundary_type for entry in request.entries))
        missing = tuple(boundary for boundary in REQUIRED_WORKFLOW_BOUNDARIES if boundary not in represented)

        if any(entry.status_category == STATUS_ERROR for entry in request.entries):
            overall = STATUS_ERROR
        elif any(entry.status_category == STATUS_WARNING for entry in request.entries):
            overall = STATUS_WARNING
        elif any(entry.status_category == STATUS_NOT_READY for entry in request.entries):
            overall = STATUS_NOT_READY
        else:
            overall = STATUS_OK

        return OperatorWorkflowStatusSnapshot(
            overall_status_category=overall,
            entries=request.entries,
            represented_boundary_types=represented,
            required_boundary_types=REQUIRED_WORKFLOW_BOUNDARIES,
            missing_boundary_types=missing,
        )
