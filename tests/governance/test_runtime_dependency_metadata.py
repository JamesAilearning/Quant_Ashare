"""Governance tests for runtime-adjacent dependency metadata."""

from __future__ import annotations

import re
import shlex
import unittest
from pathlib import Path

import yaml

try:                                    # Python 3.11+
    import tomllib
except ModuleNotFoundError:             # 3.10 —— `requires-python` 仍收 3.10
    import tomli as tomllib  # type: ignore[no-redef]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_PYPROJECT = PROJECT_ROOT / "pyproject.toml"
_WORKFLOW_DIR = PROJECT_ROOT / ".github" / "workflows"


#: 本地项目安装的**目标**：`.` 或 `.[组,组]`。认目标而不认 flag —— pip 支持
#: `-e` / `--editable`，也支持不带任何 flag 的 `pip install ".[research]"`。
#: 一个个去认 flag 是「字面形态」那条老路，每补一个下一个还在后面；而目标长
#: 什么样，与用了哪种写法无关（codex P2）。
_LOCAL_TARGET = re.compile(r"^\.(?:\[(?P<extras>[^\]]+)\])?$")

#: 命令分隔符。POSIX shell 在这些记号处结束一条命令——由语法定义，不是我在
#: 枚举写法。分组括号**不在其中**：`(` 在 `$(` 里是展开的一部分，不是边界。
_SEPARATORS = ("&&", "||", ";;", ";", "|", "&")


class UnlexableShell(ValueError):
    """这段 `run` 读不下去。

    **响亮**，不是当作「没有命令」。此前读不懂就静默返回空，于是守卫在那一段
    上是空的、且空得看不出来——codex 连着两轮命中的正是这个：相邻分隔符
    （`…qlib@sha&&pip install "numpy…"`，`shlex` 会切出 `…sha&&pip` 一个词）
    与行末续行（一条 `pip install -e` 用反斜杠续到下一行，`shlex` 直接抛）。
    """


def _split_commands(script: str) -> list[str]:
    """把一段 shell 脚本切成命令的**原文**片段。

    这里是本文件第三次、也是最后一次改判据的层次：从「正则找字面形态」到
    「`shlex` 切词后找分隔符 token」，再到**按词法扫描**。前两者都是用近似
    手段做词法分析——`shlex` 是**分词器**，它不知道命令在哪里结束，于是
    `a&&b` 这种没有空格的合法写法会被切成一个词，而续行让它当场抛异常。

    扫描认的是 POSIX 的词法构件，一个有限的闭集：单引号、双引号、反斜杠转义、
    注释、行末续行、here-document，以及上面那组分隔符。此外的一切都只是字符。
    读不下去就抛 `UnlexableShell`，不静默跳过。
    """
    segments: list[str] = []
    buffer: list[str] = []
    pending_heredocs: list[str] = []
    index = 0
    size = len(script)

    def flush() -> None:
        segment = "".join(buffer).strip()
        if segment:
            segments.append(segment)
        buffer.clear()

    def skip_heredoc_bodies(start: int) -> int:
        """从行尾出发，吞掉挂起的 here-document 正文。"""
        position = start
        for delimiter in pending_heredocs:
            while True:
                end = script.find("\n", position)
                line = script[position:end if end != -1 else size]
                if line.strip() == delimiter:
                    position = size if end == -1 else end + 1
                    break
                if end == -1:
                    raise UnlexableShell(f"here-document {delimiter!r} 没有收尾")
                position = end + 1
        pending_heredocs.clear()
        return position

    while index < size:
        char = script[index]
        if char == "'":
            end = script.find("'", index + 1)
            if end == -1:
                raise UnlexableShell("单引号没有闭合")
            buffer.append(script[index:end + 1])
            index = end + 1
            continue
        if char == '"':
            cursor = index + 1
            while cursor < size and script[cursor] != '"':
                cursor += 2 if script[cursor] == "\\" else 1
            if cursor >= size:
                raise UnlexableShell("双引号没有闭合")
            buffer.append(script[index:cursor + 1])
            index = cursor + 1
            continue
        if char == "\\":
            if index + 1 < size and script[index + 1] == "\n":
                index += 2                       # 行末续行：整个消失
                continue
            buffer.append(script[index:index + 2])
            index += 2
            continue
        if char == "#" and (not buffer or buffer[-1][-1:].isspace()):
            end = script.find("\n", index)
            index = size if end == -1 else end   # 注释到行尾为止，换行留给下面
            continue
        if script.startswith("<<", index):
            cursor = index + 2
            if script[cursor:cursor + 1] == "-":
                cursor += 1
            while script[cursor:cursor + 1] in (" ", "\t"):
                cursor += 1
            word = ""
            while cursor < size and not script[cursor].isspace():
                word += script[cursor]
                cursor += 1
            if not word:
                raise UnlexableShell("here-document 没有定界词")
            pending_heredocs.append(word.strip("'\""))
            index = cursor
            continue
        if char == "\n":
            flush()
            index = skip_heredoc_bodies(index + 1) if pending_heredocs else index + 1
            continue
        separator = next(
            (sep for sep in _SEPARATORS if script.startswith(sep, index)), None)
        if separator is not None:
            flush()
            index += len(separator)
            continue
        buffer.append(char)
        index += 1

    flush()
    if pending_heredocs:
        raise UnlexableShell(f"here-document {pending_heredocs[0]!r} 没有收尾")
    return segments


