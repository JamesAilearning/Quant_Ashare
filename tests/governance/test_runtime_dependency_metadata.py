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
#: 枚举写法。未加引号的 `(`/`)` 同样是命令语法（子 shell 分组、命令替换的
#: 边界），在词法器主循环里单独处理——`(pip install …)` 合法，把括号焊在
#: token 上会让 `_is_pip_install` 与 `_local_target` 都认不出它（codex P2）。
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
    pending_heredocs: list[tuple[str, bool]] = []
    index = 0
    size = len(script)

    def flush() -> None:
        segment = "".join(buffer).strip()
        if segment:
            segments.append(segment)
        buffer.clear()

    def _heredoc_delimiter(word: str) -> tuple[str, bool]:
        """对定界词做 POSIX 引号移除：返回（生效定界词, 是否有引号）。

        `word.strip("'\"")` 只剥两端：部分引号的 `E'O'F` 剩下 `E'O'F`，真正
        的收尾行 `EOF` 永远配不上、workflow 被误判未收尾；反斜杠形态（`<<` 后接单个反斜杠的定界词）
        也是引号（正文因此是字面量），只认引号字符会把它当活性正文误拒
        （codex 两条 P2）。构件与词法器主循环同一套：单引号、双引号、反斜杠。
        """
        effective: list[str] = []
        quoted = False
        i = 0
        while i < len(word):
            ch = word[i]
            if ch == "'":
                end = word.find("'", i + 1)
                if end == -1:
                    raise UnlexableShell("heredoc 定界词的单引号没有闭合")
                effective.append(word[i + 1:end])
                quoted = True
                i = end + 1
            elif ch == '"':
                end = word.find('"', i + 1)
                if end == -1:
                    raise UnlexableShell("heredoc 定界词的双引号没有闭合")
                effective.append(word[i + 1:end])
                quoted = True
                i = end + 1
            elif ch == "\\":
                if i + 1 >= len(word):
                    raise UnlexableShell("heredoc 定界词以孤立反斜杠结尾")
                effective.append(word[i + 1])
                quoted = True
                i += 2
            else:
                effective.append(ch)
                i += 1
        return "".join(effective), quoted

    def skip_heredoc_bodies(start: int) -> int:
        """从行尾出发，吞掉挂起的 here-document 正文。

        定界词**没加引号**时正文是活性的：POSIX 会在正文里做命令替换，
        `cat <<EOF` 的正文里一句 `$(pip install …)` 是真的会执行的安装——
        无条件丢弃正文就把它静默吞了（codex P2）。加了引号（`<<'EOF'`）的
        正文是字面量，照旧跳过；未加引号的正文若含 `$(`/反引号，与其他
        活性内容同一处置：响亮拒读。`$VAR` 这类变量展开执行不了命令，不拦。
        """
        position = start
        for delimiter, quoted, dashed in pending_heredocs:
            while True:
                end = script.find("\n", position)
                line = script[position:end if end != -1 else size]
                # 终止行**精确**等于定界词——`  EOF` 在 shell 眼里是正文，
                # `strip()` 把它当收尾会让其后的真正文被读成命令（codex P2）。
                # 唯一的例外由语法自己给出：`<<-` 允许剥前导 **tab**（只有
                # tab，不含空格）。
                terminator = line.lstrip("\t") if dashed else line
                if terminator == delimiter:
                    position = size if end == -1 else end + 1
                    break
                if not quoted and ("$(" in line or "`" in line):
                    raise UnlexableShell(
                        "未加引号的 here-document 正文里有命令替换——正文是"
                        "活性的，本词法器不建模；请给定界词加引号或拆出来")
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
            pieces = ['"']
            while cursor < size and script[cursor] != '"':
                # 双引号里 `$(`/反引号是**活性**的——shell 会执行其中的命令。
                # 本词法器不建模命令替换（那是 shell 全文法的无底洞：嵌套
                # 引号、嵌套替换、参数展开），但也**绝不静默吞掉**：吞掉就是
                # 覆盖面在这一段上空得看不出来（codex P2）。响亮拒读，由
                # 「现有 workflow 全部可词法化」那条守卫保证仓库不用这种写法。
                if script.startswith("$(", cursor) or script[cursor] == "`":
                    raise UnlexableShell(
                        "双引号内有命令替换——本词法器不建模引号内活性内容，"
                        "请把替换移到引号外")
                if script[cursor] == "\\":
                    # 双引号**内**的行末续行同样在分词前就被 shell 删掉：
                    # `".[dev,\` 换行 `ui]"` 执行时是 `.[dev,ui]`。保留原样
                    # 会让比对拿着一个执行时不存在的反斜杠换行（codex P2）。
                    if script[cursor + 1:cursor + 2] == "\n":
                        cursor += 2
                        continue
                    pieces.append(script[cursor:cursor + 2])
                    cursor += 2
                    continue
                pieces.append(script[cursor])
                cursor += 1
            if cursor >= size:
                raise UnlexableShell("双引号没有闭合")
            pieces.append('"')
            buffer.append("".join(pieces))
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
            dashed = script[cursor:cursor + 1] == "-"
            if dashed:
                cursor += 1
            while script[cursor:cursor + 1] in (" ", "\t"):
                cursor += 1
            word = ""
            while cursor < size and not script[cursor].isspace():
                word += script[cursor]
                cursor += 1
            if not word:
                raise UnlexableShell("here-document 没有定界词")
            pending_heredocs.append(_heredoc_delimiter(word) + (dashed,))
            index = cursor
            continue
        if char == "\n":
            flush()
            index = skip_heredoc_bodies(index + 1) if pending_heredocs else index + 1
            continue
        if char == "`":
            # 反引号是遗留形态的命令替换，「活性内容」一类：不建模也不静默。
            raise UnlexableShell("反引号命令替换未建模——请拆成独立命令")
        if script.startswith("$(", index):
            # 命令替换与外层命令**纠缠**：`echo $(date) pip install …` 里
            # 只有 `date` 和外层 `echo` 会执行，替换的输出成为 echo 的实参
            # ——把两个括号当无条件边界，会**发明**一条 shell 根本不跑的
            # `pip install`，让治理对 CI 没装的依赖变红（codex P2）。曾把
            # `$()` 内容当独立命令拆分，此判正是那样发明出来的。建模嵌套
            # 重挂（外层词跨过替换重新拼接）是 shell 全文法的无底洞，与
            # 引号内替换同一处置：响亮拒读。
            raise UnlexableShell("命令替换未建模——请拆成独立命令")
        if char in "()":
            # 未加引号的括号是命令边界（子 shell 分组）。`(cmd)` 之后同一条
            # 命令不能再接词（POSIX 语法），所以在这里断开是安全的；命令
            # 替换 `$(` 已在上面被响亮拒掉，不会走到这条。
            flush()
            index += 1
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
        raise UnlexableShell(
            f"here-document {pending_heredocs[0][0]!r} 没有收尾")
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
#: 死的方案（`pip` + 解释器版本后缀），不是我在枚举拼写。python 同一方案。
_PIP_EXECUTABLE = re.compile(r"^pip[\d.]*$")
_PYTHON_EXECUTABLE = re.compile(r"^python[\d.]*$")
#: POSIX 简单命令允许的前缀：`VAR=value` 赋值。可执行体在它们**之后**。
_ASSIGNMENT_PREFIX = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*=")
#: POSIX 保留字（POSIX.1-2017 §2.4 定义的**闭集**）。出现在命令开头时引导
#: 复合命令，真正的可执行体在它们之后——`if pip install …; then` 的可执行体
#: 是 `pip`，不是 `if`（codex P2）。
_RESERVED_WORDS = frozenset({
    "!", "{", "}", "case", "do", "done", "elif", "else", "esac", "fi",
    "for", "if", "in", "then", "until", "while",
})


