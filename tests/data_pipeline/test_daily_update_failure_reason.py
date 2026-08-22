"""阶段失败必须带着**它自己报出的原因**穿过 ``Runner`` 那道缝。

（openspec 2026-08-22-stage-failure-reason）

起因：2026-08-17 / 08-20 / 08-21 连续三晚夜间更新失败，退出码全是 11，状态工件
里三晚都只写着 ``fetch failed hard (exit 1)``。而 01 那时已经把话说全了——

    refusing narrower-scope merge for endpoint 'daily': ... A narrower range does
    not re-attempt every prior hole ... Re-run the full range to extend it, or
    pass --reset-manifest

——包括**怎么修**。这句话停在日志里，因为 ``Runner = Callable[[list[str]], int]``
只让一个 int 穿过去。三晚的排障因此全走在错的方向上（运维文档那时把 11 写作
「查 token / 网络」，而 token 与网络自始至终正常）。
"""

import ast
import json
import logging
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import src.data_pipeline.daily_update as du  # noqa: E402
from src.core.logger import get_logger  # noqa: E402
from src.data_pipeline.daily_update import (  # noqa: E402
    EXIT_FETCH_HARD,
    EXIT_FETCH_HOLES,
    EXIT_OK,
    EXIT_REBUILD,
    EXIT_SNAPSHOT_STALE,
    EXIT_VALIDATE,
    DailyUpdateConfig,
)

TODAY = date(2026, 6, 10)
STAGES = ("fetch", "registry", "bins", "membership", "universe", "benchmark", "validate")
_SOURCE = Path(du.__file__).read_text(encoding="utf-8")


def _mk_bundle(path: Path, marker: str) -> None:
    (path / "calendars").mkdir(parents=True)
    (path / "calendars" / "day.txt").write_text(marker, encoding="utf-8")
    (path / "instruments").mkdir()
    (path / "instruments" / "all.txt").write_text("", encoding="utf-8")
    (path / "features").mkdir()


def _write_snapshot(tushare_dir: Path, snapshot_date: str = "20260610") -> None:
    tushare_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({
        "ts_code": ["000001.SZ"], "name": ["平安银行"],
        "snapshot_date": [snapshot_date],
    }).to_parquet(tushare_dir / "active_stocks.parquet")


def _config(tmp: Path, **kw: object) -> DailyUpdateConfig:
    kw.setdefault("now", TODAY)
    return DailyUpdateConfig(
        tushare_dir=tmp / "raw",
        provider_dir=tmp / "provider",
        delisted_registry=tmp / "raw" / "delisted_registry.parquet",
        reference_cases=tmp / "reference_cases.yaml",
        **kw,  # type: ignore[arg-type]
    )


def _runners(failing: str = "", code: int = 1, say: object = None,
             staging: Path | None = None) -> dict[str, object]:
    """七个假 runner。``failing`` 那个记一条 ERROR 再返回 ``code``。

    用的是各阶段脚本**真实的** logger 名（``src.scripts.data_pipeline.*``），
    因为捕获点挂在 ``src`` 上，而 ``src.core.logger`` 恰恰在那里设了
    ``propagate = False``——用别的名字测就测不出真实布线。
    """
    def make(stage: str):
        def run(argv: list[str]) -> int:
            if stage == "bins" and staging is not None and not staging.exists():
                _mk_bundle(staging, "NEW")
            if stage != failing:
                return 0
            logger = get_logger(f"src.scripts.data_pipeline.{stage}")
            if isinstance(say, tuple):
                logger.error(*say)
            elif say is not None:
                logger.error("%s", say)
            return code
        return run
    return {s: make(s) for s in STAGES}


def _run(tmp: Path, **kw: object) -> tuple[int, str | None, str]:
    cfg = _config(tmp)
    _write_snapshot(cfg.tushare_dir)
    _mk_bundle(cfg.provider_dir, "LIVE")
    kw.setdefault("staging", du.new_dir(cfg.provider_dir))
    return du._execute_daily_update(cfg, _runners(**kw))  # type: ignore[arg-type]


# ---------------------------------------------------------------- 完备性（结构）