def _commands(script: str) -> list[list[str]]:
    """把一段 shell 切成若干条命令，每条是一串实参。

    词法器已经保证每个片段里的引号是配平的，`shlex` 在这里只负责去引号与
    分词——引号形态（单/双/不加）由它统一处理。
    """
    return [
        args for segment in _split_commands(script)
        if (args := shlex.split(segment, posix=True))
    ]


def _run_scripts(workflow: Path) -> list[str]:
    """workflow 里**真正会执行**的那些 `run` 块。

    按结构解析：YAML 取到 `steps[].run`，那才是会执行的东西；`name:` 之类的
    元数据字段天然被排除（codex 早前一条 P2）。注释、续行、引号都交给词法器。
    """
    document = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    scripts: list[str] = []
    for job in (document or {}).get("jobs", {}).values():
        for step in (job or {}).get("steps", []) or []:
            script = (step or {}).get("run")
            if isinstance(script, str):
                scripts.append(script)
    return scripts


def _workflow_commands(workflow: Path) -> list[list[str]]:
    """这个 workflow 里全部会执行的命令。"""
    return [
        command for script in _run_scripts(workflow)
        for command in _commands(script)
    ]


#: pip 自己的可执行体命名规则：`pip`、`pip3`、`pip3.12`。这是 pip 安装器写
#: 死的方案（`pip` + 解释器版本后缀），不是我在枚举拼写。
_PIP_EXECUTABLE = re.compile(r"^pip[\d.]*$")


def _is_pip_install(command: list[str]) -> bool:
    """这条命令是不是一次 pip 安装。

    认**可执行体**，不认 `pip` 一种拼写——`pip3 install ".[research]"` 同样
    合法，只匹配字面 `pip` 会把它整条排除在推导覆盖面之外，而现有 workflow
    让计数断言照样绿着（codex P2；与「认目标不认 flag」同一课）。路径前缀取
    basename，`python -m pip …` 由裸 `pip` token 覆盖。
    """
    for index, token in enumerate(command):
        name = token.replace("\\", "/").rsplit("/", 1)[-1]
        if _PIP_EXECUTABLE.match(name):
            return "install" in command[index + 1:]
    return False


def _pip_installs(workflow: Path) -> list[list[str]]:
    """这个 workflow 里所有 pip 安装命令。"""
    return [
        command for command in _workflow_commands(workflow)
        if _is_pip_install(command)
    ]


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

# 重述之所以必要：qlib 从这个固定 commit 安装，且**在项目之前**——它拿不到
# pyproject 的约束，所以每条装它的 workflow 都得自己把窗口写一遍。
# 「哪些 workflow 该有重述」因此可以推导，不必写死一个处数。
_QLIB_PIN = "git+https://github.com/microsoft/qlib.git@"

