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
from datetime import datetime, timedelta
from pathlib import Path

#: 与 ``fetcher`` 的格式串一一对应:
#: ``"  %s year=%d progress: %d/%d tickers (written=%d, skipped=%d)%s"``
#: 尾部的 ``%s`` 是 ``" provider=<规范化目录>"`` 或空串。
#:
#: provider 组**可选**是承重的:老日志(标记落地之前写的)与手工跑的 fetch
#: 都不带它,而那些行仍须照常解析——否则一次读到老日志的运行会突然「一条
#: 进度都没有」,比归属报不出来更坏。不带标记的行**不会**被当成任何人的,
#: 归属退回边界判据,也就是标记落地之前的行为。
#:
#: 标记锚**行尾**(``$``):不锚的话,一个恰好含 ``provider=`` 的路径片段
#: (endpoint 名、目录名)会被当成标记读走。写侧只把它放在行尾。
_PROGRESS_RE = re.compile(
    r"(?P<endpoint>\S+)\s+year=(?P<year>\d+)\s+progress:\s*"
    r"(?P<done>\d+)\s*/\s*(?P<total>\d+)\s+tickers\s*"
    r"\(written=(?P<written>\d+),\s*skipped=(?P<skipped>\d+)\)"
    r"(?: provider=(?P<provider>.*?))?\r?$"
)

#: 行首的挂钟。只到秒、不含日期——这正是**日志行本身**归属做不到精确的原因。
_CLOCK_RE = re.compile(r"^(?P<clock>\d{2}:\d{2}:\d{2})")

# Mirrors src/data_pipeline/daily_update.py RUN_BOUNDARY_MARK. Duplicated by
# design (web/ must not import the pipeline layer); the logic test pins the two
# to the same value.
RUN_BOUNDARY_MARK = "[daily_update] run started"

#: 边界行,锚**物理行首**:`^HH:MM:SS [<写侧 logger 名>] INFO — <标记>`
#: (src/core/logger.py 的完整格式串)。无锚搜索会把**转述**边界行的普通消息
#: (上游报错原样回显)当成真边界,其后的进度被以「已确定」口气归给一次不存在
#: 的运行;只锚消息起始仍放过「连 logger 前缀整段回显在消息中部」的形态——
#: 行首锚连它也分辨得开(codex 两轮 P2)。残余极限:消息体里带**真实换行**再
#: 逐字节复刻整行时,续行与真边界物理不可分——那需要结构化日志,超出文本
#: 读侧。logic 测试钉住本前缀与写侧 logger 名/真实格式串一致。
# `re.MULTILINE` 是**承重**的:不带它,`$` 只在整串末尾匹配,于是只有当边界恰好
# 是最后一行时才找得到——而边界之后必然还有阶段输出,也就是说它在真实日志里
# 几乎永远匹配不上。
# 行尾的 `\r?` 是**词法层**的：Windows 上 logging 落盘是 CRLF，而
# `log_window` 按 `\n` 切行——`\r` 是行终结符的残留，不是身份内容，收进
# 捕获组会让每一条真实边界都验不过写侧形态。真以 CR 结尾的 POSIX 目录名
# 本就无法在行式日志里回环（写侧一落盘就与终结符不可分），它会退化成
# foreign_boundary（如实不归属），绝不会被误归属。
_BOUNDARY_PREFIX = "[src.data_pipeline.daily_update] INFO — "

