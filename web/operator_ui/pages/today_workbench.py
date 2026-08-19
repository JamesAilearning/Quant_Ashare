"""Today Workbench: read-only summaries for the daily operating sequence."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import streamlit as st

from web.operator_ui.artifact_reader import read_json_artifact
from web.operator_ui.bundle_health import (
    resolve_default_provider_uri,
    summarise_bundle_health,
)
from web.operator_ui.components import render_stat_card
from web.operator_ui.incumbent import (
    anchored_to_repo,
    resolve_incumbent,
    resolve_model_path,
    unusable_path_reason,
)
from web.operator_ui.job_io import load_all_jobs_read_only
from web.operator_ui.page_header import render_page_header
from web.operator_ui.pages._daily_decision_helpers import (
    list_recommendation_artifacts,
    load_trainer_sidecar_sha,
)
from web.operator_ui.pages._today_workbench_helpers import (
    DailySignalSummary,
    summarise_daily_signal,
    summarise_operations,
)
from web.operator_ui.update_status import (
    RUNNING_FRESH,
    RUNNING_STALE,
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


def _render_update_summary(provider_path: Path) -> None:
    try:
        status_path = status_path_for_provider(provider_path)
    except ValueError as exc:
        _render_card("上次数据更新", "无法建立", str(exc), color="negative")
        return

    update = read_update_status(status_path)
    if update.kind not in ("missing", "corrupt") and not record_matches_provider(
        update, provider_path
    ):
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

data_col, update_col = st.columns(2)
with data_col:
    if provider_problem:
        _render_card("数据包健康", "无法建立", provider_problem, color="negative")
    else:
        health = summarise_bundle_health(provider)
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
        _render_update_summary(Path(provider))

identity_col, signal_col = st.columns(2)
with identity_col:
    incumbent = resolve_incumbent()
    if incumbent.is_ensemble:
        _render_card(
            "现任服务身份",
            f"ensemble · {len(incumbent.members)} 成员",
            "这是 serving manifest 的解析结果，不是认证或交易授权结论。",
            secondary=[("manifest", Path(str(incumbent.manifest_path)).name)],
        )
    elif incumbent.kind == "single":
        _render_card(
            "现任服务身份",
            "单模型形态",
            "这是 QUANT_ENSEMBLE_MANIFEST 显式设为 none 的 opt-out，不是缺省推断。",
            color="warning",
        )
    else:
        _render_card(
            "现任服务身份",
            "无法确认",
            incumbent.error or "现任 manifest 无法解析；请勿据此判断信号来源。",
            color="negative",
        )

with signal_col:
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
    else:
        signal = summarise_daily_signal(
            None,
            None,
            incumbent=incumbent,
            current_model_sha=None,
        )
    _render_signal_summary(signal)

st.subheader("运行状态")
try:
    operations = summarise_operations(load_all_jobs_read_only())
except (OSError, RuntimeError, ValueError) as exc:
    _render_card(
        "当前运行或异常",
        "无法读取",
        f"作业目录无法汇总：{type(exc).__name__}: {exc}",
        color="negative",
    )
else:
    if operations.kind == "running":
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
    st.caption("启动研究配置，查看作业、单次结果与滚动验证。")
    st.page_link("pages/config_run.py", label="配置运行")
    st.page_link("pages/jobs.py", label="作业")
    st.page_link("pages/results.py", label="结果")
    st.page_link("pages/walk_forward.py", label="滚动验证")
with governance_col:
    st.markdown("**生产治理**")
    st.caption("核对服务身份、认证与生产数据包健康。")
    st.page_link("pages/ops_cockpit.py", label="生产运维")
    st.page_link("pages/data_inspect.py", label="数据检视")