def _is_pip_install(command: list[str]) -> bool:
    """这条命令是不是一次 pip 安装。

    认**可执行体的位置**，不是在实参里扫 `pip` 这个串——否则一条
    `echo pip install -e ".[research]"`（比如打日志的示例）会被当成真安装，
    research 组那些刻意无上界的依赖就把治理打红，而 CI 根本没装它们
    （codex P2）。可执行体 = 跳过 POSIX 赋值前缀后的第一个 token：basename
    是 pip 命名方案（`pip`/`pip3`/`pip3.12`，路径前缀取 basename）即 pip
    本体；是 python 且紧跟 `-m pip` 即模块形态。`pipx` 形近不算。
    """
    tokens = list(command)
    # 语法顺序：保留字引导复合命令在先，赋值前缀在后，然后才是可执行体。
    while tokens and tokens[0] in _RESERVED_WORDS:
        tokens.pop(0)
    assignments: list[str] = []
    while tokens and _ASSIGNMENT_PREFIX.match(tokens[0]):
        assignments.append(tokens.pop(0))
    if not tokens:
        return False
    name = tokens[0].replace("\\", "/").rsplit("/", 1)[-1]
    if _PYTHON_EXECUTABLE.match(name) and tokens[1:3] == ["-m", "pip"]:
        rest = tokens[3:]
    elif _PIP_EXECUTABLE.match(name):
        rest = tokens[1:]
    else:
        return False
    # pip 的每个选项都能经 `PIP_<OPTION>` 环境变量注入——`PIP_DRY_RUN=1
    # pip install …` 让 pip 什么都不装，而命令实参上看不出来（codex P1）。
    # 分辨哪些 PIP_* 是「行为改变的」需要 pip 的选项表——pip 的、随版本变，
    # 不该由本守卫维护。与选项歧义同一处置：**多义即响亮**，药方是把配置
    # 写成显式 flag（flag 受 --dry-run / 文件引入等既有判据管辖）。
    pip_environment = [a for a in assignments if a.startswith("PIP_")]
    if pip_environment:
        raise AmbiguousPipCommand(
            f"pip 行为经环境赋值注入：{pip_environment} —— 命令文本推导"
            f"看不见它改变了什么；请改用显式 flag")
    # `install` 必须是**子命令**（`pip <command> [options]`），不是任意位置
    # 的实参：`pip --help install ".[research]"` 只打印帮助、装不了任何东西，
    # 在实参里搜 `install` 会把 research 记进覆盖面，治理对 CI 没装的依赖
    # 变红（codex P2）。三条判据，都不需要 pip 的选项表：
    #  1. `-h`/`--help` 出现在任何位置 → 帮助优先，pip 不会安装（argparse
    #     通例，不是 pip 特有）；
    #  2. 子命令 = 可执行体后第一个不带 `-` 的 token；
    #  3. 子命令**之前**还有别的选项 token → 有的全局选项接值（如 --log
    #     PATH），值与子命令无从区分——多义即响亮，与本地目标那条同一处置。
    if any(token in ("-h", "--help") for token in rest):
        return False
    # `--dry-run` 由 pip 自己定义为「Don't actually install anything」——
    # 带着它的 `pip install <窗口> <qlib pin>` 能同时骗过「是安装」与「带齐
    # 窗口」两道守卫，而 qlib 实际缺席，`importorskip` 让 CI 静默绿
    # （codex P1）。子命令对了仍不等于装了。
    if "--dry-run" in rest:
        return False
    subcommand_at = next(
        (i for i, token in enumerate(rest) if not token.startswith("-")), None)
    if subcommand_at is None:
        return False
    # 子命令之前的选项：`--opt=value` 连写自含值、无歧义；**裸**选项才可能
    # 把下一个 token 吃成值（如 `--log PATH`），让子命令位置无从确定。
    bare_options = [t for t in rest[:subcommand_at] if "=" not in t]
    if bare_options:
        raise AmbiguousPipCommand(
            f"pip 子命令之前有裸选项 token {bare_options}——接值的全局选项"
            f"让子命令位置无从确定；请把选项挪到子命令之后或用 --opt=value"
            f" 连写")
    return rest[subcommand_at] == "install"


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
#: 钉住的必须是**不可变**引用：完整 40 位十六进制 commit SHA。`@main` 这类
#: 可动引用让 CI 的 qlib 代码在两次运行之间漂移，「固定 commit」名存实亡
#: （codex P2）。
_IMMUTABLE_SHA = re.compile(r"^[0-9a-f]{40}$")
#: 从**文件**引入安装内容的 pip 选项（pip 文档定义的闭集：`-r/--requirement`
#: 装文件里列的每一个包，`-c/--constraint` 用文件约束解析）。命令文本推导
#: 看不见文件内容——静默跳过就是覆盖面在文件里的每一行上空着（codex P2）。
_FILE_SOURCED_OPTIONS = ("-r", "--requirement", "-c", "--constraint")

