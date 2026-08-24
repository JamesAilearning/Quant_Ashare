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

    # 与写入侧一样按 UTF-8 解码；坏字节用替换字符而不是让整份台账炸掉——
    # 那一行随后多半解析失败，于是被计入 malformed，信息不会被悄悄吞掉。
    text = raw.decode("utf-8", errors="replace")
    provider_key = os.path.normcase(str(provider_dir.resolve()))
    runs: list[LedgerRun] = []
    malformed = 0
    foreign = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            malformed += 1
            continue
        if not isinstance(record, dict):
            malformed += 1
            continue
        if not _describes(record, provider_key):
            foreign += 1
            continue
        exit_code = record.get("exit_code")
        runs.append(LedgerRun(
            run_date=str(record.get("run_date") or ""),
            started_at=str(record.get("started_at") or ""),
            finished_at=str(record.get("finished_at") or ""),
            exit_code=exit_code if isinstance(exit_code, int) else None,
            failed_stage=(
                record.get("failed_stage")
                if isinstance(record.get("failed_stage"), str) else None),
            detail=str(record.get("detail") or ""),
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