_BOUNDARY_RE = re.compile(
    r"^\d{2}:\d{2}:\d{2} "
    + re.escape(_BOUNDARY_PREFIX) + re.escape(RUN_BOUNDARY_MARK)
    + r"\s+(?P<started>\S+)\s+provider=(?P<provider>.*?)\r?$",
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
    #: 写侧盖在这条行上的 provider 身份,或空串（老日志 / 手工跑的 fetch）。
    #: 空串是**「这条行没报身份」**,绝不是「身份为空」——不带标记的行不属于
    #: 任何人,归属退回边界判据。
    provider: str = ""

    def describe(self) -> str:
        """一行如实描述,时刻与范围都写在里面。"""
        stamp = f"{self.at} " if self.at else ""
        return (
            f"{stamp}{self.endpoint} {self.year} 年:{self.done}/{self.total} 支"
            f"(已写 {self.written}、跳过 {self.skipped})"
        )


def last_fetch_progress(
    log_text: str, *, provider_key: str = "",
) -> FetchProgress | None:
    """日志文本里**最后**一条进度行;没有则 ``None``。

    取最后一条而不是第一条:日志是追加的,前面还躺着历次运行的进度行。
    ``total`` 为 0 的行直接丢弃——那种行说不出任何进度,渲染出来只会是
    ``0/0``。

    ``provider_key`` 非空时**只取带该标记的行**:兄弟 provider 的行交错在
    同一份日志里,不过滤的话「最后一条」很可能是别人的。不带标记的行在这
    条路径上**一律跳过**——把它当成自己的,就是把标记落地之前那个被证伪的
    猜测又请了回来。

    **本函数不判断这条属于哪一次运行**(见模块 docstring):它就是「尾部最后
    一条(可选:带某个标记的)」,调用方必须把这一点如实告诉读者。
    """
    if not log_text:
        return None
    hit = None
    hit_line = ""
    for line in log_text.splitlines():
        match = _PROGRESS_RE.search(line)
        if match is None:
            continue
        if provider_key:
            stamped = match.group("provider")
            if stamped is None or not _provider_matches(stamped, provider_key):
                continue
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
        provider=hit.group("provider") or "",
    )


def _provider_matches(stamped: str, provider_key: str) -> bool:
    """写侧盖的标记是不是**我们**的身份。

    精确相等**就是**完整回环:``provider_key`` 本身是
    ``normcase(provider_dir.resolve())`` 的产出,所以「等于它」已经蕴含
    「是规范形态、且指向同一个目录」。刻意不再算一次
    ``normcase(resolve(stamped))``——那一步在这条路径上不可达(变异实测:
    把它换成 ``True`` 语义不变),留着只会让人以为多了一层防御。边界那侧
    的校验不同:它比的是**边界自己写的**串,还没有一个规范对照物。

    宽容化(strip / 只比 basename / 大小写不敏感兜底)是**禁止**的:那会让
    写侧产不出的拼写被洗成我们的身份,然后以「已确定」的口气归属一条别人
    的进度。差一个字节就当成别人的——那只会退化成「归属报不出来」,退回
    标记落地之前的行为,而反过来是说错话。
    """
    return bool(stamped) and stamped == provider_key


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


def _own_boundary(
    log_text: str, provider_key: str,
) -> tuple[tuple[int, str] | None, str]:
    """本 provider **最后一条**边界的位置与起跑时刻。

    这是标记路径的定位器,判据只有一条:最后一条**我们自己的**边界。别人的
    边界穿插其中无所谓——进度行自己带身份,读侧过滤得掉。这正是
    ``_current_segment`` 的 docstring 当初写下的出路:「要把这条判据放松回
    『最后一条是我们的』,得先让写入侧给进度行本身打上 provider 标记」。

    ``window_complete`` 在这条路径上**不是**必要条件,这才是标记的真正价值:
    截断的窗口只影响「看不到更早的东西」,不影响「我们的边界之后、我们自己
    盖了标记的这些行」。而同一个 provider 不会与自己并发(单飞锁),所以
    自己的边界之后、下一条自己的边界之前,带自己标记的行只能是这一次的。
    真实日志按尾部读——独占判据因此在生产上几乎总是答「不知道」。

    边界的戳与身份仍按写侧形态**完整回环**校验(与独占路径同一套):验不过
    的边界 = 日志损坏,不硬解释。
    """
    own: list[re.Match[str]] = []
    for match in _BOUNDARY_RE.finditer(log_text):
        verdict = _boundary_defect(match)
        if verdict:
            return None, verdict
        if match.group("provider") == provider_key:
            own.append(match)
    if not own:
        return None, "no_boundary"
    last = own[-1]
    return (last.end(), last.group("started")), ""


