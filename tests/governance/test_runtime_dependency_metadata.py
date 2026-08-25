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
    segments: list[tuple[str, bool]] = []
    piped: list[bool] = [False]          # 当前片段是否与管道相邻
    buffer: list[str] = []
    pending_heredocs: list[tuple[str, bool]] = []
    index = 0
    size = len(script)

    def flush(next_piped: bool = False) -> None:
        segment = "".join(buffer).strip()
        if segment:
            segments.append((segment, piped[0]))
        buffer.clear()
        piped[0] = next_piped

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
        if char in "<>":
            # 未加引号的 `>`/`<` **在词中间也仍是重定向**：bash 把
            # `pip install numpy>=1.24,<2.0` 读成 `numpy` + 输出重定向 +
            # 输入重定向——那两个窗口根本没传给 pip，按逐字一致「通过」是
            # 假的（codex P2）。所以在扫描层把操作符从词里**切开**（前后
    # 各垫一个空格），token 层的剥离随后接手；IO_NUMBER（`2>&1` 的
            # `2`）按 POSIX 规则归并进操作符。真正的版本窗口必须**加引号**
            # ——这正是仓库 workflow 的现有写法。
            cursor = index + 1
            if char == ">" and script[cursor:cursor + 1] == ">":
                cursor += 1
            if char == ">" and script[cursor:cursor + 1] == "&":
                cursor += 1
                while script[cursor:cursor + 1].isdigit():
                    cursor += 1
            operator = script[index:cursor]
            joined = "".join(buffer)
            word_start = max(joined.rfind(" "), joined.rfind("\t"),
                             joined.rfind("\n")) + 1
            word = joined[word_start:]
            if char == ">" and word.isdigit() and word:
                buffer = [joined[:word_start]]
                operator = word + operator
            buffer.append(" " + operator + " ")
            index = cursor
            continue
        separator = next(
            (sep for sep in _SEPARATORS if script.startswith(sep, index)), None)
        if separator is not None:
            if separator in ("|", "&&", "&"):
                # 裸 `&` 把左侧**后台化**：失败不传播（codex 实测
                # `bash -e -o pipefail -c 'false & …'` 返回 0），安装可能
                # 还没跑完/已失败而 pytest 已经开跑。左侧与管道/AND 链同罪；
                # 右侧是新列表、不受牵连。
                # 两侧都打「纠缠」标记：`pip install <qlib> | tee log` 的退出
                # 码是 tee 的（失败被吞）；`false && pip install <qlib>` 在
                # bash -e 下**不会**让步骤退出——errexit 对 AND 链内的失败
                # 不生效（codex 以 Linux 腿的 bash -e -o pipefail 实测），
                # 安装被跳过而 pytest 照跑。此前「前件失败步骤即红」的判读
                # 是**错的**，&&与 | 同罪：链内的 pip 安装执行/结果无法确立。
                # 左侧此刻还在 buffer 里——标记要在 flush **之前**落上。
                piped[0] = True
                flush(next_piped=(separator != "&"))
                index += len(separator)
                continue
            if separator == "||":
                # `a || b` 的 b 只在 a **失败**时运行——a 成功则 b 从未执行
                # 而步骤照样绿。把 b 当独立命令数，会把一条从不运行的
                # `pip install <窗口> <qlib>` 当成真安装（codex P1）。本守卫
                # 不建模控制流：执行无法确立的构造响亮拒绝。（`&&` 不同：
                # 前件失败步骤即红，链上每段「要么运行、要么响亮」。）
                raise UnlexableShell(
                    "`||` 右侧只在左侧失败时运行——执行无法确立；请改写为"
                    "显式 if 或拆成独立步骤")
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


#: 独立的重定向操作符（POSIX 定义的闭集）与带粘连目标的形态。`2>&1` 这类
#: fd 复制自含目标；`> f` 的目标是下一个 token；`>f` 粘连。引号内的 `>`
#: 在分词后不带引号，但它嵌在词中间（`foo>=1,<2`）而不是词首——不误伤。
_REDIR_SELF = re.compile(r"^\d*>&\d+$|^&>>?$|^\d*>>?$|^<$")
_REDIR_ATTACHED = re.compile(r"^(\d*>>?|<)(?P<target>[^&=<>].*)$")


def _strip_redirections(tokens: list[str]) -> list[str]:
    """剥掉重定向操作符与其目标——它们是 shell 语法，不是命令实参。

    `pip install "foo>=1,<2" > install.log` 里 `>` 与 `install.log` 由 shell
    消费；留在 token 里，直接目标守卫会把 `install.log` 报成无上界的包——
    重定向输出这件无辜事把治理打红（codex P2）。
    """
    out: list[str] = []
    consume_next = False
    for token in tokens:
        if consume_next:
            consume_next = False
            continue
        if _REDIR_SELF.match(token):
            # `2>&1` / `>&2` 自含目标；裸 `>`/`>>`/`<`/`2>` 吃下一个 token。
            consume_next = "&" not in token
            continue
        if _REDIR_ATTACHED.match(token):
            continue
        out.append(token)
    return out


def _commands(script: str) -> list[list[str]]:
    """把一段 shell 切成若干条命令，每条是一串实参。

    词法器已经保证每个片段里的引号是配平的，`shlex` 在这里只负责去引号与
    分词；重定向随后剥离。**管道相邻的片段若是 pip 安装即响亮**：管道的
    退出码属于最后一段，安装失败可被下游吞掉——执行结果无法确立，与 `||`
    同族（codex P1）。
    """
    out: list[list[str]] = []
    for segment, piped in _split_commands(script):
        args = _strip_redirections(shlex.split(segment, posix=True))
        if not args:
            continue
        if piped and _is_pip_install(args):
            raise UnlexableShell(
                "管道或 &&/AND 链中的 pip 安装——失败可被吞掉或整段被跳过，"
                "执行/结果无法确立；请拆成独立命令或步骤")
        out.append(args)
    return out


