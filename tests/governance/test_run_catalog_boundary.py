"""索引写入侧的边界（openspec 2026-08-19-run-catalog-cwd-pollution）。

默认索引路径曾按进程 CWD 解析，而 pytest 从仓库根跑 —— 于是每一次触发引擎的
测试都往**操作人的真实索引**追加一行，产物却在随后被删的临时目录里。实测该
文件 3560 行中 3455 行（97.1%）是这么来的：2279 条系统临时目录、1176 条落在
四个硬编码测试夹具路径上（各 294 次）。

**这个测试文件本身就是那类污染的典型来源**，所以它盯的正是「跑测试不会写进真
实索引」这件事。

边界只管**默认那份共享索引**；显式 `catalog_path` 是给「就要记到别处」留的
逃生口，见 `ExplicitCatalogIsTheEscapeHatch`。
"""

from __future__ import annotations

import contextlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest import mock

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.core import run_catalog  # noqa: E402
from src.core.run_catalog import (  # noqa: E402
    _DEFAULT_CATALOG_PATH,
    _DEFAULT_OUTPUT_TREE,
    append_run_record,
    build_record,
    canonical_catalog_path,
    catalog_lock,
)


def _line_count(path: Path) -> int:
    if not path.is_file():
        return 0
    return len([ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()])


def _rows(path: Path) -> list[dict[str, object]]:
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip()]


def _link(alias: Path, target: Path) -> bool:
    """给 ``target`` 造一个别名拼写；造不出来（无权限）返回 False。"""
    try:
        if os.name == "nt":
            done = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(alias), str(target)],
                capture_output=True, text=True, check=False)
            return done.returncode == 0 and alias.exists()
        os.symlink(target, alias, target_is_directory=True)
    except OSError:
        return False
    return alias.exists()


def _file_link(alias: Path, target: Path) -> bool:
    """给**文件** ``target`` 造一个符号链接别名;造不出来返回 False。

    目录联接不足以复现这一类:联接下的 ``alias/_index.jsonl`` 与真目录里的
    是同一个目录条目,替换和锁都自然落在真文件上。要让别名成为**自己的**目录
    条目,只有文件符号链接(POSIX 随手可造;Windows 需要开发者模式)。
    """
    try:
        os.symlink(target, alias)
    except (OSError, NotImplementedError):
        return False
    return alias.is_symlink()


@contextlib.contextmanager
def _chdir(path: Path) -> Iterator[None]:
    old = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


class _SandboxedDefaults(unittest.TestCase):
    """把「默认索引」整体搬进临时沙盒，于是走的正是生产那条无参路径。

    连 ``_REPO_ROOT`` 一并搬，沙盒才自洽：仓库根 = ``tree.parent``。
    """

    def _sandbox(self, tree: Path, catalog: Path):
        return mock.patch.multiple(
            run_catalog,
            _REPO_ROOT=tree.parent,
            _DEFAULT_CATALOG_PATH=catalog,
            _DEFAULT_OUTPUT_TREE=tree,
        )


class DefaultPathIsAnchored(unittest.TestCase):
    def test_default_is_absolute_and_under_the_repo(self) -> None:
        # 相对路径 = 按 CWD 解析 = 同一份代码从不同目录启动写到不同文件。
        self.assertTrue(_DEFAULT_CATALOG_PATH.is_absolute())
        self.assertEqual(
            _DEFAULT_CATALOG_PATH,
            _PROJECT_ROOT / "output" / "runs" / "_index.jsonl",
        )

    def test_the_boundary_tree_is_named_not_derived(self) -> None:
        # 从索引路径反推 `<tree>/runs/<file>` 会让文件摆放位置变成隐藏契约：
        # `/tmp/catalog.jsonl` 推出 `/`，于是接受一切绝对路径。
        self.assertEqual(_DEFAULT_OUTPUT_TREE, _PROJECT_ROOT / "output")


