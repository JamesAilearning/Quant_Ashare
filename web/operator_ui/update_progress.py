"""从共享日志里读出「fetch 走到哪了」——纯解析,不碰进程也不碰 Streamlit。

`daily_update` 的 fetch 阶段每 200 支票打一条进度行
(`src/data/tushare/fetcher.py`)。信息本来就在日志里,只是埋在几百行里,
操作人得展开日志尾部自己找。这里把最后一条抬出来,让「正在运行」那句话
能接上一句「走到哪了」。

**刻意不做百分比进度条**:这条行的分母是「某个 endpoint 的某一年」的票数,
而 fetch 只是六个阶段(修复/fetch/snapshot/rebuild/validate/swap)里的第二个。
把 2400/5883 渲染成一根 40% 的条,会让人以为整轮走了四成——一行如实文字
比一根会撒谎的条有用。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: 与 ``fetcher`` 的格式串一一对应:
#: ``"  %s year=%d progress: %d/%d tickers (written=%d, skipped=%d)"``
_PROGRESS_RE = re.compile(
    r"(?P<endpoint>\S+)\s+year=(?P<year>\d+)\s+progress:\s*"
    r"(?P<done>\d+)\s*/\s*(?P<total>\d+)\s+tickers\s*"
    r"\(written=(?P<written>\d+),\s*skipped=(?P<skipped>\d+)\)"
)


@dataclass(frozen=True)
class FetchProgress:
    """日志里最后一条 fetch 进度行。

    ``done``/``total`` 是**该 endpoint 该年**的票数,不是整轮进度——用它的
    地方必须把这个范围说出来。
    """

    endpoint: str
    year: int
    done: int
    total: int
    written: int
    skipped: int

    def describe(self) -> str:
        """一行如实描述,范围写在里面。"""
        return (
            f"{self.endpoint} {self.year} 年:{self.done}/{self.total} 支"
            f"(已写 {self.written}、跳过 {self.skipped})"
        )


def last_fetch_progress(log_text: str) -> FetchProgress | None:
    """日志文本里**最后**一条进度行;没有则 ``None``。

    取最后一条而不是第一条:日志是追加的,前面还躺着历次运行的进度行。
    ``total`` 为 0 的行直接丢弃——那种行说不出任何进度,渲染出来只会是
    ``0/0``,不如不显示。
    """
    if not log_text:
        return None
    last = None
    for match in _PROGRESS_RE.finditer(log_text):
        last = match
    if last is None:
        return None
    total = int(last.group("total"))
    if total <= 0:
        return None
    return FetchProgress(
        endpoint=last.group("endpoint"),
        year=int(last.group("year")),
        done=int(last.group("done")),
        total=total,
        written=int(last.group("written")),
        skipped=int(last.group("skipped")),
    )