def _environment_keys(document: object) -> list[str]:
    """workflow / job / step 三层 `env:` 映射里声明的全部键。

    GitHub Actions 把这三层 env 施加到 run 命令上——`_run_scripts` 只取 run
    文本，看不见 `env: {PIP_DRY_RUN: "1"}` 让 pip 什么都不装、
    `PIP_REQUIREMENT` 从文件注入依赖（codex P1）。收集键交给守卫，与行内
    `PIP_*=` 赋值同一处置：多义即响亮。
    """
    keys: list[str] = []
    root = document if isinstance(document, dict) else {}
    for env in (root.get("env"),):
        if isinstance(env, dict):
            keys.extend(str(k) for k in env)
    for job in (root.get("jobs") or {}).values():
        job = job or {}
        if isinstance(job.get("env"), dict):
            keys.extend(str(k) for k in job["env"])
        for step in job.get("steps") or []:
            step = step or {}
            if isinstance(step.get("env"), dict):
                keys.extend(str(k) for k in step["env"])
    return keys


def _run_scripts(
    workflow: Path, *, unconditional_only: bool = False,
) -> list[str]:
    """workflow 里的 `run` 块；可选只取**无条件**步骤的。

    按结构解析：YAML 取到 `steps[].run`（`name:` 之类元数据天然排除）。
    step 或 job 带 `if:` 时 GitHub Actions 可能跳过它——被跳过的安装不是
    安装（codex P1）。方向分析定用法：**presence 类底数**（qlib 在场）只数
    无条件步骤——把条件安装当在场，某条矩阵腿会在 importorskip 下静默绿；
    **bounds/形状类扫描**照扫全部步骤——多扫只会更严，漏扫才是洞。
    """
    document = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    scripts: list[str] = []
    for job in (document or {}).get("jobs", {}).values():
        job = job or {}
        # `continue-on-error: true` 与 `if:` 同类：安装失败后 Actions 照样
        # 跑向 pytest——「允许失败的安装」不是 presence 保证（codex P1）。
        job_skippable = "if" in job or bool(job.get("continue-on-error"))
        for step in job.get("steps", []) or []:
            step = step or {}
            script = step.get("run")
            if not isinstance(script, str):
                continue
            if unconditional_only and (
                job_skippable or "if" in step
                or bool(step.get("continue-on-error"))
            ):
                continue
            scripts.append(script)
    return scripts


def _job_commands(
    workflow: Path,
) -> list[tuple[str, list[tuple[list[str], bool]]]]:
    """按 **job** 分组的命令，逐条带「是否有保证」标记，**保持步骤顺序**。

    jobs 跑在隔离 runner 上——A job 装的 qlib，B job 拿不到（codex P1）；
    步骤又是**顺序执行**的——qlib 装在 pytest 之后，前面的测试已经在
    importorskip 下静默跑完（codex P1）。「有保证」= 所在 step/job 既无
    `if:` 也无 `continue-on-error`。"""
    document = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    jobs: list[tuple[str, list[tuple[list[str], bool]]]] = []
    for name, job in ((document or {}).get("jobs") or {}).items():
        job = job or {}
        job_skippable = "if" in job or bool(job.get("continue-on-error"))
        entries: list[tuple[list[str], bool]] = []
        for step in job.get("steps", []) or []:
            step = step or {}
            script = step.get("run")
            if not isinstance(script, str):
                continue
            guaranteed = not (
                job_skippable or "if" in step
                or bool(step.get("continue-on-error"))
            )
            entries.extend(
                (command, guaranteed) for command in _commands(script))
        jobs.append((str(name), entries))
    return jobs


def _workflow_commands(
    workflow: Path, *, unconditional_only: bool = False,
) -> list[list[str]]:
    """这个 workflow 里全部会执行的命令。"""
    return [
        command
        for script in _run_scripts(
            workflow, unconditional_only=unconditional_only)
        for command in _commands(script)
    ]


#: pip 自己的可执行体命名规则：`pip`、`pip3`、`pip3.12`。这是 pip 安装器写
#: 死的方案（`pip` + 解释器版本后缀），不是我在枚举拼写。python 同一方案。
_PIP_EXECUTABLE = re.compile(r"^pip[\d.]*$")
_PYTHON_EXECUTABLE = re.compile(r"^python[\d.]*$")
#: POSIX 简单命令允许的前缀：`VAR=value` 赋值。可执行体在它们**之后**。
_ASSIGNMENT_PREFIX = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*=")
#: 「运行后面那条命令」的 POSIX 实用程序：`env`（可带赋值前缀）与
#: `command`（shell 内建）。第 0 个 token 是它们时真正的可执行体在后面——
#: `env pip install …` 是一次真安装，不解包会把它整条排除在覆盖面之外
#: （codex P2）。只解包这两个有实据的；新 wrapper 出现由 qlib 在场底数与
#: 可解析底数暴露，不做开放枚举。
_COMMAND_WRAPPERS = frozenset({"env", "command"})

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
    stripped_reserved = False
    while tokens and tokens[0] in _RESERVED_WORDS:
        stripped_reserved = True
        tokens.pop(0)
    assignments: list[str] = []
    while tokens and _ASSIGNMENT_PREFIX.match(tokens[0]):
        assignments.append(tokens.pop(0))
    # wrapper 解包：`env` / `command` 的语义就是「运行后面那条命令」。env
    # 自己的赋值前缀继续剥（PIP_*= 由形状规则响亮）；wrapper 的**裸选项**
    # 可能吃掉下一个 token（env -u NAME、command -p），多义即响亮。
    while tokens:
        wrapper = tokens[0].replace("\\", "/").rsplit("/", 1)[-1]
        if wrapper not in _COMMAND_WRAPPERS:
            break
        tokens.pop(0)
        while tokens and _ASSIGNMENT_PREFIX.match(tokens[0]):
            assignments.append(tokens.pop(0))
        if tokens and tokens[0].startswith("-"):
            raise AmbiguousPipCommand(
                f"wrapper 后有裸选项 {tokens[0]!r}——接值选项让命令位置无从"
                f"确定；请去掉选项或改写")
    if not tokens:
        return False
    if tokens[0].startswith("$"):
        # `INSTALLER=pip` 之后的 `$INSTALLER install …` 真的会执行 pip，而
        # 展开值在命令文本之外——把它当不透明可执行体静默返回 False，这条
        # 安装连同 extras 从覆盖面消失（codex P2）。不建模变量求值：可执行
        # 体位置上的展开响亮拒绝，请写字面命令名。
        raise AmbiguousPipCommand(
            f"可执行体是变量展开：{tokens[0]!r} —— 无法确立调用的是什么；"
            f"请写字面命令名")
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
    pip_environment = [a for a in assignments if _PIP_ENV_TOKEN.match(a)]
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
    if stripped_reserved:
        # `if pip install <qlib>; then …` 里安装是**条件**：失败被 if 吞掉、
        # 步骤照样绿地跑向 pytest，而 importorskip 让 qlib 侧静默蒸发；
        # `then pip install` 的**体**又只在条件成立时运行（codex P1，与
        # `||` 同根：执行结果/执行与否无法确立）。条件构造内的 pip 安装
        # 一律响亮，请拆成独立步骤。
        raise AmbiguousPipCommand(
            "pip 安装在条件构造（if/while/until/then/…）内——执行结果无法"
            "确立；请拆成独立命令或步骤")
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
#: pip 环境配置的 **token 形状**：`PIP_<OPTION>=…`。载体不枚举——行内前缀、
#: `export`、`env`、`set` 都只是把这个形状送进环境的不同姿势；Windows 上
#: 环境名大小写不敏感，`pip_dry_run=1` 与 `PIP_DRY_RUN=1` 等效（codex
#: P1+P2）。任何命令的任何 token 长成这样，pip 的行为就可能被无形改写。
#: 两种 shell 的 pip 环境引用形状：POSIX 的 `PIP_X=…`，以及 PowerShell 的
#: `$env:PIP_X=…` / `Env:PIP_X`（`Set-Item` 的路径实参）。Windows 腿没有
#: `shell:` 覆盖、默认跑 pwsh——`$env:PIP_DRY_RUN=1` 在那里与 POSIX 的
#: `export` 等效（codex P2）。仍然认形状不认载体。
_PIP_ENV_TOKEN = re.compile(
    r"^pip_[a-z0-9_]*=|^\$?env:pip_[a-z0-9_]*", re.IGNORECASE)


