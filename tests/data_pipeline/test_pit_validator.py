"""Tests for ``src.data.pit.pit_validator.PITValidator``.

These tests verify the report-aggregation surface only (CheckResult,
PITValidationReport, exit-code policy). The full end-to-end check
behaviors are exercised by the Phase B smoke test (real Tushare slice
+ real qlib.init), not by unit tests — bringing up a complete qlib
provider in a tempdir within a unit test pulls in too many qlib
internals for fast feedback.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.pit.pit_validator import (  # noqa: E402
    CheckResult,
    PITValidationReport,
    PITValidator,
    PITValidatorError,
)


class ExitCodeTests(unittest.TestCase):
    """Per legacy verify_survivorship.py convention: 0=clean, 1=warnings,
    2=any failure. The aggregation rule prefers the WORST status."""

    def test_all_clean_returns_zero(self) -> None:
        rep = PITValidationReport(
            checks=[CheckResult(name="x", code="A", passed=True)],
            provider_dir=Path("/dev/null"),
        )
        self.assertEqual(rep.exit_code, 0)

    def test_warnings_only_returns_one(self) -> None:
        rep = PITValidationReport(
            checks=[
                CheckResult(name="x", code="A", passed=True),
                CheckResult(name="y", code="E", passed=True,
                            warnings=["yaml deferred"]),
            ],
            provider_dir=Path("/dev/null"),
        )
        self.assertEqual(rep.exit_code, 1)

    def test_any_failure_returns_two(self) -> None:
        rep = PITValidationReport(
            checks=[
                CheckResult(name="x", code="A", passed=True),
                CheckResult(name="y", code="B", passed=False, errors=["bad"]),
                CheckResult(name="z", code="C", passed=True,
                            warnings=["minor"]),  # warning + failure -> fail wins
            ],
            provider_dir=Path("/dev/null"),
        )
        self.assertEqual(rep.exit_code, 2)


class ReportSerializationTests(unittest.TestCase):

    def test_to_dict_round_trips_basic_fields(self) -> None:
        rep = PITValidationReport(
            checks=[CheckResult(
                name="Survivorship", code="A", passed=False,
                errors=["err1", "err2"], warnings=["w1"],
                details={"sample_size": 5, "passes": 3},
            )],
            provider_dir=Path("/tmp/prov"),
        )
        d = rep.to_dict()
        self.assertEqual(d["exit_code"], 2)
        self.assertEqual(d["provider_dir"], str(Path("/tmp/prov")))
        self.assertEqual(len(d["checks"]), 1)
        c = d["checks"][0]
        self.assertEqual(c["code"], "A")
        self.assertEqual(c["errors"], ["err1", "err2"])
        self.assertEqual(c["warnings"], ["w1"])
        self.assertEqual(c["details"], {"sample_size": 5, "passes": 3})


class SanityCheckTests(unittest.TestCase):
    """``_sanity_check_provider`` validates that the target directory
    looks like a qlib provider before kicking off the full validation."""

    def test_rejects_missing_calendars(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            validator = PITValidator(
                provider_dir=tmp_path,
                delisted_registry_path=tmp_path / "absent.parquet",
            )
            with self.assertRaisesRegex(PITValidatorError, r"calendars/day\.txt"):
                validator._sanity_check_provider()

    def test_rejects_missing_instruments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "calendars").mkdir()
            (tmp_path / "calendars" / "day.txt").write_text("2020-01-01\n")
            validator = PITValidator(
                provider_dir=tmp_path,
                delisted_registry_path=tmp_path / "absent.parquet",
            )
            with self.assertRaisesRegex(PITValidatorError, r"instruments/all\.txt"):
                validator._sanity_check_provider()

    def test_rejects_missing_features(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "calendars").mkdir()
            (tmp_path / "calendars" / "day.txt").write_text("2020-01-01\n")
            (tmp_path / "instruments").mkdir()
            (tmp_path / "instruments" / "all.txt").write_text("")
            validator = PITValidator(
                provider_dir=tmp_path,
                delisted_registry_path=tmp_path / "absent.parquet",
            )
            with self.assertRaisesRegex(PITValidatorError, r"features/"):
                validator._sanity_check_provider()


if __name__ == "__main__":
    unittest.main()