# pyproject 里承载 requirement 的三处（标准定的闭集）：
#   PEP 518 `[build-system].requires` · PEP 621 `[project].dependencies` ·
#   `[project.optional-dependencies].<组名>`
# 前两处 CI 每次安装都会解析，第三处按 workflow 点名的组。
_BUILD_REQUIRES = "requires"
_RUNTIME_DEPENDENCIES = "dependencies"

def _restated_package(token: str, declared: dict[str, str]) -> str | None:
    """这个实参是不是某个受钉包的版本约束？是则返回包名。

    只看「以包名开头、紧跟一个版本运算符」，避免把 `numpydoc` 之类同前缀的
    别的包误当成重述。
    """
    for package in declared:
        rest = token[len(package):]
        if token.startswith(package) and rest[:1] in ("<", ">", "=", "~", "!"):
            return package
    return None


def _local_target(command: list[str]) -> re.Match[str] | None:
    """这条 pip install 命令装的是不是**本地项目**；是则返回目标的匹配。"""
    for token in command:
        matched = _LOCAL_TARGET.match(token)
        if matched:
            return matched
    return None

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


def _optional_groups(text: str) -> dict[str, list[str]]:
    """`[project.optional-dependencies]` 下声明的各组。"""
    project = tomllib.loads(text).get("project", {})
    return project.get("optional-dependencies", {})


def _requirement_block(text: str, group: str) -> list[str]:
    """取出某个组里声明的依赖串。

    **解析 TOML**，不再在文本上找 `"…"`。此前那版只认双引号，而 TOML 的字符串
    字面量还有单引号形式（以及三引号）：一条 `'new-tool>=1'` 会整条从扫描里
    消失，而同组里别的双引号条目让「读到了东西」这个下限照样满足——守卫在那
    一条上是空的（codex P2）。

    在此之前它还栽过一次：正则圈块时注释里一个 `]` 让匹配提前收尾，
    `dependencies` 只读到 9 条里的 3 条。同一个位置两次栽在「拿文本近似结构」
    上，所以这次换成真解析——注释、引号形态、单行/多行列表就此都不再是特例。
    """
    if group == _BUILD_REQUIRES:
        found = tomllib.loads(text).get("build-system", {}).get(_BUILD_REQUIRES)
    elif group == _RUNTIME_DEPENDENCIES:
        found = tomllib.loads(text).get("project", {}).get(_RUNTIME_DEPENDENCIES)
    else:
        found = _optional_groups(text).get(group)
    assert found, f"组 {group!r} 一条依赖都没读到 —— 本守卫已失效"
    assert all(isinstance(item, str) for item in found), (
        f"组 {group!r} 里有非字符串条目 —— 本守卫已失效")
    return list(found)


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


class TheRequirementReaderParsesTOMLNotText(unittest.TestCase):
    """依赖表按 **TOML 解析**读，不在文本上找 `"…"`。

    这个位置栽过两次：注释里一个 `]` 让正则提前收尾（`dependencies` 9 条只读到
    3 条），以及只认双引号、单引号字面量整条消失。两次都是「拿文本近似结构」，
    而两次都躲过了整体计数下限——那正是**静默缩水**的形状。

    fixture 用完整合法的 TOML（带表头），不是片段：读法换成真解析之后，片段
    fixture 会把这些用例变成「测另一个东西」。
    """

    SNIPPET = (
        '[build-system]\n'
        'requires = ["setuptools>=68,<90", "wheel>=0.40,<1"]\n'
        '\n'
        '[project]\n'
        'dependencies = [\n'
        '  "alpha>=1,<2",\n'
        '  # 说明里提到 `.[dev,ui]` —— 这个 `]` 不该终结列表\n'
        '  "beta>=2,<3",\n'
        ']\n'
        '\n'
        '[project.optional-dependencies]\n'
        'ui = [\n'
        '  # 开头就是一句含 `.[x]` 的说明\n'
        "  'gamma>=3,<4',\n"          # TOML 的**单引号**字面量，同样合法
        ']\n'
    )

    def test_a_bracket_in_a_comment_does_not_end_the_list(self) -> None:
        self.assertEqual(
            ["alpha>=1,<2", "beta>=2,<3"],
            _requirement_block(self.SNIPPET, "dependencies"))

    def test_a_single_quoted_requirement_is_read(self) -> None:
        # 只认双引号的话这条整条消失，而「读到了东西」的下限由别组满足。
        self.assertEqual(["gamma>=3,<4"], _requirement_block(self.SNIPPET, "ui"))

    def test_a_single_line_list_is_read(self) -> None:
        self.assertEqual(
            ["setuptools>=68,<90", "wheel>=0.40,<1"],
            _requirement_block(self.SNIPPET, "requires"))

    def test_an_empty_read_is_loud(self) -> None:
        # 读到空说明结构变了 —— 那是「守卫失效」，不许当成「已覆盖」。
        with self.assertRaises(AssertionError):
            _requirement_block('[project]\ndependencies = []\n', "dependencies")

    def test_a_missing_group_is_loud(self) -> None:
        with self.assertRaises(AssertionError):
            _requirement_block('[project]\ndependencies = ["a<1"]\n', "nope")


