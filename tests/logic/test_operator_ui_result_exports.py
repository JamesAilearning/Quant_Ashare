"""Regression tests for operator UI result export helpers."""

from __future__ import annotations

import csv
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


class ResultExportTests(unittest.TestCase):
    def test_metrics_csv_flattens_nested_metrics_without_recomputing(self) -> None:
        from web.operator_ui.result_exports import metrics_csv_bytes

        payload = {
            "performance": {"annual_return": 0.123456},
            "monthly_returns": [{"month": "2026-01", "strategy": 0.01}],
        }

        rows = list(csv.reader(io.StringIO(metrics_csv_bytes(payload).decode("utf-8-sig"))))

        self.assertEqual(rows[0], ["metric", "value"])
        self.assertIn(["performance.annual_return", "0.123456"], rows)
        self.assertIn(
            ["monthly_returns", '[{"month": "2026-01", "strategy": 0.01}]'],
            rows,
        )

    def test_bundle_zip_includes_run_files_under_allowed_output_root(self) -> None:
        from web.operator_ui.result_exports import bundle_zip_bytes

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            run_dir.mkdir()
            run_dir.joinpath("metrics.json").write_text("{}", encoding="utf-8")
            run_dir.joinpath("logs").mkdir()
            run_dir.joinpath("logs", "pipeline.log").write_text("ok", encoding="utf-8")

            with patch("web.operator_ui._path_guard._ALLOWED_ROOTS", (root,)):
                zip_bytes = bundle_zip_bytes(run_dir)

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            self.assertEqual(
                sorted(zf.namelist()),
                ["logs/pipeline.log", "metrics.json"],
            )

    def test_bundle_zip_rejects_paths_outside_output_root(self) -> None:
        from web.operator_ui.result_exports import bundle_zip_bytes

        with tempfile.TemporaryDirectory() as allowed, tempfile.TemporaryDirectory() as outside:
            with patch("web.operator_ui._path_guard._ALLOWED_ROOTS", (Path(allowed),)):
                with self.assertRaises(ValueError):
                    bundle_zip_bytes(Path(outside))

    def test_summary_pdf_reports_missing_optional_dependency_loudly(self) -> None:
        from web.operator_ui.result_exports import summary_pdf_bytes

        try:
            import reportlab  # noqa: F401
        except ImportError:
            with self.assertRaises(RuntimeError):
                summary_pdf_bytes(
                    run_id="pipeline_test",
                    status="success",
                    metrics={"performance": {"annual_return": 0.1}},
                    metadata={},
                )
        else:
            pdf_bytes = summary_pdf_bytes(
                run_id="pipeline_test",
                status="success",
                metrics={"performance": {"annual_return": 0.1}},
                metadata={},
            )
            self.assertTrue(pdf_bytes.startswith(b"%PDF"))


if __name__ == "__main__":
    unittest.main()
