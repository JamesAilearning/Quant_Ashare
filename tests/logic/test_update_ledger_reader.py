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
from web.operator_ui.update_runner import log_window

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

    def test_a_bad_byte_inside_a_json_string_is_malformed(self) -> None:
        """坏字节落在 JSON 字符串**里面**时，替换解码会把它洗成合法行。

        整份 `errors="replace"` 解码后，detail 里的坏字节变成 `�`，JSON 照样
        合法、验形照过、渲染成一次真实运行、malformed 计零——被悄悄改写过的
        数据成了「事实」（codex P2）。逐行严格解码：解码失败 = 坏行。
        """
        with tempfile.TemporaryDirectory() as t:
            provider = Path(t) / "prov"
            provider.mkdir()
            path = ledger_path_for_provider(provider)
            good = json.dumps(_record(provider, exit_code=0)).encode("utf-8")
            broken = json.dumps(_record(provider, exit_code=11)).encode("utf-8")
            # 在 detail 字符串里塞一个非法 UTF-8 字节 —— JSON 结构不受影响。
            broken = broken.replace(b"fetch failed", b"fetch \xc3(failed")
            path.write_bytes(good + b"\n" + broken + b"\n")
            history = read_ledger(path, provider_dir=provider)
        self.assertEqual(1, len(history.runs), "好行也被带走了")
        self.assertEqual(
            1, history.malformed,
            "字符串里带坏字节的行被替换字符洗白成了一次真实运行")

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

    def test_a_success_carrying_a_failed_stage_is_not_a_run(self) -> None:
        """`exit_code: 0` 配 `failed_stage: "fetch"` 自相矛盾。

        只查字段类型的话这行原样通过，而 `LedgerRun.ok` 会把它渲染成一次
        **成功**的运行——把损坏的数据讲成事实（codex 第二轮 P2）。状态工件
        reader 早已钉住同一条不变式，这里照抄，不另立一套。
        """
        with tempfile.TemporaryDirectory() as t:
            provider = Path(t) / "prov"
            provider.mkdir()
            path = ledger_path_for_provider(provider)
            self.assertEqual(
                0, self._one(provider, path, exit_code=0, failed_stage="fetch"))

    def test_a_failure_without_a_failed_stage_is_not_a_run(self) -> None:
        # 另一半：非零退出码却没有失败阶段。写入侧两边都不会产。
        with tempfile.TemporaryDirectory() as t:
            provider = Path(t) / "prov"
            provider.mkdir()
            path = ledger_path_for_provider(provider)
            self.assertEqual(
                0, self._one(provider, path, exit_code=11, failed_stage=None))

    def test_an_absent_failed_stage_is_not_read_as_success(self) -> None:
        """字段**缺席**与 `null` 不是一回事——前者说明这行不是写入侧产的。

        `record.get(...)` 会把缺席读成 None，于是一条缺字段的失败记录会被
        当成成功。状态工件 reader 同样把缺席单列（它报 `absent`）。
        """
        with tempfile.TemporaryDirectory() as t:
            provider = Path(t) / "prov"
            provider.mkdir()
            path = ledger_path_for_provider(provider)
            record = {**_record(provider, exit_code=0)}
            del record["failed_stage"]
            _write(path, [record])
            self.assertEqual(
                0, len(read_ledger(path, provider_dir=provider).runs))

    def test_an_empty_failed_stage_is_not_a_stage_name(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            provider = Path(t) / "prov"
            provider.mkdir()
            path = ledger_path_for_provider(provider)
            self.assertEqual(0, self._one(provider, path, failed_stage=""))

    def test_both_well_formed_outcomes_are_still_read(self) -> None:
        # 反面：合法的成功与合法的失败都必须**读得进来**，否则上面五条只是
        # 「什么都不认」的副产品。
        with tempfile.TemporaryDirectory() as t:
            provider = Path(t) / "prov"
            provider.mkdir()
            path = ledger_path_for_provider(provider)
            _write(path, [_record(provider, exit_code=0),
                          _record(provider, exit_code=11)])
            history = read_ledger(path, provider_dir=provider)
        self.assertEqual(2, len(history.runs))
        self.assertEqual(0, history.malformed)

    def test_a_shapeless_row_is_corruption_not_a_foreign_run(self) -> None:
        """`{}` 或 `{"provider_dir": 5}` 不是「别人的行」，是坏行。

        先分类后验形的话，它们被计进 foreign——页面就会告诉操作人「这行属于
        另一个 provider」，而不是披露台账损坏（codex P2）。「foreign」这个
        称谓只配给一条**完整合法**、只是身份不同的 v1 记录。
        """
        with tempfile.TemporaryDirectory() as t:
            provider = Path(t) / "prov"
            provider.mkdir()
            path = ledger_path_for_provider(provider)
            for row in ({}, {"provider_dir": 5}):
                with self.subTest(row=row):
                    _write(path, [row])
                    history = read_ledger(path, provider_dir=provider)
                    self.assertEqual(1, history.malformed, "坏行没计成坏行")
                    self.assertEqual(
                        0, history.foreign, "坏行被说成了别人的运行")

    def test_a_foreign_row_must_itself_be_a_valid_record(self) -> None:
        # 另一个 provider 的行也要先过 v1 验形：验不过就是坏行，不是外来行。
        with tempfile.TemporaryDirectory() as t:
            provider, other = Path(t) / "prov", Path(t) / "other"
            provider.mkdir()
            other.mkdir()
            path = ledger_path_for_provider(provider)
            _write(path, [{**_record(other, exit_code=11), "exit_code": True}])
            history = read_ledger(path, provider_dir=provider)
        self.assertEqual((1, 0), (history.malformed, history.foreign))

    def test_a_non_normalized_provider_identity_is_corrupt_not_foreign(
            self) -> None:
        """写入侧的 `_norm` 只产归一化绝对路径。

        `provider_dir: "../bundle"` 过了非空检查后被 `_describes` 判成
        「别人的」——它不是别人的，是坏的：告诉操作人「这行属于另一个
        provider」掩盖了台账损坏（codex P2）。
        """
        with tempfile.TemporaryDirectory() as t:
            provider = Path(t) / "prov"
            provider.mkdir()
            path = ledger_path_for_provider(provider)
            for stamped in ("../bundle", "bundle", "prov/../prov"):
                with self.subTest(provider_dir=stamped):
                    _write(path, [{**_record(provider, exit_code=0),
                                   "provider_dir": stamped}])
                    history = read_ledger(path, provider_dir=provider)
                    self.assertEqual(
                        (1, 0), (history.malformed, history.foreign),
                        "非归一化身份被说成了别人的运行")

    def test_an_empty_identity_or_time_field_is_not_a_run(self) -> None:
        """身份/时间字段要**非空**——空串通过 `isinstance(str)`。

        一条 `exit_code: 0` 配空时间戳的行会被渲染成「日期不明的成功」，而
        写入侧从不产这种行（codex P2）。
        """
        with tempfile.TemporaryDirectory() as t:
            provider = Path(t) / "prov"
            provider.mkdir()
            path = ledger_path_for_provider(provider)
            for field in ("run_date", "started_at", "finished_at"):
                with self.subTest(field=field):
                    self.assertEqual(
                        0, self._one(provider, path, exit_code=0,
                                     failed_stage=None, **{field: ""}))

    def test_gibberish_dates_and_timestamps_are_not_a_run(self) -> None:
        """非空还不够——`run_date: "foobar"` 配胡话时间戳曾照样通过。

        写入侧固定产 ISO 日期 + 带时区的 ISO 时间戳、结束不早于开始；验不过
        的行是坏行，不硬渲染成「真实」运行（codex P2）。
        """
        with tempfile.TemporaryDirectory() as t:
            provider = Path(t) / "prov"
            provider.mkdir()
            path = ledger_path_for_provider(provider)
            cases = [
                {"run_date": "foobar"},
                # fromisoformat 还接受这些写入侧永不产的形态——工作台按
                # YYYY-MM-DD 切 [5:] 会渲染成 `825` 这种鬼标签（codex P2）。
                {"run_date": "20260825"},
                {"run_date": "2026-W35-2"},
                {"started_at": "not-a-time"},
                {"finished_at": "still-not-a-time"},
                # 无时区（naive）的时间戳不是写入侧的产出。
                {"started_at": "2026-08-21T20:30:00"},
                # 结束早于开始。
                {"started_at": "2026-08-21T22:00:00+08:00",
                 "finished_at": "2026-08-21T20:00:00+08:00"},
            ]
            for override in cases:
                with self.subTest(override=override):
                    self.assertEqual(0, self._one(provider, path, **override))
            # 反面：写入侧的正常产出必须照常读进来。
            _write(path, [_record(provider, exit_code=0)])
            self.assertEqual(
                1, len(read_ledger(path, provider_dir=provider).runs))

    def test_a_boolean_or_float_schema_version_is_not_v1(self) -> None:
        """JSON 的 `true` 与 `1.0` 在 Python 里都 `== 1`。

        只比值不钉类型,一条版本字段本身就坏掉的行会被拿 v1 语义硬解释
        (codex P2)。与 exit_code 的 bool 排除同一课:先钉类型再比值。
        """
        with tempfile.TemporaryDirectory() as t:
            provider = Path(t) / "prov"
            provider.mkdir()
            path = ledger_path_for_provider(provider)
            for version in (True, 1.0):
                with self.subTest(version=version):
                    self.assertEqual(
                        0, self._one(provider, path, schema_version=version))

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
        streak = consecutive_failures(history)
        self.assertEqual((3, True, False),
                         (streak.count, streak.exact, streak.blocked),
                         "撞到成功而止的连败是精确值")

    def test_a_success_ends_the_streak(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            provider = Path(t) / "prov"
            provider.mkdir()
            path = ledger_path_for_provider(provider)
            _write(path, [_record(provider, exit_code=11),
                          _record(provider, exit_code=0)])
            streak = consecutive_failures(
                read_ledger(path, provider_dir=provider))
        self.assertEqual((0, True), (streak.count, streak.exact))

    def test_a_malformed_row_is_a_barrier_not_glue(self) -> None:
        """坏行可能是一次成功——丢掉再数会把断开的两段焊成一段。

        成功 → 失败 → **坏行** → 失败×2：可断言的连败只有坏行之后（更新侧）
        的 2 次，且因为撞上屏障只是**至少** 2（codex P2）。
        """
        with tempfile.TemporaryDirectory() as t:
            provider = Path(t) / "prov"
            provider.mkdir()
            path = ledger_path_for_provider(provider)
            good = [json.dumps(_record(provider, exit_code=0, day="2026-08-14")),
                    json.dumps(_record(provider, exit_code=11, day="2026-08-17"))]
            bad = "{torn"
            newer = [json.dumps(_record(provider, exit_code=11, day="2026-08-20")),
                     json.dumps(_record(provider, exit_code=11, day="2026-08-21"))]
            path.write_text(
                chr(10).join(good + [bad] + newer) + chr(10),
                encoding="utf-8")
            streak = consecutive_failures(
                read_ledger(path, provider_dir=provider))
        self.assertEqual((2, False, False),
                         (streak.count, streak.exact, streak.blocked),
                         "坏行两侧的失败被焊成了一段，或下界被说成精确值")

    def test_a_malformed_newest_row_blocks_the_streak(self) -> None:
        # 最新一行读不了——它可能是一次成功，连败数整体不可断。
        with tempfile.TemporaryDirectory() as t:
            provider = Path(t) / "prov"
            provider.mkdir()
            path = ledger_path_for_provider(provider)
            _write(path, [_record(provider, exit_code=11)],
                   trailing=b'{"torn"')
            streak = consecutive_failures(
                read_ledger(path, provider_dir=provider))
        self.assertTrue(streak.blocked, "最新侧的坏行没有挡住连败断言")
        self.assertEqual(0, streak.count)

    def test_a_capped_streak_is_a_lower_bound_not_an_exact_count(self) -> None:
        """8 连败与 7 连败在截到 7 条的视图里长得一样。

        把截断后的 7 报成「正好 7」低估了这份台账要暴露的模式（codex P2）。
        数到截断即**至少**；整份台账都数完了才是精确值。
        """
        with tempfile.TemporaryDirectory() as t:
            provider = Path(t) / "prov"
            provider.mkdir()
            path = ledger_path_for_provider(provider)
            _write(path, [
                _record(provider, exit_code=11, day=f"2026-08-{10 + i:02d}")
                for i in range(9)
            ])
            history = read_ledger(path, provider_dir=provider)
            streak = consecutive_failures(history)
            self.assertEqual(7, len(history.runs), "前提：视图截到 7 条")
            self.assertEqual((7, False), (streak.count, streak.exact),
                             "截断处的连败被说成了精确值")
            # 反面：整份台账全数完（无截断、无屏障）→ 精确。
            whole = consecutive_failures(
                read_ledger(path, provider_dir=provider, recent=20))
        self.assertEqual((9, True), (whole.count, whole.exact))

    def test_the_workbench_speaks_the_streaks_honesty(self) -> None:
        # 页面必须消费三态：blocked / 非精确（至少）/ 精确——少接一个，那种
        # 情形就退回精确口气。
        source = _WORKBENCH.read_text(encoding="utf-8")
        self.assertIn("streak.blocked", source, "页面没有处理 blocked")
        # 锚在**承载机制的表达式**上，不是「至少」这个词——那个词在 count==1
        # 分支里也出现，按词断言会被别处满足、真空地绿（本会话同类第 4 次，
        # 变异 BC 抓的正是它）。
        self.assertIn('"" if streak.exact else "至少 "', source,
                      "连败 ≥2 的分支没有按 exact 区分「正好/至少」")

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
            got = last_fetch_progress_for_run(
                text, provider_dir=provider, window_complete=True)
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
            got = last_fetch_progress_for_run(
                text, provider_dir=provider, window_complete=True)
        self.assertIsNone(got.progress, "边界之前那条被当成了本次的")
        self.assertTrue(got.attributed)

    def test_no_boundary_keeps_the_old_honest_answer(self) -> None:
        """窗口里没有边界时**退回边界落地之前的行为**，不是退步，也不猜。"""
        with tempfile.TemporaryDirectory() as t:
            provider = Path(t) / "prov"
            provider.mkdir()
            got = last_fetch_progress_for_run(
                f"21:00:00{_PROGRESS_LINE}", provider_dir=provider, window_complete=True)
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
            got = last_fetch_progress_for_run(
                text, provider_dir=provider, window_complete=True)
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
            got = last_fetch_progress_for_run(
                text, provider_dir=provider, window_complete=True)
        self.assertFalse(
            got.attributed, "把别人的进度当成了我们的，且说成「已确定」")

    def test_an_earlier_foreign_boundary_also_defeats_attribution(self) -> None:
        """**反向交错**：别人先起跑、我们后起跑，别人仍在写。

        B 起跑（边界 B），A 随后起跑（边界 A，成了最后一条），而 B **仍在跑**
        ——B 的进度行不会再带一条边界，于是它们落在边界 A 之后。「最后一条边界
        是我们的就算数」在这里会把 B 的进度说成 A 的（codex 第二轮 P1）。

        判据因此是**独占**：窗口里的边界全是我们的，才谈得上归属。
        """
        with tempfile.TemporaryDirectory() as t:
            provider, other = Path(t) / "prov", Path(t) / "other"
            provider.mkdir()
            other.mkdir()
            text = chr(10).join([
                self._boundary(other, "2026-08-24T20:00:00+08:00"),
                self._boundary(provider, "2026-08-24T20:30:00+08:00"),
                # 别人那次运行**没有结束**，它的进度行继续落在我们的边界之后。
                f"20:31:00{_PROGRESS_LINE}",
            ])
            got = last_fetch_progress_for_run(
                text, provider_dir=provider, window_complete=True)
        self.assertFalse(
            got.attributed,
            "别人先起跑、仍在写时，把它的进度说成了我们的「已确定」")
        self.assertIsNotNone(got.progress, "说不知道归属，不等于连进度也丢掉")

    def test_only_our_own_boundaries_still_attribute(self) -> None:
        """反面：窗口里全是我们自己的边界 —— 仍然确定。

        否则「独占」会退化成「永远说不知道」，那不是更诚实，是没用。同一个
        provider 不会与自己并发（单飞锁 per-provider），所以这一段是安全的。
        """
        with tempfile.TemporaryDirectory() as t:
            provider = Path(t) / "prov"
            provider.mkdir()
            text = chr(10).join([
                self._boundary(provider, "2026-08-23T20:00:00+08:00"),
                f"20:01:00{_PROGRESS_LINE}",
                self._boundary(provider, "2026-08-24T20:30:00+08:00"),
                f"20:31:00{_PROGRESS_LINE}",
            ])
            got = last_fetch_progress_for_run(
                text, provider_dir=provider, window_complete=True)
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
            got = last_fetch_progress_for_run(
                text, provider_dir=provider, window_complete=True)
        self.assertEqual("2026-08-24T20:30:01+08:00", got.boundary_stamp)
        assert got.progress is not None
        self.assertEqual(5000, got.progress.done)

    def test_a_truncated_window_never_claims_exclusivity(self) -> None:
        """窗口没盖住整份日志时,「看不到别人的边界」证明不了别人不存在。

        兄弟 provider B 起得足够早,它的边界已滚出尾部窗口,而 B 仍在写——
        窗口里只剩我们的边界,独占检查通过,B 的进度被说成「确定是我们的」
        (codex 第三轮 P1,同一根因的第三种形态)。所以独占判据只在
        ``window_complete=True`` 时启用;截断窗口一律如实说不知道。
        """
        with tempfile.TemporaryDirectory() as t:
            provider = Path(t) / "prov"
            provider.mkdir()
            text = chr(10).join([
                self._boundary(provider, "2026-08-24T20:30:00+08:00"),
                f"20:31:00{_PROGRESS_LINE}",
            ])
            got = last_fetch_progress_for_run(
                text, provider_dir=provider, window_complete=False)
        self.assertFalse(
            got.attributed, "截断窗口里声称了独占 —— 窗外的兄弟边界不可见")
        self.assertIsNotNone(got.progress, "说不知道归属，不等于连进度也丢掉")

    def test_the_reason_for_unknown_attribution_is_the_true_one(self) -> None:
        """三种「不知道」对操作人的下一步不同，页面必须说真原因。

        一律说「窗口里没有边界」，在最常见的截断窗口上就是撒谎——边界明明
        可见，只是窗外可能还有别人的（codex P2）。
        """
        with tempfile.TemporaryDirectory() as t:
            provider, other = Path(t) / "prov", Path(t) / "other"
            provider.mkdir()
            other.mkdir()
            ours = self._boundary(provider, "2026-08-24T20:30:00+08:00")
            theirs = self._boundary(other, "2026-08-24T20:00:00+08:00")
            line = f"20:31:00{_PROGRESS_LINE}"
            cases = [
                ("window_truncated",
                 chr(10).join([ours, line]), False),
                ("foreign_boundary",
                 chr(10).join([theirs, ours, line]), True),
                ("no_boundary", line, True),
            ]
            for expected, text, complete in cases:
                with self.subTest(reason=expected):
                    got = last_fetch_progress_for_run(
                        text, provider_dir=provider, window_complete=complete)
                    self.assertFalse(got.attributed)
                    self.assertEqual(expected, got.unattributed_reason)
            attributed = last_fetch_progress_for_run(
                chr(10).join([ours, line]), provider_dir=provider,
                window_complete=True)
        self.assertTrue(attributed.attributed)
        self.assertEqual("", attributed.unattributed_reason,
                         "归属确定时不该带失败原因")

    def test_certainty_requires_the_boundary_to_match_the_status_record(
            self) -> None:
        """「确定归属」的口气只许在边界与显示的状态记录**同一次运行**时用。

        状态写入是 best-effort（写失败只记 ERROR），日志与状态工件可以各自
        往前走：旧运行留下 running 状态、新运行只落了边界——把新进度说成
        显示的那次，又是把交错讲成确定（codex P2）。写入侧在同一次运行里用
        同一个 `started_at.isoformat()` 写两者，所以精确相等即同一次。
        """
        source = (_ROOT / "web" / "operator_ui" / "pages" / "run_center.py"
                  ).read_text(encoding="utf-8")
        self.assertIn(
            '_attributed.boundary_stamp == (_status.started_at or "")', source,
            "确定口气的分支没有以「边界==状态记录」为闸")
        self.assertIn("对不上", source,
                      "没有为边界与状态不一致的情形准备如实措辞")

    def test_the_writer_stamps_status_and_boundary_identically(self) -> None:
        # 上一条相关性的**前提**：同一次运行里状态记录与边界写同一个戳。
        # 用写侧源码钉住——两处都必须是同一个 started_at 的 isoformat()。
        source = Path(writer.__file__).read_text(encoding="utf-8")
        self.assertIn('"started_at": started_at.isoformat()', source)
        self.assertIn("run_boundary_line(started_at", source)

    def test_a_corrupt_boundary_stamp_defeats_attribution(self) -> None:
        r"""`\S+` 会把乱码当「起跑时刻」——随后以确定口气宣布归属。

        戳验不过的边界 = 日志损坏，归属整体不可断（codex P2）；与台账坏行
        同一处置。缺时区的戳同拒（写入侧永远带时区）。
        """
        with tempfile.TemporaryDirectory() as t:
            provider = Path(t) / "prov"
            provider.mkdir()
            line = f"20:31:00{_PROGRESS_LINE}"
            for stamp in ("ץȡ�", "2026-08-24T20:30:00"):
                with self.subTest(stamp=stamp):
                    text = chr(10).join([
                        f"20:30:01 [x] INFO — {RUN_BOUNDARY_MARK} {stamp} "
                        f"provider={writer._norm(provider)}",
                        line,
                    ])
                    got = last_fetch_progress_for_run(
                        text, provider_dir=provider, window_complete=True)
                    self.assertFalse(got.attributed, "坏戳的边界被当真了")
                    self.assertEqual("corrupt_boundary",
                                     got.unattributed_reason)

    def test_the_page_speaks_all_three_reasons(self) -> None:
        # 页面的未归属分支必须按原因措辞——只要有一个键没接上，那种情形就
        # 退回笼统话术，操作人拿到的又是错误解释。
        source = (_ROOT / "web" / "operator_ui" / "pages" / "run_center.py"
                  ).read_text(encoding="utf-8")
        self.assertIn("unattributed_reason", source,
                      "页面没有消费 unattributed_reason")
        for key in ("window_truncated", "foreign_boundary", "no_boundary",
                    "corrupt_boundary"):
            with self.subTest(reason=key):
                self.assertIn(key, source, f"页面没有为 {key} 给出对应措辞")

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
                last_fetch_progress_for_run(
                text, provider_dir=provider, window_complete=True).attributed)


class TheWindowReaderTellsWhetherItSawEverything(unittest.TestCase):
    """`log_window` 的第二个返回值是归属判断的前提，必须真。

    谎报「完整」，下游的独占判据就建立在半份日志上——那正是被 codex 命中的
    形状。谎报「截断」，归属永远不知道，特性空转。两个方向都要钉。
    """

    def test_a_small_log_is_read_completely(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            path = Path(t) / "x.log"
            path.write_text("line-a\nline-b\n", encoding="utf-8")
            text, complete = log_window(path)
        self.assertTrue(complete)
        self.assertIn("line-a", text)

    def test_a_log_bigger_than_the_window_is_reported_truncated(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            path = Path(t) / "x.log"
            path.write_text(
                "\n".join(f"row {i}" for i in range(4000)), encoding="utf-8")
            text, complete = log_window(path, chars=200)
        self.assertFalse(complete, "窗口没盖住整份日志却声称完整")
        self.assertLessEqual(len(text), 200)

    def test_a_missing_log_is_complete_emptiness(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            text, complete = log_window(Path(t) / "absent.log")
        self.assertEqual("", text)
        self.assertTrue(complete, "空日志是完整读完了的——没有窗外可言")

    def test_the_page_passes_the_window_verdict_through(self) -> None:
        # 调用点必须把 log_window 的判定原样交给归属函数——自填 True 就把
        # 整条前提废了。AST 取 `_read_progress` 的函数体来钉。
        source = (_ROOT / "web" / "operator_ui" / "pages" / "run_center.py"
                  ).read_text(encoding="utf-8")
        fn = next(
            node for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.FunctionDef) and node.name == "_read_progress")
        body = ast.unparse(fn)
        self.assertIn("log_window(", body, "调用点没用 log_window")
        self.assertIn("window_complete=complete", body,
                      "窗口完整性判定没有原样传下去")
        self.assertNotIn("window_complete=True", body,
                         "调用点自填了 True —— 截断被当成了完整")


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
