"""Development demo page for operator UI design-system tokens."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

import streamlit as st

from web.operator_ui.components import (
    render_accordion,
    render_badge,
    render_button,
    render_card,
    render_empty_state,
    render_error_state,
    render_field,
    render_icon_button,
    render_modal,
    render_progress_bar,
    render_skeleton,
    render_spinner,
    render_stat_card,
    render_table,
    render_tabs,
    render_tag,
    render_toast,
    render_tooltip,
)
from web.operator_ui.formatting import (
    format_date_absolute,
    format_duration,
    format_money,
    format_number,
    format_percent,
    format_relative_time,
)
from web.operator_ui.page_header import render_breadcrumbs, render_page_header

render_breadcrumbs([("系统", None)])
render_page_header("设计系统")
st.caption("运维 UI 的视觉 token 与展示格式示例。")

st.header("颜色 Token")

_SWATCHES = (
    ("--bg-page", "var(--bg-page)"),
    ("--bg-card", "var(--bg-card)"),
    ("--text-primary", "var(--text-primary)"),
    ("--text-secondary", "var(--text-secondary)"),
    ("--brand-primary", "var(--brand-primary)"),
    ("--positive", "var(--positive)"),
    ("--negative", "var(--negative)"),
    ("--warning", "var(--warning)"),
    ("--info", "var(--info)"),
    ("--neutral", "var(--neutral)"),
    ("--chart-strategy", "var(--chart-strategy)"),
    ("--chart-benchmark", "var(--chart-benchmark)"),
)

swatch_html = ['<div class="qv2-token-grid">']
for label, color in _SWATCHES:
    swatch_html.append(
        '<div class="qv2-swatch">'
        f'<div class="qv2-swatch-color" style="background: {color};"></div>'
        f'<div class="qv2-swatch-label">{label}</div>'
        "</div>"
    )
swatch_html.append("</div>")
st.markdown("\n".join(swatch_html), unsafe_allow_html=True)

st.header("排版")
st.markdown(
    """
<div class="qv2-card">
  <div class="qv2-text-page-title">页面标题样式</div>
  <div class="qv2-text-card-label">卡片标签样式</div>
  <div class="qv2-text-metric-primary">1,234.56</div>
  <div class="qv2-muted">辅助文本使用 muted token。</div>
