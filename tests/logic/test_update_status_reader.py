"""Update-status reader tests (2026-08-14-daily-update-run-status).

Pure reader-side coverage: path derivation, the missing/corrupt/running/
finished distinction, and the pin that the web helper's filename constant
matches the writer's (the two modules deliberately do not import each other —
the page must stay free of orchestrator references for the read-only
governance scan, so the shared name is pinned by THIS test instead).
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data_pipeline.daily_update import (  # noqa: E402
    STATUS_FILENAME as WRITER_STATUS_FILENAME,
)
from web.operator_ui.update_status import (  # noqa: E402
    STATUS_FILENAME,
    read_update_status,
    status_path_for_provider,
)


def _write(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


_FINISHED_OK = {
    "schema_version": 1,
    "state": "finished",
    "run_date": "2026-08-14",
    "started_at": "2026-08-14T20:43:00+08:00",
    "finished_at": "2026-08-14T21:58:12+08:00",
    "exit_code": 0,
    "failed_stage": None,
    "detail": "daily update complete",
}


class FilenamePinTests(unittest.TestCase):
    def test_reader_and_writer_agree_on_the_filename(self) -> None:
        self.assertEqual(STATUS_FILENAME, WRITER_STATUS_FILENAME)


class PathDerivationTests(unittest.TestCase):
    def test_status_path_is_sibling_of_provider_dir(self) -> None:
        provider = Path("/data/my_cn_data_pit")
        self.assertEqual(
            status_path_for_provider(provider),
            Path("/data/daily_update_status.json"),
        )


class ReadUpdateStatusTests(unittest.TestCase):

    def test_missing_file_is_informational_not_error(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            st = read_update_status(Path(t) / "nope.json")
            self.assertEqual(st.kind, "missing")
            self.assertFalse(st.ok)

    def test_finished_ok(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / STATUS_FILENAME
            _write(p, _FINISHED_OK)
            st = read_update_status(p)
            self.assertEqual(st.kind, "finished")
            self.assertTrue(st.ok)
            self.assertEqual(st.exit_code, 0)
            self.assertIsNone(st.failed_stage)
            self.assertEqual(st.run_date, "2026-08-14")

    def test_finished_failed_carries_stage_and_exit_meaning(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / STATUS_FILENAME
            _write(p, {**_FINISHED_OK, "exit_code": 15, "failed_stage": "validate",
                       "detail": "validation failed"})
            st = read_update_status(p)
            self.assertEqual(st.kind, "finished")
            self.assertFalse(st.ok)
            self.assertEqual(st.exit_code, 15)
            self.assertEqual(st.failed_stage, "validate")
            self.assertIn("校验", st.exit_meaning)

    def test_unknown_exit_code_says_so(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / STATUS_FILENAME
            _write(p, {**_FINISHED_OK, "exit_code": 99})
            st = read_update_status(p)
            self.assertEqual(st.exit_meaning, "未知退出码")

    def test_running_record(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / STATUS_FILENAME
            _write(p, {"schema_version": 1, "state": "running",
                       "run_date": "2026-08-14",
                       "started_at": "2026-08-14T20:43:00+08:00"})
            st = read_update_status(p)
            self.assertEqual(st.kind, "running")
            self.assertFalse(st.ok)
            self.assertIsNone(st.exit_code)

    def test_corrupt_json_is_loud(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / STATUS_FILENAME
            p.write_text("{ not json", encoding="utf-8")
            st = read_update_status(p)
            self.assertEqual(st.kind, "corrupt")
            self.assertIn("JSON", st.error)

    def test_non_object_top_level_is_corrupt(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / STATUS_FILENAME
            _write(p, ["not", "a", "dict"])
            st = read_update_status(p)
            self.assertEqual(st.kind, "corrupt")

    def test_missing_state_is_corrupt(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / STATUS_FILENAME
            _write(p, {"schema_version": 1})
            st = read_update_status(p)
            self.assertEqual(st.kind, "corrupt")

    def test_finished_without_int_exit_code_is_corrupt(self) -> None:
        # bool is an int subclass — a JSON `true` must not pass as exit 1.
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / STATUS_FILENAME
            _write(p, {**_FINISHED_OK, "exit_code": True})
            st = read_update_status(p)
            self.assertEqual(st.kind, "corrupt")


if __name__ == "__main__":
    unittest.main()
