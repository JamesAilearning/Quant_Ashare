"""Pure classification helpers for the read-only Today Workbench."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from web.operator_ui.incumbent import (
    IncumbentIdentity,
    anchored_to_repo,
    unusable_path_reason,
)
from web.operator_ui.job_io import JobSummary
from web.operator_ui.pages._daily_decision_helpers import (
    SUPPORTED_DAILY_RECOMMENDATION_ARTIFACT_SCHEMA_VERSION,
    VERDICT_MATCHES_INCUMBENT,
    VERDICT_SINGLE_SHA_OK,
    artifact_entry_timing_is_valid,
    artifact_meta_status,
    hold_state,
    picks_table_rows,
    provenance_verdict,
)
from web.operator_ui.pages._ops_cockpit_helpers import (
    BundleFreshness,
    RetrainWindow,
)
from web.operator_ui.update_status import NO_REASON_MARK, UpdateRunStatus

_TRUSTED_PROVENANCE = frozenset({
    VERDICT_MATCHES_INCUMBENT,
    VERDICT_SINGLE_SHA_OK,
})
_ACTIVE_JOB_STATUSES = frozenset({"pending", "running"})
_ATTENTION_JOB_STATUSES = frozenset({
    "failed", "partial", "stop_failed", "stopped", "cancelled",
})



def failed_update_summary(status: UpdateRunStatus) -> str:
    """失败运行的一行说明：退出码含义、死在哪个阶段、**为什么**。

    失败卡片与今日待办队列都从这里取,不各写一份:它们此前是两段手写的同义
    字符串,而唯一能让操作人动手的正是「为什么」那一段——两处分头演化,迟早
    有一处漏掉它。

    `detail` 空时**明说记录里没有原因**,不留白:留白读起来像「没有更多可说」,
    而真相是「这次运行没把原因写下来」,两者对操作人的下一步完全不同。

    而 `detail` **非空也不等于有原因**:阶段一条 ERROR 都没记时,写入侧存的是
    退出码摘要本身(例如 `fetch failed hard (exit 1)`)并附上一个标记。把它当成
    原因渲染成「原因:fetch failed hard (exit 1)」,是把「只有退出码」伪装成一条
    解释——比不说更糟(codex #462)。所以这里认那个标记,只在真有捕获内容时才用
    「原因」二字。
    """
    reason = (status.detail or "").strip()
    head = f"{status.exit_meaning}；失败阶段：{status.failed_stage or '未记录'}。"
    if not reason:
        return head + "状态记录未写下原因；请在运行中心查看日志。"
    if reason.endswith(NO_REASON_MARK):
        return head + reason[: -len(NO_REASON_MARK)].strip() + \
            "。该阶段未在日志中留下原因；请在运行中心查看日志。"
    return head + f"原因：{reason}"


@dataclass(frozen=True)
class DailySignalSummary:
    """A display-ready, provenance-aware daily signal summary."""

    kind: str
    detail: str
    as_of_date: str | None = None
    entry_date: str | None = None
    next_rebalance_date: str | None = None
    #: 已核验候选清单的条数。空清单是**合法产出**（`--topk 0` 或全部候选
    #: 被掩蔽），丢掉基数会让「再平衡日」被下游当成「必有买入对象」
    #: （codex #468 P1）。仅在工件通过全部核验后有值。
    pick_count: int | None = None
    #: 产出器写下的数据来源（meta.provider_uri / meta.bundle_tag）。此前
    #: 核验只绑模型身份、把数据来源丢了——provider 切换或 bundle 原地重建
    #: 后，别的 bundle 的工件按日期巧合也能冒充「最新」（codex #468 P1）。
    #: 仅在工件通过全部核验后原样留存；核验交给消费方。
    data_provider_uri: str | None = None
    data_bundle_tag: str | None = None
    #: 产出器写下的 bundle 重建 nonce（meta.bundle_built_at = stamp 的
    #: built_at）。tag 只含日历尾+day.txt 哈希——同日历的原地重建它看不
    #: 见；built_at 每次重建都刷新（codex #468 二轮 P1）。老工件无此键，
    #: None 是合法态。
    data_bundle_built_at: str | None = None


@dataclass(frozen=True)
class OperationalSummary:
    """The single operational state a workbench should surface first."""

    kind: str
    detail: str
    job: JobSummary | None = None


def summarise_daily_signal(
    artifact_date: str | None,
    payload: object | None,
    *,
    incumbent: IncumbentIdentity,
    current_model_sha: str | None,
    read_error: str | None = None,
) -> DailySignalSummary:
    """Classify one artifact without promoting an unverified signal.

    This is intentionally stricter than filename ordering: the latest file is
    meaningful only after its payload date, v2 shape, and current-incumbent
    provenance have all been confirmed by the shared daily-decision helpers.
    """

    if artifact_date is None:
        return DailySignalSummary("missing", "尚无日度信号工件。")
    if read_error:
        return DailySignalSummary("needs_verification", f"工件不可读：{read_error}")
    if not isinstance(payload, dict):
        return DailySignalSummary("needs_verification", "工件顶层不是 JSON object。")

    as_of_date = payload.get("as_of_date")
    if not isinstance(as_of_date, str) or as_of_date != artifact_date:
        return DailySignalSummary(
            "needs_verification",
            "文件名日期与工件 as_of_date 不一致，无法确认信号归属。",
        )
    entry_date = payload.get("entry_date")
    if not isinstance(entry_date, str) or not entry_date.strip():
        return DailySignalSummary(
            "needs_verification",
            "工件缺少有效 entry_date，无法确认信号时点。",
            as_of_date=as_of_date,
        )
    if not artifact_entry_timing_is_valid(payload):
        return DailySignalSummary(
            "needs_verification",
            "工件 entry_date 必须是晚于 as_of_date 的严格 ISO 日期，无法确认信号时点。",
            as_of_date=as_of_date,
            entry_date=entry_date,
        )

    schema_version = payload.get("artifact_schema_version")
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != SUPPORTED_DAILY_RECOMMENDATION_ARTIFACT_SCHEMA_VERSION
    ):
        return DailySignalSummary(
            "needs_verification",
            "工件 schema 版本缺失、格式错误或不受当前工作台支持。",
            as_of_date=as_of_date,
            entry_date=entry_date,
        )

    meta_status = artifact_meta_status(payload, current_model_sha)
    if meta_status.artifact_is_corrupt_v2:
        return DailySignalSummary(
            "needs_verification",
            "带版本标记的工件缺少有效 meta，工件可能损坏。",
            as_of_date=as_of_date,
            entry_date=entry_date,
        )
    verdict = provenance_verdict(incumbent, meta_status)
    if verdict not in _TRUSTED_PROVENANCE:
        return DailySignalSummary(
            "needs_verification",
            f"工件来源无法与现任模型确认（{verdict}）。",
            as_of_date=as_of_date,
            entry_date=entry_date,
        )

    try:
        # The detailed page treats a missing/non-list picks value, or a
        # non-object member, as a corrupt producer artifact. The workbench
        # must use that same boundary before presenting this file as current.
        pick_count = len(picks_table_rows(payload))
    except ValueError as exc:
        return DailySignalSummary(
            "needs_verification",
            f"工件候选列表不合法：{exc}",
            as_of_date=as_of_date,
            entry_date=entry_date,
        )
    # 行级契约：产出器 RecommendationPick（frozen dataclass）六键六型**恒写**
    # ——`picks: [{}]` 这类行数不出任何可买标的，却会把基数抬成 1、让最显
    # 眼的卡说「有再平衡指令 · 1 只候选」（codex P2）。详情页的 display 层
    # 刻意 pass-through（工单 §1.4）不动；驱动指令句的**基数**在此验约，
    # 违约=需核查，不做静默缩数。
    for index, pick in enumerate(payload["picks"]):
        problem = _pick_row_violation(pick)
        if problem is not None:
            return DailySignalSummary(
                "needs_verification",
                f"工件候选第 {index + 1} 行违约：{problem}（产出器恒写六键，"
                "缺任一即非产出器产物）。",
                as_of_date=as_of_date,
                entry_date=entry_date,
            )

    cadence = hold_state(payload)
    if cadence.malformed is not None:
        return DailySignalSummary(
            "needs_verification",
            cadence.malformed,
            as_of_date=as_of_date,
            entry_date=entry_date,
        )
    # 数据来源原样留存（meta 在上方已通过形态核验，必为 dict）。产出器
    # 无条件写 provider_uri；bundle_tag 在 bundle 无身份块时合法为 None。
    meta_block = payload.get("meta")
    data_provider = (
        meta_block.get("provider_uri") if isinstance(meta_block, dict) else None)
    data_tag = (
        meta_block.get("bundle_tag") if isinstance(meta_block, dict) else None)
    # 在场但类型违约 ≠ 缺席：产出器只写 str（provider_uri）/ str|null
    # （bundle_tag）。把 `123` 这类值静默降成 None 会借道「合法缺身份块」
    # 绕开 bundle 比对（codex P2）——类型违约 = 工件需核查，不降级。
    if data_provider is not None and not isinstance(data_provider, str):
        return DailySignalSummary(
            "needs_verification",
            f"工件 meta.provider_uri 非字符串（实际 "
            f"{type(data_provider).__name__}）——产出器只写 str，需核查。",
            as_of_date=as_of_date,
            entry_date=entry_date,
        )
    if data_tag is not None and not isinstance(data_tag, str):
        return DailySignalSummary(
            "needs_verification",
            f"工件 meta.bundle_tag 非 str/null（实际 "
            f"{type(data_tag).__name__}）——产出器只写这两种，需核查。",
            as_of_date=as_of_date,
            entry_date=entry_date,
        )
    data_built_at = (
        meta_block.get("bundle_built_at")
        if isinstance(meta_block, dict) else None)
    if data_built_at is not None and not isinstance(data_built_at, str):
        return DailySignalSummary(
            "needs_verification",
            f"工件 meta.bundle_built_at 非 str/null（实际 "
            f"{type(data_built_at).__name__}）——产出器只写这两种，需核查。",
            as_of_date=as_of_date,
            entry_date=entry_date,
        )
    provenance: dict[str, str | None] = {
        "data_provider_uri": data_provider,
        "data_bundle_tag": data_tag,
        "data_bundle_built_at": data_built_at,
    }
    if cadence.is_hold:
        return DailySignalSummary(
            "hold",
            "非再平衡日的监控视图，不构成入场指令。",
            as_of_date=as_of_date,
            entry_date=entry_date,
            next_rebalance_date=cadence.next_rebalance_date,
            pick_count=pick_count,
            **provenance,
        )
    if "rebalance_day" in payload:
        return DailySignalSummary(
            "rebalance",
            "再平衡日信号，仍须在详情页完成人工核对。",
            as_of_date=as_of_date,
            entry_date=entry_date,
            pick_count=pick_count,
            **provenance,
        )
    return DailySignalSummary(
        "daily",
        "日频工件，仍须在详情页完成人工核对。",
        as_of_date=as_of_date,
        entry_date=entry_date,
        pick_count=pick_count,
        **provenance,
    )


def summarise_operations(jobs: Iterable[JobSummary]) -> OperationalSummary:
    """Prioritise running work, then the latest exceptional terminal job."""

    rows = tuple(jobs)
    active = [job for job in rows if job.status in _ACTIVE_JOB_STATUSES]
    if active:
        job = max(active, key=_job_timestamp)
        detail = job.key_metric_value or (
            "等待启动" if job.status == "pending" else "运行中"
        )
        return OperationalSummary(job.status, f"{job.type}：{detail}", job)

    attention = [job for job in rows if job.status in _ATTENTION_JOB_STATUSES]
    if attention:
        job = max(attention, key=_job_timestamp)
        detail = job.error_message or job.key_metric_value or job.status
        return OperationalSummary("attention", f"{job.type}：{detail}", job)
    return OperationalSummary("idle", "没有正在运行或需要处理的作业。")


def _job_timestamp(job: JobSummary) -> tuple[str, str]:
    return (job.finished_at or job.started_at or job.created_at, job.run_id)


__all__ = [
    "DailySignalSummary",
    "OperationalSummary",
    "SUPPORTED_DAILY_RECOMMENDATION_ARTIFACT_SCHEMA_VERSION",
    "TodaysAnswer",
    "model_age_rows",
    "summarise_daily_signal",
    "summarise_operations",
    "todays_buy_answer",
]

def model_age_rows(window: RetrainWindow) -> list[tuple[str, str]]:
    """身份卡上的模型时效行——数据**照抄**生产运维页⑤的 `retrain_window`。

    P3 缺口（UI 评估已批序列③）：工作台身份卡只报形态不报年龄，「模型多旧」
    要跳页才知道。此处不自造任何判定：同一个推导函数、同一套字段，只做措辞
    （生产运维页与本页共用一份实现的既定纪律——#461 首版另写一份三个决策
    全错的教训在档）。`known=False` 时如实说推导不了，不留空。

    窗口行必须在**可见文案**里自报「推导」身份（codex P1）：仓库没有机器
    可读的「下次重训到期日」，这个窗口是从 serving 间距 pin 推导出来的——
    驾驶舱④的披露契约（cockpit 模块头「labelled as DERIVED」）跟着窗口走
    到每一处展示，docstring 不渲染、不算数。
    """
    if not getattr(window, "known", False):
        # 原因照抄契约给的 error，不硬编码一种失败：known=False 不只有
        # 「非可解析 ensemble」一种（最新 fit_end 非法同样走这里），错误
        # 归因会把操作人引向错的修法（codex P2）。
        reason = str(getattr(window, "error", "") or "").strip() or "原因未记录"
        return [("模型时效", f"无法推导：{reason}")]
    state_text = {
        "before": "未开",
        "open": "开放中",
        "closed": "已过（fit_end 须落窗内；点火按操作卡排期）",
    }.get(str(getattr(window, "state", "")), str(getattr(window, "state", "")))
    return [
        ("fit 至", str(window.newest_fit_end)),
        ("模型年龄", f"{window.days_since_newest} 天"),
        (
            "下一成员 fit_end 窗（推导）",
            f"{window.opens_on}~{window.closes_on}（{state_text}；由 serving "
            f"间距 pin [{window.spacing_min},{window.spacing_max}] 天推导，"
            "仓库无机器可读的重训到期锚）",
        ),
    ]


def _pick_row_violation(pick: dict[str, object]) -> str | None:
    """一行候选违约在哪——None = 合约内。

    钉的是产出器 `RecommendationPick`（frozen dataclass）的**全部**六键与
    类型（穷尽式，不挑其中几个——挑选就是下一个漏洞的形状）：rank int /
    stock_code 非空 str / stock_name str / predicted_score 数值 /
    tradable_flag bool / unavailable_reason str。
    """
    code = pick.get("stock_code")
    if not (isinstance(code, str) and code.strip()):
        return "stock_code 缺失或为空"
    if not isinstance(pick.get("stock_name"), str):
        return "stock_name 缺失或非字符串"
    rank = pick.get("rank")
    if isinstance(rank, bool) or not isinstance(rank, int):
        return "rank 缺失或非整数"
    score = pick.get("predicted_score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return "predicted_score 缺失或非数值"
    # 不止验类型，验**字面**：产出器只落已过可交易筛选的行（untradable 在
    # 构造前被过滤，构造器写死 True/""——src/inference/daily_recommend 的
    # _build_picks）。False/非空 reason 的行产出器产不出；只验布尔会让
    # 「工件自己标注不可交易」的行照样计入候选数（codex P2）。
    if pick.get("tradable_flag") is not True:
        return "tradable_flag 缺失或非 True（产出器只落可交易行）"
    if pick.get("unavailable_reason") != "":
        return "unavailable_reason 非空串（产出器对入选行恒写空串）"
    return None


_ANSWER_DISCLAIMER = "本句只汇总既有工件与出单侧判据，不是订单，也不授予交易许可。"


def _same_provider_spelling(artifact: str, current: str) -> bool:
    """两个 provider 拼写是否指同一份 bundle——用出单器自己的归一化。

    不自造第二套归一化（expanduser/abspath/realpath/normcase 的组合差一个
    就是一类假阴/假阳）；出单器怎么认，本卡就怎么认。

    归一化之前先**同锚**：`_normalize_provider_uri` 对相对拼写按进程 CWD
    归一，而页面的当前 provider 早已 `anchored_to_repo`（仓根锚，UI 支持
    从仓外启动的既有语境）——工件里的相对拼写来自生产配置、语境同为仓根。
    不同锚的两个相对拼写会让**同一份** bundle 比不相等，最显眼的卡片假拒
    一份有效指令（codex P1）。绝对拼写 anchored_to_repo 原样放行，不变。
    """
    from src.inference import daily_recommend as _rec  # noqa: PLC0415
    normalize = _rec._normalize_provider_uri  # type: ignore[attr-defined]
    return bool(normalize(anchored_to_repo(artifact))
                == normalize(anchored_to_repo(current)))


@dataclass(frozen=True)
class TodaysAnswer:
    """One synthesized sentence answering「今天要不要买」.

    * ``rebalance``      — 数据所及的最新指令是再平衡且清单非空（截至已收盘
                           会话；执行时点归操作人的执行惯例，仍须人工核对）。
    * ``watch``          — 最新指令为 HOLD 或再平衡清单为空：无需动作。
    * ``no_instruction`` — 流程态：尚无工件，或数据已走到最新指令之后
                           （出单没跟上）；带日期如实点名。
    * ``unanswerable``   — 异常态：出单侧判据拒绝（陈旧/完整性/健康份额）、
                           裁决不可达，或工件本身需要核查。
    """

    state: str
    value: str
    detail: str


def todays_buy_answer(
    signal: DailySignalSummary,
    freshness: BundleFreshness,
) -> TodaysAnswer:
    """合成「今天要不要买」——用户每天真正要问的那一句（UI 已批序列④）。

    **零新判定、零时钟**：数据包前置用出单侧自己的 `usable` 判据全额消费
    ——年龄、完整性，加上健康摘要「只扣分不加分」的份额（漏任何一份都会
    与同页的健康卡自相矛盾；#461 首版另写一份三个决策全错的教训在档）；
    节奏（HOLD / 再平衡）用 `summarise_daily_signal` 已核验过来源的分类，
    含候选**基数**（空清单是合法产出，不是买入指令）。

    「指令新不新」不比挂钟：`entry_date` 按基线契约是**已收盘会话**
    （v2-daily-decision-page：可交易性筛选需要该会话的真实 K 线，产出器
    永远出不了未收盘会话的清单——它**不是**「明早买入」指令，真实订单
    如何向清单收敛是操作人的执行惯例，观察期记录的正是该偏差）。所以
    「最新」的判据是 `entry_date == 出单侧日历尾`：出单器就是从那份
    bundle 跑的，尾巴对上 = 数据所及的最新指令；数据走到了指令前面 =
    出单没跟上（流程态）；指令声称的会话晚于数据尾 = 两侧有一侧在说谎
    （异常态）。codex #468 P1：把 entry 等同「今天买」会怂恿对已收盘
    价格下单——本函数不给任何执行时点，只说清「最新指令是什么」。

    「不回答」分两种，绝不混用：``no_instruction`` 是流程态（带两个日期
    如实点名），``unanswerable`` 是异常态（判据拒绝或工件需核查，带原因）。
    """
    if not getattr(freshness, "known", False):
        return TodaysAnswer(
            "unanswerable", "无法给出",
            f"出单侧新鲜度裁决不可达：{freshness.message or '原因未记录'}。"
            f"{_ANSWER_DISCLAIMER}",
        )
    if freshness.refuses_today:
        behind = (
            f"数据尾落后 {freshness.days_behind} 天，超出出单上限 "
            f"{freshness.max_age_days} 天"
            if freshness.days_behind is not None
            else "数据陈旧超出出单上限")
        return TodaysAnswer(
            "unanswerable", "无法给出",
            f"出单侧今天会拒：{behind}。先修数据再谈信号。{_ANSWER_DISCLAIMER}",
        )
    if freshness.integrity_accepted is not True:
        reason = (
            freshness.integrity_reason or "完整性未评估"
            if freshness.integrity_accepted is None
            else freshness.integrity_reason or "原因未记录")
        return TodaysAnswer(
            "unanswerable", "无法给出",
            f"出单侧完整性闸未放行：{reason}。{_ANSWER_DISCLAIMER}",
        )
    if not freshness.usable:
        # 年龄与完整性两道闸都过了，`usable` 仍可为假——健康摘要的份额
        # （instruments 缺失这类前置）。健康只能扣分不能加分（其角色如此
        # documented），漏掉它会让本卡说「有指令」而健康卡同时报问题——
        # 同页自相矛盾（codex #468 P1）。
        leftovers = "；".join(part for part in (
            f"健康状态 {freshness.health_status}"
            if freshness.health_status != "ok" else "",
            *freshness.health_warnings,
        ) if part)
        return TodaysAnswer(
            "unanswerable", "无法给出",
            f"数据包健康前置未全过：{leftovers or freshness.message or '原因未记录'}。"
            f"{_ANSWER_DISCLAIMER}",
        )
    tail = freshness.tail_date
    if not tail:
        return TodaysAnswer(
            "unanswerable", "无法给出",
            f"出单侧裁决在场但未带日历尾，无法比对指令新旧。{_ANSWER_DISCLAIMER}",
        )
    if signal.kind == "missing":
        return TodaysAnswer(
            "no_instruction", "没有可执行的指令",
            f"尚无日度信号工件；请先在运行中心生成。{_ANSWER_DISCLAIMER}",
        )
    if signal.kind not in ("hold", "rebalance", "daily"):
        return TodaysAnswer(
            "unanswerable", "无法给出",
            f"最新工件需要核查：{signal.detail}{_ANSWER_DISCLAIMER}",
        )
    # 数据来源绑定（codex #468 P1）：entry 与日历尾的比对只在「工件出自
    # **这份** bundle」时才有意义——provider 切换或 bundle 原地重建后，
    # 别的 bundle 的工件按日期巧合也能对上尾，而全页健康检查说的都是另一
    # 份数据。provider 必绑（产出器无条件写 meta.provider_uri）；身份 tag
    # 两侧都有才可比（身份块是 stamp 的可选项，pre-PR-G+I 无块是合法态
    # ——那时仅按 provider 绑定，不假装比过）。
    if signal.data_provider_uri is None:
        return TodaysAnswer(
            "unanswerable", "无法给出",
            "工件缺数据来源（meta.provider_uri，v2 产出器无条件写入）——"
            f"无法确认信号出自当前数据；请核查工件。{_ANSWER_DISCLAIMER}",
        )
    if not freshness.provider_uri:
        return TodaysAnswer(
            "unanswerable", "无法给出",
            "出单侧裁决未带 provider 身份，无法绑定工件的数据来源。"
            f"{_ANSWER_DISCLAIMER}",
        )
    # 归一化之前先过既有的拼写边界（unusable_path_reason，NUL 先于任何
    # 文件系统调用）：内嵌 NUL 的拼写会让 realpath 抛 ValueError，整页
    # 变 traceback 而不是规格要求的拒答（codex P2）。两侧对称——工件侧
    # 来自不可信文件，当前侧同一崩溃向量同一门。
    for side, spelling in (("工件", signal.data_provider_uri),
                           ("出单侧", freshness.provider_uri)):
        unusable = unusable_path_reason(spelling)
        if unusable is not None:
            return TodaysAnswer(
                "unanswerable", "无法给出",
                f"{side}的数据来源拼写不可用：{unusable}{_ANSWER_DISCLAIMER}",
            )
    if not _same_provider_spelling(
            signal.data_provider_uri, freshness.provider_uri):
        return TodaysAnswer(
            "unanswerable", "无法给出",
            f"工件出自另一个 provider（工件 {signal.data_provider_uri} vs "
            f"当前 {freshness.provider_uri}）——它不是这份数据的信号；本页"
            f"其余检查说的都是当前数据。{_ANSWER_DISCLAIMER}",
        )
    if (signal.data_bundle_tag is not None
            and freshness.identity_tag is not None
            and signal.data_bundle_tag != freshness.identity_tag):
        return TodaysAnswer(
            "unanswerable", "无法给出",
            f"工件出自另一份 bundle（工件身份戳 {signal.data_bundle_tag} vs "
            f"当前 {freshness.identity_tag}）——bundle 已重建或被替换；请"
            f"重跑出单。{_ANSWER_DISCLAIMER}",
        )
    # tag 只含日历尾+day.txt 哈希（其 docstring 明言非 full-bin 保证）——
    # 宇宙/bin 变了而日历没变的**原地重建**它看不见；built_at 是每次重建
    # 都刷新的 nonce（codex #468 二轮 P1）。两侧都有才可比：老工件无此键、
    # 无 stamp 无 built_at，都是合法缺席，按已比对的 provider/tag 绑定放
    # 行，不冒充比过。
    if (signal.data_bundle_built_at is not None
            and freshness.built_at is not None
            and signal.data_bundle_built_at != freshness.built_at):
        return TodaysAnswer(
            "unanswerable", "无法给出",
            f"bundle 已原地重建（工件建于 {signal.data_bundle_built_at}，"
            f"当前 stamp 建于 {freshness.built_at}）——身份 tag 对得上只说明"
            f"日历没变，数据内容可能已换；请重跑出单。{_ANSWER_DISCLAIMER}",
        )
    # 已核验的三类工件都带严格 ISO entry_date（summarise_daily_signal 的
    # 边界保证）；日历尾同为规范 YYYY-MM-DD——ISO 字符串可直接比序。
    entry = str(signal.entry_date)
    # 已收盘披露跟着工件内容走到**每一个**把它当指令呈现的态（基线契约 +
    # 本 change 规格；codex P2：流程态也点名了 entry，披露不能只在现行态）。
    closed = (
        f"截至 {entry} 收盘（**已收盘会话**，不是「明早买入」指令；真实"
        "订单如何向清单收敛是你的执行惯例，观察期记录的正是该偏差）")
    if entry < tail:
        return TodaysAnswer(
            "no_instruction", "最新指令未跟上数据",
            f"数据已到 {tail}，最新指令仍是{closed}的那份——之后的出单"
            f"还没跑；请在运行中心生成。{_ANSWER_DISCLAIMER}",
        )
    if entry > tail:
        return TodaysAnswer(
            "unanswerable", "无法给出",
            f"工件声称的会话（{entry}）晚于出单侧数据尾（{tail}）——产出器"
            f"出不了未收盘会话的清单，两侧必有一侧在说谎；请核查工件来源。"
            f"{_ANSWER_DISCLAIMER}",
        )
    if signal.kind == "hold":
        return TodaysAnswer(
            "watch", "不动 · 最新指令为 HOLD",
            f"{closed}：HOLD，无需动作；下一再平衡日："
            f"{signal.next_rebalance_date or '未记录'}。{_ANSWER_DISCLAIMER}",
        )
    if signal.kind == "rebalance":
        # 空清单是**合法产出**（`--topk 0` 或全部候选被掩蔽）——零个买入
        # 对象时说「有指令」是最显眼卡片上的错话（codex #468 P1）。
        if signal.pick_count is None:
            return TodaysAnswer(
                "unanswerable", "无法给出",
                "再平衡工件的候选数未随核验结果传递，无法确认有无买入对象。"
                f"{_ANSWER_DISCLAIMER}",
            )
        if signal.pick_count == 0:
            return TodaysAnswer(
                "watch", "不动 · 再平衡清单为空",
                f"{closed}：再平衡日但目标清单为空（--topk 0 或全部候选被"
                f"掩蔽都是合法产出）——没有买入对象；详情页可核对原因。"
                f"{_ANSWER_DISCLAIMER}",
            )
        return TodaysAnswer(
            "rebalance", "有再平衡指令（待人工核对）",
            f"{closed}：共 {signal.pick_count} 只候选——去日度决策页逐项"
            f"人工核对后，按你的执行惯例决定是否与如何收敛。{_ANSWER_DISCLAIMER}",
        )
    return TodaysAnswer(
        "unanswerable", "无法给出",
        "工件是日频形态但不带 HOLD/再平衡节奏标记，本页合成不了三态；"
        f"请在详情页人工判读。{_ANSWER_DISCLAIMER}",
    )
