"""Direct tests for ``src.contracts._shared_validators``.

The module is pure-function stateless helpers used by three contracts
(benchmark / universe / taxonomy). Until now they were tested only
indirectly via the per-contract test suites; this file pins down the
behavior dimensionally so future helpers added here don't quietly
drift.
"""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.contracts._shared_validators import (  # noqa: E402
    aggregate_health,
    check_coverage,
    check_metadata_fields,
    check_presence,
    check_required_columns,
    check_snapshot_at_mismatch,
    check_staleness,
    check_temporal_basic,
    dedupe,
    normalize_columns,
    parse_iso_date,
)


class _ContractError(Exception):
    """Stand-in error class for contract-specific exception types."""


# ---------------------------------------------------------------------------
# parse_iso_date
# ---------------------------------------------------------------------------


class ParseIsoDateTests(unittest.TestCase):
    def test_none_returns_none(self):
        self.assertIsNone(parse_iso_date(None, error_cls=_ContractError))

    def test_empty_string_returns_none(self):
        self.assertIsNone(parse_iso_date("", error_cls=_ContractError))
        self.assertIsNone(parse_iso_date("   ", error_cls=_ContractError))

    def test_valid_iso_date(self):
        self.assertEqual(
            parse_iso_date("2024-06-15", error_cls=_ContractError),
            date(2024, 6, 15),
        )

    def test_malformed_raises_supplied_error_class(self):
        with self.assertRaisesRegex(_ContractError, "Invalid ISO date"):
            parse_iso_date("06/15/2024", error_cls=_ContractError)

    def test_malformed_error_includes_value(self):
        with self.assertRaisesRegex(_ContractError, "yesterday"):
            parse_iso_date("yesterday", error_cls=_ContractError)


# ---------------------------------------------------------------------------
# normalize_columns / dedupe
# ---------------------------------------------------------------------------


class NormalizeColumnsTests(unittest.TestCase):
    def test_lowercases_and_strips(self):
        self.assertEqual(
            normalize_columns(["  Date ", "INSTRUMENT", "Close"]),
            ("date", "instrument", "close"),
        )

    def test_filters_empty(self):
        self.assertEqual(
            normalize_columns(["a", "  ", "", "b"]),
            ("a", "b"),
        )

    def test_handles_non_str_via_str_coercion(self):
        self.assertEqual(normalize_columns([1, 2, 3]), ("1", "2", "3"))


class DedupeTests(unittest.TestCase):
    def test_preserves_first_occurrence_order(self):
        self.assertEqual(
            dedupe(["a", "b", "a", "c", "b"]),
            ("a", "b", "c"),
        )

    def test_empty(self):
        self.assertEqual(dedupe([]), ())


# ---------------------------------------------------------------------------
# check_presence
# ---------------------------------------------------------------------------


class CheckPresenceTests(unittest.TestCase):
    def _profile(self, artifact=True, manifest=True):
        return SimpleNamespace(
            artifact_present=artifact,
            manifest_present=manifest,
        )

    def test_both_present_no_errors(self):
        out = check_presence(
            self._profile(),
            missing_artifact_code="A_MISS",
            missing_manifest_code="M_MISS",
        )
        self.assertEqual(out, [])

    def test_artifact_missing_only(self):
        out = check_presence(
            self._profile(artifact=False),
            missing_artifact_code="A_MISS",
            missing_manifest_code="M_MISS",
        )
        self.assertEqual(out, ["A_MISS"])

    def test_both_missing(self):
        out = check_presence(
            self._profile(artifact=False, manifest=False),
            missing_artifact_code="A_MISS",
            missing_manifest_code="M_MISS",
        )
        # Order preserved: artifact first, then manifest.
        self.assertEqual(out, ["A_MISS", "M_MISS"])


# ---------------------------------------------------------------------------
# check_metadata_fields
# ---------------------------------------------------------------------------


class CheckMetadataFieldsTests(unittest.TestCase):
    def _profile(self, metadata):
        return SimpleNamespace(metadata=metadata)

    def test_all_fields_present(self):
        profile = self._profile({"a": "1", "b": "2", "c": "3"})
        present, missing, errors = check_metadata_fields(
            profile, ("a", "b"),
            schema_mismatch_code="SCHEMA",
        )
        self.assertEqual(present, ("a", "b"))
        self.assertEqual(missing, ())
        self.assertEqual(errors, [])

    def test_missing_field_emits_schema_error(self):
        profile = self._profile({"a": "1"})
        present, missing, errors = check_metadata_fields(
            profile, ("a", "b"),
            schema_mismatch_code="SCHEMA",
        )
        self.assertEqual(present, ("a",))
        self.assertEqual(missing, ("b",))
        self.assertEqual(errors, ["SCHEMA"])

    def test_empty_string_treated_as_missing(self):
        profile = self._profile({"a": "1", "b": "   "})
        present, missing, _ = check_metadata_fields(
            profile, ("a", "b"),
            schema_mismatch_code="SCHEMA",
        )
        self.assertEqual(present, ("a",))
        self.assertEqual(missing, ("b",))

    def test_none_metadata_handled(self):
        profile = self._profile(None)
        present, missing, errors = check_metadata_fields(
            profile, ("a",),
            schema_mismatch_code="SCHEMA",
        )
        self.assertEqual(present, ())
        self.assertEqual(missing, ("a",))
        self.assertEqual(errors, ["SCHEMA"])


# ---------------------------------------------------------------------------
# check_required_columns
# ---------------------------------------------------------------------------


