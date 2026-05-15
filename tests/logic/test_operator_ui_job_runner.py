"""Unit tests for the operator UI job runner CLI dispatch."""

from __future__ import annotations

import json
import sys as _sys
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_PROJECT_ROOT))


class JobRunnerDispatchTests(unittest.TestCase):
    def test_tushare_provider_mode_launches_existing_ingest_cli(self) -> None:
        from web.operator_ui.job_runner import main

        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp)
            output_dir = job_dir / "qlib_provider"
            output_dir.mkdir()
            (output_dir / "calendars").mkdir()
            job_dir.joinpath("config.yaml").write_text(
                f"output_dir: {output_dir}\n"
                "start_date: '2025-01-01'\n"
                "end_date: '2025-01-31'\n"
                "data_adjust_mode: pre_adjusted\n",
                encoding="utf-8",
            )

            with patch("subprocess.run") as mock_run:
                mock_run.return_value = CompletedProcess(args=[], returncode=0)
                main([str(job_dir), "tushare_provider"])

            cmd = mock_run.call_args[0][0]
            kwargs = mock_run.call_args.kwargs
            self.assertIn("scripts/ingest_tushare_qlib_provider.py", cmd)
            self.assertFalse(kwargs["shell"])
            self.assertIn(str(Path(__file__).resolve().parents[2]), kwargs["env"]["PYTHONPATH"])
            data = json.loads(job_dir.joinpath("job.json").read_text(encoding="utf-8"))
            self.assertEqual(data["status"], "success")
            self.assertEqual(data["run_dir"], str(output_dir))


if __name__ == "__main__":
    unittest.main()