def _pip_environment_offenders(commands: list[list[str]]) -> list[str]:
    """这些命令里所有 `PIP_*=` 形状的 token——pip 配置注入的痕迹。

    抽成 helper 是**作证**需要：真实 workflow 是干净的，负断言测不出扫描
    被删（变异 BO 实测如此）；helper 可以拿合成命令直接单测，真数据上只
    留底数断言。
    """
    return [
        token for command in commands for token in command
        if _PIP_ENV_TOKEN.match(token)
    ]


def _pip_env_key_offenders(keys: list[str]) -> list[str]:
    """env 键里配置 pip 的那些（大小写不敏感——Windows 腿）。同上，可单测。"""
    return [key for key in keys if key.upper().startswith("PIP_")]

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
            if (
                token in _FILE_SOURCED_OPTIONS
                or any(
                    token.startswith(f"{option}=")
                    for option in _FILE_SOURCED_OPTIONS
                )
                # 附着式短选项值：pip 接受 `-rrequirements.txt`（optparse
                # 规则，值直接粘在短选项后）。只认精确 `-r` 与 `=` 连写，
                # 这种形态落进无条件 continue，文件内容照样绕过覆盖面
                # （codex P2）。
                or (
                    not token.startswith("--")
                    and len(token) > 2
                    and token[:2] in ("-r", "-c")
                )
            ):
                problems.append(
                    f"从文件引入安装内容：{token} —— 覆盖面推导看不见文件"
                    f"内容；请把 requirement 直接写在命令里")
            continue
        if _LOCAL_TARGET.match(token):
            continue                      # 本地项目：extras 机制管
        if token.startswith(_QLIB_PIN):
            if _blocked_by_bare_option(command, command.index(token)):
                problems.append(
                    f"qlib 引用紧跟裸选项——可能只是选项值而非安装目标："
                    f"{command[command.index(token) - 1]} {token}；请用"
                    f" --opt=value 连写消歧")
                continue
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


def _runs_pytest(commands: list[list[str]]) -> bool:
    """这些命令里有没有 pytest 调用（裸 `pytest` 或 `python -m pytest`）。

    wrapper（env/command）照 pip 侧同一套解包——`env pytest tests/` 也是在
    跑测试，不解包会让该 job 的 presence 义务真空蒸发（codex P2）。检测取
    保守方向：条件位/赋值前缀后的 pytest 一律算数。
    """
    for command in commands:
        tokens = list(command)
        stripped = True
        while stripped and tokens:
            stripped = False
            while tokens and (tokens[0] in _RESERVED_WORDS
                              or _ASSIGNMENT_PREFIX.match(tokens[0])):
                tokens.pop(0)
                stripped = True
            if tokens and tokens[0].replace("\\", "/").rsplit(
                    "/", 1)[-1] in _COMMAND_WRAPPERS:
                tokens.pop(0)
                stripped = True
        if not tokens:
            continue
        name = tokens[0].replace("\\", "/").rsplit("/", 1)[-1]
        if name == "pytest":
            return True
        if _PYTHON_EXECUTABLE.match(name) and tokens[1:3] == ["-m", "pytest"]:
            return True
    return False


def _unprotected_pytest(entries: list[tuple[list[str], bool]]) -> bool:
    """job 里是否有**前面没有保证性 qlib 安装**的 pytest 调用。

    集合成员判据的两个已被逐一击破的洞（codex 三条 P1）：混用无条件集找
    pytest（真空绿）、把别的 job 的安装记进来（隔离 runner）、以及**顺序**
    ——步骤顺序执行，qlib 装在 pytest 之后，前面的测试已在 importorskip 下
    静默跑完。所以逐条走：pytest 用**全部**条目检测（条件腿也要 qlib），
    qlib 只认**此前**出现的、**有保证**（无 if:/continue-on-error）的安装。
    """
    qlib_seen = False
    for command, guaranteed in entries:
        if guaranteed and _qlib_pin_installs([command]):
            qlib_seen = True
        if _runs_pytest([command]) and not qlib_seen:
            return True
    return False