# pyproject 里承载 requirement 的三处（标准定的闭集）：
#   PEP 518 `[build-system].requires` · PEP 621 `[project].dependencies` ·
#   `[project.optional-dependencies].<组名>`
# 前两处 CI 每次安装都会解析，第三处按 workflow 点名的组。
_BUILD_REQUIRES = "requires"
_RUNTIME_DEPENDENCIES = "dependencies"

def _canonical_name(name: str) -> str:
    """PEP 503：包名比较大小写不敏感，`-`/`_`/`.` 相互等价。"""
    return re.sub(r"[-_.]", "-", name).lower()


def _restated_package(token: str, declared: dict[str, str]) -> str | None:
    """这个实参是不是某个受钉包的版本约束？是则返回包名。

    只看「以包名开头、紧跟一个版本运算符」，避免把 `numpydoc` 之类同前缀的
    别的包误当成重述。**名字按 PEP 503 归一化比较**：`NumPy>=1.24,<2.1` 也是
    numpy 窗口的重述，大小写敏感的 `startswith` 会让它整条躲过一致性检查
    （codex P2）。随后的逐字一致断言仍比对原 token——于是非规范拼写的重述
    会被要求改成与 pyproject 完全一致的写法，而不是被放过。
    """
    for package in declared:
        rest = token[len(package):]
        if _canonical_name(token[:len(package)]) != _canonical_name(package):
            continue
        # `numpy[feature]>=…` 也是 numpy 窗口的重述——PEP 508 允许 extras 段
        # 紧跟包名。名字后是 `[` 时跳过整段 extras 再看版本运算符，否则带着
        # 分叉窗口的重述整条躲过逐字一致检查（codex P2）。
        if rest.startswith("["):
            close = rest.find("]")
            if close == -1:
                continue
            rest = rest[close + 1:]
        if rest[:1] in ("<", ">", "=", "~", "!"):
            return package
    return None


