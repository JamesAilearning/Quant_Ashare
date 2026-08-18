"""从共享日志里读出「本次运行的 fetch 走到哪了」——纯解析,不碰进程也不碰
Streamlit。

`daily_update` 的 fetch 阶段每 200 支票打一条进度行
(`src/data/tushare/fetcher.py`)。信息本来就在日志里,只是埋在几百行里。

**刻意不做百分比进度条**:那条行的分母是「某个 endpoint 的某一年」的票数,
而 fetch 只是六个阶段(修复/fetch/snapshot/rebuild/validate/swap)里的第二个。
把 2400/5883 渲染成一根 40% 的条,会让人以为整轮走了四成——一行如实文字
比一根会撒谎的条有用。

**归属问题**(codex #450 r1):日志是**追加**的,里面躺着历次运行的进度行,
而每行只带 `HH:MM:SS`、不带日期,日志里也没有每次运行的起始横幅
(`[run_center]` 标记只有 UI 启动才写,计划任务不写)。所以「取最后一条」
不等于「本次运行的进度」——一次刚起步、还没打出第一条进度行的运行,尾部
那条属于**上一次**。这里用两道与写入侧无关的判据把它挡掉:

1. 日志的 mtime 必须不早于本次 `started_at`——本次开始后日志一个字都没写,
   尾部那条必然是旧的;
2. 挂钟不得回退——同一次运行内时间戳单调不减,若最后一条进度行**之后**
   出现更早的时间戳,说明中间跨了运行边界,那条属于上一次。

两道都过不了就返回 ``None``,页面据此明说「还没有本次运行的进度行」。
宁可不显示,也不把上一次的进度当成这一次的。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

#: 与 ``fetcher`` 的格式串一一对应:
#: ``"  %s year=%d progress: %d/%d tickers (written=%d, skipped=%d)"``
_PROGRESS_RE = re.compile(
    r"(?P<endpoint>\S+)\s+year=(?P<year>\d+)\s+progress:\s*"
    r"(?P<done>\d+)\s*/\s*(?P<total>\d+)\s+tickers\s*"
    r"\(written=(?P<written>\d+),\s*skipped=(?P<skipped>\d+)\)"
)

#: 行首的挂钟。本仓日志行只有时分秒,没有日期——这正是需要「回退」判据的原因。
_CLOCK_RE = re.compile(r"^(?P<h>\d{2}):(?P<m>\d{2}):(?P<s>\d{2})")


@dataclass(frozen=True)
class FetchProgress:
    """本次运行最后一条 fetch 进度行。

    ``done``/``total`` 是**该 endpoint 该年**的票数,不是整轮进度——用它的
    地方必须把这个范围说出来。
    """

    endpoint: str
    year: int
    done: int
    total: int
    written: int
    skipped: int
    at: str = ""

    def describe(self) -> str:
        """一行如实描述,范围与时刻都写在里面。"""
        stamp = f"{self.at} " if self.at else ""
        return (
            f"{stamp}{self.endpoint} {self.year} 年:{self.done}/{self.total} 支"
            f"(已写 {self.written}、跳过 {self.skipped})"
        )


def _clock_seconds(line: str) -> int | None:
    match = _CLOCK_RE.match(line)
    if match is None:
        return None
    return (
        int(match.group("h")) * 3600
        + int(match.group("m")) * 60
        + int(match.group("s"))
    )


def last_fetch_progress(log_text: str) -> FetchProgress | None:
    """日志文本里**最后**一条进度行;没有、或它落在上一次运行里则 ``None``。

    取最后一条而不是第一条:日志是追加的,前面还躺着历次运行的进度行。
    ``total`` 为 0 的行直接丢弃——那种行说不出任何进度。

    **挂钟回退即边界**:同一次运行内时间戳单调不减。若这条之后出现更早的
    时间戳,中间必然跨了运行边界,这条属于上一次运行,丢弃(codex #450 r1)。
    """
    if not log_text:
        return None
    lines = log_text.splitlines()
    hit_index = None
    hit = None
    for index, line in enumerate(lines):
        match = _PROGRESS_RE.search(line)
        if match is not None:
            hit_index, hit = index, match
    if hit is None or hit_index is None:
        return None
    total = int(hit.group("total"))
    if total <= 0:
        return None
    at = _clock_seconds(lines[hit_index])
    if at is not None:
        for line in lines[hit_index + 1 :]:
            later = _clock_seconds(line)
            if later is not None and later < at:
                return None
    stamp = _CLOCK_RE.match(lines[hit_index])
    return FetchProgress(
        endpoint=hit.group("endpoint"),
        year=int(hit.group("year")),
        done=int(hit.group("done")),
        total=total,
        written=int(hit.group("written")),
        skipped=int(hit.group("skipped")),
        at=stamp.group(0) if stamp else "",
    )


def progress_for_run(
    log_text: str,
    *,
    log_mtime: datetime | None,
    started_at: str,
) -> FetchProgress | None:
    """能**归属到本次运行**的最后一条进度;归属不了就 ``None``。

    ``log_mtime`` 早于 ``started_at`` = 本次运行开始后日志一个字都没写,
    尾部那条必然属于上一次。``started_at`` 解析不出来时同样返回 ``None``
    ——无从归属就不显示,绝不猜。
    """
    if not started_at:
        return None
    try:
        started = datetime.fromisoformat(started_at)
    except ValueError:
        return None
    if log_mtime is None:
        return None
    if started.tzinfo is not None and log_mtime.tzinfo is None:
        return None
    if started.tzinfo is None and log_mtime.tzinfo is not None:
        return None
    if log_mtime < started:
        return None
    return last_fetch_progress(log_text)
