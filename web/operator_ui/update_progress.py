"""从共享日志里取出**最后一条** fetch 进度行——纯解析,不碰进程也不碰
Streamlit。

`daily_update` 的 fetch 阶段每 200 支票打一条进度行
(`src/data/tushare/fetcher.py`)。信息本来就在日志里,只是埋在几百行中,
操作人得展开日志尾部自己找。这里把它抬出来,省掉那一步。

## 两件**不做**的事,以及为什么

**不做百分比进度条**:那条行的分母是「某个 endpoint 的某一年」的票数,而
fetch 只是六个阶段(修复/fetch/snapshot/rebuild/validate/swap)里的第二个。
把 2400/5883 渲染成一根 40% 的条,会让人以为整轮走了四成。

**不声称它属于哪一次运行**。日志是**追加**的,里面躺着历次运行的进度行,
而每行只带 ``HH:MM:SS``、**不带日期**;计划任务启动的运行也不写任何带日期
的起始横幅(``[run_center]`` 标记只有 UI 启动才写)。于是「昨天 21:00」与
「今天 21:00」在数据里**完全不可区分**——

* 试过「日志 mtime 早于本次 started_at 就丢弃」:抓不住「本次已写了非进度
  行」的情形;
* 试过「挂钟回退即运行边界」:抓不住**起得更晚**的重跑(旧进度 10:30、新运行
  15:00 起,时间只增不减);
* 试过「进度行时刻 ≥ started_at 时刻」:抓不住跨天(昨天 21:00 vs 今天 20:43
  起跑);
* 试过「用 mtime 当日期锚点往回推断跨天」:24 小时的间隔与 30 分钟的间隔在
  时分秒上长得一模一样。

结论是**结构性的**:靠这份日志做不到精确归属。所以本模块只回答「日志尾部
最后一条进度行是什么、它带的时刻是几点」,**归属留给页面如实披露**,而不是
用启发式假装消除不确定性(codex #450 r1/r2)。要精确归属,得先让写入侧落一个
**带日期**的运行边界——那是另一个改动,不在本模块的承诺里。
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

#: 行首的挂钟。只到秒、不含日期——这正是归属做不到精确的原因。
_CLOCK_RE = re.compile(r"^(?P<clock>\d{2}:\d{2}:\d{2})")


@dataclass(frozen=True)
class FetchProgress:
    """日志尾部最后一条 fetch 进度行。

    ``done``/``total`` 是**该 endpoint 该年**的票数,不是整轮进度;``at`` 是
    该行自带的时刻(只到秒、不含日期)。两个范围限制都要由展示方说出来。
    """

    endpoint: str
    year: int
    done: int
    total: int
    written: int
    skipped: int
    at: str = ""

    def describe(self) -> str:
        """一行如实描述,时刻与范围都写在里面。"""
        stamp = f"{self.at} " if self.at else ""
        return (
            f"{stamp}{self.endpoint} {self.year} 年:{self.done}/{self.total} 支"
            f"(已写 {self.written}、跳过 {self.skipped})"
        )


def last_fetch_progress(log_text: str) -> FetchProgress | None:
    """日志文本里**最后**一条进度行;没有则 ``None``。

    取最后一条而不是第一条:日志是追加的,前面还躺着历次运行的进度行。
    ``total`` 为 0 的行直接丢弃——那种行说不出任何进度,渲染出来只会是
    ``0/0``。

    **本函数不判断这条属于哪一次运行**(见模块 docstring):它就是「尾部最后
    一条」,调用方必须把这一点如实告诉读者。
    """
    if not log_text:
        return None
    hit = None
    hit_line = ""
    for line in log_text.splitlines():
        match = _PROGRESS_RE.search(line)
        if match is not None:
            hit, hit_line = match, line
    if hit is None:
        return None
    total = int(hit.group("total"))
    if total <= 0:
        return None
    clock = _CLOCK_RE.match(hit_line)
    return FetchProgress(
        endpoint=hit.group("endpoint"),
        year=int(hit.group("year")),
        done=int(hit.group("done")),
        total=total,
        written=int(hit.group("written")),
        skipped=int(hit.group("skipped")),
        at=clock.group("clock") if clock else "",
    )
