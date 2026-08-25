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

结论是**结构性的**:靠这份日志本身做不到精确归属。所以本模块曾只回答「日志
尾部最后一条进度行是什么、它带的时刻是几点」,归属留给页面如实披露,而不是用
启发式假装消除不确定性(codex #450 r1/r2)。

## 边界落地之后(2026-08-24-daily-update-run-ledger)

上面那段的最后一句是「要精确归属,得先让写入侧落一个**带日期**的运行边界——
那是另一个改动」。**那个改动做了**:`run_daily_update` 现在在每次(非 dry-run)
运行开始时往日志里写一行

    [daily_update] run started <ISO8601 +08:00> provider=<normalized>

于是本模块多回答一个问题:**这条进度属不属于某次运行**。判据不再是启发式,
而是那条边界——它之后的行属于它,就这么简单。

两件事**仍然不做**:

* 窗口里找不到边界时**不去扩大读取直到找到**。一次两小时运行的日志无界增长,
  那条路通向没有上界的读取。此时如实报「无法归属」——也就是边界落地之前的
  行为,没有退步。
* 别的 provider 留下的边界**不采纳**(照抄状态工件的身份推理,codex #434 r18)。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

#: 与 ``fetcher`` 的格式串一一对应:
#: ``"  %s year=%d progress: %d/%d tickers (written=%d, skipped=%d)"``
_PROGRESS_RE = re.compile(
    r"(?P<endpoint>\S+)\s+year=(?P<year>\d+)\s+progress:\s*"
    r"(?P<done>\d+)\s*/\s*(?P<total>\d+)\s+tickers\s*"
    r"\(written=(?P<written>\d+),\s*skipped=(?P<skipped>\d+)\)"
)

#: 行首的挂钟。只到秒、不含日期——这正是**日志行本身**归属做不到精确的原因。
_CLOCK_RE = re.compile(r"^(?P<clock>\d{2}:\d{2}:\d{2})")

# Mirrors src/data_pipeline/daily_update.py RUN_BOUNDARY_MARK. Duplicated by
# design (web/ must not import the pipeline layer); the logic test pins the two
# to the same value.
RUN_BOUNDARY_MARK = "[daily_update] run started"

#: 边界行。logger 会在前面加上自己的 `HH:MM:SS [name] LEVEL — ` 前缀,所以这里
#: 只在行内**搜**标记,不锚定行首。
# `re.MULTILINE` 是**承重**的:不带它,`$` 只在整串末尾匹配,于是只有当边界恰好
# 是最后一行时才找得到——而边界之后必然还有阶段输出,也就是说它在真实日志里
# 几乎永远匹配不上。
_BOUNDARY_RE = re.compile(
    re.escape(RUN_BOUNDARY_MARK) + r"\s+(?P<started>\S+)\s+provider=(?P<provider>.*)$",
    re.MULTILINE,
)


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


@dataclass(frozen=True)
class AttributedProgress:
    """一条进度,以及**它属不属于**所问的那次运行。

    `attributed=False` 不代表「不属于」,而是**不知道**——读到的窗口里没有边界。
    两者对操作人的下一步不同,所以分开说,不合并成一个乐观的布尔。
    """

    progress: FetchProgress | None
    attributed: bool
    #: 边界**自己**带的那个戳,来自日志里那条边界本身。
    #:
    #: 刻意避开状态工件里那个「起跑时刻」字段的名字:在本模块里,那个名字指的是
    #: 一个被证伪并被守卫明令禁掉的启发式——拿进度行的时刻去跟它比,以此推断
    #: 归属(`test_the_module_does_not_grow_an_attribution_guess_back`)。这里的
    #: 戳不参与任何比较,只是把「是哪一次运行」说给读者听。
    boundary_stamp: str = ""
    #: `attributed=False` 时**为什么**不知道——三种失败条件对操作人的下一步
    #: 不同,页面必须说真原因,不能一律说「窗口里没有边界」(codex P2):
    #: ``window_truncated``(窗口没盖住整份日志,窗外可能还有边界)/
    #: ``foreign_boundary``(窗口里有别的 provider 的边界,行有交错可能)/
    #: ``no_boundary``(完整窗口里确实一条边界都没有)/
    #: ``corrupt_boundary``(有边界但戳验不过——日志损坏,不硬解释)。
    #: 归属确定时为空串。
    unattributed_reason: str = ""


def _current_segment(
    log_text: str, provider_key: str,
) -> tuple[tuple[int, str] | None, str]:
    """本 provider 当前那一段的起点:(边界结束的字符位置, 起跑时刻)。

    判据是**独占**:窗口里的边界**全部**是我们的,才谈得上归属。

    上一版是「最后一条边界是我们的就算数」。那条规则在**反向交错**下会说错话:
    B 先起跑(边界 B),A 随后起跑(边界 A,成了最后一条),而 B **仍在跑**——B
    的进度行不会再带一条边界,于是它们落在边界 A 之后,被当成 A 的,还是以
    「归属已确定」的口气(codex 第二轮 P1)。

    前提是实的:兄弟 bundle **共用同一条日志**(`default_log_path` 取的是
    ``<provider 父目录>/logs/daily_update.log``),而单飞锁是 **per-provider** 的
    (`single_flight.lock_path_for`)——两个 provider **可以同时在跑**,行会交错。

    所以判据抬到「这段窗口里只有我们一个写者」:进度行本身不带 provider,靠
    边界排序推不出归属;而**同一个 provider 不会与自己并发**(单飞锁),因此
    「边界全是我们的」就足以断定其后的行也是我们的。窗口里出现别人的边界,
    那次运行有没有结束这份日志答不了——如实说不知道。

    要把这条判据放松回「最后一条是我们的」,得先让写入侧给进度行本身打上
    provider 标记,或让每个 provider 写自己的日志。两者都在**生产编排器**的
    阶段语义那一侧,不在本改动的范围内。
    """
    boundaries = list(_BOUNDARY_RE.finditer(log_text))
    if not boundaries:
        return None, "no_boundary"
    for match in boundaries:
        # 边界戳必须是写入侧的形态：带时区的 ISO 时间戳。正则的 `\S+` 会把
        # 坏字节/遗留编码洗出来的乱码当成「起跑时刻」，run_center 的不一致
        # 分支随即以确定口气宣布进度属于那次「运行」（codex P2）。戳验不过
        # 的边界 = 日志损坏，归属整体不可断——与台账坏行同一处置。
        try:
            stamp = datetime.fromisoformat(match.group("started"))
        except ValueError:
            return None, "corrupt_boundary"
        if stamp.tzinfo is None:
            return None, "corrupt_boundary"
    if any(
        os.path.normcase(match.group("provider").strip()) != provider_key
        for match in boundaries
    ):
        return None, "foreign_boundary"
    last = boundaries[-1]
    return (last.end(), last.group("started")), ""


def last_fetch_progress_for_run(
    log_text: str, *, provider_dir: Path, window_complete: bool,
) -> AttributedProgress:
    """取最后一条 fetch 进度,并说清它属不属于最近一次运行。

    窗口完整且其中的边界全是我们的:只在最后一条边界之后取进度,归属确定。
    否则退回全窗口取进度,并如实说无法归属——边界落地之前就是这个行为,
    不是退步。

    ``window_complete`` 是**必填**的:独占判据只在「我看到了全部」时成立。
    窗口是截断的(真实日志几乎总是——`log_tail` 只取尾部几千字符),「窗口里
    看不到别人的边界」证明不了别人不存在:更早起跑、仍在写的兄弟 provider
    的边界可能正好落在窗口之外,它随后的进度行照样交错进来(codex 第三轮
    P1,同一根因的第三种形态)。把这个参数设成缺省值,就是邀请调用方把截断
    当成完整。
    """
    provider_key = os.path.normcase(str(provider_dir.resolve()))
    if not window_complete:
        boundary, reason = None, "window_truncated"
    else:
        boundary, reason = _current_segment(log_text, provider_key)
    if boundary is None:
        return AttributedProgress(
            progress=last_fetch_progress(log_text), attributed=False,
            unattributed_reason=reason)
    end, started = boundary
    return AttributedProgress(
        progress=last_fetch_progress(log_text[end:]),
        attributed=True,
        boundary_stamp=started,
    )