</div>
""",
    unsafe_allow_html=True,
)

st.header("格式化辅助函数")

now = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)
examples = [
    {"函数": "format_percent", "输入": "0.1834", "输出": format_percent(0.1834)},
    {"函数": "format_percent", "输入": "-0.0245", "输出": format_percent(-0.0245)},
    {"函数": "format_number", "输入": "1234567", "输出": format_number(1_234_567)},
    {
        "函数": "format_number(abbreviate)",
        "输入": "1234567",
        "输出": format_number(1_234_567, abbreviate=True),
    },
    {"函数": "format_money", "输入": "1234567.5", "输出": format_money(1_234_567.5)},
    {"函数": "format_duration", "输入": "3725", "输出": format_duration(3725)},
    {
        "函数": "format_relative_time",
        "输入": "2026-05-21T10:30:00+00:00",
        "输出": format_relative_time("2026-05-21T10:30:00+00:00", now=now),
    },
    {
        "函数": "format_date_absolute",
        "输入": "2026-05-21T10:30:00+00:00",
        "输出": format_date_absolute("2026-05-21T10:30:00+00:00", style="datetime"),
    },
    {"函数": "缺失值", "输入": "None", "输出": format_number(None)},
]

st.dataframe(examples, width="stretch", hide_index=True)

# ---------------------------------------------------------------------------
# Component showcase
# ---------------------------------------------------------------------------
st.header("徽章 Badge")

badge_cols = st.columns(5)
# Annotate the list element type so mypy keeps the variant literals
# narrow when passed to ``render_badge``, which expects a Literal.
_BadgeVariant = Literal["neutral", "info", "success", "warning", "danger"]
_badge_specs: list[tuple[_BadgeVariant, str, str, bool]] = [
    ("neutral", "排队中", "⏸", False),
    ("info", "运行中", "", True),
    ("success", "已完成", "✅", False),
    ("warning", "已取消", "⊘", False),
    ("danger", "失败", "❌", False),
]
for idx, (variant, label, icon, pulse) in enumerate(_badge_specs):
    with badge_cols[idx]:
        render_badge(variant, label, icon=icon, pulse=pulse)

st.header("指标卡 StatCard")

sc_cols = st.columns(3)
with sc_cols[0]:
    render_stat_card("年化收益", "+18.34%", trend="up", value_color="positive")
with sc_cols[1]:
    render_stat_card(
        "最大回撤",
        "-12.45%",
        trend="down",
        value_color="negative",
        secondary=[("波动率", "16.0%"), ("持续天数", "28 天")],
    )
with sc_cols[2]:
    render_stat_card(
        "夏普比率",
        "1.83",
        tooltip="风险调整收益。越高越好；> 1 即为优秀。",
    )

st.header("骨架屏 Skeleton")
render_skeleton("rect", height="48px")
render_skeleton("text", width="60%")
render_skeleton("text", width="80%")
render_skeleton("text", width="40%")

st.header("空状态 EmptyState")
render_empty_state(
    "🔁",
    "暂无滚动验证记录",
    "在滚动时间窗上验证你的策略稳定性。",
    action_label="启动一次运行",
)

st.header("错误状态 ErrorState")
render_error_state(
    "运行未找到",
    "未找到对应 ID 的运行记录，可能已被删除。",
    error="KeyError: run_id='pipeline_xxxx_yyyy'",
    on_retry="window.location.reload()",
    variant="inline",
)

st.header("按钮控件")
control_cols = st.columns(4)
with control_cols[0]:
    render_button("运行", variant="primary", icon=">")
with control_cols[1]:
    render_button("取消", variant="secondary")
with control_cols[2]:
    render_icon_button("R", "刷新")
with control_cols[3]:
    render_button("删除", variant="danger", disabled=True)

st.header("标签 Tag 与 Tooltip")
tag_cols = st.columns(4)
with tag_cols[0]:
    render_tag("UI", variant="info")
with tag_cols[1]:
    render_tag("CLI", variant="neutral")
with tag_cols[2]:
    render_tag("失败", variant="danger", removable=True)
with tag_cols[3]:
    render_tooltip("IR", "信息比率，来源于 qlib 规范化输出。")

st.header("卡片 / 选项卡 / 折叠面板")
render_card("可复用卡片", ["基于设计 token", "不直接读运行时数据"])
render_tabs(["持仓", "交易", "配置"], active_index=1)
render_accordion("高级选项", "折叠面板内容使用原生 details/summary 标签。")

st.header("反馈组件 Feedback")
feedback_cols = st.columns(3)
with feedback_cols[0]:
    render_toast("配置已复制。", title="已复制", variant="success")
with feedback_cols[1]:
    render_progress_bar(62, label="训练进度")
with feedback_cols[2]:
    render_spinner("正在启动作业")

st.header("表单字段 Form Field")
render_field(
    "Provider URI",
    '<input class="qv2-field-control" value="D:/qlib_data/cn" aria-label="Provider URI" />',
    help_text="仅演示字段，无真实交互。",
    required=True,
)

st.header("表格 Table")
render_table(
    ["运行", "状态", "来源"],
    [
        ["pipeline_54f50f26", "已完成", "UI"],
        ["walk_forward_bea7395", "运行中", "CLI"],
    ],
    caption="设计系统表格示例",
)

st.header("弹窗 Modal")
render_modal(
    "确认删除",
    "静态弹窗预览。页面级流程会接上实际的 Streamlit 操作。",
    footer="取消 | 删除",
)

# ---------------------------------------------------------------------------
st.info(
    "本页面仅供 QA / 设计演示。不读取任何运行时产物，也不计算任何官方指标。"
)
