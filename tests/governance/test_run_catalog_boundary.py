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


_LF = chr(10)
_CRLF = chr(13) + chr(10)


def _write_catalog(path: Path, lines: list[str], newline: str = _LF) -> None:
    """按**字节**写夹具，不走平台的换行翻译。

    生产索引实测是 CRLF（写入侧文本模式在 Windows 上翻译过），而工具现在按字节
    忠实地读写。夹具若还依赖平台翻译，用例在两个平台上比的就不是同一份内容。
    """
    body = newline.join(lines) + newline
    path.write_bytes(body.encode("utf-8"))


def _append_catalog_line(path: Path, line: str, newline: str = _LF) -> None:
    with open(path, "ab") as fh:
        fh.write((line + newline).encode("utf-8"))


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


class OutOfTreeRunsAreNotCatalogued(_SandboxedDefaults):
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

    def test_a_leading_space_is_a_real_directory_name_not_noise(self) -> None:
        # 前导空格是合法的文件名字符（POSIX 如此；本机 Windows 实测也能造出名为
        # `" output"` 的目录）。引擎把 `config.output_dir` 原样交给 `Path`，所以
        # `" output/wf/r1"` 的产物真在 `<root>/ output/wf/r1` —— 树外。
        #
        # 判据若先 strip 再解析，看的就不是生产者用的那个串：它会去看
        # `<root>/output/wf/r1` 并放行，而原串照旧存进索引，控制台随后指向一个
        # 毫不相干的、树内的目录。
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tree = root / "output"
            (tree / "runs").mkdir(parents=True)
            (tree / "wf" / "r1").mkdir(parents=True)      # 树内的「无辜」目录
            (root / " output" / "wf" / "r1").mkdir(parents=True)  # 产物真正的家
            catalog = tree / "runs" / "_index.jsonl"
            with self._sandbox(tree, catalog), _chdir(root):
                append_run_record(build_record(
                    engine="walk_forward", status="ok",
                    output_dir=" output/wf/r1"))
            self.assertEqual(
                _line_count(catalog), 0,
                "判据 strip 掉了前导空格，于是拿树内那个无辜目录当成了它的产物")

    def test_trailing_whitespace_makes_the_row_unaddressable(self) -> None:
        """两端带空白的路径指认不了一个目录 —— 那段空白算不算名字，取决于谁在读。

        这条与前导空格那条不同：`output/wf/r1 ` **仍然在树内**，边界判据放行，
        所以要靠单独一条规则挡住。实测两侧的分歧（本机 Windows 11）：

        - `os.path.normpath("output/wf/r1 ")` 保留尾空格
        - 控制台 `anchored_run_dir` 会 strip 掉它，于是去开隔壁的 `output/wf/r1`
        - 而 Windows 建目录时**直接把尾空格吃掉**：`mkdir("r1 ")` 建出来的是
          `r1`，记录从一开始就名不副实（POSIX 上则确实是另一个目录）

        无论哪种，这一行都无法无歧义地指认这次运行。产物一动不动，少的只是一条
        索引记录（codex #453）。
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tree = root / "output"
            (tree / "runs").mkdir(parents=True)
            (tree / "wf" / "r1").mkdir(parents=True)
            catalog = tree / "runs" / "_index.jsonl"
            with self._sandbox(tree, catalog), _chdir(root):
                append_run_record(build_record(
                    engine="walk_forward", status="ok",
                    output_dir="output/wf/r1 "))
            self.assertEqual(
                _line_count(catalog), 0,
                "收下了一行控制台会开错门的记录")

    def test_a_whitespace_only_output_dir_is_refused_as_missing(self) -> None:
        # 反面：全空白仍然算「没给」，不该被当成一个名字叫空格的目录。
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tree = root / "output"
            (tree / "runs").mkdir(parents=True)
            catalog = tree / "runs" / "_index.jsonl"
            with self._sandbox(tree, catalog), _chdir(root):
                append_run_record(build_record(
                    engine="pipeline", status="ok", output_dir="   "))
            self.assertEqual(_line_count(catalog), 0)

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


class EveryAcceptedRowIsListableByTheConsole(_SandboxedDefaults):
    """**端到端不变式**：写入侧接受一行 ⟹ 读侧列得出它。

    这是本 change 反复栽跟头那个病的收口。前面几轮我一直在**手推**「哪些拼写
    算数」——推一条，codex 找出第七种拼写；再推一条，又一种。这次不推了：测试
    直接问真正的读侧（`job_io.run_dir_is_inspectable`）认不认。

    为什么会漏：写入侧判包含时**两侧都 resolve**，于是「第三种拼写」（再套一层
    联接、8.3 短名）也算在树内；而读侧是**逐行热路径**，只能纯词法，只认树自身
    和树 resolve 后这两种拼写，第三种一律搁置。两边判据不同 ⟹ 索引里会出现
    「控制台永远列不出来的行」，正是本 change 要挡的污染。
    """

    def _assert_writer_and_reader_agree(
        self, root: Path, tree: Path, catalog: Path, output_dir: str,
        actual_dir: Path,
    ) -> None:
        """写入侧收下这一行 ⟹ 读侧**开出来的正是产物所在的那个目录**。

        这条断言曾经只问「读侧列不列得出」，而那不够锐：一行尾部带空格的
        `output/wf/r1 ` 读侧照样「列得出」，只是它 strip 之后开的是隔壁的
        `output/wf/r1` —— 列得出、开错门（codex #453）。所以现在问的是**身份**，
        不是可见性。``actual_dir`` 由各用例按建目录时的事实传进来，是地面真相，
        不是对写入侧规则的转述。
        """
        from web.operator_ui import _path_guard, job_io

        with self._sandbox(tree, catalog):
            append_run_record(build_record(
                engine="walk_forward", status="ok", output_dir=output_dir))
        self.assertEqual(_line_count(catalog), 1, f"这条被误拒了：{output_dir!r}")

        stored = str(_rows(catalog)[0]["output_dir"])
        # `job_io` 在导入时**按值**取走了 `PROJECT_ROOT`，所以两份都要搬进沙盒；
        # 只搬 `_path_guard` 那一份的话，相对行会锚回真实仓库根，用例会以一个
        # 假的「读侧列不出来」失败。生产里两者本来就是同一个仓库根。
        with mock.patch.object(_path_guard, "PROJECT_ROOT", root), \
                mock.patch.object(job_io, "PROJECT_ROOT", root), \
                mock.patch.object(_path_guard, "_ALLOWED_ROOTS", None), \
                mock.patch.object(job_io, "_ROOT_KEYS_CACHE", None):
            listable = job_io.run_dir_is_inspectable(stored)
            opened = job_io.anchored_run_dir(stored)
        self.assertTrue(
            listable,
            f"写入侧收了 {output_dir!r}、存成 {stored!r}，读侧却列不出来 —— "
            "索引里多了一行控制台永远打不开的记录",
        )
        self.assertEqual(
            opened.resolve(), actual_dir.resolve(),
            f"写入侧收了 {output_dir!r}、存成 {stored!r}，读侧却会去开 "
            f"{opened} —— 而产物其实在 {actual_dir}",
        )

    def test_the_ordinary_spellings_round_trip(self) -> None:
        # 先钉住常见情形：不变式对它们成立是底线，不然上面那条别名用例可能只是
        # 因为「什么都存绝对路径」而碰巧通过。
        for label, make in (
            ("绝对路径", lambda tree: str(tree / "wf" / "r1")),
            ("相对仓库根", lambda tree: "output/wf/r1"),
        ):
            with self.subTest(拼写=label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                tree = root / "output"
                (tree / "runs").mkdir(parents=True)
                (tree / "wf" / "r1").mkdir(parents=True)
                with _chdir(root):
                    self._assert_writer_and_reader_agree(
                        root, tree, tree / "runs" / "_index.jsonl", make(tree),
                        tree / "wf" / "r1")

    def test_an_alias_spelling_is_stored_in_a_form_the_reader_accepts(self) -> None:
        # 第三种拼写：产物目录经联接指向 output 树内。
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tree = root / "output"
            (tree / "runs").mkdir(parents=True)
            (tree / "wf" / "r1").mkdir(parents=True)
            alias = root / "alias"
            if not _link(alias, tree):
                self.skipTest("这个环境造不出目录链接（需要权限）")
            self._assert_writer_and_reader_agree(
                root, tree, tree / "runs" / "_index.jsonl",
                str(alias / "wf" / "r1"), alias / "wf" / "r1")

    def test_a_linked_tree_recorded_by_its_target_round_trips(self) -> None:
        # 镜像情形：**output 树自己**是联接，而运行记的是解析后的目标路径。
        # 读侧的两个根键正是「树自身的写法」和「树 resolve 后的写法」，所以这
        # 一侧也必须成立 —— 否则一台把 output/ 挂到别的盘的机器上，每条记录都
        # 会变成控制台打不开的行。
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = root / "real_output"
            (real / "runs").mkdir(parents=True)
            (real / "wf" / "r1").mkdir(parents=True)
            tree = root / "output"          # 联接，指向 real_output
            if not _link(tree, real):
                self.skipTest("这个环境造不出目录链接（需要权限）")
            self._assert_writer_and_reader_agree(
                root, tree, tree / "runs" / "_index.jsonl",
                str(real / "wf" / "r1"), real / "wf" / "r1")


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
        _write_catalog(catalog, [json.dumps(r) for r in rows])
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
            _append_catalog_line(catalog, json.dumps(
                {"engine": "walk_forward", "output_dir": "output/wf/keep"}))
            result = classify(catalog, tree, tree.parent)
            self.assertEqual(result.verified_in_tree, 2, "相对路径的合法行被判成了残骸")

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
            result = classify(catalog, alias, root)
            self.assertEqual(
                (result.verified_in_tree, len(result.dropped)), (1, 0),
                "--tree 走别名时，合法的相对行被判成了残骸")

    def test_unparseable_lines_are_kept_not_dropped(self) -> None:
        # 看不懂的行不动 —— 那是别人的数据，判据只针对能证明是残骸的那些。
        from scripts.prune_run_catalog import classify

        with tempfile.TemporaryDirectory() as tmp:
            catalog, tree = self._catalog_with(Path(tmp))
            _append_catalog_line(catalog, "{ this is not json")
            result = classify(catalog, tree, tree.parent)
            self.assertIn("{ this is not json" + _LF, result.retained)
            self.assertEqual(len(result.dropped), 2)

    def test_valid_json_that_is_not_a_record_is_kept_not_crashed_on(self) -> None:
        # `null` / 数组都是合法 JSON。以前这里直接 `.get()`，于是连只报数模式
        # 都会抛 AttributeError 中断。
        from scripts.prune_run_catalog import classify, main

        with tempfile.TemporaryDirectory() as tmp:
            catalog, tree = self._catalog_with(Path(tmp))
            _append_catalog_line(catalog, "null")
            _append_catalog_line(catalog, "[1, 2]")
            result = classify(catalog, tree, tree.parent)
            self.assertIn("null" + _LF, result.retained)
            self.assertIn("[1, 2]" + _LF, result.retained)
            self.assertEqual(len(result.dropped), 2)
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
            _append_catalog_line(catalog, json.dumps(
                {"engine": "pipeline", "output_dir": chr(0) + "foo"}))
            result = classify(catalog, tree, tree.parent)
            self.assertEqual(result.verified_in_tree, 1)
            self.assertEqual(len(result.dropped), 3, "畸形行既没被算进去，也没让扫描中断")

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
            _write_catalog(earlier, ["先前那次的留证"])
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

    def test_retained_unknown_rows_are_not_counted_as_verified(self) -> None:
        # 「保留」有两种：产物确实验证过在树内，和「看不懂所以不动」。混作一谈
        # 的话，一份全是 `null` 的索引会报成「100% 在树内」—— 而这份报告正是
        # 操作人按下 --prune 的依据。
        from scripts.prune_run_catalog import classify

        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "output"
            (tree / "runs").mkdir(parents=True)
            catalog = tree / "runs" / "_index.jsonl"
            catalog.write_text("null\nnull\n", encoding="utf-8")
            result = classify(catalog, tree, tree.parent)
            self.assertEqual(len(result.retained), 2)
            self.assertEqual(
                result.verified_in_tree, 0,
                "一条产物路径都没验证过，却报成「在树内」")
            self.assertEqual(result.unclassified, 2)

    def test_blank_lines_survive_a_prune(self) -> None:
        # 空行既不在保留集也不在丢弃集的话，一次 --prune 就把它从活索引里抹掉，
        # 而旁车留证里没有它 —— 「移除的行留得住」这条承诺就破了。
        from scripts.prune_run_catalog import main

        with tempfile.TemporaryDirectory() as tmp:
            catalog, tree = self._catalog_with(Path(tmp))
            body = catalog.read_text(encoding="utf-8").splitlines()
            _write_catalog(catalog, [body[0], "", *body[1:]])
            self.assertEqual(main(self._argv(catalog, tree, "--prune")), 0)
            after = catalog.read_text(encoding="utf-8").splitlines()
            self.assertIn("", after, "空行被静默删掉了，旁车里也没有它")

    def test_every_file_the_prune_leaves_behind_carries_the_catalogs_mode(self) -> None:
        # 上一轮我给暂存件抄了权限、**漏了旁车** —— 而旁车装的正是被移除的记录，
        # 内容与索引同等敏感。漏掉一个入口的根因是「靠手写清单」，所以这条守卫
        # 两侧都从结构推导：期望集来自文件系统实际多出来的文件，实际集来自真正
        # 发生的 chmod 调用。谁再加一个新文件而忘了抄权限，这里就红。
        #
        # 断言 chmod 调用而不是断言最终 mode：Windows 的 chmod 只管只读位，
        # 断言最终 mode 在本机会**恒真**——那等于没测。
        import scripts.prune_run_catalog as tool

        with tempfile.TemporaryDirectory() as tmp:
            catalog, tree = self._catalog_with(Path(tmp))
            before = {p.name for p in catalog.parent.iterdir()}
            chmodded: set[str] = set()
            real_chmod = os.chmod

            def recording(path: Any, mode: int, *a: Any, **kw: Any) -> None:
                chmodded.add(os.path.basename(str(path)))
                real_chmod(path, mode, *a, **kw)

            with mock.patch("os.chmod", recording):
                self.assertEqual(tool.main(self._argv(catalog, tree, "--prune")), 0)

            left_behind = {
                p.name for p in catalog.parent.iterdir()
            } - before - {catalog.name}
            self.assertTrue(left_behind, "这次清理什么文件都没留下，用例是空的")
            self.assertLessEqual(
                left_behind, chmodded,
                f"这些新文件没按索引的权限造：{sorted(left_behind - chmodded)}",
            )

    def test_no_file_receives_content_before_it_carries_the_mode(self) -> None:
        # 上一轮我给两个建文件点各写了一套，于是顺序分叉：旁车是「建→chmod→
        # 写」，暂存件是「写→稍后 chmod」。那段窗口里同机其他用户读得到保留下
        # 来的记录；进程若在补 chmod 前中断，宽权限的副本还会永久留在盘上。
        #
        # 这条守卫两侧都从结构推导：**谁**被写入内容，来自实际观察到的
        # `Path.write_bytes` 调用（不是手写清单）；**顺序**来自事件序列。
        # 以后谁再加一个带内容的文件而顺序写反，这里就红。
        import scripts.prune_run_catalog as tool

        with tempfile.TemporaryDirectory() as tmp:
            catalog, tree = self._catalog_with(Path(tmp))
            events: list[tuple[str, str]] = []
            real_chmod = os.chmod
            real_write_bytes = Path.write_bytes

            def rec_chmod(path: Any, mode: int, *a: Any, **kw: Any) -> None:
                events.append(("chmod", os.path.basename(str(path))))
                real_chmod(path, mode, *a, **kw)

            def rec_write(self: Path, data: bytes, *a: Any, **kw: Any) -> int:
                events.append(("write", self.name))
                return real_write_bytes(self, data, *a, **kw)

            with mock.patch("os.chmod", rec_chmod), \
                    mock.patch.object(Path, "write_bytes", rec_write):
                self.assertEqual(tool.main(self._argv(catalog, tree, "--prune")), 0)

            written = [name for op, name in events if op == "write"]
            self.assertTrue(written, "这次清理一个文件都没写，用例是空的")
            for name in dict.fromkeys(written):
                first_write = events.index(("write", name))
                self.assertIn(
                    ("chmod", name), events[:first_write],
                    f"{name} 在设好权限之前就被写入了内容 —— "
                    "那段窗口里它是按 umask 敞开的",
                )

    def test_a_failed_allocation_leaves_nothing_behind(self) -> None:
        # 旁车名占满、而暂存件名还空着时：旁车分配失败、暂存件已经建出来了。
        # 失败分支若只收拾旁车，盘上就会留下一个空的 `_index.jsonl.tmp-*`，
        # 而我们刚刚宣称「什么都没动」（codex #453）。
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
            before_text = catalog.read_text(encoding="utf-8")
            stamp = "20260820-000000"
            for serial in range(1, 100):
                suffix = "" if serial == 1 else f"-{serial}"
                _write_catalog(
                    catalog.with_name(
                        f"{catalog.stem}.pruned-{stamp}{suffix}.jsonl"),
                    ["占位"])

            with mock.patch.object(tool, "datetime", _FixedClock):
                self.assertEqual(tool.main(self._argv(catalog, tree, "--prune")), 6)

            self.assertEqual(catalog.read_text(encoding="utf-8"), before_text)
            leftovers = sorted(
                p.name for p in catalog.parent.glob(f"{catalog.name}.tmp-*"))
            self.assertEqual(
                leftovers, [], f"宣称什么都没动，却留下了 {leftovers}")

    def test_a_unicode_line_separator_does_not_split_a_record(self) -> None:
        """`str.splitlines()` 会在 U+2028 / U+0085 这类字符上断行 —— 而
        `json.dumps(ensure_ascii=False)` 会把它们**原样**吐进记录里（实测确认）。

        于是一条合法记录被切成几段畸形碎片；一旦触发 `--prune`，碎片会被用真
        换行重写回去，**永久毁掉那条记录**（codex #453）。
        """
        from scripts.prune_run_catalog import classify

        with tempfile.TemporaryDirectory() as tmp:
            catalog, tree = self._catalog_with(Path(tmp))
            row = json.dumps(
                {"engine": "pipeline", "error": "a" + chr(0x2028) + "b",
                 "output_dir": str(tree / "wf" / "keep")}, ensure_ascii=False)
            self.assertGreater(
                len(row.splitlines()), 1, "这一行没有触发 splitlines 的断行，用例是空的")
            _append_catalog_line(catalog, row)

            result = classify(catalog, tree, tree.parent)
            self.assertIn(row + _LF, result.retained, "一条合法记录被切碎了")
            self.assertEqual(
                result.verified_in_tree, 2,
                "被切碎的碎片进了「看不懂」桶，而不是算作一条树内记录")

    def test_an_undecodable_byte_does_not_abort_the_scan(self) -> None:
        # 一个非法 UTF-8 字节就让 `read_text` 整个抛 UnicodeDecodeError，连只报数
        # 模式都跑不完 —— 而本工具的职责恰恰是容忍畸形/外来数据。
        from scripts.prune_run_catalog import classify

        with tempfile.TemporaryDirectory() as tmp:
            catalog, tree = self._catalog_with(Path(tmp))
            with open(catalog, "ab") as fh:
                fh.write(bytes([0xFF, 0xFE]) + _LF.encode("utf-8"))
            result = classify(catalog, tree, tree.parent)
            self.assertEqual(result.verified_in_tree, 1)
            self.assertEqual(result.unclassified, 1, "坏字节那行没被当成「看不懂」留住")

    def test_a_crlf_catalog_keeps_its_bytes(self) -> None:
        # 操作人的真实索引实测 3560 行**全是 CRLF**（写入侧文本模式在 Windows 上
        # 翻译过）。读时归一化、写时再译回的老做法，会把一份混合行尾的文件悄悄
        # 改写成单一行尾；按字节读写才还原得回去。
        from scripts.prune_run_catalog import main

        with tempfile.TemporaryDirectory() as tmp:
            catalog, tree = self._catalog_with(Path(tmp))
            rows = catalog.read_bytes().decode("utf-8").split(_LF)[:-1]
            _write_catalog(catalog, rows, newline=_CRLF)
            keep_row = rows[0].encode("utf-8")

            self.assertEqual(main(self._argv(catalog, tree, "--prune")), 0)

            after = catalog.read_bytes()
            self.assertEqual(after, keep_row + _CRLF.encode("utf-8"),
                             "重写把 CRLF 改掉了")
            sidecar = next(catalog.parent.glob("_index.pruned-*.jsonl"))
            self.assertIn(_CRLF.encode("utf-8"), sidecar.read_bytes(),
                          "留证里的行尾也被改掉了")

    def test_the_tool_keeps_rows_the_writer_would_now_refuse(self) -> None:
        """两端带空白的**历史行**：写入侧从此不收，清理工具却要留着。

        这处不对称是刻意的，不是遗漏（codex 建议把规则并进共用判据，我不并）：

        - 写入侧的规则管的是「**从今往后**我们愿不愿意记这一行」
        - 工具的判据管的是「**控制台能不能打开它**」——读侧对齐之后（#460）
          `output/wf/r1 ` 是能打开的，把它判成残骸删掉才是错的

        共用的是**包含判据**，那条两边确实是同一个函数；这条是另一回事。
        """
        from scripts.prune_run_catalog import classify

        with tempfile.TemporaryDirectory() as tmp:
            catalog, tree = self._catalog_with(Path(tmp))
            _append_catalog_line(catalog, json.dumps(
                {"engine": "walk_forward",
                 "output_dir": str(tree / "wf" / "keep") + " "}))
            result = classify(catalog, tree, tree.parent)
            self.assertEqual(
                result.verified_in_tree, 2,
                "工具把一条控制台打得开的历史行判成了残骸")
            self.assertEqual(len(result.dropped), 2)

    def test_an_unterminated_last_row_keeps_every_byte_where_it_was(self) -> None:
        """行尾属于**行**，不是整个文件的一个开关。

        末行没有行尾、而且**末行正好被丢弃**时，一个「文件末尾有没有换行」的
        文件级标志两头都会错：保留下来的那批被抹掉最后一个换行，而旁车里那条
        无行尾的记录反被补上一个 —— 「原样留证」当场失真（codex #453）。
        """
        from scripts.prune_run_catalog import main

        with tempfile.TemporaryDirectory() as tmp:
            catalog, tree = self._catalog_with(Path(tmp))
            keep_row = json.dumps(
                {"engine": "walk_forward", "output_dir": str(tree / "wf" / "keep")})
            drop_row = json.dumps(
                {"engine": "pipeline", "output_dir": "C:/Temp/tmpdead"})
            # 末行**没有**行尾，且它是要被丢弃的那条。
            catalog.write_bytes(
                (keep_row + _LF + drop_row).encode("utf-8"))

            self.assertEqual(main(self._argv(catalog, tree, "--prune")), 0)

            self.assertEqual(
                catalog.read_bytes(), (keep_row + _LF).encode("utf-8"),
                "保留下来那行的行尾被动过了")
            sidecar = next(catalog.parent.glob("_index.pruned-*.jsonl"))
            self.assertEqual(
                sidecar.read_bytes(), drop_row.encode("utf-8"),
                "「原样留证」给无行尾的那条补了个换行")

    def test_a_zero_byte_catalog_has_zero_rows(self) -> None:
        # `"".split(chr(10))` 会给出 `[""]`，于是一份刚建出来或被截断的零字节
        # 索引被报成「总行数 1」—— 而这份报告正是操作人按下 --prune 的依据。
        from scripts.prune_run_catalog import classify

        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "output"
            (tree / "runs").mkdir(parents=True)
            catalog = tree / "runs" / "_index.jsonl"
            catalog.write_bytes(b"")
            result = classify(catalog, tree, tree.parent)
            self.assertEqual(
                (len(result.retained), len(result.dropped), result.unclassified),
                (0, 0, 0), "零字节索引被算出了行")

    def test_an_undecodable_byte_inside_valid_json_stays_unclassified(self) -> None:
        """坏字节落在 JSON 字符串**内部**时，语法仍然合法。

        `surrogateescape` 把它藏成孤立代理项，`json.loads` 照样成功 —— 于是一条
        读不懂的记录会被按 `output_dir` 判成「树内」或「可清理」，后者会被**真的
        删掉**。实测（修前）：树内 2 / 未验证 0 / 可清理 1，而应当是 1 / 2 / 0
        （codex #453）。

        这与规格里「无法解码的行应保持未分类」直接冲突 —— 之前只在坏字节正好
        破坏了 JSON 语法时才做到。
        """
        from scripts.prune_run_catalog import classify

        with tempfile.TemporaryDirectory() as tmp:
            catalog, tree = self._catalog_with(Path(tmp))
            inside = str(tree / "wf" / "keep").replace(chr(92), "/")
            with open(catalog, "ab") as fh:
                for target in (inside, "C:/Temp/tmpdead"):
                    fh.write(b'{"engine":"' + bytes([0xFF]) + b'","output_dir":"'
                             + target.encode("utf-8") + b'"}' + _LF.encode("utf-8"))
            result = classify(catalog, tree, tree.parent)
            self.assertEqual(
                (result.verified_in_tree, result.unclassified, len(result.dropped)),
                (1, 2, 2),
                "含坏字节的行被按 output_dir 分了类 —— 树外那条会被真的删掉")

    def test_pruning_never_alters_a_byte_it_did_not_remove(self) -> None:
        """**整条缝的往返性质**，不是又一个点用例。

        最近三轮的问题（行尾归一化、文件级换行标志、孤立代理项）都出在这条读写
        缝上，而每次都是靠一个新的点用例才发现。所以这里改成盯**性质**：对一组
        对抗性索引，`--prune` 之后

            活索引 == 原字节里删掉「被移除的那些行」，一个字节不多不少
            旁车   == 那些被移除的行，原样

        这条性质把行尾、编码、末行有无换行、空行全都一次覆盖住；上面那些点用例
        任何一条失守，它都会红。
        """
        from scripts.prune_run_catalog import classify, main

        u2028_row = json.dumps(
            {"engine": "pipeline", "note": "a" + chr(0x2028) + "b",
             "output_dir": "PLACEHOLDER"}, ensure_ascii=False)
        for newline in (_LF, _CRLF):
            for terminated in (True, False):
                with self.subTest(行尾=repr(newline), 末行有行尾=terminated), \
                        tempfile.TemporaryDirectory() as tmp:
                    catalog, tree = self._catalog_with(Path(tmp))
                    inside = str(tree / "wf" / "keep").replace(chr(92), "/")
                    rows = [
                        json.dumps({"engine": "walk_forward",
                                    "output_dir": inside}),
                        "",                                   # 空行
                        "null",                               # 合法但非记录
                        "{ 读不懂的一行",                       # 坏 JSON
                        u2028_row.replace("PLACEHOLDER", inside),
                        json.dumps({"engine": "pipeline",
                                    "output_dir": "C:/Temp/tmpdead"}),
                    ]
                    eol = newline.encode("utf-8")
                    parts = [row.encode("utf-8") + eol for row in rows]
                    # 末行是「坏字节藏在合法 JSON 里」那条，只能按字节拼；
                    # `terminated` 控制的正是**它**有没有行尾。
                    parts.append(b'{"engine":"' + bytes([0xFF])
                                 + b'","output_dir":"C:/Temp/tmpdead2"}'
                                 + (eol if terminated else b""))
                    original = b"".join(parts)
                    catalog.write_bytes(original)

                    result = classify(catalog, tree, tree.parent)
                    self.assertEqual(main(self._argv(catalog, tree, "--prune")), 0)

                    # 独立推导的一条：读不懂的行**不许被移除**。
                    # 上面那条「没移除的字节一个不改」是拿 `result.dropped` 反推
                    # 期望的，工具若错删一行，期望会跟着一起动 —— 性质就空转了
                    # （实测：变异「不再检测孤立代理项」时它确实抓不到）。
                    for row in result.dropped:
                        try:
                            row.encode("utf-8")
                        except UnicodeEncodeError:
                            self.fail(f"移除了一条含未解码字节的行：{row[:40]!r}")

                    expected = original
                    for row in result.dropped:
                        raw = row.encode("utf-8", errors="surrogateescape")
                        self.assertIn(raw, expected, "被移除的行不在原文件里")
                        expected = expected.replace(raw, b"", 1)
                    self.assertEqual(
                        catalog.read_bytes(), expected,
                        "重写改动了它并没有移除的字节")

                    sidecar = next(catalog.parent.glob("_index.pruned-*.jsonl"))
                    self.assertEqual(
                        sidecar.read_bytes(),
                        "".join(result.dropped).encode(
                            "utf-8", errors="surrogateescape"),
                        "留证不是被移除那些行的原样")

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
                    _append_catalog_line(path, json.dumps(
                        {"engine": "walk_forward",
                         "output_dir": str(tree / "wf" / "keep")}))
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
