"""运行台账与运行边界：写侧。

（openspec 2026-08-24-daily-update-run-ledger）

起因：`<provider>.daily_update_status.json` 是**单文件**，每次运行盖掉上一次，
所以 UI 只看得到最后一次。2026-08-17 / 08-20 / 08-21 连着三晚失败拖到第三晚
才被发现——队列的严重度是从 bundle 日期倒推的，而 bundle 日期只在成功时才动，
对「连败」只是间接证据；**那个模式本身没有任何东西记录**。

另一半：日志行只带 ``HH:MM:SS`` 不带日期，`update_progress` 的模块文档列了四种
试过又否掉的启发式，结论是「要精确归属，得先让写入侧落一个带日期的运行边界
——那是另一个改动」。本模块守的正是那个改动。
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
from src.data_pipeline.daily_update import DailyUpdateConfig  # noqa: E402

TODAY = date(2026, 6, 10)
STAGES = ("fetch", "registry", "bins", "membership", "universe", "benchmark", "validate")
_SOURCE = Path(du.__file__).read_text(encoding="utf-8")
_TREE = ast.parse(_SOURCE)


def _box(tmp: Path) -> DailyUpdateConfig:
    cfg = DailyUpdateConfig(
        tushare_dir=tmp / "raw", provider_dir=tmp / "prov",
        delisted_registry=tmp / "raw" / "reg.parquet",
        reference_cases=tmp / "cases.yaml", now=TODAY,
    )
    cfg.tushare_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({
        "ts_code": ["000001.SZ"], "name": ["平安银行"],
        "snapshot_date": ["20260610"],
    }).to_parquet(cfg.tushare_dir / "active_stocks.parquet")
    for sub in ("calendars", "instruments", "features"):
        (cfg.provider_dir / sub).mkdir(parents=True, exist_ok=True)
    (cfg.provider_dir / "calendars" / "day.txt").write_text("LIVE", encoding="utf-8")
    (cfg.provider_dir / "instruments" / "all.txt").write_text("", encoding="utf-8")
    return cfg


def _runners(failing: str = "") -> dict[str, object]:
    def make(stage: str):
        def run(argv: list[str]) -> int:
            return 1 if stage == failing else 0
        return run
    return {s: make(s) for s in STAGES}


def _lines(cfg: DailyUpdateConfig) -> list[dict]:
    path = du.default_ledger_path(cfg.provider_dir)
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _logged() -> tuple[list[str], object]:
    seen: list[str] = []
    probe = logging.Handler()
    probe.emit = lambda record: seen.append(record.getMessage())  # type: ignore[method-assign]
    logging.getLogger("src").addHandler(probe)
    return seen, probe


# ---------------------------------------------------------------- 阶段语义零改动

class TheStageBodyIsUntouched(unittest.TestCase):
    """本改动的硬约束：新增写入**全部**在 `run_daily_update`（可观测性层）。

    这不是风格问题。`_execute_daily_update` 是七个阶段、退出码与 fail-loud 通道
    所在之处；把台账写进去，就等于让一次夜间更新的成败多依赖一个文件句柄。
    这条守卫让「这个改动会不会弄坏夜间更新」有一个**结构性**的答案。
    """

    @staticmethod
    def _body(name: str) -> ast.FunctionDef:
        for node in ast.walk(_TREE):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return node
        raise AssertionError(f"找不到 {name}")

    def test_no_ledger_or_boundary_call_inside_the_stage_body(self) -> None:
        called = {
            node.func.id
            for node in ast.walk(self._body("_execute_daily_update"))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        for forbidden in ("_append_ledger", "run_boundary_line", "default_ledger_path"):
            with self.subTest(不许出现=forbidden):
                self.assertNotIn(forbidden, called, "阶段体里出现了可观测性写入")

    def test_the_observability_layer_does_call_them(self) -> None:
        # 反面：确认它们**确实**在 run_daily_update 里 —— 否则上一条真空地绿着。
        called = {
            node.func.id
            for node in ast.walk(self._body("run_daily_update"))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertIn("_append_ledger", called)
        self.assertIn("run_boundary_line", called)


# ---------------------------------------------------------------- 路径碰撞

class TheLedgerPathCannotAliasAnythingElseTheRunTouches(unittest.TestCase):
    """台账路径是派生的、没有 CLI 开关——但碰撞的**另一头**可以被打错。

    操作人把 --delisted-registry / --reference-cases / 显式 --status-path 指到
    `<provider>.daily_update_ledger.jsonl` 上：终态一到，台账把 JSON 追加进
    canonical 输入；status 撞上更糟——每次 _record_status 的原子替换会把
    「只可追加」的台账整个截掉（codex P1）。与状态工件同一处置：**构造期**
    拒绝，任何阶段执行之前。

    暂存兄弟刻意不在此列：暂存名 = 名字+".tmp"，台账名以 .jsonl 结尾，
    `_status_tmp_path(x) == 台账` 无解——查不可构造的碰撞是死守卫。
    """

    @staticmethod
    def _cfg(tmp: Path, **override: object) -> DailyUpdateConfig:
        base: dict[str, object] = dict(
            tushare_dir=tmp / "raw", provider_dir=tmp / "prov",
            delisted_registry=tmp / "raw" / "reg.parquet",
            reference_cases=tmp / "cases.yaml", now=TODAY,
        )
        return DailyUpdateConfig(**{**base, **override})  # type: ignore[arg-type]

    def test_each_aliasable_input_is_rejected_at_construction(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            ledger = du.default_ledger_path(tmp / "prov")
            for field in ("delisted_registry", "reference_cases", "status_path"):
                with self.subTest(field=field):
                    with self.assertRaises(ValueError):
                        self._cfg(tmp, **{field: ledger})

    def test_a_sibling_providers_ledger_is_reserved_too(self) -> None:
        """兄弟 provider B 把 --status-path 指到 A 的台账上。

        只比**本 provider** 的派生路径，B 的配置照样通过——B 的第一次
        _record_status 就把 A 的只可追加历史原子替换掉（codex P2）。单个
        配置看不见别的 provider，判据抬到**命名空间**：`*.<LEDGER_FILENAME>`
        整体保留，不必知道它是谁的。
        """
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            foreign = tmp / "somewhere" / f"other_prov.{du.LEDGER_FILENAME}"
            # 可变根路径同在保留名单：provider 根撞上会被 swap() 整个
            # rename 掉，tushare 根被 fetch 直写（codex 第八轮 P2）。
            for field in ("status_path", "delisted_registry", "reference_cases",
                          "provider_dir", "tushare_dir"):
                with self.subTest(field=field):
                    with self.assertRaises(ValueError):
                        self._cfg(tmp, **{field: foreign})

    def test_a_descendant_of_a_ledger_name_is_rejected_too(self) -> None:
        """`<台账名>/status.json` 的叶子无辜，祖先不无辜。

        只查 basename 会放行；而写状态要先 mkdir 出台账那个名字的**目录**，
        随后 _append_ledger 撞 IsADirectoryError 被吞，运行永远进不了历史
        （codex P2）。保留检查按**每一段**路径组件做。
        """
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            foreign = tmp / f"other.{du.LEDGER_FILENAME}" / "status.json"
            with self.assertRaises(ValueError):
                self._cfg(tmp, status_path=foreign)

    def test_an_honest_config_still_constructs(self) -> None:
        # 反面：正常布局必须照常通过，否则上面只是「什么都拒」的副产品。
        # 显式 status 覆盖（合法 .json 名）同样要照常通过。
        with tempfile.TemporaryDirectory() as t:
            self._cfg(Path(t))
            self._cfg(Path(t), status_path=Path(t) / "custom_status.json")


# ---------------------------------------------------------------- 只可追加

class TheLedgerIsAppendOnly(unittest.TestCase):

    def test_three_runs_leave_three_lines(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            cfg = _box(Path(t))
            for _ in range(3):
                du.run_daily_update(cfg, _runners(failing="fetch"))  # type: ignore[arg-type]
            self.assertEqual(3, len(_lines(cfg)), "台账被覆盖了，而不是追加")

    def test_earlier_lines_are_never_rewritten(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            cfg = _box(Path(t))
            du.run_daily_update(cfg, _runners(failing="fetch"))  # type: ignore[arg-type]
            first = _lines(cfg)[0]
            du.run_daily_update(cfg, _runners(failing="validate"))  # type: ignore[arg-type]
            self.assertEqual(first, _lines(cfg)[0], "先前那一行被改动了")

    def test_the_writer_never_opens_the_ledger_truncating(self) -> None:
        """源码级：追加实现里不许出现截断语义。

        `write_text` / `"w"` / `os.replace` 任一出现在这个函数里，「只可追加」
        就不再成立——而那正是状态工件与台账的分野。
        """
        fn = next(
            node for node in ast.walk(_TREE)
            if isinstance(node, ast.FunctionDef) and node.name == "_append_ledger")
        # 取 `open(...)` 的 mode **实参**，不在源码文本里找引号 —— `ast.unparse`
        # 会把引号统一掉，按字面形态判会红得毫无信息（本仓刚为「字面形态」那一带
        # 付过学费）。
        # 追加句柄现在经 os.open(flags) + fdopen("ab")：fdopen 的 mode 是
        # 常量实参、os.open 的 flags 是表达式，分别钉。Path.open 只许剩只读。
        path_open_modes = {
            node.args[0].value
            for node in ast.walk(fn)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute) and node.func.attr == "open"
            and not (isinstance(node.func.value, ast.Name)
                     and node.func.value.id == "os")
            and node.args and isinstance(node.args[0], ast.Constant)
        }
        fdopen_modes = {
            node.args[1].value
            for node in ast.walk(fn)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "fdopen"
            and len(node.args) > 1 and isinstance(node.args[1], ast.Constant)
        }
        self.assertEqual({"ab"}, fdopen_modes, "追加句柄没了")
        self.assertLessEqual(
            path_open_modes, {"rb"},
            f"出现了只读之外的 Path.open：{path_open_modes} —— "
            f"「只可追加」不再成立")
        # symlink 拒随是承重的，且是**两层**：前置 is_symlink（Windows 主
        # 防线；行为测试无权限会 skip）+ O_NOFOLLOW（POSIX 上把 check-then-
        # use 竞态关死，打开动作本身遇链接原子失败）。源码级分别钉。
        called = {
            node.func.attr
            for node in ast.walk(fn)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertIn("is_symlink", called,
                      "_append_ledger 不再检查符号链接 —— 拒随防线没了")
        body_src = ast.unparse(fn)
        for bearing in ("O_APPEND", "O_NOFOLLOW", "O_CREAT"):
            with self.subTest(承重位=bearing):
                self.assertIn(
                    bearing, body_src,
                    f"os.open flags 丢了 {bearing} —— 追加/拒随语义不再成立")
        # 另外三种截断途径同样不许出现。
        body = ast.unparse(fn)
        for forbidden in ("write_text", "os.replace", "truncate", "write_bytes"):
            with self.subTest(不许出现=forbidden):
                self.assertNotIn(forbidden, body)

    def test_a_symlinked_ledger_target_is_refused(self) -> None:
        """派生台账位上的符号链接不许被跟随。

        B 名下的台账名被链到 A 的台账时，`open("ab")` 跟随链接、B 的记录
        直接写进 A 的历史——构造期检查看不见事后落在派生位上的链接
        （codex P2）。拒绝 + ERROR，退出码照旧。
        """
        with tempfile.TemporaryDirectory() as t:
            cfg = _box(Path(t))
            victim = Path(t) / "victim_history.jsonl"
            victim.write_text("", encoding="utf-8")
            target = du.default_ledger_path(cfg.provider_dir)
            try:
                target.symlink_to(victim)
            except OSError:
                self.skipTest("本机无符号链接权限")
            code = du.run_daily_update(cfg, _runners(failing="fetch"))  # type: ignore[arg-type]
            self.assertEqual(du.EXIT_FETCH_HARD, code, "退出码被台账问题改变了")
            self.assertEqual("", victim.read_text(encoding="utf-8"),
                             "追加穿过符号链接写进了别人的历史")

    def test_a_torn_tail_does_not_swallow_the_new_line(self) -> None:
        """上一个进程死在写一半、留下没有换行的尾行时，新记录不许被焊上去。

        直接追加会得到一条畸形行，而**新记录从历史里静默消失**——这正是
        `decision_journal` 在 codex #330 P1 上修过的那件事，这里照抄。
        """
        with tempfile.TemporaryDirectory() as t:
            cfg = _box(Path(t))
            path = du.default_ledger_path(cfg.provider_dir)
            path.write_bytes(b'{"partial": true')      # 无换行的残尾
            du.run_daily_update(cfg, _runners(failing="fetch"))  # type: ignore[arg-type]
            raw = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(2, len(raw), "残尾与新行被焊成了一行")
        self.assertEqual(11, json.loads(raw[-1])["exit_code"], "新记录没落下来")


# ---------------------------------------------------------------- 记了什么

class TheLedgerLineCarriesTheRunsIdentityAndOutcome(unittest.TestCase):

    def test_a_failure_records_the_stage_and_the_detail(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            cfg = _box(Path(t))
            du.run_daily_update(cfg, _runners(failing="fetch"))  # type: ignore[arg-type]
            entry = _lines(cfg)[0]
        self.assertEqual(du.EXIT_FETCH_HARD, entry["exit_code"])
        self.assertEqual("fetch", entry["failed_stage"])
        self.assertIn("fetch failed hard", entry["detail"])
        self.assertEqual(du.LEDGER_SCHEMA_VERSION, entry["schema_version"])

    def test_the_line_names_the_provider_it_describes(self) -> None:
        # 照抄状态工件的身份推理（codex #434 r18）：每条记录都要指名它描述的是谁。
        with tempfile.TemporaryDirectory() as t:
            cfg = _box(Path(t))
            du.run_daily_update(cfg, _runners(failing="fetch"))  # type: ignore[arg-type]
            entry = _lines(cfg)[0]
        self.assertEqual(du._norm(cfg.provider_dir), entry["provider_dir"])

    def test_elapsed_is_not_stored(self) -> None:
        # 它是两个时间戳的差；存第三份只是多一个会与推导值分叉的地方。
        with tempfile.TemporaryDirectory() as t:
            cfg = _box(Path(t))
            du.run_daily_update(cfg, _runners(failing="fetch"))  # type: ignore[arg-type]
            entry = _lines(cfg)[0]
        self.assertNotIn("elapsed_seconds", entry)
        self.assertIn("started_at", entry)
        self.assertIn("finished_at", entry)

    def test_the_ledger_is_a_sibling_named_after_the_provider(self) -> None:
        # 兄弟：原子切换只重命名 provider 目录本身。名派生：同父目录的两个
        # bundle 不能共用一份历史（codex #434 r4 在状态工件上的同一条理由）。
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            a = du.default_ledger_path(root / "my_cn_data_pit")
            b = du.default_ledger_path(root / "my_cn_data_pit_2015")
            self.assertNotEqual(a, b, "两个 bundle 的台账塌成了同一个")
            self.assertEqual(root.resolve(), a.parent)


# ---------------------------------------------------------------- 什么时候不写

class RunsThatChangeNothingRecordNothing(unittest.TestCase):

    def test_a_dry_run_writes_neither_ledger_nor_boundary(self) -> None:
        seen, probe = _logged()
        try:
            with tempfile.TemporaryDirectory() as t:
                cfg = _box(Path(t))
                cfg = DailyUpdateConfig(**{
                    **{f.name: getattr(cfg, f.name) for f in cfg.__dataclass_fields__.values()},
                    "dry_run": True})
                du.run_daily_update(cfg, _runners())  # type: ignore[arg-type]
                self.assertEqual([], _lines(cfg), "dry-run 写了台账")
        finally:
            logging.getLogger("src").removeHandler(probe)
        self.assertFalse(
            [m for m in seen if m.startswith(du.RUN_BOUNDARY_MARK)],
            "dry-run 写了运行边界")


# ---------------------------------------------------------------- 反向耦合

class LedgerFailureNeverChangesTheExitCode(unittest.TestCase):
    """与状态工件**同一条**契约：可观测性失败只记 ERROR，绝不改变退出码。"""

    def test_an_unserialisable_detail_does_not_crash_the_run(self) -> None:
        seen, probe = _logged()
        try:
            with tempfile.TemporaryDirectory() as t:
                path = Path(t) / "led.jsonl"
                du._append_ledger(path, {"detail": object()})   # 不可序列化
                self.assertFalse(path.exists())
        finally:
            logging.getLogger("src").removeHandler(probe)
        self.assertTrue([m for m in seen if "run-ledger append FAILED" in m],
                        "缺口没有被响亮地记下来")

    def test_a_surrogate_does_not_crash_the_run(self) -> None:
        seen, probe = _logged()
        bad = b"D:/x\xff.parquet".decode("utf-8", "surrogateescape")
        try:
            with tempfile.TemporaryDirectory() as t:
                du._append_ledger(Path(t) / "led.jsonl", {"detail": bad})
        finally:
            logging.getLogger("src").removeHandler(probe)
        self.assertTrue([m for m in seen if "run-ledger append FAILED" in m])

    def test_an_unwritable_directory_does_not_crash_the_run(self) -> None:
        seen, probe = _logged()
        try:
            with tempfile.TemporaryDirectory() as t:
                blocker = Path(t) / "blocked"
                blocker.write_text("i am a file", encoding="utf-8")
                du._append_ledger(blocker / "led.jsonl", {"a": 1})
        finally:
            logging.getLogger("src").removeHandler(probe)
        self.assertTrue([m for m in seen if "run-ledger append FAILED" in m])

    def test_the_append_swallows_everything_not_just_oserror(self) -> None:
        # 源码级：`except OSError` 会漏掉 UnicodeEncodeError / TypeError，
        # 任何一个漏网都会反转这条保证（codex #434 r24 在状态工件上的原话）。
        body = ast.unparse(next(
            node for node in ast.walk(_TREE)
            if isinstance(node, ast.FunctionDef) and node.name == "_append_ledger"))
        self.assertIn("except Exception", body)


# ---------------------------------------------------------------- 运行边界

class TheRunBoundaryIsDatedAndOwned(unittest.TestCase):

    def test_the_boundary_is_written_before_any_stage_output(self) -> None:
        seen, probe = _logged()
        try:
            with tempfile.TemporaryDirectory() as t:
                cfg = _box(Path(t))
                du.run_daily_update(cfg, _runners(failing="fetch"))  # type: ignore[arg-type]
        finally:
            logging.getLogger("src").removeHandler(probe)
        boundaries = [i for i, m in enumerate(seen) if m.startswith(du.RUN_BOUNDARY_MARK)]
        self.assertEqual(1, len(boundaries), "边界应恰好一条")
        self.assertEqual(0, boundaries[0], "边界之前已经有别的输出了")

    def test_the_boundary_carries_a_full_date_and_the_provider(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            cfg = _box(Path(t))
            from datetime import datetime, timedelta, timezone
            stamped = datetime(2026, 8, 24, 20, 30, 1,
                               tzinfo=timezone(timedelta(hours=8)))
            line = du.run_boundary_line(stamped, cfg.provider_dir)
        self.assertIn("2026-08-24", line, "只有时分秒的话归属还是做不到")
        self.assertIn(du._norm(cfg.provider_dir), line)
        self.assertTrue(line.startswith(du.RUN_BOUNDARY_MARK))

    def test_no_closing_marker_is_written(self) -> None:
        """不写「结束」标记：一段的终点就是下一段的起点或文件尾。

        「跑完了没有、结果如何」由状态工件与台账回答；再写一个结束标记就是把
        同一件事说三遍，而这个仓库反复付学费的正是「一件事写两处」。
        """
        seen, probe = _logged()
        try:
            with tempfile.TemporaryDirectory() as t:
                cfg = _box(Path(t))
                du.run_daily_update(cfg, _runners())  # type: ignore[arg-type]
        finally:
            logging.getLogger("src").removeHandler(probe)
        self.assertFalse([m for m in seen if "run finished" in m.lower()])


if __name__ == "__main__":
    unittest.main()
