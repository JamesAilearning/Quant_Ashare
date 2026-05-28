"""Tushare data ingestion page.

Extracted from the Config & Run page (TICKET-C polish) so that data
ingestion lives on its own surface and never bleeds into the model-run
form.  The Tushare credentials remain environment-only — the token is
never read into the UI, the preview, or any persisted artifact (per
the project's hard rule on secrets).
"""

from __future__ import annotations

import os
from typing import Any

import streamlit as st

from src.core.canonical_backtest_contract import (
    ADJUST_MODE_NONE,
    ADJUST_MODE_POST,
    ADJUST_MODE_PRE,
)
from web.operator_ui.components import render_badge, render_empty_state
from web.operator_ui.config_forms import (
    TUSHARE_PROVIDER_KEYS,
    validate_config_keys,
)
from web.operator_ui.job_manager import JobManager, JobManagerError
from web.operator_ui.page_header import render_page_header


def _parse_instruments(raw: str) -> str | list[str]:
    value = str(raw or "").strip()
    if not value or value.lower() == "all":
        return "all"
    return [item.strip() for item in value.split(",") if item.strip()]


render_page_header(
    "Tushare 数据",
    "把 A 股日频数据拉到本地 qlib bin 存储里。完成后将产出的目录路径填到"
    "「配置运行」页面的 ``provider_uri`` 字段。",
)

# ---------------------------------------------------------------------------
# Token presence — fail loudly, never silently fall back (AGENTS.md #8).
# The token is read from os.environ ONLY at submit time, never rendered.
# ---------------------------------------------------------------------------
token_present = bool(os.environ.get("TUSHARE_TOKEN", "").strip())
if not token_present:
    render_empty_state(
        "\U0001f6e1",
        "未设置 TUSHARE_TOKEN",
        "拉取数据前请在运维环境里设置 TUSHARE_TOKEN。"
        "该令牌严禁出现在 YAML、配置文件或提交记录中。",
    )
    st.caption(
        "提示：把 `TUSHARE_TOKEN=…` 加进 `.env`（已在 .gitignore 中），"
        "然后重启 `streamlit run`。"
    )
    st.stop()

render_badge("success", "TUSHARE_TOKEN 已配置")
st.caption(
    "令牌只保留在运维环境里。Pull request、日志、产物里都不会再现它的值。"
)

# ---------------------------------------------------------------------------
# Form
# ---------------------------------------------------------------------------
with st.form("tushare_provider_form"):
    tc1, tc2 = st.columns(2)
    with tc1:
        ts_start_date = st.text_input(
            "起始日期 (start_date)",
            value="2025-01-01",
            help="ISO 日期，含本日。",
        )
        ts_end_date = st.text_input(
            "结束日期 (end_date)",
            value="2025-01-31",
            help="ISO 日期，含本日。",
        )
        ts_instruments = st.text_input(
            "标的池 (instruments)",
            value="all",
            help="填 ``all`` 表示全市场，或逗号分隔 qlib/Tushare 代码（例：SH600519,SZ300750）。",
        )
    with tc2:
        ts_adjust_mode = st.selectbox(
            "复权模式 (data_adjust_mode)",
            [ADJUST_MODE_PRE, ADJUST_MODE_POST, ADJUST_MODE_NONE],
            help="前复权 / 后复权 / 不复权，对应 qlib 的 adjust mode。",
        )
        include_hs300 = st.checkbox(
            "包含沪深 300 基准 (SH000300)",
            value=True,
            help="在 bin 存储里同时写入沪深 300 指数序列，作为基准。",
        )
        reuse_staged = st.checkbox(
            "复用已暂存的 Parquet (reuse_staged)",
            value=True,
            help="如果存在之前已下载的 Parquet 快照，跳过重复下载直接复用。",
        )
    pull_tushare = st.form_submit_button("拉取 Tushare 数据")

if pull_tushare:
    tushare_config: dict[str, Any] = {
        "start_date": ts_start_date,
        "end_date": ts_end_date,
        "data_adjust_mode": ts_adjust_mode,
        "instruments": _parse_instruments(ts_instruments),
        "benchmark_indexes": {"SH000300": "000300.SH"} if include_hs300 else {},
        "reuse_staged": reuse_staged,
        "region": "cn",
        "freq": "day",
    }
    try:
        validate_config_keys(tushare_config, TUSHARE_PROVIDER_KEYS)
        job_id = JobManager.start(tushare_config, "tushare_provider")
    except (ValueError, JobManagerError) as exc:
        st.error(str(exc))
        st.stop()
    st.success(f"Tushare 拉取作业已启动：{job_id}")
    st.info(
        f"完成后，把 ``output/operator_ui/results/{job_id}/qlib_provider`` "
        "作为「配置运行」页的 ``provider_uri``。"
    )