class AShellLineIsSplitIntoCommands(unittest.TestCase):
    """一条物理行可以串起好几条命令，判据必须落在**那条**命令上。

    `pip install git+…qlib… && pip install "numpy>=1.24,<2.0"` 里，两个约束
    确实都在这一行；但 qlib 那次解析仍然是无约束的——正是本守卫要拦的那条路
    （codex P1）。分隔符取 POSIX shell 的**闭集**，不是我在枚举写法。
    """

    QLIB = "git+https://github.com/microsoft/qlib.git@abc123"

    def test_a_compound_line_becomes_separate_commands(self) -> None:
        got = _commands(f'pip install {self.QLIB} && pip install "numpy>=1.24,<2.0"')
        self.assertEqual(2, len(got), "整行被当成了一条命令")
        self.assertNotIn("numpy>=1.24,<2.0", got[0], "约束漏进了 qlib 那条命令")

    def test_every_posix_separator_splits(self) -> None:
        for sep in ("&&", "||", ";", "|", "&"):
            with self.subTest(分隔符=sep):
                self.assertEqual(2, len(_commands(f"pip install a {sep} pip install b")))

    def test_a_quoted_separator_is_not_a_separator(self) -> None:
        # 引号里的 `&&` 是实参的一部分，不是命令边界。
        self.assertEqual(1, len(_commands('echo "a && b"')))

    def test_separators_need_no_surrounding_whitespace(self) -> None:
        """`a&&b` 是合法写法，而 `shlex` 会把它切成**一个词**。

        分词器不知道命令在哪里结束——它切词。于是
        `pip install git+…@sha&&pip install "numpy…"` 会被当成一条命令，qlib
        那次无约束的解析就此通过检查（codex 第六轮 P1）。
        """
        got = _commands(f'pip install {self.QLIB}&&pip install "numpy>=1.24,<2.0"')
        self.assertEqual(2, len(got), "没有空格的 `&&` 没被认成命令边界")
        self.assertNotIn("numpy>=1.24,<2.0", got[0], "约束漏进了 qlib 那条命令")
        self.assertIn(self.QLIB, got[0], "qlib 参数被 `&&` 粘走了")

    def test_a_line_continuation_keeps_one_command(self) -> None:
        """行末反斜杠续行：命令跨了物理行，判据不许跟着断。

        按物理行切的话，续行那半没有 `pip install`，于是它点名的 extra 悄悄
        脱离覆盖面；而 `shlex` 遇到孤立的反斜杠会直接抛（codex 第六轮 P2）。
        """
        got = _commands('pip install -e \\\n  ".[research]"')
        self.assertEqual(1, len(got), "续行把一条命令切成了两条")
        self.assertEqual(["pip", "install", "-e", ".[research]"], got[0])

    def test_a_comment_is_not_a_command_but_a_hash_in_a_word_is_not_a_comment(
            self) -> None:
        self.assertEqual([], _commands('  # pip install -e ".[research]"'))
        # URL 片段里的 `#` 不是注释 —— 它前面不是空白。
        self.assertEqual(
            ["pip", "install", "git+https://x/y.git#egg=z"],
            _commands("pip install git+https://x/y.git#egg=z")[0])

    def test_a_heredoc_body_is_data_not_commands(self) -> None:
        """here-document 的正文是**数据**，不是命令。

        `regen-baseline.yml` 里就有一段 `python - <<\'EOF\'`。不认这个构件，
        正文里的 Python 引号会把整段读崩；而崩了就静默跳过的话，那个 step
        里真正的命令也一并从覆盖面里消失。
        """
        script = "\n".join([
            "python - <<\'EOF\'",
            "print(\"pip install -e nonsense\")",
            "EOF",
            'pip install -e ".[dev]"',
        ])
        got = _commands(script)
        self.assertEqual([["python", "-"], ["pip", "install", "-e", ".[dev]"]], got)

    def test_an_unbalanced_quote_is_loud(self) -> None:
        """读不下去要**响亮**。

        静默返回空，守卫在那一段上就是空的、且空得看不出来——那正是这两轮
        被连着命中的形状。
        """
        with self.assertRaises(UnlexableShell):
            _commands('pip install "unclosed')
        with self.assertRaises(UnlexableShell):
            _commands("python - <<EOF\nnever closed\n")


