"""索引写入侧的边界（openspec 2026-08-19-run-catalog-cwd-pollution）。

默认索引路径曾按进程 CWD 解析，而 pytest 从仓库根跑 —— 于是每一次触发引擎的
测试都往**操作人的真实索引**追加一行，产物却在随后被删的临时目录里。实测该
文件 3560 行中 3455 行（97.1%）是这么来的：2279 条系统临时目录、1176 条落在
四个硬编码测试夹具路径上（各 294 次）。

**这个测试文件本身就是那类污染的典型来源**，所以它盯的正是「跑测试不会写进真
实索引」这件事。
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.core.run_catalog import (  # noqa: E402
    _DEFAULT_CATALOG_PATH,
    append_run_record,
    build_record,
)


def _line_count(path: Path) -> int:
    if not path.is_file():
        return 0
    return len([ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()])


class DefaultPathIsAnchored(unittest.TestCase):
    def test_default_is_absolute_and_under_the_repo(self) -> None:
        # 相对路径 = 按 CWD 解析 = 同一份代码从不同目录启动写到不同文件。
        self.assertTrue(_DEFAULT_CATALOG_PATH.is_absolute())
        self.assertEqual(
            _DEFAULT_CATALOG_PATH,
            _PROJECT_ROOT / "output" / "runs" / "_index.jsonl",
        )


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


class InTreeRunsStillGetCatalogued(unittest.TestCase):
    """反向断言：不得误伤真实运行。判据收紧最怕的是拒掉该收的。"""

    def test_a_run_inside_its_catalogs_tree_is_appended(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "output"
            run_dir = tree / "walk_forward" / "some_run"
            run_dir.mkdir(parents=True)
            catalog = tree / "runs" / "_index.jsonl"
            append_run_record(
                build_record(engine="walk_forward", status="ok",
                             output_dir=str(run_dir)),
                catalog_path=catalog,
            )
            self.assertEqual(_line_count(catalog), 1)
            row = json.loads(catalog.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(row["engine"], "walk_forward")

    def test_a_relative_output_dir_resolves_against_the_catalogs_tree(self) -> None:
        # 索引里 1257 条历史行是相对路径；它们该被判在树内。
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "output"
            (tree / "runs").mkdir(parents=True)
            (tree / "wf" / "r1").mkdir(parents=True)
            catalog = tree / "runs" / "_index.jsonl"
            append_run_record(
                build_record(engine="walk_forward", status="ok",
                             output_dir="output/wf/r1"),
                catalog_path=catalog,
            )
            self.assertEqual(_line_count(catalog), 1)


class PruneToolPreservesEvidence(unittest.TestCase):
    """清理工具:默认只报数;动手时必须先留证再改原文件。"""

    def _catalog_with(self, tmp: Path) -> Path:
        tree = tmp / "output"
        (tree / "runs").mkdir(parents=True)
        (tree / "wf" / "keep").mkdir(parents=True)
        catalog = tree / "runs" / "_index.jsonl"
        rows = [
            {"engine": "walk_forward", "output_dir": str(tree / "wf" / "keep")},
            {"engine": "walk_forward", "output_dir": r"C:\Temp\tmpdead"},
            {"engine": "pipeline", "output_dir": ""},
        ]
        catalog.write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        return catalog

    def test_report_only_by_default(self) -> None:
        from scripts.prune_run_catalog import main

        with tempfile.TemporaryDirectory() as tmp:
            catalog = self._catalog_with(Path(tmp))
            before = catalog.read_text(encoding="utf-8")
            self.assertEqual(main(["--catalog", str(catalog)]), 0)
            self.assertEqual(catalog.read_text(encoding="utf-8"), before,
                             "默认模式动了文件")

    def test_prune_writes_a_sidecar_before_rewriting(self) -> None:
        from scripts.prune_run_catalog import main

        with tempfile.TemporaryDirectory() as tmp:
            catalog = self._catalog_with(Path(tmp))
            self.assertEqual(main(["--catalog", str(catalog), "--prune"]), 0)
            self.assertEqual(_line_count(catalog), 1, "该留的行没留住")
            sidecars = list(catalog.parent.glob("_index.pruned-*.jsonl"))
            self.assertEqual(len(sidecars), 1, "移除的行没有留证")
            self.assertEqual(_line_count(sidecars[0]), 2)

    def test_unparseable_lines_are_kept_not_dropped(self) -> None:
        # 看不懂的行不动 —— 那是别人的数据，判据只针对能证明是残骸的那些。
        from scripts.prune_run_catalog import classify

        with tempfile.TemporaryDirectory() as tmp:
            catalog = self._catalog_with(Path(tmp))
            with open(catalog, "a", encoding="utf-8") as fh:
                fh.write("{ this is not json\n")
            keep, drop, _ = classify(catalog)
            self.assertIn("{ this is not json", keep)
            self.assertEqual(len(drop), 2)


if __name__ == "__main__":
    unittest.main()