class CheckRequiredColumnsTests(unittest.TestCase):
    def test_all_present(self):
        out = check_required_columns(
            ("date", "instrument", "close"),
            ("date", "instrument"),
            schema_mismatch_code="SCHEMA",
        )
        self.assertEqual(out, [])

    def test_missing_emits_one_error(self):
        out = check_required_columns(
            ("date", "instrument"),
            ("date", "close"),
            schema_mismatch_code="SCHEMA",
        )
        self.assertEqual(out, ["SCHEMA"])


# ---------------------------------------------------------------------------
# check_staleness / check_coverage
# ---------------------------------------------------------------------------


class CheckStalenessAndCoverageTests(unittest.TestCase):
    def test_staleness_none_no_warning(self):
        profile = SimpleNamespace(stale_days=None)
        self.assertEqual(
            check_staleness(profile, threshold=10, stale_code="STALE"),
            [],
        )

    def test_staleness_below_threshold(self):
        profile = SimpleNamespace(stale_days=5)
        self.assertEqual(
            check_staleness(profile, threshold=10, stale_code="STALE"),
            [],
        )

    def test_staleness_above_threshold(self):
        profile = SimpleNamespace(stale_days=15)
        self.assertEqual(
            check_staleness(profile, threshold=10, stale_code="STALE"),
            ["STALE"],
        )

    def test_coverage_none_no_warning(self):
        profile = SimpleNamespace(coverage_ratio=None)
        self.assertEqual(
            check_coverage(profile, min_ratio=0.9, incomplete_coverage_code="INC"),
            [],
        )

    def test_coverage_below_min(self):
        profile = SimpleNamespace(coverage_ratio=0.8)
        self.assertEqual(
            check_coverage(profile, min_ratio=0.9, incomplete_coverage_code="INC"),
            ["INC"],
        )

    def test_coverage_at_min_is_ok(self):
        # strict < check, equality is OK
        profile = SimpleNamespace(coverage_ratio=0.9)
        self.assertEqual(
            check_coverage(profile, min_ratio=0.9, incomplete_coverage_code="INC"),
            [],
        )


# ---------------------------------------------------------------------------
# check_temporal_basic
# ---------------------------------------------------------------------------


class CheckTemporalBasicTests(unittest.TestCase):
    def test_future_flag_emits_temporal_error(self):
        out = check_temporal_basic(
            snapshot_end="2024-01-01",
            reference_date="2024-12-31",
            has_future_data_flags=(True,),
            temporal_code="TEMP",
            error_cls=_ContractError,
        )
        self.assertEqual(out, ["TEMP"])

    def test_snapshot_after_reference_emits_temporal_error(self):
        out = check_temporal_basic(
            snapshot_end="2024-12-31",
            reference_date="2024-01-01",
            has_future_data_flags=(False, False),
            temporal_code="TEMP",
            error_cls=_ContractError,
        )
        self.assertEqual(out, ["TEMP"])

    def test_snapshot_equal_reference_no_error(self):
        out = check_temporal_basic(
            snapshot_end="2024-06-30",
            reference_date="2024-06-30",
            has_future_data_flags=(False,),
            temporal_code="TEMP",
            error_cls=_ContractError,
        )
        self.assertEqual(out, [])

    def test_snapshot_before_reference_no_error(self):
        out = check_temporal_basic(
            snapshot_end="2024-01-01",
            reference_date="2024-12-31",
            has_future_data_flags=(False,),
            temporal_code="TEMP",
            error_cls=_ContractError,
        )
        self.assertEqual(out, [])

    def test_no_dates_no_error(self):
        out = check_temporal_basic(
            snapshot_end=None, reference_date=None,
            has_future_data_flags=(False,),
            temporal_code="TEMP",
            error_cls=_ContractError,
        )
        self.assertEqual(out, [])

    def test_flag_takes_precedence_over_date_comparison(self):
        # When a flag is True, we emit ONE temporal code and don't
        # reach the date-comparison branch.
        out = check_temporal_basic(
            snapshot_end="2024-12-31",
            reference_date="2024-01-01",  # snapshot > ref would also fire
            has_future_data_flags=(True,),
            temporal_code="TEMP",
            error_cls=_ContractError,
        )
        self.assertEqual(out, ["TEMP"])  # exactly one, not two

    def test_invalid_date_raises_supplied_error_class(self):
        with self.assertRaises(_ContractError):
            check_temporal_basic(
                snapshot_end="not-a-date",
                reference_date="2024-01-01",
                has_future_data_flags=(False,),
                temporal_code="TEMP",
                error_cls=_ContractError,
            )


# ---------------------------------------------------------------------------
# check_snapshot_at_mismatch
# ---------------------------------------------------------------------------


class CheckSnapshotAtMismatchTests(unittest.TestCase):
    def test_mismatch_true_emits_error(self):
        profile = SimpleNamespace(has_snapshot_at_mismatch=True)
        self.assertEqual(
            check_snapshot_at_mismatch(profile, temporal_code="TEMP"),
            ["TEMP"],
        )

    def test_mismatch_false_no_error(self):
        profile = SimpleNamespace(has_snapshot_at_mismatch=False)
        self.assertEqual(
            check_snapshot_at_mismatch(profile, temporal_code="TEMP"),
            [],
        )


# ---------------------------------------------------------------------------
# aggregate_health
# ---------------------------------------------------------------------------


class AggregateHealthTests(unittest.TestCase):
    def test_errors_dominate_warnings(self):
        self.assertEqual(aggregate_health(("E1",), ("W1",)), "error")

    def test_warnings_only(self):
        self.assertEqual(aggregate_health((), ("W1",)), "warning")

    def test_clean(self):
        self.assertEqual(aggregate_health((), ()), "ok")


if __name__ == "__main__":
    unittest.main()