class AmbiguousPipCommand(ValueError):
    """一条 pip 命令里有不止一个长得像本地目标的 token。

    pip 的部分选项接**路径值**（`--find-links .`），于是
    `pip install --find-links . ".[research]"` 里 `.` 与 `.[research]` 都
    匹配目标形状——取第一个会把选项值当目标、把真目标（连同它点名的
    extras）静默丢掉（codex P2）。分辨二者需要 pip 的选项表——那是 pip
    的、随版本变的集合，不是本守卫该维护的。所以**响亮**：用 `--opt=value`
    的连写形态消歧后，值不再是独立 token，歧义就地消失。
    """


#: PEP 508 风格的 requirement 串外形：包名开头，后面跟可选 extras 与
#: 版本约束。URL / 路径不匹配它——选项值多为这两类，天然被排除。
_REQUIREMENT_SHAPE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*(\[[^\]]+\])?([<>=~!;].*)?$")


def _direct_install_targets(command: list[str]) -> tuple[int, list[str]]:
    """这条 pip install 命令的直接安装目标：（requirement 计数, 问题列表）。

    `python -m pip install --upgrade pip` 直接装了一个不经 pyproject 的包——
    pip 自己。它无上界时，一次 pip 新版就能改变/打断全部 CI 安装，而治理
    照样绿，与「CI 装的每条依赖都有上界」的不变式矛盾（codex P1）。所以
    直接目标同受上界约束；未钉 commit 的源码安装与无法归类的 token 一并
    响亮（后者多半是接值裸选项的值——用 --opt=value 连写即消歧）。
    """
    try:
        after = command[command.index("install") + 1:]
    except ValueError:
        return 0, []
    seen = 0
    problems: list[str] = []
    for token in after:
        if token.startswith("-"):
            # 从文件引入安装内容的选项不许静默跳过：pip 会安装/约束文件里的
            # 每一行，而命令文本推导看不见它们（codex P2）。裸形态与 `=`
            # 连写形态都拦。
            if token in _FILE_SOURCED_OPTIONS or any(
                token.startswith(f"{option}=")
                for option in _FILE_SOURCED_OPTIONS
            ):
                problems.append(
                    f"从文件引入安装内容：{token} —— 覆盖面推导看不见文件"
                    f"内容；请把 requirement 直接写在命令里")
            continue
        if _LOCAL_TARGET.match(token):
            continue                      # 本地项目：extras 机制管
        if token.startswith(_QLIB_PIN):
            # 前缀对了还要看**后缀**：`@main` 是可动引用，CI 的 qlib 代码
            # 会在两次运行之间漂移（codex P2）。
            if not _IMMUTABLE_SHA.match(token[len(_QLIB_PIN):]):
                problems.append(
                    f"qlib 引用不是不可变 commit SHA：{token}")
            continue
        if token.startswith("git+"):
            problems.append(f"未钉 commit 的源码安装：{token}")
            continue
        if _REQUIREMENT_SHAPE.match(token):
            seen += 1
            if not _UPPER_BOUND.search(token):
                problems.append(f"无上界：{token}")
            continue
        problems.append(
            f"无法归类的安装目标：{token}（若是选项值请用 --opt=value 连写）")
    return seen, problems


def _qlib_pin_installs(commands: list[list[str]]) -> list[list[str]]:
    """真正**安装** qlib 的命令。

    只按「URL 出现在命令里」认，一条 `echo git+…qlib… "numpy…" "scipy…"`
    也会当成安装——若真安装被误删只剩这句回显，两条断言照样绿，而
    `pytest.importorskip("qlib")` 会让 qlib 侧 CI 静默跳过（codex P1）。
    候选必须先过 `_is_pip_install`。
    """
    return [
        command for command in commands
        if any(token.startswith(_QLIB_PIN) for token in command)
        and _is_pip_install(command)
    ]


def _local_target_candidates(command: list[str]) -> list[str]:
    """可能承载本地目标的 token 值：裸 token + `--editable=` 连写的值。

    `pip install --editable=.[research]` 合法，而目标藏在 `=` 后面——只看
    裸 token，这条 extra 连同它的无上界依赖静默脱离覆盖面，可解析性检查也
    因「没有以 `.` 开头的 token」跳过它（codex P2）。
    """
    values = []
    for token in command:
        if token.startswith("--editable="):
            values.append(token[len("--editable="):])
        elif not token.startswith("-"):
            values.append(token)
    return values


