"""Governance tests for runtime-adjacent dependency metadata."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_PYPROJECT = PROJECT_ROOT / "pyproject.toml"
_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "test.yml"

# ``pip install -e ".[dev,ui]"`` —— CI 到底装了哪些 extra，从 workflow 读，不手抄。
# 手抄的那张表挡不住「CI 以后多装一组，而守卫还盯着老的两组」。
_CI_EXTRAS = re.compile(r'pip install -e "\.\[([^\]]+)\]"')

# 上界的形式：`<` / `<=` / `==`（精确钉）/ `~=`（兼容版本，蕴含上界）。
#
# `\s*\d` 那一段是**承重**的，见 `TheBoundPatternRequiresAnActualVersion`：
# 它要求操作符后面紧跟数字，于是环境标记里的 `<` 骗不过它——PEP 508 的标记值
# 必须加引号（`python_version<'3.11'`），`<` 之后是引号而不是数字。
#
# 曾一度为此加过一个「先砍掉 `;` 之后的标记」的辅助函数；一条「先证明前提」的
# 用例证明那个洞根本不存在（正则本来就挡住了），于是删掉。留下的教训是：承重的
# 是这一位，而当前依赖表里没有带标记的条目，所以它只能**直接对着正则**测——
# 实测把 `\s*\d` 去掉，依赖表上的任何用例都抓不到。
_UPPER_BOUND = re.compile(r"(<=?|==|~=)\s*\d")


def _requirement_block(text: str, group: str) -> list[str]:
    """取出某个 extra 组里声明的依赖串。

    先按行砍掉 ``#`` 之后的注释再抓引号——否则注释里随手写的一个带引号的例子
    会被当成一条真依赖，让守卫对着不存在的东西较劲。
    """
    block = re.search(
        rf"^{re.escape(group)} = \[\n(.*?)^\]", text, re.MULTILINE | re.DOTALL)
    assert block is not None, f"pyproject 里找不到 extra 组 {group!r} —— 本守卫已失效"
    stripped = "\n".join(line.split("#", 1)[0] for line in block.group(1).splitlines())
    return re.findall(r'"([^"]+)"', stripped)


class TheBoundPatternRequiresAnActualVersion(unittest.TestCase):
    """直接对着 `_UPPER_BOUND` 测它的承重位。

    依赖表上的用例覆盖不到这里：放宽这个正则只会让它**更宽容**，而现有条目
    个个都带真上界，任何判定都不会改变（实测变异如此）。
    """

    def test_an_environment_marker_is_not_read_as_a_bound(self) -> None:
        # 一条真正无上界的依赖，字面上却带着一个 `<`。
        self.assertIsNone(_UPPER_BOUND.search("tomli; python_version<'3.11'"))

    def test_a_lower_bound_alone_is_not_an_upper_bound(self) -> None:
        self.assertIsNone(_UPPER_BOUND.search("pytest>=9.1"))

    def test_every_real_upper_bound_form_is_recognised(self) -> None:
        for requirement in ("pytest>=9.1,<9.2", "x<=2.0", "y==3.1.4", "z~=1.4"):
            with self.subTest(约束=requirement):
                self.assertIsNotNone(_UPPER_BOUND.search(requirement))


class RuntimeDependencyMetadataTests(unittest.TestCase):
    def test_tushare_extra_is_declared_for_shipped_integration(self) -> None:
        text = _PYPROJECT.read_text(encoding="utf-8")
        self.assertIn("tushare = [", text)
        self.assertIn('"tushare>=', text)


class EverythingCIInstallsIsBounded(unittest.TestCase):
    """CI 装的每一条依赖都必须有上界。

    2026-08-22 实证：`pytest>=7.4` 无上界，CI 装上了当天刚发布的 9.1.1（本机
    venv 停在 9.0.3），9.1 的 logging 插件行为变化让 PR #462 六腿全红——而那天
    的改动与失败点无关。工具链在**判**代码，它一漂移，红的就不是代码。

    覆盖面从 workflow 的安装行推导，不在这里手写一张组名表：CI 以后多装一组，
    守卫要跟着扩，而不是继续盯着老的两组绿着。
    """

    @staticmethod
    def _ci_extras() -> set[str]:
        found = _CI_EXTRAS.findall(_WORKFLOW.read_text(encoding="utf-8"))
        assert found, "workflow 里找不到 extras 安装行 —— 本守卫已失效"
        return {name.strip() for group in found for name in group.split(",")}

    def test_the_extras_are_discovered_not_assumed(self) -> None:
        # 先证明推导本身没落空：一个空集合会让下面那条用例真空地绿着。
        extras = self._ci_extras()
        self.assertGreaterEqual(len(extras), 1)
        text = _PYPROJECT.read_text(encoding="utf-8")
        for extra in sorted(extras):
            with self.subTest(extra=extra):
                # `^` 必须带 MULTILINE：不带的话它只匹配整个文件的开头，
                # 于是这条守卫会对每一个 extra 都红，且红得毫无信息。
                found = re.search(rf"^{re.escape(extra)} = \[", text, re.MULTILINE)
                self.assertIsNotNone(found, f"CI 装了 pyproject 未声明的 extra: {extra}")

    def test_every_dependency_ci_installs_has_an_upper_bound(self) -> None:
        text = _PYPROJECT.read_text(encoding="utf-8")
        unbounded = []
        checked = 0
        for extra in sorted(self._ci_extras()):
            for requirement in _requirement_block(text, extra):
                checked += 1
                if not _UPPER_BOUND.search(requirement):
                    unbounded.append(f"{extra}: {requirement}")
        self.assertGreaterEqual(checked, 4, "一条依赖都没读到 —— 本守卫已失效")
        self.assertEqual(
            [], unbounded,
            "这些依赖没有上界；CI 会在它们发新版的那天变红，与当天的 PR 无关")

    def test_the_code_judging_tools_are_bounded_at_the_minor(self) -> None:
        """跑测试 / 查类型 / 挑 lint 的工具，大版本上界不够。

        打破 #462 的是一次**小版本**跳变（pytest 9.0 → 9.1）。`<10` 那种大版本
        上界对它毫无作用——新检查与行为变化本来就多在小版本引入。
        """
        text = _PYPROJECT.read_text(encoding="utf-8")
        declared = {
            req.split(">=")[0].split("<")[0].split("==")[0].strip(): req
            for req in _requirement_block(text, "dev")
        }
        # 这三个名字是手写的，因为「判代码 vs 被代码使用」这条线无法从文件结构
        # 推出来——它是判断，不是数据。pytest-cov 不在其中：`--cov` 只影响覆盖率
        # 报告，不参与判定成败，大版本上界够用。
        for tool in ("pytest", "ruff", "mypy"):
            with self.subTest(工具=tool):
                requirement = declared.get(tool)
                self.assertIsNotNone(requirement, f"dev 组里没有 {tool}")
                assert requirement is not None
                upper = re.search(r"<\s*(\d+)\.(\d+)", requirement)
                self.assertIsNotNone(
                    upper, f"{tool} 的上界不是小版本粒度：{requirement}")


class ThePinnedNumpyWindowIsStatedOnce(unittest.TestCase):
    """numpy / scipy 的窗口在 workflow 里被**又抄了一遍**。

    pyproject 的注释自己写着「also inlined in .github/workflows/test.yml」——
    因为 qlib 要在项目安装之前先装，拿不到 pyproject 的约束。抄是必要的，
    零一致性测试不是：两处分头漂移时，CI 装的与项目声明的就不是同一个窗口，
    而 REGEN-2 的确定性锚正建立在这个窗口上。
    """

    def test_the_workflow_installs_exactly_what_pyproject_declares(self) -> None:
        project = _PYPROJECT.read_text(encoding="utf-8")
        workflow = _WORKFLOW.read_text(encoding="utf-8")
        # 只在 `dependencies = [...]` 块里找：整份文件里搜的话，注释或别的 extra
        # 里一个带引号的例子就可能先命中，守卫从此比对的是一个不相干的字符串。
        requirements = _requirement_block(project, "dependencies")
        checked = 0
        for package in ("numpy", "scipy"):
            with self.subTest(包=package):
                matches = [r for r in requirements if r.startswith(package)]
                self.assertEqual(
                    1, len(matches), f"dependencies 里 {package} 约束应恰好一条")
                declared = re.match(rf"({re.escape(package)}[><=,.\d]+)$", matches[0])
                self.assertIsNotNone(declared, f"{package} 约束形状意外：{matches[0]}")
                assert declared is not None
                checked += 1
                self.assertIn(
                    f'"{declared.group(1)}"', workflow,
                    f"workflow 装的 {package} 窗口与 pyproject 声明的不一致")
        self.assertEqual(2, checked)


if __name__ == "__main__":
    unittest.main()