#: 改变安装**目的地**的 pip 选项（pip 文档定义的闭集）：装出来的包不在
#: 随后 pytest 进程的 import path 上——`--target=/tmp/x` 的 qlib 对
#: `importorskip` 而言等于没装（codex P1）。presence 不给这类安装记账。
_DESTINATION_OPTIONS = ("--target", "--root", "--prefix")


def _redirected_destination(command: list[str]) -> bool:
    """这条安装是否被改了目的地——presence 记账前必查。"""
    return any(
        token in _DESTINATION_OPTIONS
        or any(token.startswith(f"{option}=")
               for option in _DESTINATION_OPTIONS)
        for token in command
    )


def _credited(command: list[str]) -> list[str]:
    """可以拿来记账（presence/窗口/重述）的 token——排除裸选项的疑似值。

    `pip install <qlib-pin> --report "numpy>=1.24,<2.0" …` 里那个 numpy 串是
    `--report` 的**文件名**：按 token 成员资格给窗口记账，qlib 那次解析其实
    没带约束（codex P1）。与 pin/目标同一原则：紧跟裸选项（非 -e/--editable）
    的 token 不记账。
    """
    return [
        token for index, token in enumerate(command)
        if not _blocked_by_bare_option(command, index)
    ]


def _blocked_by_bare_option(command: list[str], index: int) -> bool:
    """这个 token 是否紧跟在一个**裸**选项之后——可能只是它的值。

    `pip install --trusted-host <qlib-pin> <窗口>` 装的是那两个窗口，pin 只是
    `--trusted-host` 的值（codex P1）。分辨要 pip 的选项表——不维护；
    presence 类判据（qlib 在场 / 本地目标）对这种 token **不记账**，缺席由
    相应守卫响亮。`-e/--editable` 例外：它的值**就是**安装目标，语义即此。
    """
    if index == 0:
        return False
    previous = command[index - 1]
    return (
        previous.startswith("-")
        and "=" not in previous
        and previous not in ("-e", "--editable")
    )


def _qlib_pin_installs(commands: list[list[str]]) -> list[list[str]]:
    """真正**安装** qlib 的命令。

    只按「URL 出现在命令里」认，一条 `echo git+…qlib… "numpy…" "scipy…"`
    也会当成安装——若真安装被误删只剩这句回显，两条断言照样绿，而
    `pytest.importorskip("qlib")` 会让 qlib 侧 CI 静默跳过（codex P1）。
    候选必须先过 `_is_pip_install`。
    """
    return [
        command for command in commands
        if any(
            token.startswith(_QLIB_PIN)
            and not _blocked_by_bare_option(command, i)
            for i, token in enumerate(command)
        )
        and _is_pip_install(command)
        # 改了目的地的安装不进 import path——对 presence 等于没装。
        and not _redirected_destination(command)
    ]


