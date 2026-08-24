"""运行台账 reader、进度归属、工作台条带。

（openspec 2026-08-24-daily-update-run-ledger）

写侧那一半由 `tests/data_pipeline/test_daily_update_run_ledger.py` 守。这里守
读侧三件事：与写入侧的常量/路径/归一化**逐点一致**、坏行不许毒死整份台账、
以及归属由**运行边界**判定而不是启发式。
"""

from __future__ import annotations

import ast
import json
import os
import tempfile
import unittest
from pathlib import Path

import src.data_pipeline.daily_update as writer
import web.operator_ui.update_status as status_reader
from web.operator_ui.update_ledger import (
    LEDGER_FILENAME,
    LEDGER_SCHEMA_VERSION,
    LedgerRun,
    consecutive_failures,
    ledger_path_for_provider,
    read_ledger,
)
from web.operator_ui.update_progress import (
    RUN_BOUNDARY_MARK,
    last_fetch_progress_for_run,
)

_ROOT = Path(__file__).resolve().parents[2]
_WORKBENCH = _ROOT / "web" / "operator_ui" / "pages" / "today_workbench.py"
_PROGRESS_LINE = (
    "  daily year=2026 progress: 2400/5883 tickers (written=2400, skipped=0)")


def _write(path: Path, records: list[dict], *, trailing: bytes = b"") -> None:
    blob = b"".join(
        json.dumps(r, ensure_ascii=False).encode("utf-8") + b"\n" for r in records)
    path.write_bytes(blob + trailing)


def _record(provider: Path, *, exit_code: int, day: str = "2026-08-21") -> dict:
    return {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "provider_dir": writer._norm(provider),
        "run_date": day,
        "started_at": f"{day}T20:30:00+08:00",
        "finished_at": f"{day}T22:22:00+08:00",
        "exit_code": exit_code,
        "failed_stage": None if exit_code == 0 else "fetch",
        "detail": "ok" if exit_code == 0 else "fetch failed hard (exit 1)",
    }


# ---------------------------------------------------------------- 与写侧对齐

