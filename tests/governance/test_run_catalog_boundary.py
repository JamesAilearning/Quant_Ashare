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

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
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
)


def _line_count(path: Path) -> int:
    if not path.is_file():
        return 0
    return len([ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()])


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


class _SandboxedDefaults(unittest.TestCase):
    """把「默认索引」整体搬进临时沙盒，于是走的正是生产那条无参路径。"""

    def _sandbox(self, tree: Path, catalog: Path):
        return mock.patch.multiple(
            run_catalog,
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
            row = json.loads(catalog.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(row["engine"], "walk_forward")

    def test_a_relative_output_dir_resolves_against_the_tree(self) -> None:
        # 索引里 1257 条历史行是相对路径；它们该被判在树内。
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "output"
            (tree / "runs").mkdir(parents=True)
            (tree / "wf" / "r1").mkdir(parents=True)
            catalog = tree / "runs" / "_index.jsonl"
            with self._sandbox(tree, catalog):
                append_run_record(build_record(
                    engine="walk_forward", status="ok", output_dir="output/wf/r1"))
            self.assertEqual(_line_count(catalog), 1)

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
        return ["--catalog", str(catalog), "--tree", str(tree), *extra]

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

    def test_unparseable_lines_are_kept_not_dropped(self) -> None:
        # 看不懂的行不动 —— 那是别人的数据，判据只针对能证明是残骸的那些。
        from scripts.prune_run_catalog import classify

        with tempfile.TemporaryDirectory() as tmp:
            catalog, tree = self._catalog_with(Path(tmp))
            with open(catalog, "a", encoding="utf-8") as fh:
                fh.write("{ this is not json\n")
            keep, drop, _ = classify(catalog, tree)
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
            keep, drop, _ = classify(catalog, tree)
            self.assertIn("null", keep)
            self.assertIn("[1, 2]", keep)
            self.assertEqual(len(drop), 2)
            self.assertEqual(main(self._argv(catalog, tree)), 0)

    def test_a_concurrent_append_aborts_the_prune(self) -> None:
        # 分类与重写之间追加的行，既不在 keep 里也不在旁车里 —— 一改写就永久
        # 丢失。宁可拒绝动手。
        import scripts.prune_run_catalog as tool

        with tempfile.TemporaryDirectory() as tmp:
            catalog, tree = self._catalog_with(Path(tmp))
            real_classify = tool.classify

            def racing(path: Path, boundary: Path):
                result = real_classify(path, boundary)
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(
                        {"engine": "walk_forward",
                         "output_dir": str(tree / "wf" / "keep")}) + "\n")
                return result

            with mock.patch.object(tool, "classify", racing):
                self.assertEqual(
                    tool.main(self._argv(catalog, tree, "--prune")), 3)
            self.assertEqual(_line_count(catalog), 4, "并发追加的行被吞了")
            self.assertEqual(
                list(catalog.parent.glob("_index.pruned-*.jsonl")), [],
                "拒绝动手却写了旁车")


if __name__ == "__main__":
    unittest.main()
