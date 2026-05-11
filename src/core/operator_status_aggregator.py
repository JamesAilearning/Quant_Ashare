"""Operator status aggregator — bridges data-contract statuses to the
workflow snapshot.

This module converts individual contract statuses (benchmark, universe,
taxonomy) into ``OperatorStatusEntry`` objects and feeds them through
``OperatorStatusWorkflowContract.build_snapshot()`` to produce a single
``OperatorWorkflowStatusSnapshot``.

Design invariants
-----------------
1. **No overall-status logic.**  The aggregator never computes
   ``overall_status_category`` itself.  It relies entirely on
   ``build_snapshot()`` for worst-wins aggregation.
2. **Auto-placeholder.**  Any required boundary type that has no entry
   after the caller's inputs are collected gets a ``not_ready``
   placeholder, so ``build_snapshot()`` never rejects for missing
   boundaries.
3. **Direct mapping.**  ``contract_health`` maps 1:1 to
   ``status_category`` with no reinterpretation.
"""

from __future__ import annotations

from collections.abc import Sequence

from src.contracts.benchmark_data_contract import BenchmarkContractStatus
from src.contracts.operator_status_workflow_contract import (
    BOUNDARY_DATA_CONTRACT,
    GOVERNANCE_CANONICAL,
    INFORMATIONAL_BOUNDARY_NOTE,
    REQUIRED_WORKFLOW_BOUNDARIES,
    STATUS_ERROR,
    STATUS_NOT_READY,
    STATUS_OK,
    STATUS_WARNING,
    OperatorStatusEntry,
    OperatorStatusWorkflowContract,
    OperatorWorkflowStatusInput,
    OperatorWorkflowStatusSnapshot,
)
from src.contracts.taxonomy_data_contract import TaxonomyContractStatus
from src.contracts.universe_data_contract import UniverseContractStatus

_HEALTH_TO_STATUS = {
    "ok": STATUS_OK,
    "warning": STATUS_WARNING,
    "error": STATUS_ERROR,
}

ContractStatus = BenchmarkContractStatus | UniverseContractStatus | TaxonomyContractStatus


class OperatorStatusAggregatorError(ValueError):
    """Raised on structural misuse of the aggregator."""


def _entity_name(status: ContractStatus) -> str:
    """Extract the entity name from a contract status."""
    if isinstance(status, BenchmarkContractStatus):
        return status.benchmark_code
    if isinstance(status, UniverseContractStatus):
        return status.universe_name
    if isinstance(status, TaxonomyContractStatus):
        return status.taxonomy_name
    raise OperatorStatusAggregatorError(  # pragma: no cover
        f"Unknown contract status type: {type(status).__name__}"
    )


def _contract_status_to_entry(status: ContractStatus) -> OperatorStatusEntry:
    """Map a single data-contract status to an OperatorStatusEntry."""
    health = status.contract_health
    if health not in _HEALTH_TO_STATUS:
        raise OperatorStatusAggregatorError(
            f"Unexpected contract_health '{health}' in {status.contract_name}."
        )

    category = _HEALTH_TO_STATUS[health]
    entity = _entity_name(status)
    component_id = f"{status.contract_name}:{entity}"

    return OperatorStatusEntry(
        component_id=component_id,
        boundary_type=BOUNDARY_DATA_CONTRACT,
        status_category=category,
        summary=f"{status.contract_name} [{entity}]: {health}",
        warnings=status.warnings,
        errors=status.errors,
        is_placeholder=False,
        governance_label=GOVERNANCE_CANONICAL,
        informational_note=INFORMATIONAL_BOUNDARY_NOTE,
        governance_meaning_from_status=False,
    )


def _make_placeholder(boundary_type: str) -> OperatorStatusEntry:
    """Create a not_ready placeholder for an unrepresented boundary type."""
    return OperatorStatusEntry(
        component_id=f"auto-placeholder:{boundary_type}",
        boundary_type=boundary_type,
        status_category=STATUS_NOT_READY,
        summary=f"No entry provided for {boundary_type}; auto-placeholder.",
        warnings=(),
        errors=(),
        is_placeholder=True,
        governance_label=GOVERNANCE_CANONICAL,
        informational_note=INFORMATIONAL_BOUNDARY_NOTE,
        governance_meaning_from_status=False,
    )


class OperatorStatusAggregator:
    """Bridges data-contract statuses to an OperatorWorkflowStatusSnapshot.

    Usage::

        snapshot = OperatorStatusAggregator.aggregate(
            contract_statuses=[benchmark_status, universe_status, taxonomy_status],
        )
        print(snapshot.overall_status_category)
    """

    @classmethod
    def aggregate(
        cls,
        *,
        contract_statuses: Sequence[ContractStatus] | None = None,
        extra_entries: Sequence[OperatorStatusEntry] | None = None,
    ) -> OperatorWorkflowStatusSnapshot:
        """Aggregate contract statuses and optional extra entries into a snapshot.

        Parameters
        ----------
        contract_statuses
            Zero or more data-contract statuses to convert.
        extra_entries
            Pre-built ``OperatorStatusEntry`` objects for non-data
            boundary types (e.g. runtime, placeholder).  These are
            included as-is.

        Returns
        -------
        OperatorWorkflowStatusSnapshot
            The validated, aggregated snapshot from
            ``OperatorStatusWorkflowContract.build_snapshot()``.
        """
        entries: list[OperatorStatusEntry] = []

        for status in contract_statuses or ():
            entries.append(_contract_status_to_entry(status))

        for entry in extra_entries or ():
            entries.append(entry)

        # Auto-fill missing boundary types with not_ready placeholders.
        represented = {e.boundary_type for e in entries}
        for boundary in REQUIRED_WORKFLOW_BOUNDARIES:
            if boundary not in represented:
                entries.append(_make_placeholder(boundary))

        return OperatorStatusWorkflowContract.build_snapshot(
            OperatorWorkflowStatusInput(entries=tuple(entries))
        )
