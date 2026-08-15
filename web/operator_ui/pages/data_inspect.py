"""数据检视 — read-only inspector of the PRODUCTION qlib bundle (P3-6b / U3).

U3 retired the UI's tushare ingest path; this page is the promised THIN
replacement: it only INSPECTS the production bundle — the fetch-integrity stamp
(P3-4c), the bundle-health summary, and an on-demand SUBPROCESS run of the 06
PIT validator — and never builds, ingests, or mutates anything. Bundles are
made by the pipeline (``scripts/daily_update.py`` / ``scripts/data_pipeline``),
not by the UI.

READ-ONLY is a hard contract here, enforced by a governance test: this module
must not contain any write-side filesystem API. The validator runs in a
subprocess (qlib is a per-process singleton), so this page never imports the
validator or the qlib runtime — only the subprocess runner's parsed result.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from src.data.pit.bundle_integrity import (
    BundleIntegrityError,
    read_bundle_integrity,
)
from web.operator_ui.bundle_health import (
    normalize_provider_uri,
    resolve_default_provider_uri,
    summarise_bundle_health,
)
from web.operator_ui.page_header import render_page_header
from web.operator_ui.pit_validation_runner import run_pit_validation
from web.operator_ui.update_status import (
    RUNNING_FRESH,
    RUNNING_STALE,
    RUNNING_STALE_AFTER,
    classify_running,
    read_update_status,
    record_matches_provider,
    status_path_for_provider,
)

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
provider_dir = Path(normalize_provider_uri(provider_uri.strip()))

# ---------------------------------------------------------------------------
# Section 0: last data-update run status (2026-08-14-daily-update-run-status).
# The scheduled overnight refresh writes one machine-readable record per run as
# a SIBLING of the provider dir; this section renders it read-only so the
# operator sees "did the last refresh succeed, and if not, where did it die"
# without leaving the UI. Missing file = never recorded (fresh machine), not an
# error; a corrupt record is shown loud, never defaulted.
# ---------------------------------------------------------------------------
st.subheader("上次数据更新")
try:
    _derived_status = status_path_for_provider(provider_dir)
except ValueError as _exc:
    # A filesystem-root provider has no sibling to derive the artifact from.
    # Say so; a traceback here would take the whole page down (codex #434 r5).
    st.error(f"⚠ {_exc}")
    _derived_status = None
# The scheduler may run the updater with the advertised `--status-path`
# override, and the writer then records somewhere this page's derivation
# never looks — the operator would see 从未记录 (or a stale older record)
# while last night's run sits in the custom file (codex #434 r10). Same
# "paths explicit" discipline as the provider input: default = the derived
# location, override to mirror the scheduler's flag.
_status_input = st.text_input(
    "状态工件路径（若计划任务用了 --status-path 覆盖，请填同一路径）",
    value=str(_derived_status) if _derived_status else "",
    # KEYED to the provider: Streamlit keeps a widget's state across reruns
    # and applies `value=` only on first creation, so editing provider_uri
    # above would leave this box holding the PREVIOUS provider's derived
    # path — the bundle sections would inspect the new provider while this
    # section silently read the old provider's artifact (codex #434 r13).
    # A provider-derived key makes the widget a NEW widget when the provider
    # changes, re-applying the fresh default; an operator override typed for
    # the current provider still survives that provider's own reruns.
    key=f"data_inspect::status_path::{provider_dir}",
    help="默认从 provider 路径派生（<provider>.daily_update_status.json）。"
         "本页对该文件只读。",
)
_status_file = Path(_status_input.strip()) if _status_input.strip() else None
if _status_file is None:
    st.info("无可用的状态工件路径——请在上方填写计划任务实际写入的位置。")
_update_status = read_update_status(_status_file) if _status_file else None
if _update_status is None:
    pass
elif (_update_status.kind not in ("missing", "corrupt")
        and not record_matches_provider(_update_status, provider_dir)):
    # The record names a DIFFERENT provider: two schedules pointing one
    # explicit --status-path at the same file race through the same .tmp,
    # and this file holds whichever finished last. Presenting it as THIS
    # provider's status would be the r4 mix-up through a new door
    # (codex #434 r18).
    st.error(
        f"⚠ 该状态记录属于**另一个 provider**"
        f"（记录:`{_update_status.provider_dir or '未标注'}`;"
        f"当前:`{provider_dir}`）——多个计划任务可能把同一个 --status-path "
        "指向了这份文件。请为每个 provider 配置各自的状态路径;"
        "本页拒绝把别人的运行当作本 bundle 的状态展示。"
    )
elif _update_status.kind == "missing":
    st.info(
        "从未记录数据更新运行（状态文件不存在）。新机或首跑前属正常；"
        "若计划任务已在运行，请确认其写权限；若它用了 --status-path 覆盖，"
        "请在上方路径框填写同一位置。"
    )
elif _update_status.kind == "corrupt":
    st.error(
        f"⚠ 更新状态记录损坏（绝不用默认值顶替）：{_update_status.error}"
        f"（文件：{_update_status.path}）"
    )
elif _update_status.kind == "running":
    # Classified by the reader (pure + tested), not inline: the r8 inline
    # comparison let a negative age (future timestamp / clock skew) pass the
    # upper-bound-only check, and its unknown-age fallback asserted
    # 已超过 6 小时 about an age nobody computed (codex #434 r9).
    _cls = classify_running(_update_status)
    if _cls == RUNNING_FRESH:
        st.info(
            f"🔄 数据更新**正在运行**：始于 {_update_status.started_at or '?'} "
            f"（run_date={_update_status.run_date or '?'}）。完成后本小节显示终态。"
        )
    elif _cls == RUNNING_STALE:
        st.warning(
            f"⚠ 状态记录停在**运行中**且已超过 "
            f"{int(RUNNING_STALE_AFTER.total_seconds() // 3600)} 小时"
            f"（始于 {_update_status.started_at or '?'}，"
            f"run_date={_update_status.run_date or '?'}）——"
            "进程可能已被中断（断电/被杀/未处理异常），也可能仍在异常缓慢地运行;"
            "本页无法区分。请检查计划任务日志;下次运行会覆盖此记录。"
        )
    else:
        st.warning(
            f"⚠ 状态记录自称**运行中**，但其起始时间无法核实"
            f"（`{_update_status.started_at or '缺失'}` —— 不可解析、无时区，"
            "或在未来/时钟偏移）——本页**无法确认**它是否仍在运行,也无法给出"
            "已运行时长。请检查计划任务日志;下次运行会覆盖此记录。"
        )
elif _update_status.ok:
    st.success(
        f"🟢 上次更新成功（exit 0 — {_update_status.exit_meaning}）："
        f"run_date={_update_status.run_date or '?'}，"
        f"{_update_status.started_at or '?'} → {_update_status.finished_at or '?'}"
        f"{('。' + _update_status.detail) if _update_status.detail else ''}"
    )
else:
    st.error(
        f"🔴 上次更新**失败**：exit {_update_status.exit_code} "
        f"（{_update_status.exit_meaning}），"
        f"失败阶段：**{_update_status.failed_stage or '?'}** — "
        f"{_update_status.detail or '（无详情）'}。"
        f"run_date={_update_status.run_date or '?'}，"
        f"失败于 {_update_status.finished_at or '?'}。排查后重跑。"
    )

# The existence check comes AFTER the status section on purpose: the artifact
# is a SIBLING of the provider dir, so on a fresh machine whose first update
# died before the swap it exists while the provider dir does not. Stopping
# first would hide the one record that explains why (codex #434 r4).
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
# Section 3: thin 06 validator — on-demand, read-only, in a SUBPROCESS.
# qlib is a per-process singleton, so running the validator in-process would
# hard-fail the moment this UI session had initialized qlib for another
# provider; the subprocess runner gives every run a fresh interpreter and
# returns the CLI's structured report (parsed dicts), which is all this page
# renders. Nothing here touches the bundle but read-only validation.
# ---------------------------------------------------------------------------
st.subheader("PIT 校验(06_validate,只读)")
st.caption(
    "校验在独立子进程中运行(全新解释器,UI 进程不加载 qlib),因此可校验任意 "
    "provider_uri、可重复运行,无需重启 UI。"
)
registry_default = str(provider_dir.parent / "tushare_raw" / "delisted_registry.parquet")
registry_path = st.text_input(
    "delisted_registry.parquet 路径",
    value=registry_default,
    help="校验 NaN-after-delist 等检查所需的退市登记表。",
)
if st.button("运行校验(只读,子进程,可能需要数十秒)"):
    reg = Path(registry_path.strip())
    if not reg.exists():
        st.error(f"登记表不存在:{reg}")
    else:
        with st.spinner("正在子进程中对生产 bundle 运行 PIT 校验 …"):
            result = run_pit_validation(provider_dir, reg)
        if result.kind != "ok":
            # timeout / launch_failed / run_failed / corrupt_report — all
            # fail-loud, never a silent default.
            st.error(f"校验无法运行({result.kind}):{result.error}")
        else:
            badge = "🟢 全部通过" if result.exit_code == 0 else (
                "🟡 有警告" if result.exit_code == 1 else "🔴 有失败"
            )
            st.write(
                f"结果:**{badge}**(exit_code={result.exit_code},"
                f"用时 {result.elapsed_s:.0f}s)"
            )
            st.dataframe(
                [
                    {
                        "check": c["code"], "name": c["name"],
                        "passed": "✅" if c["passed"] else "❌",
                        "warnings": len(c["warnings"]),
                        "errors": len(c["errors"]),
                    }
                    for c in result.checks
                ],
                width="stretch",
            )
            for c in result.checks:
                if c["errors"] or c["warnings"]:
                    with st.expander(f"{c['code']} — {c['name']}"):
                        for e in c["errors"]:
                            st.error(e)
                        for w in c["warnings"]:
                            st.warning(w)
