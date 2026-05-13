"""Unit tests for report_reader — path guard, read-only, no recomputation."""

from __future__ import annotations

import json
import sys as _sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_PROJECT_ROOT))


class ReportReaderPathGuardTests(unittest.TestCase):
    def test_allows_path_under_output(self) -> None:
        with patch("web.operator_ui.report_reader._ALLOWED_ROOTS", (Path("output").resolve(),)):
            from web.operator_ui.report_reader import _guard_path
            _guard_path(Path("output/runs/test_run"))

    def test_rejects_path_outside_roots(self) -> None:
        with patch("web.operator_ui.report_reader._ALLOWED_ROOTS", (Path("output").resolve(),)):
            import tempfile

            from web.operator_ui.report_reader import _guard_path
            outside = Path(tempfile.gettempdir())
            with self.assertRaises(ValueError):
                _guard_path(outside)


class ReportReaderReadTests(unittest.TestCase):
    def test_pipeline_report_reads_expected_fields(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        report = {"risk_analysis": {"excess_return_with_cost": {"annualized_return": 0.12}}}
        tmp.joinpath("pipeline_report.json").write_text(json.dumps(report), encoding="utf-8")
        with patch("web.operator_ui.report_reader._ALLOWED_ROOTS", (tmp,)):
            from web.operator_ui.report_reader import read_pipeline_report
            result = read_pipeline_report(tmp)
            self.assertIn("risk_analysis", result)

    def test_missing_metric_shows_available_field_not_fallback(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        report = {"risk_analysis": {}}
        tmp.joinpath("pipeline_report.json").write_text(json.dumps(report), encoding="utf-8")
        with patch("web.operator_ui.report_reader._ALLOWED_ROOTS", (tmp,)):
            from web.operator_ui.report_reader import read_pipeline_report
            result = read_pipeline_report(tmp)
            risk = result.get("risk_analysis", {}).get("excess_return_with_cost", {})
            # Should be empty or default, not computed
            self.assertTrue(risk.get("annualized_return") is None or risk == {})

    def test_fold_reports_returns_list_of_dicts(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        tmp.joinpath("fold_00_report.json").write_text('{"fold_index": 0}', encoding="utf-8")
        tmp.joinpath("fold_01_report.json").write_text('{"fold_index": 1}', encoding="utf-8")
        with patch("web.operator_ui.report_reader._ALLOWED_ROOTS", (tmp,)):
            from web.operator_ui.report_reader import read_fold_reports
            folds = read_fold_reports(tmp)
            self.assertEqual(len(folds), 2)


if __name__ == "__main__":
    unittest.main()