class OutOfTreeRunsAreNotCatalogued(unittest.TestCase):
    """这条是本 change 的核心：测试不得污染操作人的真实索引。"""

    def test_a_temp_output_dir_does_not_touch_the_real_catalog(self) -> None:
        before = _line_count(_DEFAULT_CATALOG_PATH)
        with tempfile.TemporaryDirectory() as tmp:
            append_run_record(
                build_record(engine="walk_forward", status="ok", output_dir=tmp)
            )
        self.assertEqual(
            _line_count(_DEFAULT_CATALOG_PATH), before,
            "跑测试往真实索引写了行 —— 这正是 3455 行残骸的来源",
        )

    def test_a_missing_output_dir_is_also_refused(self) -> None:
        before = _line_count(_DEFAULT_CATALOG_PATH)
        append_run_record(build_record(engine="pipeline", status="ok"))
        self.assertEqual(_line_count(_DEFAULT_CATALOG_PATH), before)


class RelativeOutputsFollowTheProducer(_SandboxedDefaults):
    """相对路径按**生产者的 CWD** 解析 —— 产物就在那里。

    `WalkForwardConfig.output_dir` 缺省是相对的 `"output/walk_forward"`，
    真实索引里 105 条合法行有 101 条是相对路径，所以这条不是假想场景。
    """

    def test_a_run_launched_from_the_repo_root_is_appended(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tree = root / "output"
            (tree / "runs").mkdir(parents=True)
            (tree / "wf" / "r1").mkdir(parents=True)
            catalog = tree / "runs" / "_index.jsonl"
            with self._sandbox(tree, catalog), _chdir(root):
                append_run_record(build_record(
                    engine="walk_forward", status="ok", output_dir="output/wf/r1"))
            self.assertEqual(_line_count(catalog), 1)

    def test_the_stored_text_is_unchanged_when_both_readings_agree(self) -> None:
        # 常见情形（从仓库根启动）下，存进去的字符串一个字节都不该变 —— 否则
        # 索引会平白从「相对、可跨机器」变成「绝对、钉死本机」。
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tree = root / "output"
            (tree / "runs").mkdir(parents=True)
            (tree / "wf" / "r1").mkdir(parents=True)
            catalog = tree / "runs" / "_index.jsonl"
            with self._sandbox(tree, catalog), _chdir(root):
                append_run_record(build_record(
                    engine="walk_forward", status="ok", output_dir="output/wf/r1"))
            self.assertEqual(_rows(catalog)[0]["output_dir"], "output/wf/r1")

    def test_a_run_launched_outside_the_repo_is_refused(self) -> None:
        # codex 的原场景：在 /tmp 里启动，产物真在 /tmp/output/... 下。按仓库根
        # 去解析会把它当成 <repo>/output/... 而放行 —— 索引照旧被污染，控制台
        # 还会指向毫不相干的仓库产物。
        with tempfile.TemporaryDirectory() as tmp, \
                tempfile.TemporaryDirectory() as elsewhere:
            root = Path(tmp)
            tree = root / "output"
            (tree / "runs").mkdir(parents=True)
            (tree / "wf" / "r1").mkdir(parents=True)
            # 别处也有个同名的相对目录，正是它让「按仓库根解析」显得成立。
            (Path(elsewhere) / "output" / "wf" / "r1").mkdir(parents=True)
            catalog = tree / "runs" / "_index.jsonl"
            with self._sandbox(tree, catalog), _chdir(Path(elsewhere)):
                append_run_record(build_record(
                    engine="walk_forward", status="ok", output_dir="output/wf/r1"))
            self.assertEqual(
                _line_count(catalog), 0,
                "在树外启动的运行被当成树内放行了")

    def test_a_divergent_reading_is_stored_absolute(self) -> None:
        # CWD 不是仓库根、但产物仍落在树内（cwd = <root>/output，
        # output_dir = "wf/r1"）。两种读法分歧，就必须存绝对路径，否则控制台
        # 会去开 <root>/wf/r1 —— 一个不存在的目录。
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tree = root / "output"
            (tree / "runs").mkdir(parents=True)
            (tree / "wf" / "r1").mkdir(parents=True)
            catalog = tree / "runs" / "_index.jsonl"
            with self._sandbox(tree, catalog), _chdir(tree):
                append_run_record(build_record(
                    engine="walk_forward", status="ok", output_dir="wf/r1"))
            self.assertEqual(_line_count(catalog), 1)
            stored = Path(str(_rows(catalog)[0]["output_dir"]))
            self.assertTrue(stored.is_absolute(), "分歧时没有改存绝对路径")
            self.assertEqual(stored.resolve(), (tree / "wf" / "r1").resolve())


class InTreeRunsStillGetCatalogued(_SandboxedDefaults):
    """反向断言：不得误伤真实运行。判据收紧最怕的是拒掉该收的。"""

    def test_a_run_inside_the_tree_is_appended(self) -> None:
        # 注：GitHub 的 Windows runner 把 TEMP 设成 8.3 短名
        # （`C:/Users/RUNNER~1/...`），所以这个用例在 CI 上顺带就是短名/长名
        # 错位的回归 —— 判据只解析一侧时它会红，实测红过。
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "output"
            run_dir = tree / "walk_forward" / "some_run"
            run_dir.mkdir(parents=True)
            catalog = tree / "runs" / "_index.jsonl"
            with self._sandbox(tree, catalog):
                append_run_record(build_record(
                    engine="walk_forward", status="ok", output_dir=str(run_dir)))
            self.assertEqual(_line_count(catalog), 1)
            self.assertEqual(_rows(catalog)[0]["engine"], "walk_forward")

    def test_a_linked_spelling_of_the_same_dir_is_still_inside(self) -> None:
        # `output/` 是符号链接/联接时，两侧拼写不同但指的是同一棵树。判据只
        # 归一化词法就会把**每一次**合法运行都拒掉。
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "real"
            alias = Path(tmp) / "alias"
            (real / "runs").mkdir(parents=True)
            (real / "wf" / "r1").mkdir(parents=True)
            if not _link(alias, real):
                self.skipTest("这个环境造不出目录链接（需要权限）")
            catalog = real / "runs" / "_index.jsonl"
            with self._sandbox(real, catalog):
                append_run_record(build_record(
                    engine="walk_forward", status="ok",
                    output_dir=str(alias / "wf" / "r1")))
            self.assertEqual(
                _line_count(catalog), 1,
                "同一目录的另一种拼写被判成树外 —— 合法运行全军覆没")


class ExplicitCatalogIsTheEscapeHatch(unittest.TestCase):
    def test_an_explicit_catalog_path_is_not_second_guessed(self) -> None:
        # 传 catalog_path 本身就是「我知道我在做什么」。不设边界的代价是清楚的：
        # 污染仍被堵住，因为测试走的是无参默认路径。
        with tempfile.TemporaryDirectory() as tmp:
            catalog = Path(tmp) / "elsewhere" / "index.jsonl"
            append_run_record(
                build_record(engine="pipeline", status="ok",
                             output_dir=str(Path(tmp) / "anywhere")),
                catalog_path=catalog)
            self.assertEqual(_line_count(catalog), 1)


class TheCatalogLockSerializesWriters(unittest.TestCase):
    """锁是清理脚本能安全改写活文件的**唯一**依据。"""

    def test_the_lock_is_exclusive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog = Path(tmp) / "_index.jsonl"
            catalog.write_text("", encoding="utf-8")
            with catalog_lock(catalog, timeout=0.2) as first:
                self.assertTrue(first, "第一次就没拿到锁")
                with catalog_lock(catalog, timeout=0.2) as second:
                    self.assertFalse(second, "锁不排他 —— 清理脚本的保护是空的")

    def test_canonical_path_collapses_aliased_spellings(self) -> None:
        # 规范化函数本身:同一个文件经别名目录拼写,必须归到同一个路径。
        # （这条用目录联接，Windows 上不需要特权，于是本机也跑得到；下面两条
        # 需要文件符号链接，Windows 无开发者模式时会 skip，由 ubuntu CI 腿跑。）
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "real"
            real.mkdir()
            catalog = real / "_index.jsonl"
            catalog.write_text("", encoding="utf-8")
            alias = Path(tmp) / "alias"
            if not _link(alias, real):
                self.skipTest("这个环境造不出目录链接（需要权限）")
            self.assertEqual(
                canonical_catalog_path(alias / "_index.jsonl"),
                canonical_catalog_path(catalog),
                "别名拼写没有归到同一个规范路径 —— 锁与替换都会各走各的",
            )

    def test_the_lock_identity_ignores_the_spelling(self) -> None:
        # 锁名跟着调用方传进来的字符串走,等于把互斥交给了拼写:一个符号链接
        # 别名会让两边各拿各的锁,互斥直接落空。
        with tempfile.TemporaryDirectory() as tmp:
            catalog = Path(tmp) / "_index.jsonl"
            catalog.write_text("", encoding="utf-8")
            alias = Path(tmp) / "alias.jsonl"
            if not _file_link(alias, catalog):
                self.skipTest("这个环境造不出文件符号链接（需要权限）")
            with catalog_lock(catalog, timeout=0.2) as first:
                self.assertTrue(first)
                with catalog_lock(alias, timeout=0.2) as second:
                    self.assertFalse(
                        second, "同一个文件的两种拼写各拿各的锁 —— 互斥落空")

    def test_a_writer_that_cannot_get_the_lock_does_not_bypass_it(self) -> None:
        # 曾经是「等不到就照样写」。那条刻意的无锁写入路径正是残余竞态的唯一
        # 来源:它可能落在清理脚本最后一次指纹比对之后、替换之前,于是那一行
        # 照样被丢掉 —— 指纹兜底兜不住它。不绕过,窗口才真的关上。
        with tempfile.TemporaryDirectory() as tmp:
            catalog = Path(tmp) / "_index.jsonl"
            catalog.write_text("", encoding="utf-8")
            with catalog_lock(catalog, timeout=0.2) as held:
                self.assertTrue(held)
                with mock.patch.object(run_catalog, "_WRITER_LOCK_TIMEOUT", 0.1),                         self.assertLogs("src.core.run_catalog", "ERROR") as logs:
                    append_run_record(
                        build_record(engine="pipeline", status="ok",
                                     output_dir=str(Path(tmp) / "x")),
                        catalog_path=catalog)
            self.assertEqual(_line_count(catalog), 0, "绕过了锁 —— 竞态窗口还开着")
            # 没进索引,但绝不是静默丢弃:整条记录原样在日志里。
            self.assertIn("pipeline", "".join(logs.output))


class PruneToolPreservesEvidence(unittest.TestCase):
    """清理工具:默认只报数;动手时必须先留证再改原文件。"""

    def _catalog_with(self, tmp: Path) -> tuple[Path, Path]:
        tree = tmp / "output"
        (tree / "runs").mkdir(parents=True)
        (tree / "wf" / "keep").mkdir(parents=True)
        catalog = tree / "runs" / "_index.jsonl"
        rows: list[object] = [
            {"engine": "walk_forward", "output_dir": str(tree / "wf" / "keep")},
            {"engine": "walk_forward", "output_dir": "C:/Temp/tmpdead"},
            {"engine": "pipeline", "output_dir": ""},
        ]
        catalog.write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        return catalog, tree

    def _argv(self, catalog: Path, tree: Path, *extra: str) -> list[str]:
        # 相对锚点**独立点名**,绝不从 --tree 反推:--tree 一旦经别名指向 output
        # 树,反推出来的锚会把合法的相对行判成残骸(codex #453)。
        return ["--catalog", str(catalog), "--tree", str(tree),
                "--relative-base", str(tree.parent), *extra]

    def test_report_only_by_default(self) -> None:
        from scripts.prune_run_catalog import main

        with tempfile.TemporaryDirectory() as tmp:
            catalog, tree = self._catalog_with(Path(tmp))
            before = catalog.read_text(encoding="utf-8")
            self.assertEqual(main(self._argv(catalog, tree)), 0)
            self.assertEqual(catalog.read_text(encoding="utf-8"), before,
                             "默认模式动了文件")

    def test_prune_writes_a_sidecar_before_rewriting(self) -> None:
        from scripts.prune_run_catalog import main

        with tempfile.TemporaryDirectory() as tmp:
            catalog, tree = self._catalog_with(Path(tmp))
            self.assertEqual(main(self._argv(catalog, tree, "--prune")), 0)
            self.assertEqual(_line_count(catalog), 1, "该留的行没留住")
            sidecars = list(catalog.parent.glob("_index.pruned-*.jsonl"))
            self.assertEqual(len(sidecars), 1, "移除的行没有留证")
            self.assertEqual(_line_count(sidecars[0]), 2)

    def test_a_relative_row_is_read_the_way_the_console_reads_it(self) -> None:
        # 脚本拿不到历史行当年的 CWD，只能沿用控制台那条约定（相对 = 相对仓库
        # 根）。判据本来就是「控制台永远打不开的行」，同约定才自洽。
        from scripts.prune_run_catalog import classify

        with tempfile.TemporaryDirectory() as tmp:
            catalog, tree = self._catalog_with(Path(tmp))
            with open(catalog, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(
                    {"engine": "walk_forward", "output_dir": "output/wf/keep"}) + "\n")
            keep, drop, _ = classify(catalog, tree, tree.parent)
            self.assertEqual(len(keep), 2, "相对路径的合法行被判成了残骸")

    def test_the_relative_anchor_does_not_follow_the_tree_spelling(self) -> None:
        # `--tree` 经别名/联接指向 output 树时，用 `tree.parent` 当锚会把合法的
        # 相对行锚到别名旁边而判成残骸。锚点必须独立点名 —— 这是「从路径的拼写
        # 或位置推导语义」这个病的第三次复发（codex #453）。
        from scripts.prune_run_catalog import classify

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            tree = root / "output"
            (tree / "runs").mkdir(parents=True)
            (tree / "wf" / "keep").mkdir(parents=True)
            alias = Path(tmp) / "alias"
            if not _link(alias, tree):
                self.skipTest("这个环境造不出目录链接（需要权限）")
            catalog = tree / "runs" / "_index.jsonl"
            catalog.write_text(json.dumps(
                {"engine": "walk_forward", "output_dir": "output/wf/keep"}) + "\n",
                encoding="utf-8")
            keep, drop, _ = classify(catalog, alias, root)
            self.assertEqual(
                (len(keep), len(drop)), (1, 0),
                "--tree 走别名时，合法的相对行被判成了残骸")

    def test_unparseable_lines_are_kept_not_dropped(self) -> None:
        # 看不懂的行不动 —— 那是别人的数据，判据只针对能证明是残骸的那些。
        from scripts.prune_run_catalog import classify

        with tempfile.TemporaryDirectory() as tmp:
            catalog, tree = self._catalog_with(Path(tmp))
            with open(catalog, "a", encoding="utf-8") as fh:
                fh.write("{ this is not json\n")
            keep, drop, _ = classify(catalog, tree, tree.parent)
            self.assertIn("{ this is not json", keep)
            self.assertEqual(len(drop), 2)

    def test_valid_json_that_is_not_a_record_is_kept_not_crashed_on(self) -> None:
        # `null` / 数组都是合法 JSON。以前这里直接 `.get()`，于是连只报数模式
        # 都会抛 AttributeError 中断。
        from scripts.prune_run_catalog import classify, main

        with tempfile.TemporaryDirectory() as tmp:
            catalog, tree = self._catalog_with(Path(tmp))
            with open(catalog, "a", encoding="utf-8") as fh:
                fh.write("null\n[1, 2]\n")
            keep, drop, _ = classify(catalog, tree, tree.parent)
            self.assertIn("null", keep)
            self.assertIn("[1, 2]", keep)
            self.assertEqual(len(drop), 2)
            self.assertEqual(main(self._argv(catalog, tree)), 0)

    def test_prune_through_a_symlink_rewrites_the_real_catalog(self) -> None:
        # 替换若落在别名条目上:符号链接被换成一个普通文件，真索引纹丝不动，
        # 而工具报告清理成功。走规范路径的写入者继续往没被清理的那份追加。
        from scripts.prune_run_catalog import main

        with tempfile.TemporaryDirectory() as tmp:
            catalog, tree = self._catalog_with(Path(tmp))
            alias = catalog.with_name("alias.jsonl")
            if not _file_link(alias, catalog):
                self.skipTest("这个环境造不出文件符号链接（需要权限）")
            self.assertEqual(main(self._argv(alias, tree, "--prune")), 0)
            self.assertEqual(
                _line_count(catalog), 1, "替换动的是别名条目 —— 真索引没被清理")
            self.assertTrue(alias.is_symlink(), "符号链接被换成了普通文件")

    def test_the_tool_acts_on_the_canonical_path_not_the_given_one(self) -> None:
        # 上一条要文件符号链接，Windows 无开发者模式时会 skip —— 而 skip 的测试
        # 等于没测。这条把「工具是否真的走规范路径」变成平台无关的行为断言：
        # 让规范化返回另一个真实文件，被清理的必须是它，传进来的那份不许动。
        import scripts.prune_run_catalog as tool

        with tempfile.TemporaryDirectory() as tmp:
            given, tree = self._catalog_with(Path(tmp))
            stand_in = given.with_name("canonical.jsonl")
            stand_in.write_text(given.read_text(encoding="utf-8"), encoding="utf-8")
            with mock.patch.object(
                    tool, "canonical_catalog_path", lambda _p: stand_in):
                self.assertEqual(tool.main(self._argv(given, tree, "--prune")), 0)
            self.assertEqual(_line_count(stand_in), 1, "没有对规范路径动手")
            self.assertEqual(_line_count(given), 3, "对传进来的那个拼写动了手")

    def test_prune_refuses_a_hard_linked_catalog(self) -> None:
        # 硬链接靠路径规范化认不出来（两个名字同一个 inode）。而本工具是
        # 「换文件」式重写：换掉其中一个名字，别的名字仍指着旧内容。
        from scripts.prune_run_catalog import main

        with tempfile.TemporaryDirectory() as tmp:
            catalog, tree = self._catalog_with(Path(tmp))
            before = catalog.read_text(encoding="utf-8")
            try:
                os.link(catalog, catalog.with_name("second-name.jsonl"))
            except (OSError, NotImplementedError):
                self.skipTest("这个环境造不出硬链接")
            self.assertEqual(main(self._argv(catalog, tree, "--prune")), 5)
            self.assertEqual(catalog.read_text(encoding="utf-8"), before)
            self.assertEqual(
                list(catalog.parent.glob("_index.pruned-*.jsonl")), [])

    def test_an_unusable_path_string_does_not_abort_the_scan(self) -> None:
        # 内嵌 NUL 的串让 `Path.resolve()` 抛 ValueError（不是 OSError）。不接住
        # 它，一条畸形记录就能让**只报数模式**整个中断 —— 而这工具存在的意义
        # 正是容忍畸形/外来数据。
        from scripts.prune_run_catalog import classify

        with tempfile.TemporaryDirectory() as tmp:
            catalog, tree = self._catalog_with(Path(tmp))
            with open(catalog, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(
                    {"engine": "pipeline", "output_dir": chr(0) + "foo"}) + "\n")
            keep, drop, _ = classify(catalog, tree, tree.parent)
            self.assertEqual(len(keep), 1)
            self.assertEqual(len(drop), 3, "畸形行既没被算进去，也没让扫描中断")

    def test_the_sidecar_never_overwrites_earlier_evidence(self) -> None:
        # 同一秒里跑两次清理（或时钟重复）会派生同名旁车，`write_text` 会静默
        # 截断前一次的留证 —— 本工具的全部承诺就是「移除的行留得住」。
        import scripts.prune_run_catalog as tool

        class _FixedClock:
            @staticmethod
            def now() -> Any:
                class _T:
                    @staticmethod
                    def strftime(_fmt: str) -> str:
                        return "20260820-000000"
                return _T()

        with tempfile.TemporaryDirectory() as tmp:
            catalog, tree = self._catalog_with(Path(tmp))
            earlier = catalog.with_name("_index.pruned-20260820-000000.jsonl")
            earlier.write_text("先前那次的留证\n", encoding="utf-8")
            with mock.patch.object(tool, "datetime", _FixedClock):
                self.assertEqual(tool.main(self._argv(catalog, tree, "--prune")), 0)
            self.assertEqual(
                earlier.read_text(encoding="utf-8"), "先前那次的留证\n",
                "覆盖掉了先前那次的留证")
            second = catalog.with_name("_index.pruned-20260820-000000-2.jsonl")
            self.assertEqual(_line_count(second), 2, "这次的留证没落在新名字上")

    def test_replacement_carries_over_the_catalogs_access_mode(self) -> None:
        # `write_text` 按 umask 造暂存件（常见 0644），`os.replace` 会把这个更
        # 宽松的权限换到活索引上 —— 一份 0600 的索引跑完 --prune 就对同机其他
        # 用户敞开了。
        #
        # 真行为断言只在 POSIX 成立（Windows 的 chmod 只管只读位），所以这里
        # 断言的是「替换前把原文件的模式抄给了暂存件」——平台无关，于是本机也
        # 测得到。skip 的测试等于没测。
        import scripts.prune_run_catalog as tool

        with tempfile.TemporaryDirectory() as tmp:
            catalog, tree = self._catalog_with(Path(tmp))
            expected = stat.S_IMODE(os.stat(catalog).st_mode)
            seen: list[tuple[str, int]] = []
            real_chmod = os.chmod

            def recording(path: Any, mode: int, *a: Any, **kw: Any) -> None:
                seen.append((str(path), mode))
                real_chmod(path, mode, *a, **kw)

            with mock.patch("os.chmod", recording):
                self.assertEqual(tool.main(self._argv(catalog, tree, "--prune")), 0)
            staged_chmods = [m for p, m in seen if ".tmp-" in p]
            self.assertEqual(
                staged_chmods, [expected],
                "替换前没有把原索引的权限抄给暂存件")

    def test_prune_refuses_when_it_cannot_take_the_lock(self) -> None:
        # 拿不到锁 = 有运行正在写。宁可不动手。
        import scripts.prune_run_catalog as tool

        with tempfile.TemporaryDirectory() as tmp:
            catalog, tree = self._catalog_with(Path(tmp))
            before = catalog.read_text(encoding="utf-8")
            with catalog_lock(catalog, timeout=0.2) as held:
                self.assertTrue(held)
                with mock.patch.object(tool, "_PRUNE_LOCK_TIMEOUT", 0.1):
                    self.assertEqual(
                        tool.main(self._argv(catalog, tree, "--prune")), 4)
            self.assertEqual(catalog.read_text(encoding="utf-8"), before)
            self.assertEqual(
                list(catalog.parent.glob("_index.pruned-*.jsonl")), [])

    def test_an_append_that_ignored_the_lock_aborts_the_prune(self) -> None:
        # 走到这里索引还会变，只剩一种可能：有个不遵守这把锁的写入者。现有写入
        # 侧不会（等不到锁就放弃追加），所以这是给「将来某个新写入口忘了拿锁」
        # 留的兜底。分类与写回之间追加的行，既不在保留集也不在旁车里。
        import scripts.prune_run_catalog as tool

        with tempfile.TemporaryDirectory() as tmp:
            catalog, tree = self._catalog_with(Path(tmp))
            real_classify = tool.classify
            calls: list[int] = []

            def racing(path: Path, boundary: Path, base: Path):
                result = real_classify(path, boundary, base)
                calls.append(1)
                if len(calls) == 2:        # 锁内那次分类之后才插进去
                    with open(path, "a", encoding="utf-8") as fh:
                        fh.write(json.dumps(
                            {"engine": "walk_forward",
                             "output_dir": str(tree / "wf" / "keep")}) + "\n")
                return result

            with mock.patch.object(tool, "classify", racing):
                self.assertEqual(
                    tool.main(self._argv(catalog, tree, "--prune")), 3)
            self.assertEqual(len(calls), 2, "锁内没有重新分类")
            self.assertEqual(_line_count(catalog), 4, "并发追加的行被吞了")
            self.assertEqual(
                list(catalog.parent.glob("_index.pruned-*.jsonl")), [],
                "拒绝动手却留下了旁车")


if __name__ == "__main__":
    unittest.main()
