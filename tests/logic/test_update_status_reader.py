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
    # normalized provider identity, as the writer stamps it (codex #434 r18)
    "provider_dir": "d:" + chr(92) + "qlib_data" + chr(92) + "my_cn_data_pit",
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
        # Host-independent on purpose (codex #434 r28, same class as W35):
        # on POSIX, `Path("D:/...").resolve()` anchors under the CWD, so a
        # literal `Path("D:/qlib_data")` parent assertion is a Windows-only
        # premise. Assert the RELATIONSHIPS instead — distinct artifacts,
        # same parent as each provider's own resolved parent.
        pa = Path("D:/qlib_data/my_cn_data_pit")
        pb = Path("D:/qlib_data/my_cn_data_pit_2015")
        a = status_path_for_provider(pa)
        b = status_path_for_provider(pb)
        self.assertNotEqual(a, b)
        # …and each stays a SIBLING of its (resolved) provider, so the atomic
        # swap cannot carry it away.
        self.assertEqual(a.parent, pa.resolve().parent)
        self.assertEqual(b.parent, pb.resolve().parent)

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
                       "provider_dir": _FINISHED_OK["provider_dir"],
                       "run_date": "2026-08-14",
                       "started_at": "2026-08-14T20:43:00+08:00"})
            st = read_update_status(p)
            self.assertEqual(st.kind, "running")
            self.assertFalse(st.ok)
            self.assertIsNone(st.exit_code)

    def test_the_writer_pid_is_parsed_and_optional(self) -> None:
        # pid 是取消收养的**进程身份**判据（#470 第九轮）:新产出器落
        # os.getpid();旧的在盘记录没有这个键——缺 = None（不算截断,收养
        # 侧对 None fail-closed 不绑定）,在场就必须是正 int。
        base = {"schema_version": 1, "state": "running",
                "provider_dir": _FINISHED_OK["provider_dir"],
                "run_date": "2026-08-14",
                "started_at": "2026-08-14T20:43:00+08:00"}
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / STATUS_FILENAME
            _write(p, {**base, "pid": 31337})
            self.assertEqual(31337, read_update_status(p).pid)
            _write(p, base)
            st = read_update_status(p)
            self.assertEqual(st.kind, "running", "缺 pid 被误判截断")
            self.assertIsNone(st.pid)

    def test_a_malformed_pid_is_corrupt_not_coerced(self) -> None:
        # 在场即验:``true`` 冒充 pid 会 ``True == 1`` 误绑定,字符串会
        # 静默永不绑定——两者都不是「读出来再说」,是损坏。
        base = {"schema_version": 1, "state": "running",
                "provider_dir": _FINISHED_OK["provider_dir"],
                "run_date": "2026-08-14",
                "started_at": "2026-08-14T20:43:00+08:00"}
        # None 即显式 ``"pid": null``——键在场就得是正 int,经 .get() 与
        # 缺键混同会把畸形记录放行成合法 legacy（codex 第二十一轮 P2:
        # 硬取消后证据绑不上它,页面误称无匹配 running、锁启动六小时）。
        for bad in (True, "31337", 0, -4, 3.0, None):
            with self.subTest(pid=bad):
                with tempfile.TemporaryDirectory() as t:
                    p = Path(t) / STATUS_FILENAME
                    _write(p, {**base, "pid": bad})
                    st = read_update_status(p)
                    self.assertEqual(st.kind, "corrupt")
                    self.assertIn("pid", st.error)

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

    def test_running_age_fresh_stale_and_unknowable(self) -> None:
        # codex #434 r8: a killed updater leaves `running` on disk forever;
        # past the staleness threshold the page must not keep asserting an
        # update is in progress. `None` (unparseable / tz-naive started_at)
        # counts as "cannot establish freshness" and must render ambiguous.
        from datetime import datetime, timedelta, timezone

        from web.operator_ui.update_status import (
            RUNNING_STALE_AFTER,
            UpdateRunStatus,
            running_age,
        )
        now = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)

        def running(started_at: str) -> UpdateRunStatus:
            return UpdateRunStatus(kind="running", path=Path("x"),
                                   state="running", started_at=started_at)

        fresh = running((now - timedelta(hours=1)).isoformat())
        self.assertLessEqual(running_age(fresh, now), RUNNING_STALE_AFTER)
        stale = running((now - timedelta(hours=20)).isoformat())
        self.assertGreater(running_age(stale, now), RUNNING_STALE_AFTER)
        # unparseable / naive -> None (freshness cannot be established)
        self.assertIsNone(running_age(running("not-a-date"), now))
        self.assertIsNone(running_age(running("2026-08-15T10:00:00"), now))
        # non-running records have no age at all
        done = UpdateRunStatus(kind="finished", path=Path("x"))
        self.assertIsNone(running_age(done, now))

    def test_classify_running_three_states(self) -> None:
        # codex #434 r9: the r8 inline comparison was wrong twice — a
        # NEGATIVE age (future started_at: clock skew / fabricated stamp)
        # satisfied the upper-bound-only check and rendered 正在运行 until
        # six hours past the FUTURE instant; and unknown age shared the
        # stale wording. Three honest answers, decided in a pure function.
        from datetime import datetime, timedelta, timezone

        from web.operator_ui.update_status import (
            RUNNING_FRESH,
            RUNNING_STALE,
            RUNNING_STALE_AFTER,
            RUNNING_UNVERIFIABLE,
            UpdateRunStatus,
            classify_running,
        )
        now = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)

        def running(started_at: str) -> UpdateRunStatus:
            return UpdateRunStatus(kind="running", path=Path("x"),
                                   state="running", started_at=started_at)

        cases = {
            RUNNING_FRESH: (now - timedelta(hours=1)).isoformat(),
            RUNNING_STALE: (now - RUNNING_STALE_AFTER
                            - timedelta(minutes=1)).isoformat(),
            RUNNING_UNVERIFIABLE: (now + timedelta(hours=2)).isoformat(),
        }
        for expected, stamp in cases.items():
            with self.subTest(expected=expected):
                self.assertEqual(expected,
                                 classify_running(running(stamp), now))
        # unparseable / naive -> unverifiable too
        for stamp in ("not-a-date", "2026-08-15T10:00:00"):
            with self.subTest(stamp=stamp):
                self.assertEqual(RUNNING_UNVERIFIABLE,
                                 classify_running(running(stamp), now))
        # exactly at the threshold boundary is still fresh; just past is not
        self.assertEqual(RUNNING_FRESH, classify_running(
            running((now - RUNNING_STALE_AFTER).isoformat()), now))
        # non-running records classify as None
        self.assertIsNone(classify_running(
            UpdateRunStatus(kind="finished", path=Path("x")), now))

    def test_the_page_honors_a_status_path_override(self) -> None:
        # codex #434 r10: the CLI advertises --status-path, but the page
        # always derived the location — a deployment using the override saw
        # 从未记录 while last night's run sat in the custom file.
        page = (Path(__file__).resolve().parents[2] / "web" / "operator_ui"
                / "pages" / "data_inspect.py").read_text(encoding="utf-8")
        self.assertIn("--status-path", page)
        # the read goes through the operator-editable value, not the
        # derivation directly
        self.assertIn("read_update_status(_status_file)", page)
        self.assertIn("_status_input", page)
        self.assertNotIn("read_update_status(status_path_for_provider", page)
        # …and the missing state names the override as a cause to check
        missing_at = page.index("从未记录数据更新运行")
        self.assertIn("--status-path", page[missing_at:missing_at + 300])
        # The widget is KEYED to the provider (codex #434 r13): Streamlit
        # applies `value=` only on first creation, so without a
        # provider-derived key, editing provider_uri leaves the box holding
        # the previous provider's path — the bundle sections inspect one
        # provider while this section silently reads another's artifact.
        self.assertIn('key=f"data_inspect::status_path::{provider_dir}"', page)

    def test_the_page_never_asserts_running_it_cannot_verify(self) -> None:
        # Source pins (codex #434 r8/r9): the fresh banner is conditional on
        # the classifier; stale and unverifiable use DIFFERENT wording, and
        # "已超过" may appear only in the stale branch — asserting an age
        # nobody computed is the same defect as asserting the run is active.
        page = (Path(__file__).resolve().parents[2] / "web" / "operator_ui"
                / "pages" / "data_inspect.py").read_text(encoding="utf-8")
        self.assertIn("classify_running(_update_status)", page)
        self.assertIn("可能已被中断", page)          # stale wording
        self.assertIn("无法核实", page)              # unverifiable wording
        self.assertLess(page.index("_cls = classify_running"),
                        page.index("数据更新**正在运行**"))
        # The WHOLE unverifiable branch (its `else:` to the next top-level
        # `elif`) must not claim an elapsed duration. A window anchored after
        # one keyword missed a spelling that put 已超过 before it — the first
        # cut of this pin did exactly that and its reverse validation caught
        # it, not the review.
        branch_start = page.index("else:", page.index("RUNNING_STALE:"))
        branch_end = page.index("elif _update_status.ok:")
        unverifiable_branch = page[branch_start:branch_end]
        self.assertIn("无法核实", unverifiable_branch)
        self.assertNotIn("已超过", unverifiable_branch)
        self.assertNotIn("小时", unverifiable_branch)

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

    def test_record_without_provider_identity_is_corrupt(self) -> None:
        # codex #434 r18: two schedules can point one explicit --status-path
        # at the same file; without an identity stamp the reader cannot even
        # in principle detect the mix-up.
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / STATUS_FILENAME
            payload = dict(_FINISHED_OK)
            del payload["provider_dir"]
            _write(p, payload)
            st = read_update_status(p)
            self.assertEqual(st.kind, "corrupt")
            self.assertIn("provider_dir", st.error)

    def test_record_matches_provider_normalization_parity(self) -> None:
        # The reader's normalization must equal the writer's `_norm` — pinned
        # by comparison, since the two modules deliberately do not import
        # each other.
        import os

        from src.data_pipeline.daily_update import _norm
        from web.operator_ui.update_status import (
            UpdateRunStatus,
            record_matches_provider,
        )
        with tempfile.TemporaryDirectory() as t:
            provider = Path(t) / "My_Provider"
            provider.mkdir()
            stamped = UpdateRunStatus(
                kind="finished", path=Path("x"),
                provider_dir=_norm(provider))
            # spelled differently (relative hop) — still the same dir;
            # host-independent because resolve() collapses "." everywhere
            self.assertTrue(record_matches_provider(
                stamped, provider.parent / "." / provider.name))
            # case folding is a WINDOWS filesystem semantic (normcase is the
            # identity on POSIX, where an uppercased path is a DIFFERENT
            # path) — asserted only where the host folds case, or this line
            # is a Windows-only premise that reds every Ubuntu leg
            # (codex #434 r28, same class as W35).
            if os.path.normcase("A") == "a":
                self.assertTrue(record_matches_provider(
                    stamped, Path(str(provider).upper())))
            other = UpdateRunStatus(
                kind="finished", path=Path("x"),
                provider_dir=_norm(Path(t) / "Other"))
            self.assertFalse(record_matches_provider(other, provider))
            del os

    def test_the_page_refuses_a_foreign_providers_record(self) -> None:
        page = (Path(__file__).resolve().parents[2] / "web" / "operator_ui"
                / "pages" / "data_inspect.py").read_text(encoding="utf-8")
        self.assertIn("record_matches_provider(_update_status, provider_dir)", page)
        self.assertIn("属于**另一个 provider**", page)
        # the refusal comes BEFORE any state-specific rendering
        self.assertLess(page.index("record_matches_provider"),
                        page.index('elif _update_status.kind == "missing"'))

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