def _local_target(command: list[str]) -> re.Match[str] | None:
    """这条 pip install 命令装的是不是**本地项目**；是则返回目标的匹配。"""
    matches = [
        m for value in _local_target_candidates(command)
        if (m := _LOCAL_TARGET.match(value))
    ]
    if len(matches) > 1:
        raise AmbiguousPipCommand(
            f"命令里有 {len(matches)} 个本地目标形状的 token"
            f"（{[m.group(0) for m in matches]}）——选项值与安装目标无法"
            f"区分；请把带路径值的选项写成 --opt=value 连写形态")
    return matches[0] if matches else None

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

    def test_a_continuation_inside_quotes_vanishes_before_tokenizing(
            self) -> None:
        """双引号**内**的 `\\<换行>` 也是续行——shell 在分词前删掉它。

        保留原样，`pip install ".[dev,\\<换行>ui]"` 的目标读出来带着一个执行
        时不存在的反斜杠换行：要么发明一个未声明的 extra，要么把执行时逐字
        一致的重述拒掉（codex P2）。
        """
        got = _commands('pip install ".[dev,\\\nui]"')
        self.assertEqual([["pip", "install", ".[dev,ui]"]], got)
        matched = _local_target(got[0])
        assert matched is not None
        self.assertEqual("dev,ui", matched.group("extras"))
        # 反面：引号内**转义的反斜杠**后跟换行，不是续行——换行是真内容。
        kept = _commands('echo "a\\\\\nb"')
        self.assertEqual(1, len(kept))
        self.assertIn("\n", kept[0][1], "转义反斜杠后的真换行被误删了")

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

    def test_an_unquoted_heredoc_body_with_substitution_is_loud(self) -> None:
        """未加引号的定界词让正文保持活性：`$(pip install …)` 真的会执行。

        无条件丢弃正文，这条安装既不进覆盖面也不响亮（codex P2）。纯文本的
        未引号正文照旧跳过（`$VAR` 执行不了命令），加引号的正文是字面量。
        """
        active = "\n".join([
            "cat <<EOF",
            "$(pip install '.[research]')",
            "EOF",
        ])
        with self.assertRaises(UnlexableShell):
            _commands(active)
        # 纯文本正文：跳过，不误伤。
        benign = "\n".join(["cat <<EOF", "plain $VAR text", "EOF", "echo ok"])
        self.assertEqual([["cat"], ["echo", "ok"]], _commands(benign))
        # 加了引号的定界词：正文是字面量，$( 也只是文本。
        literal = "\n".join(
            ["cat <<'EOF'", "$(pip install '.[research]')", "EOF"])
        self.assertEqual([["cat"]], _commands(literal))

    def test_an_indented_delimiter_line_is_body_not_terminator(self) -> None:
        """`  EOF`（带前导空格）在 shell 眼里是**正文**，不是收尾。

        `strip()` 把它当收尾，其后的 `pip install -e '.[research]'` 就被读成
        命令——发明覆盖面或误报红（codex P2）。终止行精确匹配；`<<-` 形态
        只剥前导 **tab**。
        """
        script = "\n".join([
            "cat <<'EOF'",
            "  EOF",
            "pip install -e '.[research]'",
            "EOF",
            "echo ok",
        ])
        got = _commands(script)
        self.assertEqual([["cat"], ["echo", "ok"]], got,
                         "缩进的假收尾把正文放了出来")

    def test_a_dashed_heredoc_strips_leading_tabs_only(self) -> None:
        script = "cat <<-'EOF'\n\tbody\n\tEOF\necho ok"
        self.assertEqual([["cat"], ["echo", "ok"]], _commands(script))
        # 反面：普通 `<<` 不剥 tab —— tab 缩进的行是正文，收尾必须顶格。
        undashed = "cat <<'EOF'\n\tEOF\nEOF\necho ok"
        self.assertEqual([["cat"], ["echo", "ok"]], _commands(undashed))

    def test_a_partially_quoted_delimiter_still_terminates(self) -> None:
        """`<<E'O'F` 的生效定界词是 `EOF`——POSIX 引号移除的结果。

        只剥两端引号会把它存成 `E'O'F`，真正的收尾行永远配不上、workflow 被
        误判未收尾（codex P2）。带任何引号 = 正文字面量。
        """
        script = "\n".join([
            "cat <<E'O'F",
            "$(pip install '.[research]')",   # 字面量正文，不该炸
            "EOF",
            "echo ok",
        ])
        self.assertEqual([["cat"], ["echo", "ok"]], _commands(script))

    def test_a_backslash_quoted_delimiter_is_literal_too(self) -> None:
        # `<<` + 反斜杠EOF：反斜杠也是引号——正文是字面量。
        script = "\n".join([
            "cat <<" + chr(92) + "EOF",
            "$(pip install '.[x]')",
            "EOF",
            "echo ok",
        ])
        self.assertEqual([["cat"], ["echo", "ok"]], _commands(script))

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

    def test_active_substitution_inside_quotes_is_loud(self) -> None:
        """双引号里的 `$(`/反引号是活性的——shell 会执行它。

        当成不透明字符串吞掉，`echo "$(pip install \'.[research]\')"` 里的
        安装就从覆盖面里静默消失（codex P2）。本词法器不建模引号内替换
        （shell 全文法的无底洞），但拒读必须**响亮**；转义了的 `\\$` 不算。
        """
        with self.assertRaises(UnlexableShell):
            _commands('echo "$(pip install \'.[research]\')"')
        with self.assertRaises(UnlexableShell):
            _commands('echo "`pip --version`"')
        # 转义的 `\$` 是字面字符，不是替换——不许误伤。
        self.assertEqual(1, len(_commands('echo "\\$(literal)"')))

    def test_a_backtick_substitution_is_loud(self) -> None:
        with self.assertRaises(UnlexableShell):
            _commands("echo `pip --version`")

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

    def test_an_option_value_dot_makes_the_target_ambiguous_loudly(
            self) -> None:
        """`pip install --find-links . ".[research]"` 是合法命令。

        取第一个目标形状的 token 会把选项值 `.` 当目标，research 连同它的
        extras 静默脱离覆盖面（codex P2）。选项表是 pip 的、随版本变——不
        维护它，**响亮**：要求 `--opt=value` 连写形态消歧。
        """
        with self.assertRaises(AmbiguousPipCommand):
            _local_target(
                _commands('pip install --find-links . ".[research]"')[0])
        # 连写形态：值不再是独立 token，目标唯一、extras 完整。
        matched = _local_target(
            _commands('pip install --find-links=. ".[research]"')[0])
        assert matched is not None
        self.assertEqual("research", matched.group("extras"))

    def test_an_equals_joined_editable_target_is_found(self) -> None:
        """`pip install --editable=.[research]` 的目标藏在 `=` 后面。

        只看裸 token，这条 extra 连同它的无上界依赖静默脱离覆盖面，可解析性
        检查也因「没有以 `.` 开头的 token」跳过它（codex P2）。
        """
        matched = _local_target(_commands("pip install --editable=.[research]")[0])
        self.assertIsNotNone(matched, "= 连写的 editable 目标没被认出来")
        assert matched is not None
        self.assertEqual("research", matched.group("extras"))
        self.assertIn(
            ".[research]",
            _local_target_candidates(
                _commands("pip install --editable=.[research]")[0]),
            "可解析性过滤的候选值里没有 = 连写的目标")

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

    def test_an_assignment_prefix_does_not_hide_the_executable(self) -> None:
        # POSIX 允许 `VAR=value cmd …`——可执行体在赋值前缀之后。
        self.assertTrue(_is_pip_install(
            _commands('MY_FLAG=1 pip install ".[dev]"')[0]))

    def test_a_pip_environment_assignment_is_ambiguous_loudly(self) -> None:
        """pip 的每个选项都能经 `PIP_<OPTION>` 环境注入。

        `PIP_DRY_RUN=1 pip install <窗口> <qlib pin>` 让 pip 什么都不装，而
        实参上看不出来——安装与窗口两道守卫全过、qlib 缺席、CI 静默绿
        （codex P1）。哪些 PIP_* 改行为要查 pip 的选项表——不维护它，多义
        即响亮，含此前当反例用过的 PIP_NO_CACHE_DIR：一视同仁。
        """
        for line in ('PIP_DRY_RUN=1 pip install ".[dev]"',
                     'PIP_NO_CACHE_DIR=1 pip install ".[dev]"',
                     'PIP_REQUIREMENT=r.txt python -m pip install .'):
            with self.subTest(line=line):
                with self.assertRaises(AmbiguousPipCommand):
                    _is_pip_install(_commands(line)[0])
        # 非 PIP_ 前缀的赋值不拦——它不配置 pip。
        self.assertTrue(_is_pip_install(
            _commands('RUST_LOG=debug pip install ".[dev]"')[0]))

    def test_a_lookalike_executable_is_not_pip(self) -> None:
        # `pipx install` 装的是隔离环境里的应用，不是项目依赖。
        self.assertFalse(_is_pip_install(_commands("pipx install ruff")[0]))

    def test_pip_as_an_argument_is_not_an_install(self) -> None:
        """`pip` 出现在**实参**里不算——可执行体是位置，不是子串。

        `echo pip install -e ".[research]"` 只是打日志的示例，把它当真安装，
        research 组那些刻意无上界的依赖就把治理打红，而 CI 根本没装它们
        （codex P2）。误报与漏报同样是错的。
        """
        for line in ('echo pip install -e ".[research]"',
                     "echo install pip"):
            with self.subTest(line=line):
                self.assertFalse(_is_pip_install(_commands(line)[0]))

    def test_install_must_follow_the_executable(self) -> None:
        self.assertFalse(_is_pip_install(_commands("pip download numpy")[0]))

    def test_install_must_be_the_subcommand_not_any_argument(self) -> None:
        """`pip --help install ".[research]"` 只打印帮助，什么都不装。

        在实参里搜 `install`，research 被记进覆盖面，治理对 CI 没装的依赖
        变红（codex P2）。帮助优先是 argparse 通例；`pip install --help`
        同样只打印帮助。
        """
        for line in ('pip --help install ".[research]"',
                     'pip install --help ".[research]"',
                     'pip -h install ".[research]"'):
            with self.subTest(line=line):
                self.assertFalse(_is_pip_install(_commands(line)[0]))

    def test_options_before_the_subcommand_are_ambiguous_loudly(self) -> None:
        # `--log PATH` 这类接值的全局选项让子命令位置无从确定——多义即响亮。
        with self.assertRaises(AmbiguousPipCommand):
            _is_pip_install(_commands('pip --log x.log install ".[dev]"')[0])
        # 连写形态消歧后照常判定。
        self.assertTrue(
            _is_pip_install(_commands('pip --log=x.log install ".[dev]"')[0]))


