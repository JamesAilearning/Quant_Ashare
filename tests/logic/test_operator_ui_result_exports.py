"""Regression tests for operator UI result export helpers."""

from __future__ import annotations

import csv
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from web.operator_ui import result_exports
from web.operator_ui.result_exports import (
    DEFAULT_BUNDLE_SIZE_LIMIT_BYTES,
    BundleTooLargeError,
    bundle_zip_bytes,
)


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

    def test_bundle_zip_raises_bundle_too_large_when_source_exceeds_limit(self) -> None:
        """When the run directory totals more than ``size_limit_bytes``,
        ``bundle_zip_bytes`` MUST raise ``BundleTooLargeError`` instead
        of OOM-ing the Streamlit server with a 1-5 GiB in-memory zip."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            run_dir.mkdir()
            run_dir.joinpath("a.bin").write_bytes(b"x" * 60)
            run_dir.joinpath("b.bin").write_bytes(b"y" * 60)

            with patch("web.operator_ui._path_guard._ALLOWED_ROOTS", (root,)):
                with self.assertRaises(BundleTooLargeError) as ctx:
                    bundle_zip_bytes(run_dir, size_limit_bytes=100)

        self.assertEqual(ctx.exception.size_bytes, 120)
        self.assertEqual(ctx.exception.limit_bytes, 100)
        self.assertEqual(ctx.exception.run_dir, run_dir)
        # ValueError subclass so old ``except (OSError, ValueError)`` still
        # catches it — important for call sites that haven't migrated yet.
        self.assertIsInstance(ctx.exception, ValueError)

    def test_bundle_zip_streams_via_tempfile_not_in_memory_bytesio(self) -> None:
        """The implementation MUST write the zip to ``tempfile`` so server
        RSS does not double-buffer the payload. We patch
        ``tempfile.NamedTemporaryFile`` to verify it is called, then check
        the returned bytes are still a valid zip with the source files."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            run_dir.mkdir()
            run_dir.joinpath("metrics.json").write_text("{}", encoding="utf-8")
            run_dir.joinpath("logs").mkdir()
            run_dir.joinpath("logs", "pipeline.log").write_text(
                "ok", encoding="utf-8"
            )

            real_named_tempfile = result_exports.tempfile.NamedTemporaryFile
            with patch("web.operator_ui._path_guard._ALLOWED_ROOTS", (root,)), \
                 patch.object(
                     result_exports.tempfile,
                     "NamedTemporaryFile",
                     wraps=real_named_tempfile,
                 ) as spy:
                zip_bytes = bundle_zip_bytes(run_dir)

        self.assertTrue(
            spy.called,
            "bundle_zip_bytes should write the zip through tempfile.NamedTemporaryFile",
        )
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            self.assertEqual(
                sorted(zf.namelist()),
                ["logs/pipeline.log", "metrics.json"],
            )

    def test_bundle_zip_cleans_up_tempfile_after_read(self) -> None:
        """Tempfile must be unlinked once the bytes are returned so a
        long-lived Streamlit session does not accumulate stale zip
        scratch files in the OS temp dir."""

        captured_paths: list[Path] = []
        real_named_tempfile = result_exports.tempfile.NamedTemporaryFile

        def _capture(*args: object, **kwargs: object) -> object:
            handle = real_named_tempfile(*args, **kwargs)
            captured_paths.append(Path(handle.name))
            return handle

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            run_dir.mkdir()
            run_dir.joinpath("a.txt").write_text("hi", encoding="utf-8")

            with patch("web.operator_ui._path_guard._ALLOWED_ROOTS", (root,)), \
                 patch.object(
                     result_exports.tempfile,
                     "NamedTemporaryFile",
                     side_effect=_capture,
                 ):
                bundle_zip_bytes(run_dir)

        self.assertEqual(
            len(captured_paths), 1,
            "expected exactly one tempfile per bundle build",
        )
        self.assertFalse(
            captured_paths[0].exists(),
            f"tempfile {captured_paths[0]} should have been unlinked after read",
        )

    def test_default_bundle_size_limit_is_500_mib(self) -> None:
        """Pin the documented 500 MiB threshold so a refactor cannot
        silently widen the OOM gap. See the report comment in
        result_exports.py for the rationale (1-5 GiB pipeline runs are
        routine)."""

        self.assertEqual(DEFAULT_BUNDLE_SIZE_LIMIT_BYTES, 500 * 1024 * 1024)

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