class EveryWorkflowIsFullyLexed(unittest.TestCase):
    """真实 workflow 必须**全部**读得下来。

    上一条把「读不懂」变成响亮的异常；这一条是它的另一半：仓库里现有的
    workflow 不许有读不下来的 `run` 块，否则那个 step 的命令从覆盖面里消失。
    """

    def test_every_run_block_lexes(self) -> None:
        for workflow in _workflows():
            with self.subTest(workflow=workflow.name):
                for script in _run_scripts(workflow):
                    _commands(script)

    def test_the_workflows_really_contain_commands(self) -> None:
        # 先证明前提：全都读成空的话，上一条会真空地绿着。
        total = sum(len(_workflow_commands(w)) for w in _workflows())
        self.assertGreaterEqual(total, 10, f"只读出 {total} 条命令 —— 覆盖面塌了")


class TheLocalProjectTargetIsRecognisedRegardlessOfSpelling(unittest.TestCase):
    """认**目标**，不认 flag。

    pip 支持 `-e` / `--editable`，也支持不带任何 flag 的
    `pip install ".[research]"`。一个个去认 flag 是「字面形态」那条老路，每补
    一个下一个还在后面；而目标长什么样与用了哪种写法无关（codex P2）。
    """

    def test_every_spelling_finds_the_same_extras(self) -> None:
        for line in ('pip install -e ".[dev,ui]"',
                     'pip install --editable ".[dev,ui]"',
                     "pip install '.[dev,ui]'",
                     "pip install .[dev,ui]"):
            with self.subTest(写法=line):
                matched = _local_target(_commands(line)[0])
                self.assertIsNotNone(matched, "这种写法没被认出来")
                assert matched is not None
                self.assertEqual("dev,ui", matched.group("extras"))

    def test_a_bare_dot_is_a_local_install_without_extras(self) -> None:
        matched = _local_target(_commands("pip install -e .")[0])
        self.assertIsNotNone(matched)
        assert matched is not None
        self.assertIsNone(matched.group("extras"))

    def test_a_third_party_install_is_not_a_local_target(self) -> None:
        self.assertIsNone(_local_target(_commands("pip install pytest")[0]))


class APipInstallIsRecognisedByItsExecutable(unittest.TestCase):
    """认**可执行体**，不认 `pip` 一种拼写。

    `pip3 install ".[research]"` 是合法写法；只匹配字面 `pip` 会把它整条排除
    在推导覆盖面之外——research 组那些无上界的依赖就此脱离守卫，而现有
    workflow 让计数断言照样绿着（codex P2）。与「认目标不认 flag」同一课，
    这次轮到可执行体：pip 的命名方案（`pip` + 版本后缀）是它安装器写死的，
    不是开放集合。
    """

    def test_every_executable_spelling_is_recognised(self) -> None:
        for line in ('pip install ".[research]"',
                     'pip3 install ".[research]"',
                     'pip3.12 install ".[research]"',
                     '/usr/local/bin/pip3 install ".[research]"',
                     'python -m pip install ".[research]"',
                     'python3 -m pip install ".[research]"'):
            with self.subTest(写法=line):
                self.assertTrue(_is_pip_install(_commands(line)[0]),
                                "这种 pip 写法没被认出来 —— 覆盖面在此静默缩水")

    def test_a_lookalike_executable_is_not_pip(self) -> None:
        # `pipx install` 装的是隔离环境里的应用，不是项目依赖。
        self.assertFalse(_is_pip_install(_commands("pipx install ruff")[0]))

    def test_install_must_follow_the_executable(self) -> None:
        self.assertFalse(_is_pip_install(_commands("pip download numpy")[0]))
        self.assertFalse(_is_pip_install(_commands("echo install pip")[0]))


