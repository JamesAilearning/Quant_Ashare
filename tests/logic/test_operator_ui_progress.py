"""Unit tests for operator UI job progress estimates."""

from __future__ import annotations

import json
import sys as _sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_PROJECT_ROOT))


class OperatorUiProgressTests(unittest.TestCase):
    def test_success_job_reports_completed_progress(self) -> None:
        from web.operator_ui.progress import build_job_progress

        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp)
            progress = build_job_progress(
                job_dir,
                {"status": "success", "mode": "pipeline", "run_dir": str(job_dir)},
            )

        self.assertEqual(progress["percent"], 100)
        self.assertEqual(progress["label"], "已完成")
        self.assertIn("run_dir=", progress["detail"])

    def test_pipeline_progress_detects_report_artifact(self) -> None:
        from web.operator_ui.progress import build_job_progress

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job_dir = root / "job"
            job_dir.mkdir()
            output_dir = root / "output"
            run_dir = output_dir / "runs" / "run_1"
            run_dir.mkdir(parents=True)
            (run_dir / "pipeline_report.json").write_text("{}", encoding="utf-8")
            config_path = job_dir / "config.yaml"
            config_path.write_text(f"output_dir: {output_dir}\n", encoding="utf-8")

            progress = build_job_progress(
                job_dir,
                {
                    "status": "running",
                    "mode": "pipeline",
                    "config_path": str(config_path),
                },
            )

        self.assertGreaterEqual(progress["percent"], 92)
        self.assertEqual(progress["label"], "已写入流水线报告")

    def _pipeline_progress(
        self, *, run_files: dict[str, str], log_text: str = "",
    ) -> dict[str, Any]:
        """Build pipeline progress for a synthetic run dir + log content.

        ``run_files`` maps a filename under the run dir to its content;
        ``log_text`` is written to ``job_dir/stderr.log`` (where the
        pipeline's ``_logger`` phase markers land).
        """

        from web.operator_ui.progress import build_job_progress

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job_dir = root / "job"
            job_dir.mkdir()
            output_dir = root / "output"
            run_dir = output_dir / "runs" / "run_1"
            run_dir.mkdir(parents=True)
            for name, content in run_files.items():
                (run_dir / name).write_text(content, encoding="utf-8")
            if log_text:
                (job_dir / "stderr.log").write_text(log_text, encoding="utf-8")
            config_path = job_dir / "config.yaml"
            config_path.write_text(
                f"output_dir: {output_dir}\n", encoding="utf-8",
            )
            return build_job_progress(
                job_dir,
                {
                    "status": "running",
                    "mode": "pipeline",
                    "config_path": str(config_path),
                },
            )

    def test_pipeline_progress_smooths_55_to_92_via_prereport_signals(self) -> None:
        """UI review P2-14 + Codex follow-up on PR #207. The smoothing
        between model.pkl (55%) and report (92%) keys ONLY on signals
        emitted BEFORE the report: the backtest-step ``positions.json``
        and the ``Running …`` phase log markers. ``predictions.parquet``
        / ``metrics.json`` / ``nav.parquet`` are written by
        ``write_pipeline_result_artifacts`` AFTER the report, so they
        must NOT be treated as pre-report checkpoints."""

        # Backtest phase log marker → 65.
        p = self._pipeline_progress(
            run_files={"model.pkl": "x"},
            log_text="INFO Running canonical backtest...\n",
        )
        self.assertEqual(p["percent"], 65)
        self.assertEqual(p["label"], "正在运行回测")

        # positions.json (written by backtest, pre-report) → 70.
        p = self._pipeline_progress(
            run_files={"model.pkl": "x", "positions.json": "{}"},
            log_text="INFO Running canonical backtest...\n",
        )
        self.assertEqual(p["percent"], 70)
        self.assertEqual(p["label"], "已写入回测持仓")

        # Attribution phase marker outranks positions → 86.
        p = self._pipeline_progress(
            run_files={"model.pkl": "x", "positions.json": "{}"},
            log_text=(
                "INFO Running canonical backtest...\n"
                "INFO Running performance attribution...\n"
            ),
        )
        self.assertEqual(p["percent"], 86)
        self.assertEqual(p["label"], "正在做绩效归因")

    def test_post_report_artifacts_are_not_prereport_checkpoints(self) -> None:
        """``predictions.parquet`` / ``metrics.json`` / ``nav.parquet``
        present WITHOUT a report or any phase log must NOT bump the bar
        past the model checkpoint (55%) — they don't exist before the
        report in a live run, so treating them as smoothing signals was
        the bug Codex flagged on PR #207."""

        for artifact in ("predictions.parquet", "metrics.json", "nav.parquet"):
            with self.subTest(artifact=artifact):
                p = self._pipeline_progress(
                    run_files={"model.pkl": "x", artifact: "x"},
                )
                self.assertEqual(p["percent"], 55)
                self.assertEqual(p["label"], "已写入模型产物")

    def test_pipeline_log_phase_reads_only_trailing_window(self) -> None:
        """Codex follow-up on PR #207: the phase reader seeks from EOF
        and reads only the last 64 KiB, so a marker that sits BEFORE
        that trailing window (as in a long verbose log) is not seen —
        proving the whole file isn't loaded each poll. A marker WITHIN
        the trailing window is still found."""

        marker = "Running canonical backtest..."
        pad = "x" * (80 * 1024)  # 80 KiB > 64 KiB tail window

        # Marker only at the very start, then >64 KiB of padding → the
        # tail read should miss it, so we stay at the model checkpoint.
        p = self._pipeline_progress(
            run_files={"model.pkl": "x"},
            log_text=marker + "\n" + pad,
        )
        self.assertEqual(p["percent"], 55)

        # Marker in the trailing window → found.
        p = self._pipeline_progress(
            run_files={"model.pkl": "x"},
            log_text=pad + "\n" + marker + "\n",
        )
        self.assertEqual(p["percent"], 65)

    def test_pipeline_log_phase_seeks_from_eof(self) -> None:
        """Source pin: the reader must seek from EOF (SEEK_END) rather
        than ``read_bytes()`` the whole file (Codex follow-up PR #207)."""

        from pathlib import Path as _P

        source = _P("web/operator_ui/progress.py").read_text(encoding="utf-8")
        self.assertIn("handle.seek(-tail_bytes, 2)", source)
        # The whole-file read in this helper must be gone.
        func = source[source.index("def _pipeline_log_phase("):]
        func = func[: func.index("\n\n\n") if "\n\n\n" in func else len(func)]
        self.assertNotIn("read_bytes()", func)

    def test_job_manager_status_attaches_progress(self) -> None:
        job_root = Path(tempfile.mkdtemp())
        job_dir = job_root / "test_job"
        job_dir.mkdir(parents=True)
        job_dir.joinpath("job.json").write_text(
            json.dumps({"job_id": "test_job", "status": "success", "mode": "pipeline"}),
            encoding="utf-8",
        )

        with patch("web.operator_ui.job_manager.JOB_ROOT", job_root):
            from web.operator_ui.job_manager import JobManager

            status = JobManager.status("test_job")

        self.assertEqual(status["progress"]["percent"], 100)
        self.assertEqual(status["progress"]["label"], "已完成")


if __name__ == "__main__":
    unittest.main()
