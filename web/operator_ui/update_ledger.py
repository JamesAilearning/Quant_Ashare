"""运行台账 reader —— 只读，纯解析，不碰进程也不碰 Streamlit。

`daily_update` 每次运行在终态**追加**一行到
``<provider>.daily_update_ledger.jsonl``。状态工件是单文件、每次运行盖掉上一次，
所以它只回答「**这一次**怎么样」；台账回答「**最近几次**是什么形态」——
2026-08-17 / 08-20 / 08-21 连着三晚失败拖到第三晚才被发现，正是因为没有任何
东西记录那个**模式**。

## 容错，但不许静默

一条坏行不能毒死整份台账：解析失败的行被**计数**并跳过，其余照常返回。计数要
交出去——「有 3 条读不了」与「一条都没有」对操作人是两回事，而这个页面已经为
「留白读起来像没有更多可说」付过一次学费。

## 与写入侧的常量对齐

`web/` 不 import 管线层，所以文件名与 schema 版本在这里各声明一次，由
`tests/logic/test_update_ledger_reader.py` 钉住两边相等——与 `update_status`
同样的处理。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# Mirrors src/data_pipeline/daily_update.py LEDGER_FILENAME / LEDGER_SCHEMA_VERSION.
# Duplicated by design (web/ must not import the pipeline layer); the logic test
# pins each to the writer's value.
LEDGER_FILENAME = "daily_update_ledger.jsonl"
LEDGER_SCHEMA_VERSION = 1

#: 条带默认取多少条。取「最近的」而不是全部：台账只增不减，而操作人要看的是
#: 「最近有没有连着栽」，不是全部历史。
DEFAULT_RECENT = 7


@dataclass(frozen=True)
class LedgerRun:
    """台账里的一次运行。字段与写入侧的终态记录同名。"""

    run_date: str = ""
    started_at: str = ""
    finished_at: str = ""
    exit_code: int | None = None
    failed_stage: str | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    @property
    def elapsed_seconds(self) -> float | None:
        """耗时由两个时间戳**推导**，不是台账里的字段。

        存第三份只会多一个与推导值分叉的地方。时间戳读不出来时返回 None——
        不知道就不编。
        """
        try:
            start = datetime.fromisoformat(self.started_at)
            end = datetime.fromisoformat(self.finished_at)
        except (TypeError, ValueError):
            return None
        return (end - start).total_seconds()


@dataclass(frozen=True)
class LedgerHistory:
    """读取结果。``kind`` 驱动页面的渲染分支。

    * ``ok``          —— 读到了（``runs`` 可能仍为空：文件在但没有本 provider 的行）
    * ``missing``     —— 台账文件不存在（新机器 / 首次运行之前）
    * ``unreadable``  —— 文件在但读不动（权限 / IO），``error`` 带原因
    """

    kind: str
    path: Path
    runs: tuple[LedgerRun, ...] = ()
    malformed: int = 0
    foreign: int = 0
    error: str = ""


def ledger_path_for_provider(provider_dir: Path) -> Path:
    """``<provider>.<LEDGER_FILENAME>`` —— provider 目录的兄弟，且为它独有。

    兄弟：原子切换只重命名 provider 目录本身，兄弟因此存活。名派生：
    ``<provider>.parent/<FILENAME>`` 对同父目录的两个 bundle 会塌成一个，而这个
    仓库正是那种布局。与写入侧的推导由测试钉住。
    """
    resolved = provider_dir.resolve()
    if not resolved.name:
        # 合法的相对写法（如 "."）解析后仍有名字；只有文件系统根是无名的，
        # 而它没有可派生的兄弟。抛出而不是返回，页面才能说出这件事。
        raise ValueError(
            f"无法从 provider 路径 {provider_dir!r} 推导运行台账位置："
            f"它解析为文件系统根 {resolved!r}，没有可派生的兄弟名"
        )
    return resolved.with_name(f"{resolved.name}.{LEDGER_FILENAME}")


#: v1 行必须**非空**的身份/时间字段（`failed_stage` 允许 null，单列在下面判；
#: `detail` 只要求是 str——它的空与非空是写入侧的措辞问题，不是身份问题）。
_REQUIRED_NONEMPTY = ("provider_dir", "run_date", "started_at", "finished_at")


def _is_valid_v1(record: dict[str, object]) -> bool:
    """这行是不是一条**可解释的** v1 记录。

    不校验就把任何带对 provider 的 JSON 对象当成一次运行渲染出去：一条未来
    版本的记录、或 ``exit_code: true`` 这种（`isinstance(True, int)` 在 Python
    里为真！），都会被显示成一次**失败的运行**——把损坏的数据讲成事实，比
    报「读不了」糟得多（codex P2）。与状态工件 reader 同样的处理：版本不对就
    不用 v1 语义去解释它。
    """
    version = record.get("schema_version")
    # 钉**类型**再比值：JSON 的 `true` 与 `1.0` 在 Python 里都 `== 1`，只比值
    # 会拿 v1 语义去解释一条版本字段本身就坏掉的行（codex P2）。状态 reader
    # 对 exit_code 用的同一招（bool 是 int 的子类）。
    if type(version) is not int or version != LEDGER_SCHEMA_VERSION:
        return False
    exit_code = record.get("exit_code")
    # `bool` 是 `int` 的子类，必须显式排除，否则 True/False 会被当成退出码。
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        return False
    # `failed_stage` 必须**在场**：缺字段与 `null` 在这里不是一回事，前者说明
    # 这行不是写入侧产的。写入侧在每个终态都落这个字段（成功 null、失败带阶段）。
    if "failed_stage" not in record:
        return False
    stage = record["failed_stage"]
    if stage is not None and not (isinstance(stage, str) and stage):
        return False
    # 跨字段不变式，照抄状态工件 reader 已有的那一条：退出码与失败阶段互相印证。
    # 只查字段类型的话，`exit_code: 0` 配 `failed_stage: "fetch"` 这种自相矛盾
    # 的行会原样通过，而 `LedgerRun.ok` 会把它渲染成一次**成功**的运行——把
    # 损坏的数据讲成事实（codex 第二轮 P2）。
    if (exit_code == 0) != (stage is None):
        return False
    # 身份/时间字段要**非空**：只查 `isinstance(str)` 会放过空串，一条
    # `exit_code: 0` 配三个空时间戳的行会被渲染成一次「日期不明的成功」——
    # 写入侧从不产这种行（codex P2）。
    if not all(
        isinstance(record.get(key), str) and record.get(key)
        for key in _REQUIRED_NONEMPTY
    ):
        return False
    return isinstance(record.get("detail"), str)


def _describes(record: object, provider_key: str) -> bool:
    """这行记录描述的是不是**这个** provider？

    写入侧把 resolve + normcase 之后的 provider 路径写进每一行（照抄状态工件的
    身份推理，codex #434 r18）。归一化方式与写入侧的 `_norm` 相同，由测试钉住
    ——两个模块刻意不互相 import。
    """
    if not isinstance(record, dict):
        return False
    stamped = record.get("provider_dir")
    return isinstance(stamped, str) and os.path.normcase(stamped) == provider_key


def read_ledger(
    path: Path, *, provider_dir: Path, recent: int = DEFAULT_RECENT,
) -> LedgerHistory:
    """读最近 ``recent`` 次运行，**新的在前**。

    只保留描述本 provider 的行；别人的行被计数（``foreign``）而不是混进来。
    解析不了的行同样被计数（``malformed``）而不是让整份台账失败。
    """
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return LedgerHistory(kind="missing", path=path)
    except OSError as exc:
        return LedgerHistory(
            kind="unreadable", path=path,
            error=f"{type(exc).__name__}: {exc}")

    provider_key = os.path.normcase(str(provider_dir.resolve()))
    runs: list[LedgerRun] = []
    malformed = 0
    foreign = 0
    # **逐行严格**解码。整份 replace-解码曾以为「坏行随后多半解析失败」——
    # 错：坏字节落在 JSON 字符串**里面**（比如 detail）时，替换字符 `�` 仍是
    # 合法 JSON，那行验形照过、渲染成一次真实运行、malformed 计零——把被
    # 悄悄改写过的数据当成事实交给操作人（codex P2）。解码失败 = 坏行。
    for raw_line in raw.split(b"\n"):
        if not raw_line.strip():
            continue
        try:
            record = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            malformed += 1
            continue
        if not isinstance(record, dict) or not _is_valid_v1(record):
            # **先验形，后分类**。`{}` 或 `{"provider_dir": 5}` 不是「别人的
            # 行」，是坏行——把它计进 foreign，页面就会告诉操作人「这行属于
            # 另一个 provider」，而不是披露台账损坏（codex P2）。「foreign」
            # 这个称谓只配给一条**完整合法**、只是身份不同的 v1 记录。
            malformed += 1
            continue
        if not _describes(record, provider_key):
            foreign += 1
            continue
        runs.append(LedgerRun(
            run_date=record["run_date"],
            started_at=record["started_at"],
            finished_at=record["finished_at"],
            exit_code=record["exit_code"],
            failed_stage=record.get("failed_stage"),
            detail=record["detail"],
        ))
    return LedgerHistory(
        kind="ok", path=path,
        runs=tuple(reversed(runs))[:max(recent, 0)],
        malformed=malformed, foreign=foreign,
    )


def consecutive_failures(history: LedgerHistory) -> int:
    """从最近一次往回数，连着失败了几次。

    这就是三晚事故里没人看得见的那个数。它**只**数台账里已有的行——台账没记到
    的运行（比如 exit 2 / 17 那类根本没进编排器的）不在其中，也不该被算进来。
    """
    count = 0
    for run in history.runs:
        if run.exit_code is None or run.ok:
            break
        count += 1
    return count