class RuntimeDependencyMetadataTests(unittest.TestCase):
    def test_tushare_extra_is_declared_for_shipped_integration(self) -> None:
        requirements = _requirement_block(
            _PYPROJECT.read_text(encoding="utf-8"), "tushare")
        self.assertTrue(
            any(r.startswith("tushare>=") for r in requirements),
            f"tushare 组里没有 tushare 本身：{requirements}")


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
        found = []
        for workflow in _workflows():
            for command in _pip_installs(workflow):
                matched = _local_target(command)
                if matched is not None and matched.group("extras"):
                    found.append(matched.group("extras"))
        assert found, "workflow 里找不到 extras 安装行 —— 本守卫已失效"
        return {name.strip() for group in found for name in group.split(",")}

    @classmethod
    def _ci_installed_groups(cls) -> list[str]:
        """`pip install -e ".[dev,ui]"` 会解析的**全部**声明。

        这里不再一类一类地补。此前是 extra（首版）→ base 依赖（第二轮）→
        构建依赖（第三轮），每轮由 codex 指出又少了一类——**根子是我在枚举
        「CI 装了什么」，而不是从声明本身推**。

        pyproject 里能承载 requirement 的位置是**标准定的闭集**，只有三处：
        PEP 518 的 `[build-system].requires`、PEP 621 的
        `[project].dependencies`、以及 `[project.optional-dependencies]` 下的
        各组。前两处 CI 每次都会解析（`pip install -e .` 缺省走隔离构建，
        pip 独立解析 build-system.requires），extra 则按 workflow 实际点名的
        那些。
        """
        return [_BUILD_REQUIRES, _RUNTIME_DEPENDENCIES, *sorted(cls._ci_extras())]

    def test_the_extras_are_discovered_not_assumed(self) -> None:
        # 先证明推导本身没落空：一个空集合会让下面那条用例真空地绿着。
        extras = self._ci_extras()
        self.assertGreaterEqual(len(extras), 1)
        declared = _optional_groups(_PYPROJECT.read_text(encoding="utf-8"))
        for extra in sorted(extras):
            with self.subTest(extra=extra):
                self.assertIn(
                    extra, declared, f"CI 装了 pyproject 未声明的 extra: {extra}")

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
            # **逐条命令**看，不是「文件里有一处能解析就算过」：同一个 workflow
            # 里若还有第二条本地项目安装，「任一匹配」会让它整个溜过去，那组
            # extra 就悄悄脱离覆盖面（codex P2）。
            for command in _pip_installs(workflow):
                # 只看装**本地项目**的那些：`pip install pytest` 之类装的是
                # 第三方包，不点名任何 extra，与覆盖面无关。
                if not any(token.startswith(".") for token in command):
                    continue
                checked += 1
                if _local_target(command) is None:
                    unparsed.append(f"{workflow.name}: {' '.join(command)}")
        self.assertGreaterEqual(
            checked, 2, "一处本地项目安装都没找到 —— 本守卫已失效")
        self.assertEqual(
            [], unparsed,
            "这些本地项目安装读不出目标 —— 覆盖面在这里静默塌了")

    def test_every_dependency_ci_installs_has_an_upper_bound(self) -> None:
        groups = self._ci_installed_groups()
        # 对**自己实际用到的**覆盖面作证，而不是另设一条守卫去盯它：把这一行
        # 换回只走 extra（首版的样子），这里当场红。单独一条断言 `_ci_installed
        # _groups()` 的用例挡不住——它测的是那个方法，不是这里用了什么；而
        # 「覆盖面塌了」在当前数据上没有任何用例会红，因为每条 base 依赖现在
        # 都已有上界（实测变异如此）。
        self.assertLessEqual(
            {_BUILD_REQUIRES, _RUNTIME_DEPENDENCIES}, set(groups),
            "覆盖面漏了 pyproject 承载 requirement 的位置 —— "
            "`pip install -e \".[dev,ui]\"` 会解析 base 依赖，缺省的隔离构建"
            "还会独立解析 build-system.requires；漏掉哪一处，那一处就自由漂移"
            "（codex 两轮 P1，每轮少一类）")
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

        # 扫**全部** workflow，不按 qlib URL 过滤。按 URL 过滤时，一个为别的
        # 安装路径重述同一窗口的新 workflow 永远不会被检查——而「新 workflow
        # 重述了窗口就要自动被查」正是本 change 自己写下的场景（codex P2）。
        checked = 0
        for workflow in _workflows():
            for command in _workflow_commands(workflow):
                for token in command:
                    package = _restated_package(token, declared)
                    if package is None:
                        continue
                    checked += 1
                    # 比的是**整个实参**。此前从整行文本上用字符类匹配，遇到
                    # 类外字符就停、闭合引号又对不上，带环境标记的重述整条从
                    # 扫描里消失；而只认双引号又会漏掉单引号写法（codex 两条
                    # P2）。切成 token 之后，引号形态由 `shlex` 统一处理，比对
                    # 的是实参本身。
                    self.assertEqual(
                        declared[package], token,
                        f"{workflow.name} 重述的 {package} 窗口与 pyproject 不一致")
        self.assertGreaterEqual(
            checked, 1, "一处重述都没找到 —— 本守卫已失效")

    def test_the_qlib_install_command_carries_both_windows(self) -> None:
        """约束必须挂在**装 qlib 的那条命令上**，不是同一个文件的某处。

        上一条只问「这个 workflow 里有没有一处一致的重述」。qlib 那条命令若把
        numpy/scipy 参数弄丢，而同一文件别处（另一条命令、甚至一段注释）仍留着
        那两个串，它照样绿——而 `test.yml` 自己记着：没有约束的首次解析会装出
        numpy-2 era 的 scipy，随后的降级会打断 `qlib.backtest` 的 import 链
        （codex P1）。约束的作用点是那条命令，判据也必须落在那条命令上。
        """
        project = _PYPROJECT.read_text(encoding="utf-8")
        requirements = _requirement_block(project, "dependencies")
        declared = {}
        for package in ("numpy", "scipy"):
            matches = [r for r in requirements if r.startswith(package)]
            self.assertEqual(1, len(matches), f"{package} 约束应恰好一条")
            declared[package] = matches[0]

        # **逐条命令**，不是逐行：一条物理行可以用 `&&` 串起好几条命令，
        # 把整行当成一条来看，`pip install git+…qlib… && pip install "numpy…"`
        # 就会通过 —— 两个约束确实都在这一行里，但 qlib 那次解析仍然是无约束
        # 的，正是本守卫要拦的那条路（codex P1）。
        commands = [
            (workflow.name, command)
            for workflow in _workflows()
            for command in _workflow_commands(workflow)
            if any(token.startswith(_QLIB_PIN) for token in command)
        ]
        self.assertGreaterEqual(
            len(commands), 1, "没有在项目之前装 qlib 的命令 —— 本守卫已失效")
        for name, tokens in commands:
            for package, constraint in declared.items():
                with self.subTest(包=package, workflow=name):
                    self.assertIn(
                        constraint, tokens,
                        f"{name} 里装 qlib 的那条命令没带上 {package} 窗口 —— "
                        f"无约束的首次解析会装出不兼容的环境")

    def test_the_workflows_are_discovered_not_named(self) -> None:
        # 先证明发现本身没落空，也证明它确实看到了不止一个文件：
        # 只发现 test.yml 的话，上一条会在少一半的覆盖面上照样绿。
        names = {w.name for w in _workflows()}
        self.assertIn("test.yml", names)
        self.assertGreaterEqual(len(names), 2, "只发现一个 workflow —— 覆盖面塌了")


if __name__ == "__main__":
    unittest.main()