class EveryDirectWorkflowInstallTargetIsBounded(unittest.TestCase):
    """workflow 直接装的包（不经 pyproject）同样每条都要有上界。

    实证就在本仓：两条 `python -m pip install --upgrade pip` 无上界——pip
    是解析安装一切的那个工具，一次新版就能改变全部 CI 安装的结果，而此前
    的覆盖面只走 pyproject 三处 + extras，对它是盲的（codex P1）。
    """

    def test_every_direct_target_in_the_workflows_is_bounded(self) -> None:
        seen = 0
        problems: list[str] = []
        for workflow in _workflows():
            for command in _pip_installs(workflow):
                count, bad = _direct_install_targets(command)
                seen += count
                problems.extend(f"{workflow.name}: {p}" for p in bad)
        self.assertEqual([], problems)
        # 底数：pip 引导 ×2 + numpy ×2 + scipy ×2。读少了=覆盖面塌了。
        self.assertGreaterEqual(seen, 6, f"只读到 {seen} 个直接目标")

    def test_an_unbounded_bootstrap_is_flagged(self) -> None:
        count, problems = _direct_install_targets(
            _commands("python -m pip install --upgrade pip")[0])
        self.assertEqual(1, count)
        self.assertTrue(problems and "pip" in problems[0], "无上界的 pip 没被点名")

    def test_a_bounded_bootstrap_passes(self) -> None:
        self.assertEqual(
            (1, []),
            _direct_install_targets(
                _commands('python -m pip install --upgrade "pip>=24,<26"')[0]))

    def test_a_file_sourced_option_is_flagged_not_skipped(self) -> None:
        """`--requirement=requirements.txt` 会装文件里的每一个包。

        整个 token 被当普通选项跳过，文件里潜在无上界的依赖静默绕过守卫
        （codex P2）。裸形态 `-r` 同拦；约束文件 `-c/--constraint` 同理。
        """
        for line in ("pip install --requirement=requirements.txt",
                     "pip install -r requirements.txt",
                     "pip install --constraint=c.txt x>=1,<2"):
            with self.subTest(line=line):
                _, problems = _direct_install_targets(_commands(line)[0])
                self.assertTrue(problems, "文件引入选项被静默跳过了")

    def test_a_mutable_qlib_reference_is_flagged(self) -> None:
        """`@main` 是可动引用——CI 的 qlib 代码会在两次运行之间漂移。

        只看 URL 前缀，`@` 后面挂什么都算「钉住」（codex P2）。必须是完整
        40 位十六进制 commit SHA。
        """
        _, problems = _direct_install_targets(_commands(
            "pip install git+https://github.com/microsoft/qlib.git@main")[0])
        self.assertTrue(problems, "可动引用被当成了钉死的 commit")
        _, ok = _direct_install_targets(_commands(
            "pip install git+https://github.com/microsoft/qlib.git@"
            + "a" * 40)[0])
        self.assertEqual([], ok, "合法的 40 位 SHA 被误拒")

    def test_an_unpinned_source_install_is_flagged(self) -> None:
        _, problems = _direct_install_targets(
            _commands("pip install git+https://github.com/x/y.git")[0])
        self.assertTrue(problems, "未钉 commit 的源码安装没被点名")


