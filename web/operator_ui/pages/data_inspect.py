"""数据检视 — read-only inspector of the PRODUCTION qlib bundle (P3-6b / U3).

U3 retired the UI's tushare ingest path; this page is the promised THIN
replacement: it only INSPECTS the production bundle — the fetch-integrity stamp
(P3-4c), the bundle-health summary, and an on-demand run of the 06 PIT
validator — and never builds, ingests, or mutates anything. Bundles are made by
the pipeline (``scripts/daily_update.py`` / ``scripts/data_pipeline``), not by
the UI.

READ-ONLY is a hard contract here, enforced by a governance test: this module
must not contain any write-side filesystem API.
"""

from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

from src.core.qlib_runtime import QlibRuntimeInitError
from src.data.pit.bundle_integrity import (
    BundleIntegrityError,
    read_bundle_integrity,
)
from src.data.pit.pit_validator import PITValidator, PITValidatorError
from web.operator_ui.bundle_health import (
    _expand_env,
    resolve_default_provider_uri,
    summarise_bundle_health,
)
from web.operator_ui.page_header import render_page_header

render_page_header(
    "数据检视",
    "只读检视生产 bundle:抓取完整性戳、健康摘要、PIT 校验报告。"
    "本页不构建任何数据 — bundle 由数据管线(daily_update)产出。",
)

# ---------------------------------------------------------------------------
# Target bundle (production by default; operator may point elsewhere).
# ---------------------------------------------------------------------------
_default_uri = resolve_default_provider_uri() or ""
provider_uri = st.text_input(
    "生产 bundle 路径 (provider_uri)",
    value=_default_uri,
    help="默认从 config.yaml / QUANT_PROVIDER_URI 解析。本页对该目录只读。",
)

if not provider_uri.strip():
    st.info("配置 provider_uri 后即可检视。")
    st.stop()

# codex P2: accept the same path forms supported elsewhere — expand
# `${VAR:-default}` references (config-loader style, via the bundle_health
# expander) and a `~` prefix — before the literal existence check, so a valid
# production URI typed in a supported form is not rejected as missing.
provider_dir = Path(os.path.expanduser(_expand_env(provider_uri.strip())))
if not provider_dir.exists():
    st.error(f"目录不存在:{provider_dir}")
    st.stop()

# ---------------------------------------------------------------------------
# Section 1: fetch-integrity stamp (P3-4c) — was this bundle built from a
# complete fetch?
# ---------------------------------------------------------------------------
st.subheader("抓取完整性戳 (_fetch_integrity.json)")
try:
    integrity = read_bundle_integrity(provider_dir)
except BundleIntegrityError as exc:
    st.error(f"完整性戳损坏(fail-loud):{exc}")
else:
    if integrity is None:
        st.warning(
            "该 bundle 没有完整性戳(P3-4c 之前构建)。无法确认其抓取完整;"
            "重建 bundle 可获得戳。推荐边界默认会拒绝无戳 bundle。"
        )
    elif integrity.built_from_holey_fetch:
        st.error(
            f"⛔ 此 bundle 由 **有洞的抓取** 构建(--allow-holey-fetch),"
            f"记录 {len(integrity.holes)} 个洞;构建时间 {integrity.built_at}。"
            "推荐边界默认拒绝它(需独立的 --allow-holey-recommend)。"
        )
        st.dataframe(
            [
                {
                    "endpoint": h.endpoint, "unit": h.unit,
                    "reason": h.reason_class, "attempts": h.attempts,
                    "last_error": h.last_error,
                }
                for h in integrity.holes
            ],
            width="stretch",
        )
    else:
        st.success(f"🟢 完整抓取构建;构建时间 {integrity.built_at}。")

# ---------------------------------------------------------------------------
# Section 2: bundle health summary (FU-8 banner machinery, full view).
# ---------------------------------------------------------------------------
st.subheader("Bundle 健康摘要")
health = summarise_bundle_health(str(provider_dir))
st.write(
    f"状态:**{health.status}** — {health.message} "
    f"(tail={health.tail_date or '?'}, 标的数={health.instrument_count or '?'})"
)
for w in health.warnings:
    st.warning(w)
for e in health.errors:
    st.error(e)

# ---------------------------------------------------------------------------
# Section 3: thin 06 validator — on-demand, read-only.
# ---------------------------------------------------------------------------
st.subheader("PIT 校验(06_validate,只读)")
registry_default = str(provider_dir.parent / "tushare_raw" / "delisted_registry.parquet")
registry_path = st.text_input(
    "delisted_registry.parquet 路径",
    value=registry_default,
    help="校验 NaN-after-delist 等检查所需的退市登记表。",
)
if st.button("运行校验(只读,可能需要数十秒)"):
    reg = Path(registry_path.strip())
    if not reg.exists():
        st.error(f"登记表不存在:{reg}")
    else:
        with st.spinner("正在对生产 bundle 运行 PIT 校验 …"):
            try:
                report = PITValidator(provider_dir, reg).validate()
            except PITValidatorError as exc:
                st.error(f"校验无法运行:{exc}")
            except QlibRuntimeInitError as exc:
                # codex P2: qlib is a per-process singleton — once this UI
                # process initialized it for one provider, validating a
                # DIFFERENT provider_uri cannot re-init. Render a controlled
                # error instead of crashing the read-only page.
                st.error(
                    f"qlib 已在本 UI 进程中用另一 provider 初始化,无法切换:{exc} "
                    "重启 UI 后再校验这个 bundle。"
                )
            else:
                badge = "🟢 全部通过" if report.exit_code == 0 else (
                    "🟡 有警告" if report.exit_code == 1 else "🔴 有失败"
                )
                st.write(f"结果:**{badge}**(exit_code={report.exit_code})")
                st.dataframe(
                    [
                        {
                            "check": c.code, "name": c.name,
                            "passed": "✅" if c.passed else "❌",
                            "warnings": len(c.warnings),
                            "errors": len(c.errors),
                        }
                        for c in report.checks
                    ],
                    width="stretch",
                )
                for c in report.checks:
                    if c.errors or c.warnings:
                        with st.expander(f"{c.code} — {c.name}"):
                            for e in c.errors:
                                st.error(e)
                            for w in c.warnings:
                                st.warning(w)