def _local_target_candidates(command: list[str]) -> list[str]:
    """可能承载本地目标的 token 值：裸 token + `--editable=` 连写的值。

    `pip install --editable=.[research]` 合法，而目标藏在 `=` 后面——只看
    裸 token，这条 extra 连同它的无上界依赖静默脱离覆盖面，可解析性检查也
    因「没有以 `.` 开头的 token」跳过它（codex P2）。
    """
    values = []
    for index, token in enumerate(command):
        if token.startswith("--editable="):
            values.append(token[len("--editable="):])
        elif (
            token.startswith("-e")
            and not token.startswith("--")
            and len(token) > 2
        ):
            # 附着式短选项值（`-e.[research]`）——与 `-r` 附着形态同一条
            # optparse 规则；只认裸 token 会让这条 extra 静默脱离覆盖面。
            values.append(token[2:])
        elif not token.startswith("-") and not _blocked_by_bare_option(
                command, index):
            # 紧跟裸选项（非 -e）的 token 可能只是选项值——不记为目标候选
            # （codex P1 的同一原则用在本地目标上）。
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

    def test_a_compound_install_line_is_refused_outright(self) -> None:
        """`pip install <qlib> && pip install "numpy…"`——整条响亮。

        判读的三段演进：r6 按分隔符拆开（防约束漏认）；本轮 codex 证明
        bash -e 对 AND 链内的失败不 errexit——`false && pip install` 的安装
        被跳过而步骤照样绿，「前件失败步骤即红」是错的。&& 链内的安装与
        管道同罪：执行/结果无法确立即响亮。
        """
        with self.assertRaises(UnlexableShell):
            _commands(f'pip install {self.QLIB} && pip install "numpy>=1.24,<2.0"')
        # 非安装的 && 链照常拆分。
        self.assertEqual(2, len(_commands("echo a && echo b")))

    def test_every_posix_separator_splits(self) -> None:
        # `||` 不在此列（右侧执行无法确立，整段响亮）；`|`/`&&` 链内的
        # **安装**响亮（fixture 用非安装命令验证拆分本身）。
        for sep in ("&&", ";", "|", "&"):
            with self.subTest(分隔符=sep):
                self.assertEqual(2, len(_commands(f"echo a {sep} echo b")))
        # `&` 也不再进「独立安装」列——左侧被后台化，失败不传播
        # （见 test_a_backgrounded_install_is_refused）。
        with self.subTest(分隔符=";", 安装="独立"):
            self.assertEqual(
                2, len(_commands("pip install a ; pip install b")))

    def test_a_quoted_separator_is_not_a_separator(self) -> None:
        # 引号里的 `&&` 是实参的一部分，不是命令边界。
        self.assertEqual(1, len(_commands('echo "a && b"')))

    def test_separators_need_no_surrounding_whitespace(self) -> None:
        """`a&&b` 是合法写法，而 `shlex` 会把它切成**一个词**。

        分词器不知道命令在哪里结束——它切词。于是
        `pip install git+…@sha&&pip install "numpy…"` 会被当成一条命令，qlib
        那次无约束的解析就此通过检查（codex 第六轮 P1）。
        """
        # 无空格形态同样要被认出边界——认出之后按 && 链内安装响亮。
        with self.assertRaises(UnlexableShell):
            _commands(f'pip install {self.QLIB}&&pip install "numpy>=1.24,<2.0"')
        self.assertEqual(2, len(_commands("echo a&&echo b")),
                         "没有空格的 `&&` 没被认成命令边界")

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

    def test_redirections_are_shell_syntax_not_arguments(self) -> None:
        """`pip install "foo>=1,<2" > install.log` 的 `>` 与目标由 shell 消费。

        留在 token 里，直接目标守卫把 `install.log` 报成无上界的包——重定向
        输出这件无辜事把治理打红（codex P2）。剥独立/粘连/fd 复制三种形态；
        引号里词中的 `>`（版本约束）不误伤。
        """
        self.assertEqual(
            [["pip", "install", "foo>=1,<2"]],
            _commands('pip install "foo>=1,<2" > install.log'))
        # 无引号的 `x<1` 在 bash 里是 `x` + 输入重定向——不是版本约束
        # （codex P2）。加引号的才是。
        self.assertEqual(
            [["pip", "install", "x"]],
            _commands("pip install x<1 2>&1 >>log.txt"))
        self.assertEqual(
            [["pip", "install", "x<1"]],
            _commands('pip install "x<1"'))
        self.assertEqual(
            [["echo", "hi"]], _commands("echo hi >out.txt"))
        count, problems = _direct_install_targets(
            _commands('pip install "foo>=1,<2" > install.log')[0])
        self.assertEqual((1, []), (count, problems),
                         "重定向目标被当成了安装目标")

    def test_an_unquoted_window_is_a_redirection_not_a_requirement(
            self) -> None:
        """`pip install numpy>=1.24,<2.0`（无引号）在 bash 里不是约束。

        `>` 与 `<` 在词中间仍是重定向——bash 传给 pip 的只有 `numpy`，
        版本窗口变成了输出/输入重定向。把整个词当逐字一致的有界 requirement
        是假通过（codex P2）。扫描层拆开后，裸 `numpy` 走无上界告警——
        治理红着要求加引号，与仓库现有写法一致。
        """
        got = _commands("pip install numpy>=1.24,<2.0")
        self.assertEqual([["pip", "install", "numpy"]], got,
                         "无引号窗口没有被当成重定向切开")
        count, problems = _direct_install_targets(got[0])
        self.assertEqual(1, count)
        self.assertTrue(problems and "numpy" in problems[0],
                        "裸 numpy 没有触发无上界告警——假窗口静默通过")

    def test_a_conditional_step_does_not_satisfy_presence(self) -> None:
        """`if:` 可跳过的步骤里的安装不算「在场」。

        某条矩阵腿跳过 qlib 安装步骤时，扁平化命令列表里它仍然在——
        `_pytest_without_qlib` 被满足而那条腿在 importorskip 下静默绿
        （codex P1）。presence 只数无条件步骤；bounds/形状扫描照扫全部。
        """
        import tempfile as _tf
        doc = """
jobs:
  j:
    steps:
      - run: pytest tests/
        if: matrix.os == 'ubuntu-latest'
      - run: pip install git+https://github.com/microsoft/qlib.git@{sha}
        if: matrix.os == 'ubuntu-latest'
""".format(sha="a" * 40)
        with _tf.TemporaryDirectory() as t:
            wf = Path(t) / "w.yml"
            wf.write_text(doc, encoding="utf-8")
            everything = _workflow_commands(wf)
            entries = dict(_job_commands(wf))["j"]
        self.assertTrue(_runs_pytest(everything), "条件腿的 pytest 也要被看见")
        self.assertTrue(all(not guaranteed for _, guaranteed in entries),
                        "带 if: 的步骤不该算有保证")
        self.assertTrue(
            _unprotected_pytest(entries),
            "条件腿跑 pytest + 条件安装 qlib —— 该判违例（安装可被跳过）")
        # 接线钉：真数据守卫必须真的传 unconditional_only=True——真实
        # workflow 的 qlib 安装本就无条件，接线退化在干净数据上测不出
        # （变异 CE 实测），只能钉调用点源码。
        import inspect as _inspect
        guard_src = _inspect.getsource(
            EveryPytestWorkflowInstallsQlibItself
            .test_each_pytest_workflow_carries_its_own_install)
        self.assertIn("_unprotected_pytest(entries)", guard_src,
                      "presence 守卫没有用带序带保证标记的判据")

    def test_a_short_circuit_right_side_is_refused(self) -> None:
        """`true || pip install <窗口> <qlib>`：右侧从不运行、步骤照样绿。

        把右侧当独立命令数=把从不运行的安装当真安装（codex P1）。不建模
        控制流，执行无法确立即响亮；`&&` 不拒——前件失败步骤即红，链上每段
        「要么运行、要么响亮」。
        """
        with self.assertRaises(UnlexableShell):
            _commands('true || pip install "numpy>=1.24,<2.0"')
        with self.assertRaises(UnlexableShell):
            _commands('false && pip install "numpy>=1.24,<2.0"')

    def test_a_piped_install_is_refused(self) -> None:
        """`pip install <qlib> | tee log` 的退出码是 tee 的。

        pip 失败被吞、步骤照样绿（sh -e 实测继续执行）——把管道段当独立命令
        数就是把失败可吞的安装当 presence 保证（codex P1，与 `||` 同族）。
        非安装的管道照常拆分。
        """
        with self.assertRaises(UnlexableShell):
            _commands("pip install x | tee install.log")
        with self.assertRaises(UnlexableShell):
            _commands("echo start | pip install x")
        self.assertEqual(
            [["echo", "hi"], ["wc", "-l"]], _commands("echo hi | wc -l"))

    def test_an_allowed_to_fail_install_is_not_presence(self) -> None:
        """`continue-on-error: true` 的安装失败后 Actions 照样跑向 pytest。

        只滤 `if:` 时这种步骤仍算「无条件」——失败的安装被当 presence 保证
        （codex P1）。与 `if:` 同一过滤，step/job 两级都看。
        """
        import tempfile as _tf
        doc = """
jobs:
  j:
    steps:
      - run: pip install git+https://github.com/microsoft/qlib.git@{sha}
        continue-on-error: true
      - run: pytest tests/
""".format(sha="a" * 40)
        with _tf.TemporaryDirectory() as t:
            wf = Path(t) / "w.yml"
            wf.write_text(doc, encoding="utf-8")
            entries = dict(_job_commands(wf))["j"]
        self.assertTrue(
            _unprotected_pytest(entries),
            "允许失败的安装被当成了 presence 保证")

    def test_a_wrapped_pytest_is_still_pytest(self) -> None:
        # `env pytest tests/`——wrapper 照 pip 侧同一套解包（codex P2）。
        for line in ("env pytest tests/", "command pytest",
                     "env RUST_LOG=x python -m pytest"):
            with self.subTest(line=line):
                self.assertTrue(_runs_pytest(_commands(line)),
                                "包着 wrapper 的 pytest 没被认出来")
        self.assertFalse(_runs_pytest(_commands("env python train.py")))

    def test_presence_is_evaluated_per_job(self) -> None:
        """jobs 是隔离 runner——A job 装的 qlib，B job 拿不到。

        摊到 workflow 层，「装在别的 job 里」蒙混过关（codex P1）。
        """
        import tempfile as _tf
        doc = """
jobs:
  install-only:
    steps:
      - run: pip install git+https://github.com/microsoft/qlib.git@{sha}
  test-only:
    steps:
      - run: pytest tests/
""".format(sha="a" * 40)
        with _tf.TemporaryDirectory() as t:
            wf = Path(t) / "w.yml"
            wf.write_text(doc, encoding="utf-8")
            jobs = dict(_job_commands(wf))
        self.assertTrue(
            _unprotected_pytest(jobs["test-only"]),
            "别的 job 里的 qlib 被当成了本 job 的 presence")
        self.assertFalse(
            _runs_pytest([c for c, _ in jobs["install-only"]]))
        # 接线钉：真数据守卫必须真按 job 分组——本仓每个 workflow 只有一个
        # job，接线退回 workflow 摊平在干净数据上测不出（变异 CS 实测），
        # 只能钉调用点源码。
        import inspect as _inspect
        guard_src = _inspect.getsource(
            EveryPytestWorkflowInstallsQlibItself
            .test_each_pytest_workflow_carries_its_own_install)
        self.assertIn("_job_commands(workflow", guard_src,
                      "presence 守卫没有按 job 分组评估")

    def test_a_variable_executable_is_refused(self) -> None:
        # `INSTALLER=pip` 后的 `$INSTALLER install …` 真的执行 pip——展开值
        # 在命令文本之外，静默 False 让安装从覆盖面消失（codex P2）。
        with self.assertRaises(AmbiguousPipCommand):
            _is_pip_install(_commands('$INSTALLER install ".[research]"')[0])

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

    def test_an_option_value_dot_is_excluded_from_target_candidates(
            self) -> None:
        """`pip install --find-links . ".[research]"`：`.` 是选项值。

        判读两段演进：先前多候选即响亮；本轮「紧跟裸选项的 token 不记为
        presence/目标候选」把选项值**确定性排除**——真目标唯一可得，extras
        完整（codex P1 的同一原则）。真正的双目标歧义仍响亮。
        """
        matched = _local_target(
            _commands('pip install --find-links . ".[research]"')[0])
        assert matched is not None
        self.assertEqual("research", matched.group("extras"))
        matched = _local_target(
            _commands('pip install --find-links=. ".[research]"')[0])
        assert matched is not None
        self.assertEqual("research", matched.group("extras"))
        # 两个都不被裸选项遮挡的目标——仍然响亮。
        with self.assertRaises(AmbiguousPipCommand):
            _local_target(_commands('pip install . ".[research]"')[0])

    def test_an_attached_editable_target_is_found(self) -> None:
        # `-e.[research]`：附着式短选项值里的本地目标，同一条 optparse 规则。
        matched = _local_target(_commands("pip install -e.[research]")[0])
        self.assertIsNotNone(matched, "附着式 -e 目标没被认出来")
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
                     'pip_dry_run=1 pip install ".[dev]"',
                     'PIP_NO_CACHE_DIR=1 pip install ".[dev]"',
                     'PIP_REQUIREMENT=r.txt python -m pip install .'):
            with self.subTest(line=line):
                with self.assertRaises(AmbiguousPipCommand):
                    _is_pip_install(_commands(line)[0])
        # 非 PIP_ 前缀的赋值不拦——它不配置 pip。
        self.assertTrue(_is_pip_install(
            _commands('RUST_LOG=debug pip install ".[dev]"')[0]))

    def test_a_wrapped_install_is_still_an_install(self) -> None:
        """`env pip install …` / `command pip install …` 是真安装。

        第 0 个 token 是 wrapper 时直接返回 False，这条安装从 extras 发现、
        直接目标上界、qlib 窗口三处覆盖面里同时消失（codex P2）。解包这两个
        POSIX 定义的「运行后面那条命令」实用程序；wrapper 的裸选项可能吃值，
        多义即响亮。
        """
        for line in ('env pip install ".[dev]"',
                     'command pip install ".[dev]"',
                     'env MY_VAR=1 pip install ".[dev]"',
                     'env command pip install ".[dev]"'):
            with self.subTest(line=line):
                self.assertTrue(_is_pip_install(_commands(line)[0]),
                                "包着 wrapper 的安装没被认出来")
        with self.assertRaises(AmbiguousPipCommand):
            _is_pip_install(_commands('env -u X pip install ".[dev]"')[0])
        # env 携带的 PIP_*= 赋值照样响亮（形状规则经 assignments 生效）。
        with self.assertRaises(AmbiguousPipCommand):
            _is_pip_install(_commands('env PIP_DRY_RUN=1 pip install .')[0])

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
        （codex P2）。裸形态 `-r`、`=` 连写、**附着式**短选项值
        （`-rrequirements.txt`，optparse 规则）三种形态同拦；约束文件
        `-c/--constraint` 同理。
        """
        for line in ("pip install --requirement=requirements.txt",
                     "pip install -r requirements.txt",
                     "pip install -rrequirements.txt",
                     "pip install -cconstraints.txt x>=1,<2",
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


class NoCommandAnywhereCarriesAPipEnvironmentToken(unittest.TestCase):
    """`PIP_*=` 形状的 token 在**任何**命令里出现都响亮——载体不枚举。

    行内前缀已拦，但 `export PIP_DRY_RUN=1; pip install …` 把赋值放在**另一
    条命令**里、随后持续生效（codex P1）；`env PIP_X=1 pip …` 又是一种载体。
    与其枚举 export/env/set，不如认形状：这个 token 无论坐在哪条命令里，
    唯一的作用就是配置 pip。大小写不敏感（Windows 腿）。
    """

    def test_no_workflow_command_carries_the_shape(self) -> None:
        scanned = 0
        offenders: list[str] = []
        for workflow in _workflows():
            commands = _workflow_commands(workflow)
            scanned += len(commands)
            offenders.extend(
                f"{workflow.name}: {token}"
                for token in _pip_environment_offenders(commands))
        self.assertEqual([], offenders, "有命令在经环境配置 pip")
        self.assertGreaterEqual(scanned, 10, f"只扫到 {scanned} 条命令")

    def test_every_carrier_spelling_is_caught(self) -> None:
        # 直接对着**上面那条真数据守卫用的同一个 helper** 测——真实 workflow
        # 是干净的，负断言测不出扫描被删（变异 BO），作证要在 helper 层做。
        # 含 PowerShell 形态：Windows 腿默认跑 pwsh（codex P2）。
        for line in ("export PIP_DRY_RUN=1",
                     "env pip_dry_run=1 pip install .",
                     "$env:PIP_DRY_RUN=1",
                     "Set-Item Env:PIP_DRY_RUN 1",
                     "set PIP_REQUIREMENT=r.txt"):
            with self.subTest(line=line):
                self.assertTrue(
                    _pip_environment_offenders(_commands(line)),
                    "这种载体里的 PIP_*= 形状没被认出来")
        # 反面：非 PIP_ 前缀的赋值不拦。
        self.assertEqual(
            [], _pip_environment_offenders(_commands("export RUST_LOG=debug")))


class WorkflowEnvironmentCannotConfigurePip(unittest.TestCase):
    """workflow / job / step 的 `env:` 会施加到 run 命令上。

    `env: {PIP_DRY_RUN: "1"}` 让步骤里的 pip 什么都不装、`PIP_REQUIREMENT`
    从文件注入依赖——而 run 文本上毫无痕迹（codex P1）。与行内 `PIP_*=`
    赋值同一处置：任何一层 env 声明 `PIP_*` 键即响亮。
    """

    def test_no_workflow_env_declares_a_pip_key(self) -> None:
        seen = 0
        offenders: list[str] = []
        for workflow in _workflows():
            document = yaml.safe_load(workflow.read_text(encoding="utf-8"))
            keys = _environment_keys(document)
            seen += len(keys)
            offenders.extend(
                f"{workflow.name}: {key}"
                for key in _pip_env_key_offenders(keys))
        self.assertEqual(
            [], offenders,
            "workflow env 在配置 pip —— run 文本上看不见它改变了什么")
        # 底数：test.yml 的多个步骤声明 RUN_E2E，regen-baseline 声明 REASON。
        # 读到 0 个键说明 env 收集失效——守卫在整个注入面上空转。
        self.assertGreaterEqual(seen, 3, f"只读到 {seen} 个 env 键")

    def test_every_declaration_level_is_seen(self) -> None:
        document = {
            "env": {"PIP_A": "1"},
            "jobs": {"j": {
                "env": {"pip_b": "1"},        # 小写同效（Windows 腿）
                "steps": [{"run": "pip install .", "env": {"PIP_C": "1"}}],
            }},
        }
        keys = _environment_keys(document)
        self.assertEqual(["PIP_A", "pip_b", "PIP_C"], keys,
                         "三层 env 没有全部被收集")
        # 对着真数据守卫用的同一个 helper 测——含小写键（Windows 腿等效）。
        self.assertEqual(["PIP_A", "pip_b", "PIP_C"],
                         _pip_env_key_offenders(keys),
                         "小写的 pip 键没被认出来")
        self.assertEqual([], _pip_env_key_offenders(["RUN_E2E", "REASON"]))


class EveryPytestWorkflowInstallsQlibItself(unittest.TestCase):
    """跑 pytest 的 workflow 必须**自己**装 qlib——底数不许全局摊。

    test.yml 的 qlib 安装被删时，regen-baseline 的那条让全局 `>= 1` 照样
    过线，而六条测试腿在 `pytest.importorskip("qlib")` 下静默绿（codex P1）。
    """

    def test_each_pytest_workflow_carries_its_own_install(self) -> None:
        pytest_workflows = 0
        offenders = []
        for workflow in _workflows():
            # 判据按 **job** 且**按顺序**评估——jobs 是隔离 runner，
            # 步骤顺序执行：qlib 必须在每个 pytest **之前**有保证地装上
            # （codex 三条 P1 的合并终态）。
            for job_name, entries in _job_commands(workflow):
                if _runs_pytest([c for c, _ in entries]):
                    pytest_workflows += 1
                    if _unprotected_pytest(entries):
                        offenders.append(f"{workflow.name}:{job_name}")
        self.assertEqual([], offenders, "这些 workflow 跑 pytest 却不装 qlib")
        self.assertGreaterEqual(
            pytest_workflows, 1, "一个跑 pytest 的 workflow 都没发现——覆盖面塌了")

    def test_the_rule_itself_bites_on_synthetic_input(self) -> None:
        # 两层作证：真实 workflow 干净，规则被删负断言测不出——直接单测。
        # entries = 按步骤顺序的 (命令, 有保证) 列表。
        qlib = ["pip", "install",
                "git+https://github.com/microsoft/qlib.git@" + "a" * 40]
        pytest_cmd = ["pytest", "tests/"]
        # 有保证的 qlib 在 pytest **之前** → 合规（pytest 本身可以是条件腿）。
        self.assertFalse(_unprotected_pytest([(qlib, True), (pytest_cmd, False)]))
        # qlib 在 pytest **之后** → 违例：前面的测试已经裸跑（codex P1 顺序）。
        self.assertTrue(_unprotected_pytest([(pytest_cmd, False), (qlib, True)]))
        # qlib 只在条件步骤里（无保证）→ 违例。
        self.assertTrue(_unprotected_pytest([(qlib, False), (pytest_cmd, True)]))
        # 根本没有 qlib → 违例；不跑 pytest → 无义务。
        self.assertTrue(_unprotected_pytest([(pytest_cmd, True)]))
        self.assertFalse(_unprotected_pytest([(["echo", "hi"], True)]))


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

    def test_a_window_as_an_option_value_is_not_credited(self) -> None:
        """`--report "numpy>=1.24,<2.0"` 的 numpy 串是文件名，不是约束。

        按 token 成员资格记账，qlib 那次解析其实没带窗口而三道守卫全绿
        （codex P1）。`_credited` 排除裸选项的疑似值；正常写法全数保留。
        """
        line = (f'pip install {self.QLIB} --report "numpy>=1.24,<2.0" '
                f'"scipy>=1.10,<1.14"')
        credited = _credited(_commands(line)[0])
        self.assertNotIn("numpy>=1.24,<2.0", credited,
                         "选项值里的窗口串被记了账")
        self.assertIn("scipy>=1.10,<1.14", credited,
                      "正常位置的窗口被误排除")
        normal = _credited(_commands(
            f'pip install "numpy>=1.24,<2.0" "scipy>=1.10,<1.14" {self.QLIB}')[0])
        self.assertIn("numpy>=1.24,<2.0", normal)
        # 接线钉：两处记账调用点必须真用 _credited——真实 workflow 的窗口
        # 都在正常位置，接线退化在干净数据上测不出（变异 DH 实测）。
        import inspect as _inspect
        self.assertIn("_credited(tokens)", _inspect.getsource(
            EveryRestatementOfAPinnedWindowMatches
            .test_the_qlib_install_command_carries_both_windows),
            "窗口检查没有走可记账 token")
        self.assertIn("_credited(command)", _inspect.getsource(
            EveryRestatementOfAPinnedWindowMatches
            .test_every_workflow_restatement_is_byte_identical),
            "重述扫描没有走可记账 token")

    def test_a_backgrounded_install_is_refused(self) -> None:
        """`pip install … & pytest`：安装被后台化，失败不传播。

        codex 实测 `bash -e -o pipefail -c 'false & printf x'` 返回 0——
        pytest 开跑时安装可能还没完成或已失败（P1）。左侧与管道/AND 链
        同罪；右侧不受牵连，非安装的 & 照常拆分。
        """
        with self.assertRaises(UnlexableShell):
            _commands("pip install x & pytest tests/")
        self.assertEqual([["echo", "a"], ["pytest"]],
                         _commands("echo a & pytest"))

    def test_a_destination_redirected_install_is_not_presence(self) -> None:
        """`--target=/tmp/detached` 把 qlib 装到 import path 之外。

        随后的 pytest 进程 import 不到它——`importorskip` 全跳而 presence
        守卫照绿（codex P1）。目的地选项是 pip 文档定义的闭集
        （--target/--root/--prefix），裸形态与 `=` 连写都不记账。
        """
        for extra in ("--target=/tmp/detached", "--target /tmp/detached",
                      "--root=/tmp/r", "--prefix=/tmp/p"):
            with self.subTest(option=extra):
                line = f"pip install {extra} {self.QLIB}"
                self.assertEqual([], _qlib_pin_installs(_commands(line)),
                                 "改了目的地的安装被记成了 presence")
        self.assertEqual(
            1, len(_qlib_pin_installs(_commands(f"pip install {self.QLIB}"))))

    def test_a_pin_as_an_option_value_is_not_presence(self) -> None:
        """`pip install --trusted-host <qlib-pin> <窗口>` 装的是那两个窗口。

        pin 只是 `--trusted-host` 的值——记进 presence，qlib 缺席而两道守卫
        全绿（codex P1）。紧跟裸选项（非 -e/--editable）的 pin 不记账；
        `-e <pin>` 是合法的 editable-VCS 安装，照记。
        """
        line = (f'pip install --trusted-host {self.QLIB} '
                f'"numpy>=1.24,<2.0" "scipy>=1.10,<1.14"')
        self.assertEqual([], _qlib_pin_installs(_commands(line)),
                         "选项值里的 pin 被记成了安装")
        self.assertEqual(
            1, len(_qlib_pin_installs(_commands(f"pip install -e {self.QLIB}"))),
            "-e 的 VCS 目标没被记为安装")

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

    def test_an_install_inside_a_conditional_construct_is_loud(self) -> None:
        """条件构造内的 pip 安装——执行结果/执行与否无法确立。

        `if pip install <qlib>; then …`：安装失败被 if 吞掉、步骤照样绿地跑
        向 pytest，importorskip 让 qlib 侧静默蒸发；`then pip install` 的体
        又只在条件成立时运行（codex P1，两轮判读的合并终态：先前认「可执行
        体在保留字之后」只对了一半——认出来之后还要问执行可不可确立）。
        保留字仍是 POSIX 闭集；条件构造内的安装一律响亮，拆成独立步骤。
        """
        for keyword in ("if", "while", "until", "elif", "else", "do", "then", "!"):
            with self.subTest(保留字=keyword):
                with self.assertRaises(AmbiguousPipCommand):
                    _is_pip_install(
                        _commands(f'{keyword} pip install ".[x]"')[0])
        # 反面：保留字后的**非安装**命令不响亮（条件本身随便写）；
        # 无保留字的安装照常认。
        self.assertFalse(_is_pip_install(_commands("if true")[0]))
        self.assertTrue(_is_pip_install(_commands('pip install ".[x]"')[0]))


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
                for token in _credited(command):
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
            for command in _qlib_pin_installs(_workflow_commands(
                workflow, unconditional_only=True))
        ]
        self.assertGreaterEqual(
            len(commands), 1, "没有在项目之前装 qlib 的命令 —— 本守卫已失效")
        for name, tokens in commands:
            for package, constraint in declared.items():
                with self.subTest(包=package, workflow=name):
                    # 窗口只在**可记账** token 里找——裸选项的疑似值不算
                    # （codex P1：--report 的文件名不是约束）。
                    self.assertIn(
                        constraint, _credited(tokens),
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