class TheQlibPinMustBeARealInstall(unittest.TestCase):
    """URL 出现在命令里 ≠ 安装了 qlib。

    真安装被误删只剩一句 `echo git+…qlib… "numpy…"` 时，「装 qlib 的命令带齐
    了窗口」两条断言照样绿——而 `pytest.importorskip("qlib")` 让 qlib 侧 CI
    静默跳过（codex P1）。候选先过 `_is_pip_install`。
    """

    QLIB = "git+https://github.com/microsoft/qlib.git@abc123"

    def test_an_echo_carrying_the_pin_is_not_an_install(self) -> None:
        self.assertEqual(
            [], _qlib_pin_installs(
                _commands(f'echo {self.QLIB} "numpy>=1.24,<2.0"')))

    def test_a_real_install_is_kept(self) -> None:
        got = _qlib_pin_installs(
            _commands(f'pip install {self.QLIB} "numpy>=1.24,<2.0"'))
        self.assertEqual(1, len(got))

    def test_a_dry_run_is_not_an_install(self) -> None:
        """`--dry-run` 由 pip 定义为「Don't actually install anything」。

        带着它的命令能同时满足「是安装」「带齐窗口」两道守卫而 qlib 缺席，
        `importorskip` 让 CI 静默绿（codex P1）。
        """
        self.assertEqual(
            [], _qlib_pin_installs(_commands(
                f'pip install --dry-run {self.QLIB} "numpy>=1.24,<2.0"')))
        self.assertFalse(
            _is_pip_install(_commands('pip install --dry-run ".[dev]"')[0]))

    def test_a_control_keyword_does_not_hide_the_install(self) -> None:
        """`if pip install …; then` 的可执行体是 `pip`，不是 `if`。

        保留字引导复合命令；只认第 0 个 token，条件化的安装整条从覆盖面里
        消失，而 workflow 里写 `if pip install…` 完全合法（codex P2）。
        保留字是 POSIX.1-2017 §2.4 定义的闭集，不是我在枚举写法。
        """
        script = 'if pip install ".[research]"; then echo ok; fi'
        first = _commands(script)[0]
        self.assertTrue(_is_pip_install(first), "if 后面的安装没被认出来")
        for keyword in ("while", "until", "elif", "else", "do", "then", "!"):
            with self.subTest(保留字=keyword):
                self.assertTrue(_is_pip_install(
                    _commands(f'{keyword} pip install ".[x]"')[0]))
        # 反面：保留字自己不是可执行体。
        self.assertFalse(_is_pip_install(_commands("if true")[0]))


