"""Unit tests for OperatorStatusAggregator.

Hermetic: constructs contract status objects directly without going
through loader/publisher/contract validate pipelines.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.contracts.benchmark_data_contract import (  # noqa: E402
    BENCHMARK_CONTRACT_NAME,
    BenchmarkContractStatus,
)
from src.contracts.operator_status_workflow_contract import (  # noqa: E402
    BOUNDARY_CANONICAL_RUNTIME,
    BOUNDARY_DATA_CONTRACT,
    BOUNDARY_RUNTIME_PLACEHOLDER,
    GOVERNANCE_CANONICAL,
    INFORMATIONAL_BOUNDARY_NOTE,
    OperatorStatusEntry,
    STATUS_NOT_READY,
    STATUS_OK,
)
from src.contracts.taxonomy_data_contract import (  # noqa: E402
    TAXONOMY_CONTRACT_NAME,
    TaxonomyContractStatus,
)
from src.contracts.universe_data_contract import (  # noqa: E402
    UNIVERSE_CONTRACT_NAME,
    UniverseContractStatus,
)
from src.core.operator_status_aggregator import (  # noqa: E402
    OperatorStatusAggregator,
)


# ---------------------------------------------------------------------------
# Helpers — minimal contract status constructors
# ---------------------------------------------------------------------------

def _benchmark_status(
    health: str = "ok",
    warnings: tuple[str, ...] = (),
    errors: tuple[str, ...] = (),
    code: str = "SH000300",
) -> BenchmarkContractStatus:
    return BenchmarkContractStatus(
        contract_name=BENCHMARK_CONTRACT_NAME,
        contract_health=health,
        benchmark_code=code,
        source_of_truth="explicit_artifact_with_manifest",
        artifact_path="/tmp/b.csv",
        manifest_path="/tmp/b.json",
        artifact_present=True,
        manifest_present=True,
        metadata_fields_present=("benchmark_code", "source_name", "source_uri", "snapshot_at", "schema_version"),
        metadata_fields_missing=(),
        snapshot_start="2026-01-01",
        snapshot_end="2026-02-27",
        rows=40,
        columns_present=("date", "close"),
        stale_days=0,
        coverage_ratio=1.0,
        warnings=warnings,
        errors=errors,
    )


def _universe_status(
    health: str = "ok",
    warnings: tuple[str, ...] = (),
    errors: tuple[str, ...] = (),
    name: str = "CSI300",
) -> UniverseContractStatus:
    return UniverseContractStatus(
        contract_name=UNIVERSE_CONTRACT_NAME,
        contract_health=health,
        universe_name=name,
        source_of_truth="explicit_artifact_with_manifest",
        artifact_path="/tmp/u.csv",
        manifest_path="/tmp/u.json",
        artifact_present=True,
        manifest_present=True,
        temporal_mode="trade_date",
        metadata_fields_present=("universe_name", "source_name", "source_uri", "snapshot_at", "schema_version"),
        metadata_fields_missing=(),
        snapshot_start="2026-01-01",
        snapshot_end="2026-02-27",
        rows=300,
        columns_present=("instrument", "in_universe", "trade_date"),
        stale_days=0,
        coverage_ratio=1.0,
        membership_consistency_status="consistent",
        warnings=warnings,
        errors=errors,
    )


def _taxonomy_status(
    health: str = "ok",
    warnings: tuple[str, ...] = (),
    errors: tuple[str, ...] = (),
    name: str = "SW2021",
) -> TaxonomyContractStatus:
    return TaxonomyContractStatus(
        contract_name=TAXONOMY_CONTRACT_NAME,
        contract_health=health,
        taxonomy_name=name,
        source_of_truth="explicit_artifact_with_manifest",
        artifact_path="/tmp/t.csv",
        manifest_path="/tmp/t.json",
        artifact_present=True,
        manifest_present=True,
        temporal_mode="static",
        metadata_fields_present=("taxonomy_name", "source_name", "source_uri", "snapshot_at", "schema_version"),
        metadata_fields_missing=(),
        snapshot_start=None,
        snapshot_end=None,
        rows=300,
        columns_present=("instrument", "industry_code"),
        stale_days=0,
        coverage_ratio=None,
        mapping_consistency_status="consistent",
        warnings=warnings,
        errors=errors,
    )


def _runtime_entry(boundary: str, status: str = STATUS_OK) -> OperatorStatusEntry:
    """Build a minimal ok/not_ready entry for a non-data boundary."""
    is_placeholder = status == STATUS_NOT_READY
    return OperatorStatusEntry(
        component_id=f"test:{boundary}",
        boundary_type=boundary,
        status_category=status,
        summary=f"test {boundary}: {status}",
        warnings=(),
        errors=(),
        is_placeholder=is_placeholder,
        governance_label=GOVERNANCE_CANONICAL,
        informational_note=INFORMATIONAL_BOUNDARY_NOTE,
        governance_meaning_from_status=False,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class AggregatorAllOkTests(unittest.TestCase):
    """All three data contracts healthy."""

    def test_all_ok_no_runtime_entries_yields_not_ready(self) -> None:
        """With no runtime entries the auto-placeholders make overall not_ready."""
        snapshot = OperatorStatusAggregator.aggregate(
            contract_statuses=[
                _benchmark_status(),
                _universe_status(),
                _taxonomy_status(),
            ],
        )
        # Auto-placeholders for runtime + placeholder boundaries → not_ready
        self.assertEqual(snapshot.overall_status_category, "not_ready")
        # Data contract entries should be ok
        data_entries = [
            e for e in snapshot.entries if e.boundary_type == BOUNDARY_DATA_CONTRACT
        ]
        self.assertEqual(len(data_entries), 3)
        for entry in data_entries:
            self.assertEqual(entry.status_category, "ok")

    def test_all_ok_with_runtime_entries_yields_ok(self) -> None:
        snapshot = OperatorStatusAggregator.aggregate(
            contract_statuses=[
                _benchmark_status(),
                _universe_status(),
                _taxonomy_status(),
            ],
            extra_entries=[
                _runtime_entry(BOUNDARY_CANONICAL_RUNTIME),
                _runtime_entry(BOUNDARY_RUNTIME_PLACEHOLDER),
            ],
        )
        self.assertEqual(snapshot.overall_status_category, "ok")
        self.assertEqual(len(snapshot.missing_boundary_types), 0)


class AggregatorErrorPropagationTests(unittest.TestCase):
    def test_one_contract_error_propagates(self) -> None:
        snapshot = OperatorStatusAggregator.aggregate(
            contract_statuses=[
                _benchmark_status(health="error", errors=("missing_artifact_file",)),
                _universe_status(),
                _taxonomy_status(),
            ],
            extra_entries=[
                _runtime_entry(BOUNDARY_CANONICAL_RUNTIME),
                _runtime_entry(BOUNDARY_RUNTIME_PLACEHOLDER),
            ],
        )
        self.assertEqual(snapshot.overall_status_category, "error")

    def test_benchmark_error_universe_warning_yields_error(self) -> None:
        snapshot = OperatorStatusAggregator.aggregate(
            contract_statuses=[
                _benchmark_status(health="error", errors=("missing_artifact_file",)),
                _universe_status(health="warning", warnings=("stale_data",)),
                _taxonomy_status(),
            ],
            extra_entries=[
                _runtime_entry(BOUNDARY_CANONICAL_RUNTIME),
                _runtime_entry(BOUNDARY_RUNTIME_PLACEHOLDER),
            ],
        )
        self.assertEqual(snapshot.overall_status_category, "error")


class AggregatorWarningTests(unittest.TestCase):
    def test_one_contract_warning_propagates(self) -> None:
        snapshot = OperatorStatusAggregator.aggregate(
            contract_statuses=[
                _benchmark_status(),
                _universe_status(health="warning", warnings=("stale_data",)),
                _taxonomy_status(),
            ],
            extra_entries=[
                _runtime_entry(BOUNDARY_CANONICAL_RUNTIME),
                _runtime_entry(BOUNDARY_RUNTIME_PLACEHOLDER),
            ],
        )
        self.assertEqual(snapshot.overall_status_category, "warning")

    def test_warning_entry_preserves_warning_codes(self) -> None:
        snapshot = OperatorStatusAggregator.aggregate(
            contract_statuses=[
                _universe_status(health="warning", warnings=("stale_data",)),
            ],
        )
        universe_entries = [
            e for e in snapshot.entries
            if e.boundary_type == BOUNDARY_DATA_CONTRACT
            and "universe" in e.component_id
        ]
        self.assertEqual(len(universe_entries), 1)
        self.assertEqual(universe_entries[0].warnings, ("stale_data",))
        self.assertEqual(universe_entries[0].status_category, "warning")


class AggregatorAutoPlaceholderTests(unittest.TestCase):
    def test_empty_aggregation_yields_three_placeholders(self) -> None:
        snapshot = OperatorStatusAggregator.aggregate()
        self.assertEqual(snapshot.overall_status_category, "not_ready")
        self.assertEqual(len(snapshot.entries), 3)
        for entry in snapshot.entries:
            self.assertTrue(entry.is_placeholder)
            self.assertEqual(entry.status_category, STATUS_NOT_READY)

    def test_auto_placeholders_do_not_override_provided_entries(self) -> None:
        snapshot = OperatorStatusAggregator.aggregate(
            extra_entries=[
                _runtime_entry(BOUNDARY_CANONICAL_RUNTIME),
            ],
        )
        # Should have auto-placeholder for data_contract + runtime_placeholder
        # but NOT for canonical_runtime (already provided)
        canonical = [
            e for e in snapshot.entries
            if e.boundary_type == BOUNDARY_CANONICAL_RUNTIME
        ]
        self.assertEqual(len(canonical), 1)
        self.assertFalse(canonical[0].is_placeholder)

    def test_only_data_contracts_auto_fills_two_boundaries(self) -> None:
        snapshot = OperatorStatusAggregator.aggregate(
            contract_statuses=[_benchmark_status()],
        )
        placeholders = [e for e in snapshot.entries if e.is_placeholder]
        self.assertEqual(len(placeholders), 2)
        placeholder_boundaries = {e.boundary_type for e in placeholders}
        self.assertEqual(
            placeholder_boundaries,
            {BOUNDARY_CANONICAL_RUNTIME, BOUNDARY_RUNTIME_PLACEHOLDER},
        )


class AggregatorComponentIdTests(unittest.TestCase):
    def test_component_id_format(self) -> None:
        snapshot = OperatorStatusAggregator.aggregate(
            contract_statuses=[
                _benchmark_status(code="SH000300"),
                _universe_status(name="CSI300"),
                _taxonomy_status(name="SW2021"),
            ],
        )
        data_entries = [
            e for e in snapshot.entries if e.boundary_type == BOUNDARY_DATA_CONTRACT
        ]
        ids = {e.component_id for e in data_entries}
        self.assertIn(f"{BENCHMARK_CONTRACT_NAME}:SH000300", ids)
        self.assertIn(f"{UNIVERSE_CONTRACT_NAME}:CSI300", ids)
        self.assertIn(f"{TAXONOMY_CONTRACT_NAME}:SW2021", ids)


class AggregatorDelegationTests(unittest.TestCase):
    def test_aggregator_does_not_assign_overall_status_directly(self) -> None:
        """Governance: the aggregator source must not contain overall_status assignment."""
        import inspect
        from src.core import operator_status_aggregator as mod

        source = inspect.getsource(mod)
        # The module should never assign overall_status_category directly
        self.assertNotIn("overall_status_category =", source)
        self.assertNotIn("overall_status_category=", source.replace(
            "overall_status_category == ", ""  # exclude comparisons in comments
        ).replace("overall_status_category:", ""))  # exclude type hints


if __name__ == "__main__":
    unittest.main()
