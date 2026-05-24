"""Unit tests for UI-only training launch guards."""

from __future__ import annotations

import json
import sys as _sys
import tempfile
import unittest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_PROJECT_ROOT))


def _write_provider(root: Path) -> Path:
    provider = root / "qlib_provider"
    (provider / "calendars").mkdir(parents=True)
    (provider / "instruments").mkdir()
    (provider / "calendars" / "day.txt").write_text(
        "\n".join([
            "2025-01-02",
            "2025-01-03",
            "2025-01-06",
            "2025-07-01",
            "2025-09-30",
            "2025-10-09",
            "2025-12-29",
            "2025-12-30",
            "2025-12-31",
        ]),
        encoding="utf-8",
    )
    (provider / "instruments" / "all.txt").write_text("SH600000\t2025-01-02\t2025-12-31\n", encoding="utf-8")
    (root / "validation.json").write_text(
        json.dumps({
            "health": "ok",
            "coverage_start_date": "2025-01-02",
            "coverage_end_date": "2025-12-31",
            "calendar_count": 6,
            "instrument_count": 1,
            "row_count": 6,
        }),
        encoding="utf-8",
    )
    return provider


class OperatorUiTrainingGuardTests(unittest.TestCase):
    def test_provider_metadata_reads_adjacent_validation_and_provider_files(self) -> None:
        from web.operator_ui.training_guards import inspect_provider_metadata, provider_metadata_summary

        with tempfile.TemporaryDirectory() as tmp:
            provider = _write_provider(Path(tmp))
            metadata = inspect_provider_metadata(str(provider))

        self.assertEqual(str(metadata.coverage_start_date), "2025-01-02")
        self.assertEqual(str(metadata.coverage_end_date), "2025-12-31")
        self.assertEqual(metadata.health, "ok")
        self.assertEqual(metadata.instrument_universes, ("all",))
        summary = provider_metadata_summary(metadata)
        self.assertEqual(summary["coverage"], "2025-01-02 to 2025-12-31")
        self.assertEqual(summary["health"], "ok")

    def test_pipeline_guard_rejects_overlapping_train_and_valid_dates(self) -> None:
        from web.operator_ui.training_guards import validate_pipeline_training_inputs

        with tempfile.TemporaryDirectory() as tmp:
            provider = _write_provider(Path(tmp))
            result = validate_pipeline_training_inputs(
                provider_uri=str(provider),
                instruments="all",
                train_start="2025-01-02",
                train_end="2025-06-30",
                valid_start="2025-01-06",
                valid_end="2025-09-30",
                test_start="2025-10-09",
                test_end="2025-12-30",
            )

        self.assertFalse(result.ok)
        self.assertTrue(any("valid_start 必须严格晚于 train_end" in item for item in result.errors))

    def test_pipeline_guard_rejects_provider_final_trading_day_as_test_end(self) -> None:
        from web.operator_ui.training_guards import validate_pipeline_training_inputs

        with tempfile.TemporaryDirectory() as tmp:
            provider = _write_provider(Path(tmp))
            result = validate_pipeline_training_inputs(
                provider_uri=str(provider),
                instruments="all",
                train_start="2025-01-02",
                train_end="2025-01-03",
                valid_start="2025-01-06",
                valid_end="2025-12-29",
                test_start="2025-12-30",
                test_end="2025-12-31",
            )

        self.assertFalse(result.ok)
        self.assertTrue(any("必须早于数据源最后一个交易日" in item for item in result.errors))
        self.assertTrue(any("test_end 设为 ≤ 2025-12-30" in item for item in result.errors))

    def test_pipeline_guard_warns_when_forward_buffer_is_short(self) -> None:
        from web.operator_ui.training_guards import validate_pipeline_training_inputs

        with tempfile.TemporaryDirectory() as tmp:
            provider = _write_provider(Path(tmp))
            result = validate_pipeline_training_inputs(
                provider_uri=str(provider),
                instruments="all",
                train_start="2025-01-02",
                train_end="2025-01-03",
                valid_start="2025-01-06",
                valid_end="2025-09-30",
                test_start="2025-10-09",
                test_end="2025-12-30",
            )

        self.assertTrue(result.ok)
        self.assertTrue(any("20 日前向收益" in item for item in result.warnings))

    def test_pipeline_guard_rejects_missing_named_universe(self) -> None:
        from web.operator_ui.training_guards import validate_pipeline_training_inputs

        with tempfile.TemporaryDirectory() as tmp:
            provider = _write_provider(Path(tmp))
            result = validate_pipeline_training_inputs(
                provider_uri=str(provider),
                instruments="csi300",
                train_start="2025-01-02",
                train_end="2025-01-03",
                valid_start="2025-01-06",
                valid_end="2025-12-29",
                test_start="2025-12-29",
                test_end="2025-12-30",
            )

        self.assertFalse(result.ok)
        self.assertTrue(any("instruments='csi300' 不在数据源的" in item for item in result.errors))


if __name__ == "__main__":
    unittest.main()