class ParenthesesAreCommandSyntaxNotWordCharacters(unittest.TestCase):
    """未加引号的 `(`/`)` 是 POSIX 的命令语法，不是词的一部分。

    `(pip install -e ".[research]")` 合法；把括号焊在 token 上，
    `_is_pip_install` 与 `_local_target` 都认不出它，那组 extra 静默脱离
    覆盖面（codex P2）。
    """

    def test_a_subshell_install_is_recognised(self) -> None:
        got = _commands('(pip install -e ".[research]")')
        self.assertEqual(1, len(got))
        self.assertTrue(_is_pip_install(got[0]), "子 shell 里的安装没被认出来")
        matched = _local_target(got[0])
        self.assertIsNotNone(matched, "子 shell 里的本地目标没被认出来")
        assert matched is not None
        self.assertEqual("research", matched.group("extras"))

    def test_a_command_substitution_is_refused_not_reinvented(self) -> None:
        """`$()` 与外层命令纠缠：替换的输出是外层命令的实参。

        把两个括号当无条件边界，`echo $(date) pip install ".[research]"` 会被
        拆出一条 shell 根本不跑的 `pip install`——治理对 CI 没装的依赖变红
        （codex P2）。不建模嵌套重挂，与引号内替换同一处置：响亮拒读。
        """
        with self.assertRaises(UnlexableShell):
            _commands('echo $(date) pip install ".[research]"')

    def test_a_quoted_parenthesis_is_still_a_word_character(self) -> None:
        self.assertEqual([["echo", "(not a subshell)"]],
                         _commands('echo "(not a subshell)"'))


class ARestatementIsFoundByItsCanonicalName(unittest.TestCase):
    """PEP 503：包名大小写不敏感，`-`/`_`/`.` 等价。

    大小写敏感的 `startswith`，`NumPy>=1.24,<2.1` 整条躲过一致性检查——它
    确实重述了 numpy 窗口，还带着一个**分叉的**上界（codex P2）。检测按归一
    名，断言仍逐字：非规范拼写会被要求改写，不是被放过。
    """

    DECLARED = {"numpy": "numpy>=1.24,<2.0", "scipy": "scipy>=1.10,<1.14"}

    def test_a_differently_cased_restatement_is_detected(self) -> None:
        for token in ("NumPy>=1.24,<2.1", "SCIPY>=1.10,<1.14"):
            with self.subTest(token=token):
                self.assertIsNotNone(_restated_package(token, self.DECLARED))

    def test_a_shared_prefix_is_still_not_a_restatement(self) -> None:
        self.assertIsNone(_restated_package("numpydoc>=1.5", self.DECLARED))

    def test_separator_variants_are_the_same_name(self) -> None:
        declared = {"python-dateutil": "python-dateutil>=2.8,<3"}
        self.assertEqual(
            "python-dateutil",
            _restated_package("python_dateutil>=2.8,<3", declared))

    def test_an_extras_segment_does_not_hide_a_restatement(self) -> None:
        """`numpy[feature]>=1.24,<2.1` 也是 numpy 窗口的重述。

        名字后紧跟 `[` 时旧判据（下一个字符必须是版本运算符）返回 None——
        带着**分叉窗口**的重述整条躲过逐字一致检查（codex P2）。跳过 extras
        段再看运算符；随后的逐字断言自然把它打红，要求与 pyproject 完全一致。
        """
        self.assertEqual(
            "numpy",
            _restated_package("numpy[feature]>=1.24,<2.1", self.DECLARED))
        # 反面：没闭合的 `[` 不是 extras 段，不算重述。
        self.assertIsNone(_restated_package("numpy[oops>=1.24", self.DECLARED))


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
                # 第三方包，不点名任何 extra，与覆盖面无关。候选值经
                # `_local_target_candidates`——`--editable=` 连写的值也在内。
                if not any(
                    value.startswith(".")
                    for value in _local_target_candidates(command)
                ):
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
            for command in _qlib_pin_installs(_workflow_commands(workflow))
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
