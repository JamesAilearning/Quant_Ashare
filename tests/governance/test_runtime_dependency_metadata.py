"""Governance tests for runtime-adjacent dependency metadata."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_PYPROJECT = PROJECT_ROOT / "pyproject.toml"
_WORKFLOW_DIR = PROJECT_ROOT / ".github" / "workflows"


def _workflows() -> list[Path]:
    """全部 workflow —— 不点名任何一个。

    首版只读 `test.yml`，而 `regen-baseline.yml` 独立地把同一组 numpy/scipy
    窗口又抄了一遍（codex P1）。守卫身上于是有着它自己要防的那个洞：窗口在
    pyproject 与 test.yml 上一起改、漏掉重生成工作流时，这里照样绿——而
    REGEN-2 的确定性锚正是在那条工作流上生成的。
    """
    found = sorted(_WORKFLOW_DIR.glob("*.yml")) + sorted(_WORKFLOW_DIR.glob("*.yaml"))
    assert found, "找不到任何 workflow —— 本守卫已失效"
    return found

# ``pip install -e ".[dev,ui]"`` —— CI 到底装了哪些 extra，从 workflow 读，不手抄。
# 手抄的那张表挡不住「CI 以后多装一组，而守卫还盯着老的两组」。
_CI_EXTRAS = re.compile(r'pip install -e "\.\[([^\]]+)\]"')

# 重述之所以必要：qlib 从这个固定 commit 安装，且**在项目之前**——它拿不到
# pyproject 的约束，所以每条装它的 workflow 都得自己把窗口写一遍。
# 「哪些 workflow 该有重述」因此可以推导，不必写死一个处数。
_QLIB_PIN = "git+https://github.com/microsoft/qlib.git@"

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
        found = [
            group
            for workflow in _workflows()
            for group in _CI_EXTRAS.findall(workflow.read_text(encoding="utf-8"))
        ]
        assert found, "workflow 里找不到 extras 安装行 —— 本守卫已失效"
        return {name.strip() for group in found for name in group.split(",")}

    @classmethod
    def _ci_installed_groups(cls) -> list[str]:
        """`pip install -e ".[dev,ui]"` 装的是 **base 依赖 + 这些 extra**。

        首版只遍历 extra，于是 base 里那六条无上界的依赖（pyarrow / optuna
        …）照样可以漂移，而守卫全绿——与本 change 那条「CI 装的每一条都要有
        上界」的规范正文直接矛盾（codex P1）。
        """
        return ["dependencies", *sorted(cls._ci_extras())]

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

    def test_every_editable_install_line_is_parseable(self) -> None:
        """每一条 editable 安装行都必须解析得出 extras。

        扫单个 workflow 时，把安装行写成正则读不出的形式会让集合变空、断言
        当场炸掉。扫全部 workflow 之后这层保护没了：另一个 workflow 的安装行
        仍能解析，于是**这一个的覆盖面静默缩水**，谁都不会红（实测变异）。
        所以直接钉：凡是做 editable 安装的 workflow，它那行都必须读得出来。
        """
        unparsed = []
        checked = 0
        for workflow in _workflows():
            text = workflow.read_text(encoding="utf-8")
            # **逐处**看，不是「文件里有一处能解析就算过」：同一个 workflow 里
            # 若还有第二条 editable 安装（比如单引号写法装了另一组 extra），
            # 「任一匹配」会让它整个溜过去，那组 extra 就悄悄脱离覆盖面
            # （codex P2）。
            for occurrence in re.finditer(r"pip install -e\s+\S+", text):
                checked += 1
                if not _CI_EXTRAS.search(occurrence.group(0)):
                    unparsed.append(f"{workflow.name}: {occurrence.group(0)}")
        self.assertGreaterEqual(
            checked, 2, "一处 editable 安装都没找到 —— 本守卫已失效")
        self.assertEqual(
            [], unparsed,
            "这些 editable 安装行读不出 extras —— 覆盖面在这里静默塌了")

    def test_every_dependency_ci_installs_has_an_upper_bound(self) -> None:
        groups = self._ci_installed_groups()
        # 对**自己实际用到的**覆盖面作证，而不是另设一条守卫去盯它：把这一行
        # 换回只走 extra（首版的样子），这里当场红。单独一条断言 `_ci_installed
        # _groups()` 的用例挡不住——它测的是那个方法，不是这里用了什么；而
        # 「覆盖面塌了」在当前数据上没有任何用例会红，因为每条 base 依赖现在
        # 都已有上界（实测变异如此）。
        self.assertIn(
            "dependencies", groups,
            "覆盖面里没有 base 依赖 —— `pip install -e \".[dev,ui]\"` 会把它们"
            "一并装上，漏掉就等于放它们自由漂移（codex P1）")
        text = _PYPROJECT.read_text(encoding="utf-8")
        unbounded = []
        checked = 0
        for group in groups:
            for requirement in _requirement_block(text, group):
                checked += 1
                if not _UPPER_BOUND.search(requirement):
                    unbounded.append(f"{group}: {requirement}")
        self.assertGreaterEqual(checked, 4, "一条依赖都没读到 —— 本守卫已失效")
        self.assertEqual(
            [], unbounded,
            "这些依赖没有上界；CI 会在它们发新版的那天变红，与当天的 PR 无关")

    def test_every_code_judging_tool_is_capped_at_its_next_minor(self) -> None:
        """`dev` 组里的每一条都必须钉到**下一个小版本**。

        打破 #462 的是一次小版本跳变（pytest 9.0 → 9.1）。`<10` 那种大版本上界
        对它毫无作用——新检查与行为变化本来就多在小版本引入。而只要求「上界带
        两段数字」同样不够：`<10.0` 是一个穿着小版本外衣的大版本上界（codex
        P2），所以要求上界正好等于下界的下一个小版本。

        名单不再手写。`dev` 组按定义装的就是判代码的那批工具（跑测试及其插件、
        查类型、挑 lint），所以覆盖面由**结构**给出：谁在 dev 里，谁就受这条
        约束。首版手写了三个名字并把 `pytest-cov` 排除在外，既与本 change 自己
        的规范矛盾，也让 7.2 可以照样漂进来（codex P1）——而手写名单的问题正在
        于此：漏掉一个，守卫在那一条上就是空的，且空得看不出来。
        """
        text = _PYPROJECT.read_text(encoding="utf-8")
        requirements = _requirement_block(text, "dev")
        self.assertGreaterEqual(
            len(requirements), 4, "dev 组读不到 —— 本守卫已失效")
        for requirement in requirements:
            with self.subTest(约束=requirement):
                floor = re.search(r">=\s*(\d+)\.(\d+)", requirement)
                ceiling = re.search(r"<\s*(\d+)\.(\d+)", requirement)
                self.assertIsNotNone(
                    floor, f"没有小版本粒度的下界：{requirement}")
                self.assertIsNotNone(
                    ceiling, f"上界不是小版本粒度：{requirement}")
                assert floor is not None and ceiling is not None
                self.assertEqual(
                    (int(floor.group(1)), int(floor.group(2)) + 1),
                    (int(ceiling.group(1)), int(ceiling.group(2))),
                    f"上界不是下界的下一个小版本：{requirement}")


class EveryRestatementOfAPinnedWindowMatches(unittest.TestCase):
    """numpy / scipy 的窗口在 workflow 里被**又抄了一遍**——而且不止一处。

    pyproject 的注释只提到「also inlined in .github/workflows/test.yml」，但
    `regen-baseline.yml` 独立地抄了同一行（codex P1）。抄是必要的（qlib 要在
    项目安装之前先装，拿不到 pyproject 的约束），零一致性测试不是：两处分头
    漂移时，CI 装的与项目声明的就不是同一个窗口——而 REGEN-2 的确定性锚正是在
    那条重生成工作流上产生的。

    所以这里**扫遍全部 workflow**，不点名任何一个：谁重述了这个包的版本窗口，
    谁就必须与 pyproject 逐字一致。点名一个文件的守卫，挡不住「以后新增的第三
    个 workflow 又抄一遍」。
    """

    def test_every_workflow_restatement_is_byte_identical(self) -> None:
        project = _PYPROJECT.read_text(encoding="utf-8")
        # 权威值只在 `dependencies = [...]` 块里取：整份文件里搜的话，注释或
        # 别的 extra 里一个带引号的例子可能先命中，守卫从此比对的是别的字符串。
        requirements = _requirement_block(project, "dependencies")
        declared: dict[str, str] = {}
        for package in ("numpy", "scipy"):
            matches = [r for r in requirements if r.startswith(package)]
            self.assertEqual(
                1, len(matches), f"dependencies 里 {package} 约束应恰好一条")
            declared[package] = matches[0]

        # 期望处数从**为什么要重述**推出来，不写魔法数：qlib 在项目之前安装，
        # 拿不到 pyproject 的约束，所以凡是装 qlib 的 workflow 都必须自己把这
        # 两个窗口重述一次——恰好一次。写死一个 4，第三个 workflow 出现时它就
        # 兜不住了（少一处仍 ≥ 4，静默放行）。
        pinning = [
            w for w in _workflows()
            if _QLIB_PIN in w.read_text(encoding="utf-8")
        ]
        self.assertGreaterEqual(
            len(pinning), 1, "没有 workflow 在项目之前装 qlib —— 本守卫已失效")
        for workflow in pinning:
            text = workflow.read_text(encoding="utf-8")
            for package, constraint in declared.items():
                with self.subTest(workflow=workflow.name, 包=package):
                    # `[^"]*` 一直吃到闭合引号，比的是**整串**。用
                    # `[><=,.\d]+` 那种字符类会在遇到类外字符时停下，闭合引号
                    # 又匹配不上，于是整条重述**从扫描里消失**——带环境标记的
                    # `"numpy>=1.24,<2.0; python_version < '3.12'"` 就是这样溜
                    # 过去的：它换了解析结果，守卫却一声不吭（codex P2）。
                    restated = re.findall(
                        rf'"({re.escape(package)}[^"]*)"', text)
                    self.assertEqual(
                        1, len(restated),
                        f"{workflow.name} 在项目之前装 qlib，却没有恰好一处 "
                        f"{package} 窗口")
                    self.assertEqual(
                        constraint, restated[0],
                        f"{workflow.name} 重述的 {package} 窗口与 pyproject 不一致")

    def test_the_workflows_are_discovered_not_named(self) -> None:
        # 先证明发现本身没落空，也证明它确实看到了不止一个文件：
        # 只发现 test.yml 的话，上一条会在少一半的覆盖面上照样绿。
        names = {w.name for w in _workflows()}
        self.assertIn("test.yml", names)
        self.assertGreaterEqual(len(names), 2, "只发现一个 workflow —— 覆盖面塌了")


if __name__ == "__main__":
    unittest.main()