class EveryStageInvocationSitsInsideACaptureWindow(unittest.TestCase):
    """完备性判据是**结构**的，不是一张手写的阶段表。

    手写表拦不住「将来新增第八个阶段却忘了套窗口」——新阶段不在表里，参数化
    用例照样全绿。所以这里直接问源码：编排器里每一处阶段调用，是否都在
    ``with _capture_stage_errors()`` 之内。
    """

    # 整棵树只 parse 一次：`id()` 比对的是**对象身份**，两次 parse 出的是两套
    # 互不相干的节点，谁都不在谁的集合里——守卫会永远红，且红得毫无信息。
    _TREE = ast.parse(_SOURCE)

    @classmethod
    def _body(cls) -> ast.FunctionDef:
        for node in ast.walk(cls._TREE):
            if isinstance(node, ast.FunctionDef) and node.name == "_execute_daily_update":
                return node
        raise AssertionError("找不到 _execute_daily_update")

    @classmethod
    def _guarded_node_ids(cls) -> set[int]:
        guarded: set[int] = set()
        for node in ast.walk(cls._body()):
            if not isinstance(node, ast.With):
                continue
            uses_capture = any(
                isinstance(item.context_expr, ast.Call)
                and isinstance(item.context_expr.func, ast.Name)
                and item.context_expr.func.id == "_capture_stage_errors"
                for item in node.items
            )
            if uses_capture:
                guarded.update(id(inner) for inner in ast.walk(node))
        return guarded

    def test_every_runner_call_is_wrapped(self) -> None:
        guarded = self._guarded_node_ids()
        calls = [
            node for node in ast.walk(self._body())
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Subscript)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "active"
        ]
        self.assertGreaterEqual(len(calls), 3, "没找到阶段调用——本守卫已失效")
        unwrapped = [ast.unparse(c) for c in calls if id(c) not in guarded]
        self.assertEqual([], unwrapped, "有阶段调用不在捕获窗口内，它的失败原因会丢")

    def test_the_snapshot_stage_is_wrapped_too(self) -> None:
        # 快照阶段不是 `Runner`，是编排器内的函数——但它的原因同样只记在日志里。
        guarded = self._guarded_node_ids()
        calls = [
            node for node in ast.walk(self._body())
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_verify_snapshot_refreshed"
        ]
        self.assertEqual(1, len(calls))
        self.assertIn(id(calls[0]), guarded)

    def test_every_stage_failure_return_composes_the_detail(self) -> None:
        """每个阶段失败的 return 都必须经由 ``_stage_detail``。

        只套窗口而返回时不用它，捕获到的原因照样进不了工件。
        """
        # startup_repair / swap 刻意不套：原因来自被捕获的异常对象本身，已经在
        # detail 里，再套一层只会把同一句话写两遍。判别用**结构**——该 return 是否
        # 位于 `except` 处理块内——而不是在文本里找 "exc"：后者会被将来任何一句
        # 含 "exc" 的措辞（"stage exceeded ..."）静默跳过，守卫从此形同虚设。
        in_handler = {
            id(inner)
            for node in ast.walk(self._body()) if isinstance(node, ast.ExceptHandler)
            for inner in ast.walk(node)
        }
        offenders = []
        checked = 0
        for node in ast.walk(self._body()):
            if not isinstance(node, ast.Return) or not isinstance(node.value, ast.Tuple):
                continue
            parts = node.value.elts
            if len(parts) != 3:
                continue
            code = ast.unparse(parts[0])
            if code == "EXIT_OK" or "EXIT_" not in code:
                continue
            if id(node) in in_handler:
                continue
            checked += 1
            if "_stage_detail" not in ast.unparse(parts[2]):
                offenders.append(f"{code} -> {ast.unparse(parts[2])[:60]}")
        self.assertGreaterEqual(checked, 4, "没找到阶段失败返回——本守卫已失效")
        self.assertEqual([], offenders, "失败返回没走 _stage_detail，原因会丢")


# ---------------------------------------------------------------- 行为

