"""Today Workbench: read-only summaries for the daily operating sequence."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import streamlit as st

from web.operator_ui.artifact_reader import read_json_artifact
from web.operator_ui.bundle_health import (
    BundleHealthSummary,
    resolve_default_provider_uri,
    summarise_bundle_health,
)
from web.operator_ui.components import render_stat_card
from web.operator_ui.decision_journal import DecisionJournalError, read_journal
from web.operator_ui.incumbent import (
    anchored_to_repo,
    resolve_incumbent,
    resolve_model_path,
    unusable_path_reason,
)
from web.operator_ui.job_io import (
    JobSummary,
    count_cli_rows_outside_output_tree,
    count_malformed_cli_entries,
    count_malformed_ui_job_entries,
    load_all_jobs_read_only,
)
from web.operator_ui.page_header import render_page_header
from web.operator_ui.pages._daily_decision_helpers import (
    list_recommendation_artifacts,
    load_trainer_sidecar_sha,
    picks_table_rows,
)
from web.operator_ui.pages._daily_review_progress_helpers import (
    DailyReviewProgress,
    summarise_daily_review_progress,
)
from web.operator_ui.pages._ops_cockpit_helpers import (
    bundle_calendar_tail,
    bundle_freshness,
    recommender_integrity_check,
)
from web.operator_ui.pages._today_decision_queue_helpers import (
    TodayQueueItem,
    build_today_decision_queue,
    queue_counts,
    queue_page_link,
)
from web.operator_ui.pages._today_workbench_helpers import (
    DailySignalSummary,
    summarise_daily_signal,
    summarise_operations,
)
from web.operator_ui.update_ledger import (
    consecutive_failures,
    ledger_path_for_provider,
    read_ledger,
)
from web.operator_ui.update_status import (
    RUNNING_FRESH,
    RUNNING_STALE,
    UpdateRunStatus,
    classify_running,
    read_update_status,
    record_matches_provider,
    status_path_for_provider,
)

_CardColor = Literal["default", "positive", "negative", "warning"]
_HEALTH_COLORS: dict[str, _CardColor] = {
    "ok": "positive",
    "warning": "warning",
    "error": "negative",
    "unconfigured": "warning",
}


def _render_card(
    label: str,
    value: str,
    detail: str,
    *,
    color: _CardColor = "default",
    secondary: list[tuple[str, str]] | None = None,
) -> None:
    render_stat_card(label, value, secondary=secondary, value_color=color)
    st.caption(detail)


def _render_update_summary(
    provider_path: Path,
) -> tuple[UpdateRunStatus | None, bool | None, str | None]:
    try:
        status_path = status_path_for_provider(provider_path)
    except ValueError as exc:
        _render_card("上次数据更新", "无法建立", str(exc), color="negative")
        return None, None, str(exc)

    update = read_update_status(status_path)
    matches_provider = (
        record_matches_provider(update, provider_path)
        if update.kind not in ("missing", "corrupt") else None
    )
    if matches_provider is False:
        _render_card(
            "上次数据更新",
            "来源不匹配",
            "状态工件属于另一个 provider；不会把它当作当前数据的更新结果。",
            color="negative",
        )
    elif update.kind == "missing":
        _render_card(
            "上次数据更新",
            "尚无记录",
            "首次更新前的正常空状态；可在运行中心查看或补跑。",
            color="warning",
        )
    elif update.kind == "corrupt":
        _render_card("上次数据更新", "状态损坏", update.error, color="negative")
    elif update.kind == "running":
        running_class = classify_running(update)
        if running_class == RUNNING_FRESH:
            _render_card(
                "上次数据更新",
                "正在更新",
                f"始于 {update.started_at or '未记录'}；运行中心提供日志与刷新。",
                color="warning",
            )
        elif running_class == RUNNING_STALE:
            _render_card(
                "上次数据更新",
                "状态待核实",
                "running 记录已陈旧；请在运行中心查看日志，勿把它当作仍在运行。",
                color="warning",
            )
        else:
            _render_card(
                "上次数据更新",
                "状态待核实",
                "running 记录的起始时间无法核实；请在运行中心查看日志。",
                color="warning",
            )
    elif update.ok:
        _render_card(
            "上次数据更新",
            "更新成功",
            update.detail or "已完成；运行中心保留完整状态与日志。",
            color="positive",
            secondary=[("run_date", update.run_date or "未记录")],
        )
    else:
        _render_card(
            "上次数据更新",
            f"更新失败（exit {update.exit_code}）",
            f"{update.exit_meaning}；失败阶段：{update.failed_stage or '未记录'}。",
            color="negative",
        )
    return update, matches_provider, None


def _render_recent_runs(provider_dir: Path) -> None:
    """近几次更新运行的形态 —— 让「连着栽」一眼可见。

    2026-08-17 / 08-20 / 08-21 连着三晚失败，拖到第三晚才被发现。队列的严重度
    是从出单侧的陈旧度裁决推的，而那个裁决看的是 bundle 日期——**bundle 日期只
    在成功时才动**，所以它对「连败」只是间接证据。这条带直接把台账里记下的
    形态摆出来。

    只**复现**台账记下的东西，不自造任何判定：谁失败、失败在哪一阶段，都是写入
    侧当时写下的。
    """
    try:
        path = ledger_path_for_provider(provider_dir)
    except ValueError as exc:
        st.caption(f"近期运行：无法定位运行台账 —— {exc}")
        return
    history = read_ledger(path, provider_dir=provider_dir)
    if history.kind == "missing":
        # 空条带与「台账不在」对操作人长得一样，除非页面说出是哪一种 ——
        # 这一页已经为「留白读起来像没有更多可说」付过一次学费。
        st.caption(f"近期运行：还没有运行台账（`{path.name}`）——首次运行之后才会有。")
        return
    if history.kind == "unreadable":
        st.caption(f"近期运行：运行台账读不了 —— {history.error}")
        return
    notes = []
    if history.malformed:
        notes.append(f"{history.malformed} 行读不了")
    if history.foreign:
        notes.append(f"{history.foreign} 行属于别的 provider")
    note_text = f"（{'；'.join(notes)}）" if notes else ""
    if not history.runs:
        # 计数必须**在这条分支里也说出来**：整份台账全坏时 `runs` 同样为空，
        # 而只说「还没有记录」会把一份损坏的历史讲成良性的空历史（codex P2）。
        st.caption(
            f"近期运行：台账里还没有属于本 provider 的可用记录"
            f"（`{path.name}`）。{note_text}")
        return
    marks = " ".join(
        ("✅" if run.ok else "❌") + (run.run_date[5:] or "?") for run in history.runs)
    streak = consecutive_failures(history)
    tail = ""
    if streak >= 2:
        tail = f" —— **最近连续 {streak} 次失败**"
    elif streak == 1:
        tail = " —— 最近一次失败"
    st.caption(f"近期运行（新→旧）：{marks}{tail}。{note_text}")


def _render_queue_item(item: TodayQueueItem) -> None:
    label = {
        "blocker": "阻塞",
        "attention": "需关注",
        "in_progress": "进行中",
        "review": "可审阅",
        "information": "信息",
    }[item.kind]
    st.markdown(f"**{label} · {item.title}**")
    st.caption(item.detail)
    if item.source_time:
        st.caption(f"来源时间：{item.source_time}")
    page, query_params = queue_page_link(item)
    if page == "pages/jobs.py":
        # Status alone is insufficient as a cross-page handoff: the operator
        # can leave Jobs after changing its widget, then follow another queue
        # link requesting the same status.  Mint a navigation-only token so
        # Jobs applies this specific request exactly once without changing the
        # read-only queue item or any job/artifact state.
        query_params = {**(query_params or {}), "handoff": uuid4().hex}
    st.page_link(page, label="前往详情", query_params=query_params)


def _render_signal_summary(signal: DailySignalSummary) -> None:
    if signal.kind == "hold":
        _render_card(
            "最新日度信号",
            "HOLD · 监控",
            signal.detail,
            color="warning",
            secondary=[
                ("as_of", signal.as_of_date or "未记录"),
                ("下一再平衡日", signal.next_rebalance_date or "未记录"),
            ],
        )
    elif signal.kind == "rebalance":
        _render_card(
            "最新日度信号",
            "再平衡日 · 待人工核对",
            signal.detail,
            color="warning",
            secondary=[
                ("as_of", signal.as_of_date or "未记录"),
                ("entry", signal.entry_date or "未记录"),
            ],
        )
    elif signal.kind == "daily":
        _render_card(
            "最新日度信号",
            "日频工件 · 待人工核对",
            signal.detail,
            color="warning",
            secondary=[
                ("as_of", signal.as_of_date or "未记录"),
                ("entry", signal.entry_date or "未记录"),
            ],
        )
    elif signal.kind == "missing":
        _render_card(
            "最新日度信号",
            "尚无工件",
            "请先在运行中心生成日度信号；本页不会代跑。",
            color="warning",
        )
    else:
        _render_card("最新日度信号", "需要核查", signal.detail, color="negative")


render_page_header(
    "今日工作台",
    "先核对数据、服务身份与日度信号，再进入对应的执行或人工决策页面。"
    "本页只汇总既有工件，不运行策略、不下单、不授予交易许可。",
)
st.caption(
    "状态卡是导航摘要：数据健康不是授权结论，日度信号不是自动订单；"
    "请在详情页核对来源、HOLD 与 entry_date。"
)

provider = anchored_to_repo(resolve_default_provider_uri())
provider_problem = ""
if not provider.strip():
    provider_problem = "未从 config.yaml 解析出 provider_uri。"
else:
    provider_problem = unusable_path_reason(provider) or ""
# 先算健康摘要:它是新鲜度裁决的一个**输入**(见下),不是并列的另一个结论。
health: BundleHealthSummary | None = (
    None if provider_problem else summarise_bundle_health(provider))
# 出单侧对 bundle 陈旧的判定,**照抄生产运维页 ⑤ 的取法**，一个字节都不自己算:
# 时钟用出单侧的宿主本地日(不是面向操作人的 CN 日历日)、边界用出单侧的
# `behind > limit`(14 天整仍接受)、末日读 `calendars/day.txt`(不是
# `summarise_bundle_health` 偏好的 `_fetch_integrity` 身份戳)。
#
# #461 首版在这里另写了一份,三个决策全错,三条 P1 —— 而这套判据早就存在、
# 且被 `test_ops_cockpit_page_source.py` 的多条守卫钉着。
_cal_tail = bundle_calendar_tail(provider)
_integrity = recommender_integrity_check(provider)
_freshness = bundle_freshness(
    # 不传 today:默认就是出单侧那个时钟。让调用点无从写错,好过要求每个调用点
    # 都记得写对。
    tail_date=(
        _cal_tail.tail.isoformat()
        if _cal_tail.known and _cal_tail.tail else None),
    provider_uri=health.provider_uri if health is not None else provider,
    message=(
        _cal_tail.reason if not _cal_tail.known
        else (health.message if health is not None else "")),
    # 健康摘要只能**扣分不能加分**:它刻意宽容,吞掉坏的 integrity 戳后仍可
    # 能返回 ok,所以真正的闸是下面那个 integrity。
    health_status=health.status if health is not None else "ok",
    health_warnings=health.warnings if health is not None else (),
    integrity_accepted=_integrity.accepted,
    integrity_reason=_integrity.reason,
)
update_status: UpdateRunStatus | None = None
update_matches_provider: bool | None = None
update_error: str | None = None

data_col, update_col = st.columns(2)
with data_col:
    if provider_problem:
        _render_card("数据包健康", "无法建立", provider_problem, color="negative")
    elif health is None:
        _render_card("数据包健康", "无法建立", "健康摘要不可用。", color="negative")
    else:
        health_value = health.tail_date or {
            "ok": "数据可读",
            "warning": "存在警告",
            "error": "无法使用",
            "unconfigured": "未配置",
        }.get(health.status, "状态未知")
        _render_card(
            "数据包健康",
            health_value,
            health.message,
            color=_HEALTH_COLORS.get(health.status, "warning"),
            secondary=[
                ("状态", health.status),
                (
                    "股票数",
                    str(health.instrument_count)
                    if health.instrument_count is not None else "未记录",
                ),
            ],
        )

with update_col:
    if provider_problem:
        _render_card(
            "上次数据更新",
            "无法建立",
            "未使用空或不可用的 provider 路径读取状态工件。",
            color="negative",
        )
    else:
        update_status, update_matches_provider, update_error = _render_update_summary(
            Path(provider)
        )
        # 摘要说「这一次」，条带说「最近几次」—— 后者才看得出连败。
        _render_recent_runs(Path(provider))

identity_col, signal_col = st.columns(2)
incumbent_detail = ""
with identity_col:
    incumbent = resolve_incumbent()
    if incumbent.is_ensemble:
        incumbent_detail = "当前 serving manifest 指向 ensemble；仅供身份核对。"
        _render_card(
            "现任服务身份",
            f"ensemble · {len(incumbent.members)} 成员",
            "这是 serving manifest 的解析结果，不是认证或交易授权结论。",
            secondary=[("manifest", Path(str(incumbent.manifest_path)).name)],
        )
    elif incumbent.kind == "single":
        incumbent_detail = "当前 serving manifest 显式声明单模型形态；仅供身份核对。"
        _render_card(
            "现任服务身份",
            "单模型形态",
            "这是 QUANT_ENSEMBLE_MANIFEST 显式设为 none 的 opt-out，不是缺省推断。",
            color="warning",
        )
    else:
        incumbent_detail = incumbent.error or "现任 manifest 无法解析；请勿据此判断信号来源。"
        _render_card(
            "现任服务身份",
            "无法确认",
            incumbent.error or "现任 manifest 无法解析；请勿据此判断信号来源。",
            color="negative",
        )

with signal_col:
    signal_payload: dict[str, Any] | None = None
    artifacts = list_recommendation_artifacts()
    if artifacts:
        artifact_date, artifact_path = artifacts[0]
        artifact_read = read_json_artifact(
            artifact_path, artifact_name="daily_recommendation"
        )
        signal = summarise_daily_signal(
            artifact_date,
            artifact_read.value,
            incumbent=incumbent,
            current_model_sha=load_trainer_sidecar_sha(resolve_model_path()),
            read_error=(
                artifact_read.issue.message if artifact_read.issue is not None else None
            ),
        )
        if isinstance(artifact_read.value, dict):
            signal_payload = artifact_read.value
    else:
        signal = summarise_daily_signal(
            None,
            None,
            incumbent=incumbent,
            current_model_sha=None,
        )
    _render_signal_summary(signal)

st.subheader("运行状态")
all_jobs: tuple[JobSummary, ...] = ()
jobs_error: str | None = None
try:
    all_jobs = tuple(load_all_jobs_read_only())
    operations = summarise_operations(all_jobs)
    malformed_cli_entries = count_malformed_cli_entries()
    malformed_ui_entries = count_malformed_ui_job_entries()
    outside_output_entries = count_cli_rows_outside_output_tree()
except (OSError, RuntimeError, ValueError) as exc:
    jobs_error = f"作业目录无法汇总：{type(exc).__name__}: {exc}"
    _render_card(
        "当前运行或异常",
        "无法读取",
        f"作业目录无法汇总：{type(exc).__name__}: {exc}",
        color="negative",
    )
else:
    job_verification_reasons: list[str] = []
    if malformed_cli_entries:
        job_verification_reasons.append(
            f"作业目录含 {malformed_cli_entries} 行损坏的 CLI 索引记录"
        )
    if malformed_ui_entries:
        job_verification_reasons.append(
            f"作业目录含 {malformed_ui_entries} 个无法读取或结构无效的 UI 作业记录"
        )
    if outside_output_entries:
        job_verification_reasons.append(
            f"另有 {outside_output_entries} 行 CLI 记录的产物目录在可读边界外"
        )
    if job_verification_reasons:
        jobs_error = "；".join(job_verification_reasons) + "；当前作业状态需要核验。"
        _render_card("当前运行或异常", "需要核验", jobs_error, color="negative")
    elif operations.kind == "running":
        _render_card(
            "当前运行或异常",
            "有作业运行中",
            operations.detail,
            color="warning",
            secondary=[("run_id", operations.job.run_id if operations.job else "未记录")],
        )
    elif operations.kind == "pending":
        _render_card(
            "当前运行或异常",
            "作业等待启动",
            operations.detail,
            color="warning",
            secondary=[("run_id", operations.job.run_id if operations.job else "未记录")],
        )
    elif operations.kind == "attention":
        _render_card(
            "当前运行或异常",
            "最近作业需要处理",
            operations.detail,
            color="negative",
            secondary=[("run_id", operations.job.run_id if operations.job else "未记录")],
        )
    else:
        _render_card("当前运行或异常", "全部空闲", operations.detail)

review: DailyReviewProgress | None = None
review_error: str | None = None
if signal.kind in {"daily", "rebalance"}:
    if signal_payload is None:
        review_error = "有效信号没有可读取的原始 payload，无法核验人工审阅进度。"
    else:
        try:
            candidate_rows = picks_table_rows(signal_payload)  # shared shape boundary
            candidate_codes = [str(row.get("代码") or "") for row in candidate_rows]
            journal = read_journal()
            if journal.malformed_count:
                review_error = f"决策日志含 {journal.malformed_count} 行坏行，审阅进度需要核验。"
            else:
                review = summarise_daily_review_progress(
                    signal.as_of_date or "", candidate_codes, journal.effective,
                )
        except (DecisionJournalError, ValueError) as exc:
            review_error = f"人工审阅进度不可读：{type(exc).__name__}: {exc}"

update_detail = ""
update_time = ""
update_kind = None
update_running_class = None
if update_status is not None:
    update_kind = update_status.kind
    update_time = update_status.finished_at or update_status.started_at
    if update_matches_provider is False:
        update_detail = "状态工件属于另一个 provider；不会把它当作当前数据的更新结果。"
    elif update_status.kind == "corrupt":
        update_detail = update_status.error
    elif update_status.kind == "running":
        update_detail = update_status.detail or update_status.started_at or "状态记录未给出开始时间。"
        update_running_class = classify_running(update_status)
    elif update_status.kind == "finished" and not update_status.ok:
        update_detail = (
            f"{update_status.exit_meaning}；失败阶段："
            f"{update_status.failed_stage or '未记录'}。"
        )

queue = build_today_decision_queue(
    provider_problem=provider_problem or None,
    bundle_status=health.status if health is not None else None,
    bundle_detail=health.message if health is not None else provider_problem,
    update_kind=update_kind,
    update_detail=update_detail or update_error or "",
    update_time=update_time,
    update_matches_provider=update_matches_provider,
    update_running_class=update_running_class,
    # 「更新失败」排多严重,由**出单侧自己的**新鲜度判定说了算。
    bundle_refuses_today=_freshness.refuses_today,
    bundle_integrity_accepted=_freshness.integrity_accepted,
    bundle_headroom_days=_freshness.headroom_days,
    bundle_max_age_days=_freshness.max_age_days,
    signal=signal,
    jobs=all_jobs,
    jobs_error=jobs_error,
    review=review,
    review_error=review_error,
    incumbent_kind=incumbent.kind,
    incumbent_detail=incumbent_detail,
)
counts = queue_counts(queue)
st.subheader("今日待办")
count_cols = st.columns(5)
for column, (kind, label) in zip(count_cols, (
    ("blocker", "阻塞"),
    ("attention", "需关注"),
    ("in_progress", "进行中"),
    ("review", "可审阅"),
    ("information", "信息"),
), strict=True):
    column.metric(label, counts[kind])
if not counts["blocker"]:
    st.success("当前没有阻塞项；这不是交易、持仓或订单已经执行的结论。")
primary_items = [item for item in queue if item.kind in {"blocker", "attention"}]
for item in primary_items:
    _render_queue_item(item)
secondary_items = [item for item in queue if item.kind not in {"blocker", "attention"}]
if secondary_items:
    with st.expander("查看进行中、可审阅与信息项", expanded=False):
        for item in secondary_items:
            _render_queue_item(item)

st.markdown("---")
st.subheader("按任务继续")
daily_col, research_col, governance_col = st.columns(3)
with daily_col:
    st.markdown("**日常决策**")
    st.caption("更新数据、生成日度信号，再进入人工决策详情。")
    st.page_link("pages/run_center.py", label="运行中心")
    st.page_link("pages/daily_decision.py", label="日度信号与人工决策")
with research_col:
    st.markdown("**研究与验证**")
    st.caption("启动研究配置，查看作业、单次结果、滚动验证与研究运行对比。")
    st.page_link("pages/config_run.py", label="配置运行")
    st.page_link("pages/jobs.py", label="作业")
    st.page_link("pages/results.py", label="结果")
    st.page_link("pages/walk_forward.py", label="滚动验证")
    st.page_link("pages/research_run_comparison.py", label="研究运行对比")
with governance_col:
    st.markdown("**生产治理**")
    st.caption("核对服务身份、认证与生产数据包健康。")
    st.page_link("pages/ops_cockpit.py", label="生产运维")
    st.page_link("pages/data_inspect.py", label="数据检视")
