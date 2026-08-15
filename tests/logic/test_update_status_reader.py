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
from src.data_pipeline.daily_update import (
    STATUS_SCHEMA_VERSION as WRITER_STATUS_SCHEMA_VERSION,
)
from web.operator_ui.update_status import (  # noqa: E402
    STATUS_FILENAME,
    STATUS_SCHEMA_VERSION,
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

    def test_reader_and_writer_agree_on_the_DERIVATION(self) -> None:
        # Agreeing on the FILENAME is not agreeing on the path: the two
        # modules each restate `provider -> artifact`, and the rule is what
        # can drift. Pinned over sibling providers because that is exactly
        # where the old rule collapsed (codex #434 r4).
        from src.data_pipeline.daily_update import default_status_path
        for provider in (Path("D:/qlib_data/my_cn_data_pit"),
                         Path("D:/qlib_data/my_cn_data_pit_2015"),
                         Path("/srv/bundles/live")):
            with self.subTest(provider=str(provider)):
                self.assertEqual(default_status_path(provider),
                                 status_path_for_provider(provider))

    def test_sibling_providers_do_not_share_one_artifact(self) -> None:
        # This repo SHIPS the colliding layout (my_cn_data_pit next to
        # my_cn_data_pit_2015), so the research bundle would have shown the
        # production provider's last run as its own.
        a = status_path_for_provider(Path("D:/qlib_data/my_cn_data_pit"))
        b = status_path_for_provider(Path("D:/qlib_data/my_cn_data_pit_2015"))
        self.assertNotEqual(a, b)
        # …and it stays a SIBLING, so the atomic swap cannot carry it away.
        self.assertEqual(a.parent, Path("D:/qlib_data"))

    def test_reader_and_writer_agree_on_the_schema_version(self) -> None:
        self.assertEqual(STATUS_SCHEMA_VERSION, WRITER_STATUS_SCHEMA_VERSION)


class PathDerivationTests(unittest.TestCase):
    def test_status_path_is_a_provider_specific_sibling(self) -> None:
        # Sibling (survives the swap) AND name-derived (unique per provider);
        # the original `<parent>/<FILENAME>` collided for sibling bundles
        # (codex #434 r4).
        # Asserted as a RELATIONSHIP, not a literal: the derivation resolves
        # the provider first (so a relative spelling like "." works), and a
        # hardcoded "/data/..." expectation is host-dependent — on Windows
        # `Path("/data/x").resolve()` acquires the current drive.
        provider = Path("/data/my_cn_data_pit")
        got = status_path_for_provider(provider)
        resolved = provider.resolve()
        self.assertEqual(resolved.parent, got.parent)          # sibling
        self.assertEqual(f"{resolved.name}.{STATUS_FILENAME}", got.name)

    def test_a_relative_provider_spelling_derives_a_real_path(self) -> None:
        # codex #434 r5: `Path(".").name` is empty and `with_name` raises, so
        # a valid relative provider URI took the page down with a traceback.
        got = status_path_for_provider(Path("."))
        self.assertTrue(got.is_absolute())
        self.assertTrue(got.name.endswith(STATUS_FILENAME))

    def test_a_filesystem_root_provider_is_refused_clearly(self) -> None:
        # A root has no sibling to derive from; refuse with a message rather
        # than let `with_name` raise an opaque ValueError deep in the page.
        import os
        root = Path(os.path.abspath(os.sep))
        with self.assertRaises(ValueError):
            status_path_for_provider(root)


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
        # A future exit code the reader has no meaning string for still
        # renders (loudly labeled unknown) — provided the record is otherwise
        # schema-complete (failed_stage required on any non-zero exit).
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / STATUS_FILENAME
            _write(p, {**_FINISHED_OK, "exit_code": 99, "failed_stage": "fetch"})
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


class StrictSchemaTests(unittest.TestCase):
    """codex P2: the reader must validate the COMPLETE state-specific schema
    before believing a record — a truncated write or a future schema_version
    reads as corrupt, never as a green success under v1 semantics."""

    def test_minimal_finished_record_does_not_render_green(self) -> None:
        # codex's exact example: {"state":"finished","exit_code":0} previously
        # rendered a green success despite missing every other field.
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / STATUS_FILENAME
            _write(p, {"state": "finished", "exit_code": 0})
            st = read_update_status(p)
            self.assertEqual(st.kind, "corrupt")
            self.assertFalse(st.ok)

    def test_unsupported_schema_version_is_corrupt(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / STATUS_FILENAME
            _write(p, {**_FINISHED_OK, "schema_version": 2})
            st = read_update_status(p)
            self.assertEqual(st.kind, "corrupt")
            self.assertIn("schema_version", st.error)

    def test_missing_schema_version_is_corrupt(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / STATUS_FILENAME
            payload = {k: v for k, v in _FINISHED_OK.items()
                       if k != "schema_version"}
            _write(p, payload)
            st = read_update_status(p)
            self.assertEqual(st.kind, "corrupt")
            self.assertIn("schema_version", st.error)

    def test_finished_missing_finished_at_is_corrupt(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / STATUS_FILENAME
            payload = {k: v for k, v in _FINISHED_OK.items()
                       if k != "finished_at"}
            _write(p, payload)
            st = read_update_status(p)
            self.assertEqual(st.kind, "corrupt")
            self.assertIn("finished_at", st.error)

    def test_finished_missing_run_date_is_corrupt(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / STATUS_FILENAME
            payload = {k: v for k, v in _FINISHED_OK.items()
                       if k != "run_date"}
            _write(p, payload)
            st = read_update_status(p)
            self.assertEqual(st.kind, "corrupt")
            self.assertIn("run_date", st.error)

    def test_running_missing_started_at_is_corrupt(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / STATUS_FILENAME
            _write(p, {"schema_version": 1, "state": "running",
                       "run_date": "2026-08-14"})
            st = read_update_status(p)
            self.assertEqual(st.kind, "corrupt")
            self.assertIn("started_at", st.error)

    def test_empty_string_field_counts_as_missing(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / STATUS_FILENAME
            _write(p, {**_FINISHED_OK, "finished_at": ""})
            st = read_update_status(p)
            self.assertEqual(st.kind, "corrupt")


class FinishedFieldCompletenessTests(unittest.TestCase):
    """codex P2 round 2: the writer ALWAYS emits failed_stage (null on
    success) and detail — a truncated record missing either key, or one
    breaking the exit_code/failed_stage invariant, is corrupt, never green."""

    def test_finished_missing_failed_stage_key_is_corrupt(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / STATUS_FILENAME
            payload = {k: v for k, v in _FINISHED_OK.items()
                       if k != "failed_stage"}
            _write(p, payload)
            st = read_update_status(p)
            self.assertEqual(st.kind, "corrupt")
            self.assertIn("failed_stage", st.error)

    def test_finished_missing_detail_key_is_corrupt(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / STATUS_FILENAME
            payload = {k: v for k, v in _FINISHED_OK.items() if k != "detail"}
            _write(p, payload)
            st = read_update_status(p)
            self.assertEqual(st.kind, "corrupt")
            self.assertIn("detail", st.error)

    def test_success_with_failed_stage_breaks_the_invariant(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / STATUS_FILENAME
            _write(p, {**_FINISHED_OK, "exit_code": 0, "failed_stage": "fetch"})
            st = read_update_status(p)
            self.assertEqual(st.kind, "corrupt")
            self.assertIn("不变式", st.error)

    def test_failure_without_failed_stage_breaks_the_invariant(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / STATUS_FILENAME
            _write(p, {**_FINISHED_OK, "exit_code": 15, "failed_stage": None})
            st = read_update_status(p)
            self.assertEqual(st.kind, "corrupt")
            self.assertIn("failed_stage", st.error)

    def test_failed_stage_wrong_type_is_corrupt(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / STATUS_FILENAME
            _write(p, {**_FINISHED_OK, "exit_code": 15, "failed_stage": 15})
            st = read_update_status(p)
            self.assertEqual(st.kind, "corrupt")

    def test_boolean_schema_version_is_corrupt(self) -> None:
        # codex #434 r3 (P2): `True == 1` in Python, so a JSON `true` satisfied
        # the version comparison and an otherwise-complete record rendered
        # GREEN on a document nothing had validated. Type first, value second.
        for bogus in (True, False):
            with self.subTest(schema_version=bogus):
                with tempfile.TemporaryDirectory() as t:
                    p = Path(t) / STATUS_FILENAME
                    _write(p, {**_FINISHED_OK, "schema_version": bogus})
                    st = read_update_status(p)
                    self.assertEqual(st.kind, "corrupt")
                    self.assertIn("schema_version", st.error)
        # …and the real version still reads clean (no over-broad rejection).
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / STATUS_FILENAME
            _write(p, dict(_FINISHED_OK))
            self.assertEqual(read_update_status(p).kind, "finished")

    def test_an_unreadable_artifact_is_not_reported_missing(self) -> None:
        # codex #434 r7 (P2): `Path.exists()` answers False for a file it
        # cannot STAT, so a permissions failure rendered as the benign
        # 从未记录. Only FileNotFoundError may mean "missing"; every other
        # OSError must surface as the loud corrupt/read-error state.
        from unittest.mock import patch
        target = Path("Z:/somewhere") / STATUS_FILENAME
        with patch.object(Path, "read_text",
                          side_effect=PermissionError("denied")):
            st = read_update_status(target)
        self.assertEqual(st.kind, "corrupt")
        self.assertIn("denied", st.error or "")
        with patch.object(Path, "read_text",
                          side_effect=FileNotFoundError()):
            st = read_update_status(target)
        self.assertEqual(st.kind, "missing")

    def test_float_schema_version_is_corrupt(self) -> None:
        # codex #434 r4: JSON `1.0` parses to a float and `1.0 == 1`, so the
        # bool-exclusion spelling still let it through. `type(...) is int`
        # covers bool and float in one predicate.
        for bogus in (1.0, True, False, "1", None):
            with self.subTest(schema_version=bogus):
                with tempfile.TemporaryDirectory() as t:
                    p = Path(t) / STATUS_FILENAME
                    _write(p, {**_FINISHED_OK, "schema_version": bogus})
                    st = read_update_status(p)
                    self.assertEqual(st.kind, "corrupt")
                    self.assertIn("schema_version", st.error)

    def test_detail_wrong_type_is_corrupt(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / STATUS_FILENAME
            _write(p, {**_FINISHED_OK, "detail": 15})
            st = read_update_status(p)
            self.assertEqual(st.kind, "corrupt")
            self.assertIn("detail", st.error)


if __name__ == "__main__":
    unittest.main()