class AStagesOwnErrorReachesTheStatusArtifact(unittest.TestCase):

    REAL = ("refusing narrower-scope merge for endpoint %r: this run covered "
            "[20180101, 20260821] but the manifest already covers "
            "[20151001, 20260821] with unresolved holes. Re-run the full range "
            "to extend it, or pass --reset-manifest", "daily")

    def test_the_fetch_reason_that_cost_three_nights(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            rc, stage, detail = _run(Path(t), failing="fetch", say=self.REAL)
        self.assertEqual((EXIT_FETCH_HARD, "fetch"), (rc, stage))
        self.assertIn("refusing narrower-scope merge", detail)
        self.assertIn("--reset-manifest", detail, "修法被截断了——留下抱怨切掉办法")

    def test_every_stage_carries_its_own_reason(self) -> None:
        expected_exit = {
            "fetch": EXIT_FETCH_HARD, "validate": EXIT_VALIDATE,
        }
        for stage in STAGES:
            with self.subTest(阶段=stage), tempfile.TemporaryDirectory() as t:
                rc, failed, detail = _run(
                    Path(t), failing=stage, code=2, say=f"{stage} 自己报的原因")
                self.assertEqual(stage, failed)
                self.assertEqual(expected_exit.get(stage, EXIT_REBUILD), rc)
                self.assertIn(f"{stage} 自己报的原因", detail)

    def test_the_snapshot_stage_reason_is_carried(self) -> None:
        # 快照阶段的原因由 `_verify_snapshot_refreshed` 记在自己函数体内。
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            cfg = _config(tmp)
            _write_snapshot(cfg.tushare_dir, snapshot_date="20260601")   # 陈旧
            _mk_bundle(cfg.provider_dir, "LIVE")
            rc, stage, detail = du._execute_daily_update(cfg, _runners())  # type: ignore[arg-type]
        self.assertEqual((EXIT_SNAPSHOT_STALE, "snapshot"), (rc, stage))
        self.assertIn("2026-06-01", detail, "没说清是哪一天的戳，操作人还得去翻日志")

    def test_holes_exit_also_carries_the_reason(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            rc, stage, detail = _run(
                Path(t), failing="fetch", code=3, say="stock_basic 有洞：3 个交易日缺失")
        self.assertEqual((EXIT_FETCH_HOLES, "fetch"), (rc, stage))
        self.assertIn("stock_basic 有洞", detail, "哪个端点有洞是可行动信息")

    def test_it_survives_the_round_trip_through_the_status_artifact(self) -> None:
        # 端到端：原因必须落到操作人真正读的那个 JSON 里，不只是函数返回值。
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            cfg = _config(tmp)
            _write_snapshot(cfg.tushare_dir)
            _mk_bundle(cfg.provider_dir, "LIVE")
            du.run_daily_update(cfg, _runners(failing="fetch", say=self.REAL))  # type: ignore[arg-type]
            record = json.loads(
                du.default_status_path(cfg.provider_dir).read_text(encoding="utf-8"))
        self.assertEqual(EXIT_FETCH_HARD, record["exit_code"])
        self.assertIn("--reset-manifest", record["detail"])


class TheWindowSeparatesTheStageFromTheOrchestratorsNarration(unittest.TestCase):
    """分辨「阶段自己报的」与「编排器对它的转述」靠的是**作用域**，不是 logger 名。"""

    def test_the_orchestrators_own_error_line_is_not_the_reason(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            _, _, detail = _run(Path(t), failing="fetch", say="真正的原因")
        # `_logger.error("Fetch FAILED (exit %d); aborting the update.", rc)` 在
        # 阶段调用**返回之后**才发出，落在窗口外。
        self.assertNotIn("aborting the update", detail)
        self.assertIn("真正的原因", detail)

    def test_a_helper_module_error_still_counts_as_the_stages_reason(self) -> None:
        # 不按 logger 名过滤：阶段栽在它调用的 helper 里时，那条 ERROR 同样是原因。
        def run(argv: list[str]) -> int:
            get_logger("src.data_pipeline.tushare_client").error("token 已过期")
            return 1
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            cfg = _config(tmp)
            _write_snapshot(cfg.tushare_dir)
            _mk_bundle(cfg.provider_dir, "LIVE")
            runners = {**_runners(), "fetch": run}
            _, _, detail = du._execute_daily_update(cfg, runners)  # type: ignore[arg-type]
        self.assertIn("token 已过期", detail)

    def test_lower_levels_are_not_swept_in(self) -> None:
        def run(argv: list[str]) -> int:
            logger = get_logger("src.scripts.data_pipeline.fetch_tushare")
            logger.info("拉取 000001.SZ")
            logger.warning("重试第 2 次")
            logger.error("最终失败：额度耗尽")
            return 1
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            cfg = _config(tmp)
            _write_snapshot(cfg.tushare_dir)
            _mk_bundle(cfg.provider_dir, "LIVE")
            _, _, detail = du._execute_daily_update(
                cfg, {**_runners(), "fetch": run})  # type: ignore[arg-type]
        self.assertIn("额度耗尽", detail)
        self.assertNotIn("拉取 000001", detail)
        self.assertNotIn("重试第 2 次", detail)


class CaptureNeverChangesTheRun(unittest.TestCase):
    """可观测性绝不允许改变运行结果——少一行详情可以，改退出码不可以。"""

    def test_a_malformed_log_call_does_not_crash_the_run(self) -> None:
        # `record.getMessage()` 会对畸形 args 抛 TypeError，而
        # `logging.Handler.handle` 并不包住 `emit`：不吞掉的话，异常会从阶段
        # 自己那句 `logger.error(...)` 冒出去，把可诊断的失败变成崩溃。
        def run(argv: list[str]) -> int:
            get_logger("src.scripts.data_pipeline.fetch_tushare").error("%d", "不是整数")
            return 1
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            cfg = _config(tmp)
            _write_snapshot(cfg.tushare_dir)
            _mk_bundle(cfg.provider_dir, "LIVE")
            rc, stage, detail = du._execute_daily_update(
                cfg, {**_runners(), "fetch": run})  # type: ignore[arg-type]
        self.assertEqual((EXIT_FETCH_HARD, "fetch"), (rc, stage))
        self.assertIn("fetch failed hard", detail, "详情丢了也得保住兜底那句")

    def test_the_handler_is_removed_even_when_a_stage_raises(self) -> None:
        # 摘除写在 `finally` 而非 `except`：漏摘的话 handler 会在进程存活期间
        # 一直挂着，把后续每个阶段的错误都收进一个没人读的死列表。
        def boom(argv: list[str]) -> int:
            raise RuntimeError("阶段自己炸了")
        before = len(logging.getLogger("src").handlers)
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            cfg = _config(tmp)
            _write_snapshot(cfg.tushare_dir)
            _mk_bundle(cfg.provider_dir, "LIVE")
            with self.assertRaises(RuntimeError):
                du._execute_daily_update(cfg, {**_runners(), "fetch": boom})  # type: ignore[arg-type]
        self.assertEqual(before, len(logging.getLogger("src").handlers))
        self.assertFalse(
            [h for h in logging.getLogger("src").handlers
             if isinstance(h, du._StageErrorCollector)])

    def test_the_stages_own_log_output_still_reaches_the_log(self) -> None:
        # 捕获是**旁听**，不是拦截：日志文件里那句话必须照常写出去。
        seen: list[str] = []
        probe = logging.Handler()
        probe.emit = lambda record: seen.append(record.getMessage())  # type: ignore[method-assign]
        logging.getLogger("src").addHandler(probe)
        try:
            with du._capture_stage_errors():
                get_logger("src.scripts.data_pipeline.fetch_tushare").error("要写进日志")
        finally:
            logging.getLogger("src").removeHandler(probe)
        self.assertIn("要写进日志", seen)

    def test_the_capture_point_is_where_propagation_actually_stops(self) -> None:
        """挂错 logger，这套机制会**静默空转**。

        `src.core.logger.setup_logging` 在 `logging.getLogger("src")` 上设了
        `propagate = False`（为免重复输出），所以挂在真 root 上的 handler 一条
        记录都收不到。这条钉的是布线本身，不是数值。
        """
        self.assertEqual("src", du._STAGE_LOG_ROOT)
        self.assertFalse(
            logging.getLogger("src").propagate,
            "`src` 不再截断传播了——捕获点该重新审视，别让守卫过时地绿着")


class TheDetailStaysOneLineAndTruncationIsDeclared(unittest.TestCase):
    """`detail` 的契约是**一行**（它被渲染进表格单元格与卡片正文）。"""

    def test_embedded_newlines_are_folded_not_dropped(self) -> None:
        got = du._stage_detail("摘要", ["第一行\n第二行\r\n第三行"])
        self.assertNotIn("\n", got)
        self.assertNotIn("\r", got)
        for part in ("第一行", "第二行", "第三行"):
            self.assertIn(part, got, "折行时丢内容——traceback 最后一帧往往正是有用的那半")

    def test_nothing_captured_means_nothing_invented(self) -> None:
        self.assertEqual("摘要", du._stage_detail("摘要", []))
        self.assertEqual("摘要", du._stage_detail("摘要", ["", "   ", "\n"]))

    def test_multiple_error_lines_are_all_carried(self) -> None:
        got = du._stage_detail("摘要", ["为什么失败", "怎么修"])
        self.assertIn("为什么失败", got)
        self.assertIn("怎么修", got, "只留最后一条会丢掉「为什么」，只留第一条会丢掉「怎么办」")

    def test_truncation_is_declared_and_counts_what_was_dropped(self) -> None:
        got = du._stage_detail("摘要", ["x" * 500, "y" * 500, "z" * 500])
        self.assertIn("y" * 500, got, "还装得下的那条被无谓丢了")
        self.assertNotIn("z" * 500, got)
        self.assertIn("另有 1 条", got, "静默丢弃读起来像「就这些」")
        self.assertIn("见日志", got)

    def test_a_single_over_long_line_is_kept_not_dropped(self) -> None:
        # 第一条无论多长都要收下：否则一条超长消息会让详情整个消失，
        # 又回到「只有退出码」的原点。
        got = du._stage_detail("摘要", ["x" * 5000])
        self.assertIn("已截断", got)
        self.assertGreater(len(got), 1000)
        self.assertLess(len(got), 2000, "上限失效了")

    def test_the_cap_is_far_above_the_jobs_page_200(self) -> None:
        # 本改动要救的那句真实消息约 350 字符，而**后**半句才是修法；
        # 按 `job_io._extract_failure_detail` 的 200 截，会精准地留下抱怨、切掉办法。
        self.assertGreaterEqual(du._STAGE_DETAIL_MAX_CHARS, 600)

    def test_the_summary_survives_alongside_the_reason(self) -> None:
        got = du._stage_detail("fetch failed hard (exit 1)", ["原因"])
        self.assertIn("fetch failed hard (exit 1)", got)
        self.assertIn("原因", got)


class StagesWhoseReasonAlreadyArrivesAreLeftAlone(unittest.TestCase):
    """`startup_repair` 与 `swap` 的原因来自被捕获的异常对象本身。

    再套一层捕获只会把同一句话在 `detail` 里写两遍。这条是回归钉：将来若有人
    「为了统一」给它们也套上，这里会红并提醒先想清楚重复的问题。
    """

    def test_swap_detail_is_still_the_exception_message(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            cfg = _config(tmp)
            _write_snapshot(cfg.tushare_dir)
            _mk_bundle(cfg.provider_dir, "LIVE")
            _mk_bundle(du.new_dir(cfg.provider_dir), "NEW")

            def boom(provider_dir: Path) -> None:
                raise du.BundleSwapError("目标被占用")

            original = du.swap
            du.swap = boom  # type: ignore[assignment]
            try:
                rc, stage, detail = du._execute_daily_update(
                    cfg, _runners())  # type: ignore[arg-type]
            finally:
                du.swap = original  # type: ignore[assignment]
        self.assertEqual((du.EXIT_SWAP, "swap"), (rc, stage))
        self.assertEqual("swap failed: 目标被占用", detail)
        self.assertEqual(1, detail.count("目标被占用"), "同一句原因写了两遍")


if __name__ == "__main__":
    unittest.main()