def _boundary_defect(match: re.Match[str]) -> str:
    """边界的戳与身份验不过写侧形态时的失败原因;通过则空串。

    独占路径与标记路径共用同一份校验。分头写两份,两份会漂——而漂的那一份
    会以「已确定」的口气把一条损坏边界当成合法起点。
    """
    # 边界戳必须是写入侧的形态：带时区的 ISO 时间戳。正则的 `\S+` 会把
    # 坏字节/遗留编码洗出来的乱码当成「起跑时刻」，run_center 的不一致
    # 分支随即以确定口气宣布进度属于那次「运行」（codex P2）。戳验不过
    # 的边界 = 日志损坏，归属整体不可断——与台账坏行同一处置。
    raw = match.group("started")
    try:
        stamp = datetime.fromisoformat(raw)
    except ValueError:
        return "corrupt_boundary"
    # 解析得动还不够：fromisoformat 接受 `20260825T203000+08:00`、
    # 周历、`Z` 后缀等写入侧永不产的拼写（codex P2）。要求与
    # `datetime.isoformat()` 的产出**精确回环**——写入侧只产这种。
    if stamp.tzinfo is None or stamp.isoformat() != raw:
        return "corrupt_boundary"
    if stamp.utcoffset() != timedelta(hours=8):
        # 写入侧只在东八区落边界——别的时区产不出（同一原则顺带钉上）。
        return "corrupt_boundary"
    # 身份与戳同罪同治（codex P2）：写侧只产 `normcase(resolve())` 的
    # 精确形态。此前读侧 `.strip()+normcase` 宽容化，`provider= /tmp/x `
    # 这种写侧产不出的拼写会被洗成 provider_key 并以「已确定」口气归属；
    # 而真以空白结尾的合法 POSIX 目录名反被 strip 改掉。判据是**完整
    # 回环**——stamped 必须等于它自己的 resolve+normcase（半套「不动点
    # +isabs」放过 `/a/../b` 这类 resolve 早消掉的拼写，还把它误判成
    # foreign：给操作人的解释从「日志损坏」变成「跨 provider 交错」，
    # codex 第二轮 P2）。解析不动 = 同一处置。
    stamped = match.group("provider")
    try:
        canonical = os.path.normcase(str(Path(stamped).resolve()))
    except (OSError, ValueError, RuntimeError):
        # RuntimeError = 符号链接环（3.10–3.12 实测，台账读者同款处
        # 置）——不接住它，一条恶意/损坏边界就崩掉整个 run_center 页。
        return "corrupt_boundary"
    if canonical != stamped:
        return "corrupt_boundary"
    return ""


def _current_segment(
    log_text: str, provider_key: str,
) -> tuple[tuple[int, str] | None, str]:
    """本 provider 当前那一段的起点:(边界结束的字符位置, 起跑时刻)。

    判据是**独占**:窗口里的边界**全部**是我们的,才谈得上归属。

    这是**进度行不带标记**时的退路(老日志、手工跑的 fetch)。带标记的日志走
    ``_own_boundary``,判据宽得多——因为那时行自己带身份。

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
        verdict = _boundary_defect(match)
        if verdict:
            return None, verdict
    if any(match.group("provider") != provider_key for match in boundaries):
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

    # 标记路径优先。窗口里有**我们自己**盖过标记的进度行,就说明这份日志的
    # fetch 侧已经在报身份了——归属可以只靠「我们最后一条边界 + 按标记过滤」,
    # 不需要窗口完整,也不怕别人的边界穿插。这是本路径存在的全部理由:真实
    # 日志按尾部读,独占判据在生产上几乎总答「不知道」。
    #
    # 判据是「**我们的**标记出现过」而不是「任何标记出现过」:别人在报身份
    # 不代表我们在报。我们这一侧还没升级(或这次是手工跑的 fetch)时,我们的
    # 行仍不带标记,只能走边界独占。
    tagged_boundary, tagged_reason = _own_boundary(log_text, provider_key)
    if tagged_boundary is not None:
        end, started = tagged_boundary
        tagged = last_fetch_progress(log_text[end:], provider_key=provider_key)
        if tagged is not None:
            return AttributedProgress(
                progress=tagged, attributed=True, boundary_stamp=started)

    if not window_complete:
        boundary, reason = None, "window_truncated"
    else:
        boundary, reason = _current_segment(log_text, provider_key)
    if boundary is None:
        # 标记路径失败的原因更贴切时用它:损坏的边界就是损坏的边界,不该被
        # 报成「窗口截断」——那会让操作人去调大读取窗口,而问题在别处。
        if tagged_reason == "corrupt_boundary":
            reason = tagged_reason
        return AttributedProgress(
            progress=last_fetch_progress(log_text), attributed=False,
            unattributed_reason=reason)
    end, started = boundary
    return AttributedProgress(
        progress=last_fetch_progress(log_text[end:]),
        attributed=True,
        boundary_stamp=started,
    )