class TheReaderAgreesWithTheWriter(unittest.TestCase):
    """`web/` 不 import 管线层，所以这些值在两边各声明一次——由本类钉住相等。

    与 `update_status` 同样的处理：重复是刻意的，零一致性测试不是。
    """

    def test_the_filename_matches(self) -> None:
        self.assertEqual(writer.LEDGER_FILENAME, LEDGER_FILENAME)

    def test_the_schema_version_matches(self) -> None:
        self.assertEqual(writer.LEDGER_SCHEMA_VERSION, LEDGER_SCHEMA_VERSION)

    def test_the_boundary_mark_matches(self) -> None:
        self.assertEqual(writer.RUN_BOUNDARY_MARK, RUN_BOUNDARY_MARK)

    def test_the_path_derivation_matches(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            for name in ("my_cn_data_pit", "my_cn_data_pit_2015", "."):
                with self.subTest(provider=name):
                    provider = Path(t) / name if name != "." else Path(t)
                    self.assertEqual(
                        writer.default_ledger_path(provider),
                        ledger_path_for_provider(provider))

    def test_the_normalisation_matches_on_all_three_sides(self) -> None:
        """写侧 `_norm`、状态 reader、台账 reader 三方必须同一套归一化。

        任意两方不一致，「这行是不是本 provider 的」就会给出两个答案。
        """
        with tempfile.TemporaryDirectory() as t:
            provider = Path(t) / "prov"
            provider.mkdir()
            ours = os.path.normcase(str(provider.resolve()))
            self.assertEqual(writer._norm(provider), ours)
            record = status_reader.UpdateRunStatus(
                kind="finished", path=Path("x"), provider_dir=ours)
            self.assertTrue(
                status_reader.record_matches_provider(record, provider))
            history = read_ledger(
                ledger_path_for_provider(provider), provider_dir=provider)
            self.assertEqual("missing", history.kind)   # 前提：还没有台账

    def test_a_root_provider_is_refused_loudly(self) -> None:
        # 文件系统根没有可派生的兄弟名。抛出而不是返回，页面才能说出这件事。
        with self.assertRaises(ValueError):
            ledger_path_for_provider(Path(Path(os.sep).anchor or os.sep))


# ---------------------------------------------------------------- 容错但不静默

class OneBadLineDoesNotPoisonTheLedger(unittest.TestCase):

    def test_a_malformed_line_is_counted_and_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            provider = Path(t) / "prov"
            provider.mkdir()
            path = ledger_path_for_provider(provider)
            path.write_bytes(
                json.dumps(_record(provider, exit_code=0)).encode() + b"\n"
                + b"{not json at all}\n"
                + json.dumps(_record(provider, exit_code=11)).encode() + b"\n")
            history = read_ledger(path, provider_dir=provider)
        self.assertEqual(2, len(history.runs), "坏行把好行一起带走了")
        self.assertEqual(1, history.malformed, "坏行没有被计数")

    def test_another_providers_line_is_counted_not_adopted(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            provider, other = Path(t) / "prov", Path(t) / "other"
            provider.mkdir()
            other.mkdir()
            path = ledger_path_for_provider(provider)
            _write(path, [_record(other, exit_code=0), _record(provider, exit_code=11)])
            history = read_ledger(path, provider_dir=provider)
        self.assertEqual(1, len(history.runs))
        self.assertEqual(1, history.foreign, "外来行没有被计数")
        self.assertEqual(11, history.runs[0].exit_code)

    def test_a_torn_tail_is_one_malformed_line_not_a_lost_run(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            provider = Path(t) / "prov"
            provider.mkdir()
            path = ledger_path_for_provider(provider)
            _write(path, [_record(provider, exit_code=0)], trailing=b'{"partial"')
            history = read_ledger(path, provider_dir=provider)
        self.assertEqual(1, len(history.runs))
        self.assertEqual(1, history.malformed)

    def test_missing_and_unreadable_are_different_kinds(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            provider = Path(t) / "prov"
            provider.mkdir()
            self.assertEqual(
                "missing",
                read_ledger(ledger_path_for_provider(provider),
                            provider_dir=provider).kind)
            # 一个目录挡在路径上 —— 读不动，但不是「不存在」。
            blocked = Path(t) / "blocked"
            blocked.mkdir()
            self.assertEqual(
                "unreadable", read_ledger(blocked, provider_dir=provider).kind)


class ARecordThatIsNotInterpretableIsMalformedNotAFailedRun(unittest.TestCase):
    """带对 provider 的 JSON 对象**不等于**一条可解释的运行记录。

    不校验就把未来版本的记录、或 `exit_code: true` 这种（`isinstance(True, int)`
    在 Python 里为真！）显示成一次**失败的运行**——把损坏的数据讲成事实，比
    报「读不了」糟得多（codex P2）。
    """

    def _one(self, provider: Path, path: Path, **override: object) -> int:
        record = {**_record(provider, exit_code=11), **override}
        _write(path, [record])
        return len(read_ledger(path, provider_dir=provider).runs)

    def test_a_future_schema_version_is_not_read_with_v1_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            provider = Path(t) / "prov"
            provider.mkdir()
            path = ledger_path_for_provider(provider)
            self.assertEqual(0, self._one(provider, path, schema_version=2))
            self.assertEqual(
                1, read_ledger(path, provider_dir=provider).malformed,
                "未来版本的记录没有被计成读不了")

    def test_a_boolean_exit_code_is_not_a_failed_run(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            provider = Path(t) / "prov"
            provider.mkdir()
            path = ledger_path_for_provider(provider)
            self.assertEqual(0, self._one(provider, path, exit_code=True))

    def test_missing_or_mistyped_fields_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            provider = Path(t) / "prov"
            provider.mkdir()
            path = ledger_path_for_provider(provider)
            for override in ({"run_date": None}, {"started_at": 123},
                             {"detail": []}, {"failed_stage": 7},
                             {"exit_code": "11"}):
                with self.subTest(改动=override):
                    self.assertEqual(0, self._one(provider, path, **override))

    def test_a_wellformed_v1_record_still_reads(self) -> None:
        # 前提：上面那些拒绝不是因为整条路径坏了。
        with tempfile.TemporaryDirectory() as t:
            provider = Path(t) / "prov"
            provider.mkdir()
            path = ledger_path_for_provider(provider)
            self.assertEqual(1, self._one(provider, path))


# ---------------------------------------------------------------- 连败与耗时

class TheRunsAreOrderedNewestFirstAndTheStreakIsCounted(unittest.TestCase):

    def test_newest_first(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            provider = Path(t) / "prov"
            provider.mkdir()
            path = ledger_path_for_provider(provider)
            _write(path, [
                _record(provider, exit_code=0, day="2026-08-17"),
                _record(provider, exit_code=11, day="2026-08-20"),
                _record(provider, exit_code=11, day="2026-08-21"),
            ])
            runs = read_ledger(path, provider_dir=provider).runs
        self.assertEqual(["2026-08-21", "2026-08-20", "2026-08-17"],
                         [r.run_date for r in runs])

    def test_the_streak_is_the_number_the_three_nights_needed(self) -> None:
        """三晚事故里没人看得见的就是这个数。"""
        with tempfile.TemporaryDirectory() as t:
            provider = Path(t) / "prov"
            provider.mkdir()
            path = ledger_path_for_provider(provider)
            _write(path, [
                _record(provider, exit_code=0, day="2026-08-14"),
                _record(provider, exit_code=11, day="2026-08-17"),
                _record(provider, exit_code=11, day="2026-08-20"),
                _record(provider, exit_code=11, day="2026-08-21"),
            ])
            history = read_ledger(path, provider_dir=provider)
        self.assertEqual(3, consecutive_failures(history))

    def test_a_success_ends_the_streak(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            provider = Path(t) / "prov"
            provider.mkdir()
            path = ledger_path_for_provider(provider)
            _write(path, [_record(provider, exit_code=11),
                          _record(provider, exit_code=0)])
            self.assertEqual(
                0, consecutive_failures(read_ledger(path, provider_dir=provider)))

    def test_elapsed_is_derived_not_stored(self) -> None:
        run = LedgerRun(started_at="2026-08-21T20:30:00+08:00",
                        finished_at="2026-08-21T22:22:00+08:00")
        self.assertEqual(6720.0, run.elapsed_seconds)

    def test_an_unparseable_timestamp_yields_none_not_zero(self) -> None:
        # 不知道就不编：0 会被读成「瞬间跑完」。
        self.assertIsNone(LedgerRun(started_at="x", finished_at="y").elapsed_seconds)


# ---------------------------------------------------------------- 归属靠边界

class AttributionComesFromTheBoundaryNotAHeuristic(unittest.TestCase):
    """`update_progress` 曾列出四种试过又否掉的启发式，结论是结构性的：
    要精确归属，得先让写入侧落一个带日期的边界。现在它落了。
    """

    @staticmethod
    def _boundary(provider: Path, started: str) -> str:
        return (f"20:30:01 [x] INFO — {RUN_BOUNDARY_MARK} {started} "
                f"provider={writer._norm(provider)}")

    def test_progress_after_our_boundary_is_attributed(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            provider = Path(t) / "prov"
            provider.mkdir()
            text = "\n".join([
                f"21:00:00{_PROGRESS_LINE}",                       # 上一次留下的
                self._boundary(provider, "2026-08-24T20:30:01+08:00"),
                f"20:31:00{_PROGRESS_LINE}",
            ])
            got = last_fetch_progress_for_run(text, provider_dir=provider)
        self.assertTrue(got.attributed)
        self.assertEqual("2026-08-24T20:30:01+08:00", got.boundary_stamp)

    def test_progress_before_the_boundary_is_not_this_runs(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            provider = Path(t) / "prov"
            provider.mkdir()
            text = "\n".join([
                f"21:00:00{_PROGRESS_LINE}",
                self._boundary(provider, "2026-08-24T20:30:01+08:00"),
            ])
            got = last_fetch_progress_for_run(text, provider_dir=provider)
        self.assertIsNone(got.progress, "边界之前那条被当成了本次的")
        self.assertTrue(got.attributed)

    def test_no_boundary_keeps_the_old_honest_answer(self) -> None:
        """窗口里没有边界时**退回边界落地之前的行为**，不是退步，也不猜。"""
        with tempfile.TemporaryDirectory() as t:
            provider = Path(t) / "prov"
            provider.mkdir()
            got = last_fetch_progress_for_run(
                f"21:00:00{_PROGRESS_LINE}", provider_dir=provider)
        self.assertFalse(got.attributed)
        self.assertIsNotNone(got.progress, "没有边界不该连进度也丢掉")

    def test_a_foreign_boundary_is_not_adopted(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            provider, other = Path(t) / "prov", Path(t) / "other"
            provider.mkdir()
            other.mkdir()
            text = "\n".join([
                self._boundary(other, "2026-08-23T09:00:00+08:00"),
                f"09:01:00{_PROGRESS_LINE}",
            ])
            got = last_fetch_progress_for_run(text, provider_dir=provider)
        self.assertFalse(got.attributed, "采纳了别的 provider 的边界")

    def test_a_foreign_boundary_after_ours_defeats_attribution(self) -> None:
        """别人的边界排在我们后面时，**不知道**，而不是回头用我们那条旧的。

        兄弟 bundle 共用同一条日志（`default_log_path` 取
        `<provider 父目录>/logs/daily_update.log`），而单飞锁是 per-provider 的
        ——两个 provider **可以同时在跑**，行会交错。跳过别人的边界去用我们更
        早那条，就会把交错进来的**别人的**进度当成我们的，还以「归属已确定」
        的口气说出来（codex P1）。
        """
        with tempfile.TemporaryDirectory() as t:
            provider, other = Path(t) / "prov", Path(t) / "other"
            provider.mkdir()
            other.mkdir()
            text = chr(10).join([
                self._boundary(provider, "2026-08-24T20:30:00+08:00"),
                f"20:31:00{_PROGRESS_LINE}",
                self._boundary(other, "2026-08-24T20:35:00+08:00"),
                "  daily year=2026 progress: 9999/9999 tickers (written=9999, skipped=0)",
            ])
            got = last_fetch_progress_for_run(text, provider_dir=provider)
        self.assertFalse(
            got.attributed, "把别人的进度当成了我们的，且说成「已确定」")

    def test_our_boundary_last_still_attributes(self) -> None:
        # 反面：别人的边界在**前**、我们的在后 —— 仍然确定。
        with tempfile.TemporaryDirectory() as t:
            provider, other = Path(t) / "prov", Path(t) / "other"
            provider.mkdir()
            other.mkdir()
            text = chr(10).join([
                self._boundary(other, "2026-08-24T20:00:00+08:00"),
                self._boundary(provider, "2026-08-24T20:30:00+08:00"),
                f"20:31:00{_PROGRESS_LINE}",
            ])
            got = last_fetch_progress_for_run(text, provider_dir=provider)
        self.assertTrue(got.attributed)
        self.assertEqual("2026-08-24T20:30:00+08:00", got.boundary_stamp)

    def test_the_latest_of_several_boundaries_wins(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            provider = Path(t) / "prov"
            provider.mkdir()
            text = "\n".join([
                self._boundary(provider, "2026-08-23T20:30:00+08:00"),
                f"20:40:00{_PROGRESS_LINE}",
                self._boundary(provider, "2026-08-24T20:30:01+08:00"),
                "  daily year=2026 progress: 5000/5883 tickers (written=5000, skipped=0)",
            ])
            got = last_fetch_progress_for_run(text, provider_dir=provider)
        self.assertEqual("2026-08-24T20:30:01+08:00", got.boundary_stamp)
        assert got.progress is not None
        self.assertEqual(5000, got.progress.done)

    def test_a_boundary_mid_file_is_found(self) -> None:
        """边界之后必然还有阶段输出，所以它几乎永远不是最后一行。

        少了 `re.MULTILINE`，`$` 只在整串末尾匹配 —— 于是这条正则在真实日志里
        几乎永远匹配不上（实测如此）。
        """
        with tempfile.TemporaryDirectory() as t:
            provider = Path(t) / "prov"
            provider.mkdir()
            text = "\n".join([
                self._boundary(provider, "2026-08-24T20:30:01+08:00"),
                "20:30:02 [x] INFO — Startup bundle-state action: healthy",
                f"20:31:00{_PROGRESS_LINE}",
            ])
            self.assertTrue(
                last_fetch_progress_for_run(text, provider_dir=provider).attributed)


# ---------------------------------------------------------------- 工作台条带

class TheWorkbenchReproducesTheLedger(unittest.TestCase):

    _SOURCE = _WORKBENCH.read_text(encoding="utf-8")
    _TREE = ast.parse(_SOURCE)

    def test_the_page_reads_the_ledger_through_the_reader(self) -> None:
        called = {
            node.func.id for node in ast.walk(self._TREE)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertIn("read_ledger", called)
        self.assertIn("ledger_path_for_provider", called)

    def test_the_page_computes_no_verdict_of_its_own(self) -> None:
        """条带只**复现**台账记下的东西。

        UI 自己算判定，就会与写入侧分头漂移——这一页已经为「同一件事写两处」
        付过一整轮学费（#461 三条 P1）。
        """
        strip = next(
            node for node in ast.walk(self._TREE)
            if isinstance(node, ast.FunctionDef) and node.name == "_render_recent_runs")
        body = ast.unparse(strip)
        for forbidden in ("date.today(", "datetime.now(", "timedelta(", "exit_meaning"):
            with self.subTest(不许出现=forbidden):
                self.assertNotIn(forbidden, body)

    def test_a_missing_ledger_is_stated_not_rendered_as_empty(self) -> None:
        # 空条带与「台账不在」对操作人长得一样，除非页面说出是哪一种。
        strip = ast.unparse(next(
            node for node in ast.walk(self._TREE)
            if isinstance(node, ast.FunctionDef) and node.name == "_render_recent_runs"))
        self.assertIn("missing", strip)
        self.assertIn("unreadable", strip)
        self.assertIn("还没有运行台账", strip)

    def test_an_all_corrupt_ledger_is_not_shown_as_benign_emptiness(self) -> None:
        """整份台账全坏时 `runs` 也是空的 —— 那条分支必须一起说出计数。

        只说「还没有记录」会把一份**损坏的**历史讲成良性的空历史（codex P2）。
        """
        fn = next(
            node for node in ast.walk(self._TREE)
            if isinstance(node, ast.FunctionDef) and node.name == "_render_recent_runs")
        # 用 AST 精确取**那一个分支的分支体**。按文本位置切会把后面那条正常
        # caption 也圈进来，`note_text` 在那里出现，于是断言真空地绿着
        # （实测变异如此）。
        branches = [
            node for node in ast.walk(fn)
            if isinstance(node, ast.If) and "not history.runs" in ast.unparse(node.test)
        ]
        self.assertEqual(1, len(branches), "找不到空态分支 —— 本守卫已失效")
        body = "".join(ast.unparse(stmt) for stmt in branches[0].body)
        self.assertIn("note_text", body, "空分支没有带上计数")
        # 计数必须在**进入**空分支之前就拼好，否则那条分支拿不到它。
        whole = ast.unparse(fn)
        self.assertLess(whole.index("note_text ="), whole.index("if not history.runs"))

    def test_the_strip_discloses_malformed_and_foreign_counts(self) -> None:
        strip = ast.unparse(next(
            node for node in ast.walk(self._TREE)
            if isinstance(node, ast.FunctionDef) and node.name == "_render_recent_runs"))
        self.assertIn("malformed", strip)
        self.assertIn("foreign", strip)


if __name__ == "__main__":
    unittest.main()
